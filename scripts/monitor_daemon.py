#!/usr/bin/env python3
"""monitor_daemon.py

盘中监控守护进程（10秒循环，纯脚本，无LLM）：
- 交易时间（默认 09:15-15:00）内，每10秒循环一次
- 非交易时间休眠到下个交易时段
- 每次循环：
  - 读取 account.json / strategy_params.json
  - 调新浪实时行情获取持仓 & watchlist 股票价格
  - 检查硬止损 / 固定止盈 / ATR追踪止盈（回撤>1.5*ATR）
  - 生成买入/卖出信号 -> 写入 data/trade_signals.json
  - **自动执行卖出交易（止损/止盈）**
  - **买入信号 -> 写pending_buy_signals.json + 唤醒OpenClaw**
  - 有信号时通过飞书直接通知
  - 追加快照到 data/intraday_snapshots/YYYY-MM-DD.json
  - 更新 account.json 中的 current_price/market_value/pnl_pct/high_since_entry

自动交易：
- 止损（pnl <= -4.2%）→ 全部卖出
- ATR追踪止盈 → 减仓55%
- 固定止盈 → 全部卖出
- 买入信号 → 唤醒LLM决策

安全措施：
- 止损后同日禁买同一只
- 每日最多自动执行10笔交易
- 所有自动交易标记 source: monitor_daemon

注意：
- 任何API失败不崩溃
- 日志：/tmp/monitor_daemon.log
- SIGTERM 优雅退出
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# 复用项目内行情/ATR逻辑
sys.path.insert(0, str(Path(__file__).parent))
from fetch_stock_data import fetch_realtime_sina, fetch_kline  # noqa: E402
from technical_analysis import calculate_hybrid_atr  # noqa: E402
from trading_engine import execute_trade, can_sell_today, get_today_stop_loss_codes  # noqa: E402

# 可转债自动交易
from cb_trading_engine import process_cb_trading  # noqa: E402
from cb_scanner import (
    fetch_cb_list as cb_fetch_cb_list,
    scan as cb_scan,
    fetch_sina_batch as cb_fetch_sina_batch,
    get_sina_bond_code as cb_get_sina_bond_code,
    get_sina_stock_code as cb_get_sina_stock_code,
)  # noqa: E402

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
SNAPSHOT_DIR = DATA_DIR / "intraday_snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

ACCOUNT_FILE = BASE_DIR / "account.json"
WATCHLIST_FILE = BASE_DIR / "watchlist.json"
STRATEGY_PARAMS_FILE = BASE_DIR / "strategy_params.json"

TRADE_SIGNALS_FILE = DATA_DIR / "trade_signals.json"
PENDING_BUY_SIGNALS_FILE = DATA_DIR / "pending_buy_signals.json"
TRANSACTIONS_FILE = BASE_DIR / "transactions.json"

LOG_FILE = Path("/tmp/monitor_daemon.log")
ALERT_STATE_FILE = Path("/tmp/monitor_daemon_alert_state.json")
DAILY_TRADE_COUNT_FILE = Path("/tmp/monitor_daemon_trade_count.json")

FEISHU_APP_ID = "cli_a902d1bb49785bb6"
FEISHU_RECEIVE_OPEN_ID = "ou_145ffee609d2803dea598344dded0299"
FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"

# 盘中定时快报 - 每30分钟发一次
_last_periodic_report = 0.0


# ------------------------- logging & utils -------------------------

def setup_logging() -> logging.Logger:
    logger = logging.getLogger("monitor_daemon")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    # avoid duplicate handlers if re-import
    if not logger.handlers:
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    return logger


def safe_load_json(path: Path, default: Any) -> Any:
    try:
        from file_lock import locked_read_json
        return locked_read_json(path, default)
    except ImportError:
        try:
            if not path.exists():
                return default
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default


def safe_write_json(path: Path, data: Any) -> None:
    try:
        from file_lock import locked_write_json
        locked_write_json(path, data)
    except ImportError:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def now_ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


def is_weekday(dt: datetime) -> bool:
    return dt.weekday() < 5


def in_trading_time(dt: datetime) -> bool:
    """判断是否在交易时段（排除午休和集合竞价）"""
    if not is_weekday(dt):
        return False
    t = dt.hour * 60 + dt.minute
    # 早盘: 09:30-11:30, 午盘: 13:00-15:00
    # 排除 09:15-09:25 集合竞价（不可撤单）、11:30-13:00 午休、15:00后收盘竞价
    morning = (9 * 60 + 30) <= t <= (11 * 60 + 30)
    afternoon = (13 * 60) <= t <= (15 * 60)
    return morning or afternoon


def in_monitoring_time(dt: datetime) -> bool:
    """判断是否在监控时段（比交易时段稍宽，含开盘前准备和盘后处理）"""
    if not is_weekday(dt):
        return False
    t = dt.hour * 60 + dt.minute
    return (9 * 60 + 15) <= t <= (15 * 60 + 5)


def next_trading_start(dt: datetime) -> datetime:
    """返回下一次交易时段开始时间（09:15），不考虑节假日，仅处理周末。"""
    candidate = dt.replace(hour=9, minute=15, second=0, microsecond=0)
    if dt <= candidate and is_weekday(dt):
        return candidate

    # next day 09:15
    d = (dt + timedelta(days=1)).replace(hour=9, minute=15, second=0, microsecond=0)
    while d.weekday() >= 5:  # weekend
        d = d + timedelta(days=1)
    return d


# ------------------------- strategy params -------------------------

@dataclass
class StrategyParams:
    stop_loss_pct: float = -0.042
    take_profit_pct: float = 0.04
    trailing_stop_atr_multiplier: float = 1.5
    trailing_stop_sell_pct: float = 0.55
    min_score: int = 65
    max_position_pct: float = 0.12
    max_total_position: float = 0.50
    min_buy_amount: float = 5000
    atr_period: int = 20
    atr_use_hybrid: bool = True


def load_strategy_params() -> StrategyParams:
    raw = safe_load_json(STRATEGY_PARAMS_FILE, {})
    sp = StrategyParams()
    for k in sp.__dataclass_fields__.keys():
        if k in raw:
            try:
                setattr(sp, k, raw[k])
            except Exception:
                pass
    # 兼容：文件里如果没有 min_buy_amount 就用交易引擎默认
    if "min_buy_amount" in raw:
        try:
            sp.min_buy_amount = float(raw["min_buy_amount"])
        except Exception:
            pass
    return sp


# ------------------------- feishu alert -------------------------

def _load_feishu_app_secret() -> Optional[str]:
    cfg_path = os.environ.get("OPENCLAW_CONFIG", "/root/.openclaw/openclaw.json")
    cfg = safe_load_json(Path(cfg_path), {})
    try:
        return cfg["channels"]["feishu"]["accounts"]["main"]["appSecret"]
    except Exception:
        return None


def _get_feishu_tenant_token(app_secret: str, logger: logging.Logger) -> Optional[str]:
    try:
        resp = requests.post(
            FEISHU_TOKEN_URL,
            json={"app_id": FEISHU_APP_ID, "app_secret": app_secret},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            logger.warning(f"Feishu token error: {data}")
            return None
        return data.get("tenant_access_token")
    except Exception as e:
        logger.warning(f"Feishu token request failed: {e}")
        return None


def send_feishu_alert(message: str, logger: logging.Logger) -> bool:
    """只在有交易信号时调用。失败返回 False，不抛异常。"""
    app_secret = _load_feishu_app_secret()
    if not app_secret:
        logger.warning("Feishu appSecret not found (check OPENCLAW_CONFIG or /root/.openclaw/openclaw.json)")
        return False

    token = _get_feishu_tenant_token(app_secret, logger)
    if not token:
        return False

    try:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = {
            "receive_id": FEISHU_RECEIVE_OPEN_ID,
            "msg_type": "text",
            "content": json.dumps({"text": message}, ensure_ascii=False),
        }
        resp = requests.post(FEISHU_MSG_URL, headers=headers, json=payload, timeout=10)
        data = resp.json()
        if data.get("code") != 0:
            logger.warning(f"Feishu send message error: {data}")
            return False
        return True
    except Exception as e:
        logger.warning(f"Feishu send message failed: {e}")
        return False


def format_intraday_report() -> Optional[str]:
    """
    生成盘中快报，从 account.json 读取数据。
    无论有没有信号，定时发送账户概览。
    """
    try:
        account = safe_load_json(ACCOUNT_FILE, {})
        if not isinstance(account, dict):
            return None
        
        now_str = datetime.now().strftime("%H:%M")
        lines = [f"📊 盘中快报 | {now_str}", ""]
        
        # 股票持仓
        holdings = account.get("holdings", []) or []
        if holdings:
            lines.append(f"📈 股票 ({len(holdings)}只)")
            stock_total_mv = 0.0
            stock_today_pnl = 0.0
            for h in holdings:
                name = h.get("name", h.get("code", "?"))
                code = str(h.get("code", "")).zfill(6)
                current_price = float(h.get("current_price", 0) or 0)
                cost_price = float(h.get("cost_price", 0) or 0)
                pnl_pct = float(h.get("pnl_pct", 0) or 0)
                market_value = float(h.get("market_value", 0) or 0)
                qty = int(h.get("quantity", 0) or 0)
                
                stock_total_mv += market_value
                
                # 估算今日盈亏（基于持仓盈亏百分比）
                if cost_price > 0 and qty > 0:
                    stock_today_pnl += (current_price - cost_price) * qty
                
                # 格式化盈亏百分比
                pnl_sign = "+" if pnl_pct >= 0 else ""
                lines.append(f"  {name[:6]}  {pnl_sign}{pnl_pct:.1f}%  ¥{current_price:.2f}")
            
            # 股票小计
            pnl_sign = "+" if stock_today_pnl >= 0 else ""
            stock_pnl_pct = (stock_today_pnl / (stock_total_mv - stock_today_pnl) * 100) if (stock_total_mv - stock_today_pnl) > 0 else 0
            lines.append(f"  小计: ¥{stock_total_mv:,.0f}  今日 {pnl_sign}¥{abs(stock_today_pnl):,.0f} ({pnl_sign}{stock_pnl_pct:.2f}%)")
            lines.append("")
        
        # 转债持仓
        cb_holdings = account.get("cb_holdings", []) or []
        if cb_holdings:
            lines.append(f"📊 转债 ({len(cb_holdings)}只)")
            cb_total_mv = 0.0
            cb_today_pnl = 0.0
            for cb in cb_holdings:
                name = cb.get("bond_name", cb.get("bond_code", "?"))
                current_price = float(cb.get("current_price", 0) or 0)
                cost_price = float(cb.get("cost_price", 0) or 0)
                pnl_pct = float(cb.get("pnl_pct", 0) or cb.get("profit_pct", 0) or 0)
                market_value = float(cb.get("market_value", 0) or 0)
                shares = float(cb.get("shares", 0) or 0)
                
                cb_total_mv += market_value
                
                # 估算今日盈亏
                if cost_price > 0 and shares > 0:
                    cb_today_pnl += (current_price - cost_price) * shares
                
                pnl_sign = "+" if pnl_pct >= 0 else ""
                lines.append(f"  {name[:6]}  {pnl_sign}{pnl_pct:.1f}%  ¥{current_price:.2f}")
            
            # 转债小计
            pnl_sign = "+" if cb_today_pnl >= 0 else ""
            cb_pnl_pct = (cb_today_pnl / (cb_total_mv - cb_today_pnl) * 100) if (cb_total_mv - cb_today_pnl) > 0 else 0
            lines.append(f"  小计: ¥{cb_total_mv:,.0f}  今日 {pnl_sign}¥{abs(cb_today_pnl):,.0f} ({pnl_sign}{cb_pnl_pct:.2f}%)")
            lines.append("")
        
        # 总览
        total_value = float(account.get("total_value", 0) or 0)
        current_cash = float(account.get("current_cash", 0) or 0)
        initial_capital = float(account.get("initial_capital", 0) or 0)
        
        # 计算总盈亏
        if initial_capital > 0:
            total_pnl = total_value - initial_capital
            total_pnl_pct = (total_pnl / initial_capital) * 100
        else:
            total_pnl = 0
            total_pnl_pct = 0
        
        pnl_sign = "+" if total_pnl >= 0 else ""
        
        # 计算仓位
        stock_mv = sum(float(h.get("market_value", 0) or 0) for h in holdings)
        cb_mv = sum(float(cb.get("market_value", 0) or 0) for cb in cb_holdings)
        stock_pct = round(stock_mv / total_value * 100) if total_value > 0 else 0
        cb_pct = round(cb_mv / total_value * 100) if total_value > 0 else 0
        total_pos_pct = stock_pct + cb_pct
        
        lines.append("💰 总览")
        lines.append(f"  总资产: ¥{total_value:,.0f} ({pnl_sign}{total_pnl_pct:.2f}%)")
        lines.append(f"  累计盈亏: {pnl_sign}¥{abs(total_pnl):,.0f}")
        lines.append(f"  仓位: 股票{stock_pct}% + 转债{cb_pct}% = {total_pos_pct}%")
        lines.append(f"  现金: ¥{current_cash:,.0f}")
        lines.append("")
        
        # 信号
        signals_data = safe_load_json(TRADE_SIGNALS_FILE, {})
        signals = signals_data.get("signals", []) if isinstance(signals_data, dict) else []
        
        if signals:
            signal_strs = []
            for s in signals[:3]:  # 最多显示3条
                sig_type = "买" if s.get("type") == "buy" else "卖"
                name = s.get("name", s.get("code", "?"))
                signal_strs.append(f"{sig_type}-{name}")
            lines.append(f"⚡ 信号: {', '.join(signal_strs)}")
        else:
            lines.append("⚡ 信号: 无")
        
        return "\n".join(lines)
    
    except Exception as e:
        logging.getLogger("monitor_daemon").error(f"format_intraday_report error: {e}")
        return None


def should_send_alert(signals: List[Dict[str, Any]], logger: logging.Logger) -> bool:
    """避免10秒重复刷屏：同一批信号5分钟内不重复发送。"""
    if not signals:
        return False

    # signature based on sorted (type, code, reason)
    items = sorted([(s.get("type"), s.get("code"), s.get("reason")) for s in signals])
    signature = json.dumps(items, ensure_ascii=False)

    state = safe_load_json(ALERT_STATE_FILE, {})
    last_sig = state.get("last_signature")
    last_ts = state.get("last_sent_ts")

    if last_sig == signature and last_ts:
        try:
            last_dt = datetime.fromisoformat(last_ts)
            if datetime.now() - last_dt < timedelta(minutes=5):
                return False
        except Exception:
            pass

    safe_write_json(ALERT_STATE_FILE, {"last_signature": signature, "last_sent_ts": now_ts()})
    return True


# ------------------------- 飞书通知格式化 -------------------------

def format_sell_alert(trade: Dict[str, Any], signal: Dict[str, Any], account: Dict[str, Any]) -> str:
    """格式化卖出通知（止损/止盈）"""
    code = trade.get("code", "")
    name = trade.get("name", code)
    reason = signal.get("reason", "")
    
    # 判断是股票还是转债
    is_cb = code.startswith("11") or code.startswith("12")
    asset_type = "转债" if is_cb else "股票"
    
    # 判断通知类型
    if "止损" in reason:
        emoji = "🔴"
        action_type = "止损卖出"
    elif "ATR追踪止盈" in reason:
        emoji = "🟡"
        action_type = "追踪止盈"
    elif "止盈" in reason:
        emoji = "🟢"
        action_type = "止盈卖出"
    else:
        emoji = "📤"
        action_type = "卖出"
    
    qty = trade.get("quantity", 0)
    price = trade.get("price", 0)
    amount = trade.get("amount", qty * price)
    pnl = trade.get("pnl", 0)
    pnl_pct = trade.get("pnl_pct", 0)
    
    # 计算仓位变化
    total_value = float(account.get("total_value", 1) or 1)
    cash_before = float(account.get("current_cash", 0) or 0) - amount
    cash_after = float(account.get("current_cash", 0) or 0)
    pos_before = round((1 - cash_before / total_value) * 100) if total_value > 0 else 0
    pos_after = round((1 - cash_after / total_value) * 100) if total_value > 0 else 0
    
    # 盈亏显示
    pnl_sign = "+" if pnl >= 0 else ""
    pnl_label = "盈利" if pnl >= 0 else "亏损"
    
    lines = [
        f"{emoji} {action_type}",
        "",
        f"{asset_type}: {name} ({code})",
        f"操作: {'全部卖出' if signal.get('suggested_action', '').startswith('立即卖出全部') else '减仓'} {qty}{'张' if is_cb else '股'}",
        f"价格: ¥{price:.2f}",
        f"{pnl_label}: {pnl_sign}¥{abs(pnl):,.0f} ({pnl_sign}{pnl_pct:.2f}%)",
        f"原因: {reason.split(':')[0] if ':' in reason else reason}",
        "",
        f"当前仓位: {pos_before}% → {pos_after}%",
    ]
    
    return "\n".join(lines)


def format_buy_signal_alert(signal: Dict[str, Any], account: Dict[str, Any]) -> str:
    """格式化买入信号通知"""
    code = signal.get("code", "")
    name = signal.get("name", code)
    reason = signal.get("reason", "")
    
    # 判断是股票还是转债
    is_cb = code.startswith("11") or code.startswith("12")
    asset_type = "转债" if is_cb else "股票"
    
    # 提取评分
    score_match = None
    if "score=" in reason:
        try:
            score_match = reason.split("score=")[1].split(")")[0].split(" ")[0]
        except:
            pass
    
    lines = [
        "🟢 买入信号",
        "",
        f"{asset_type}: {name} ({code})",
        f"理由: {reason}",
    ]
    
    if score_match:
        lines.append(f"评分: {score_match}分")
    
    lines.append(f"建议: {signal.get('suggested_action', '')}")
    
    return "\n".join(lines)


def format_executed_buy_alert(trade: Dict[str, Any], account: Dict[str, Any]) -> str:
    """格式化已执行的买入通知"""
    code = trade.get("code", "")
    name = trade.get("name", code)
    
    # 判断是股票还是转债
    is_cb = code.startswith("11") or code.startswith("12")
    asset_type = "转债" if is_cb else "股票"
    
    qty = trade.get("quantity", 0)
    price = trade.get("price", 0)
    amount = trade.get("amount", qty * price)
    score = trade.get("score", "")
    reasons = trade.get("reasons", [])
    reason_str = reasons[0] if reasons else ""
    
    # 计算仓位变化
    total_value = float(account.get("total_value", 1) or 1)
    cash_after = float(account.get("current_cash", 0) or 0)
    cash_before = cash_after + amount
    pos_before = round((1 - cash_before / total_value) * 100) if total_value > 0 else 0
    pos_after = round((1 - cash_after / total_value) * 100) if total_value > 0 else 0
    
    lines = [
        "🟢 买入",
        "",
        f"{asset_type}: {name} ({code})",
        f"操作: 买入 {qty}{'张' if is_cb else '股'}",
        f"价格: ¥{price:.2f}",
        f"金额: ¥{amount:,.0f}",
    ]
    
    if score:
        lines.append(f"评分: {score}分")
    if reason_str:
        lines.append(f"理由: {reason_str}")
    
    lines.append("")
    lines.append(f"当前仓位: {pos_before}% → {pos_after}%")
    
    return "\n".join(lines)


def format_cb_trade_alert(trade: Dict[str, Any]) -> str:
    """格式化可转债交易通知"""
    trade_type = trade.get("type", "").upper()
    bond_name = trade.get("bond_name", "")
    bond_code = trade.get("bond_code", "")
    qty = trade.get("quantity", 0)
    price = float(trade.get("price", 0) or 0)
    strategy = trade.get("strategy", "")
    
    if trade_type == "SELL":
        emoji = "🔴" if "止损" in strategy else "🟢"
        action = "卖出"
    elif trade_type == "CONVERT":
        emoji = "🔄"
        action = "转股"
    else:
        emoji = "🟢"
        action = "买入"
    
    lines = [
        f"{emoji} 转债{action}",
        "",
        f"转债: {bond_name} ({bond_code})",
        f"操作: {action} {qty}张",
        f"价格: ¥{price:.2f}",
        f"策略: {strategy}",
    ]
    
    return "\n".join(lines)


def format_batch_trade_summary(
    executed_trades: List[Dict[str, Any]],
    pending_signals: List[Dict[str, Any]],
    account: Dict[str, Any]
) -> str:
    """格式化批量交易摘要（当多笔交易时使用）"""
    now_str = datetime.now().strftime("%H:%M")
    
    lines = [f"📊 交易快报 | {now_str}", ""]
    
    if executed_trades:
        lines.append("✅ 已执行:")
        for t in executed_trades:
            code = t.get("code", "")
            name = t.get("name", code)
            qty = t.get("quantity", 0)
            price = t.get("price", 0)
            pnl = t.get("pnl")
            trade_type = t.get("type", "sell").upper()
            
            is_cb = code.startswith("11") or code.startswith("12")
            unit = "张" if is_cb else "股"
            
            pnl_str = ""
            if pnl is not None:
                pnl_sign = "+" if pnl >= 0 else ""
                pnl_str = f" {pnl_sign}¥{pnl:,.0f}"
            
            emoji = "🔴" if trade_type == "SELL" else "🟢"
            lines.append(f"  {emoji} {name} {qty}{unit} @¥{price:.2f}{pnl_str}")
        lines.append("")
    
    if pending_signals:
        lines.append("⏳ 待决策:")
        for s in pending_signals:
            code = s.get("code", "")
            name = s.get("name", code)
            reason = s.get("reason", "")
            lines.append(f"  🟡 {name}({code}) - {reason[:30]}")
        lines.append("")
    
    # 账户概览
    total = account.get("total_value", 0)
    cash = account.get("current_cash", 0)
    pos_pct = round((1 - cash / total) * 100) if total > 0 else 0
    
    lines.append(f"💰 总资产: ¥{total:,.0f}")
    lines.append(f"📊 仓位: {pos_pct}%")
    
    return "\n".join(lines)


# ------------------------- auto trading -------------------------

MAX_DAILY_AUTO_TRADES = 10


def get_daily_auto_trade_count() -> int:
    """获取今日自动交易次数"""
    today = datetime.now().strftime("%Y-%m-%d")
    state = safe_load_json(DAILY_TRADE_COUNT_FILE, {})
    if state.get("date") != today:
        return 0
    return state.get("count", 0)


def increment_daily_auto_trade_count(logger: logging.Logger) -> int:
    """增加今日自动交易计数，返回新计数"""
    today = datetime.now().strftime("%Y-%m-%d")
    state = safe_load_json(DAILY_TRADE_COUNT_FILE, {})
    if state.get("date") != today:
        state = {"date": today, "count": 0}
    state["count"] = state.get("count", 0) + 1
    safe_write_json(DAILY_TRADE_COUNT_FILE, state)
    logger.info(f"Daily auto trade count: {state['count']}")
    return state["count"]


def check_stop_loss_rebuy_ban(code: str) -> bool:
    """检查是否今日止损禁买"""
    try:
        banned_codes = get_today_stop_loss_codes()
        return code in banned_codes
    except Exception:
        return False


def load_openclaw_gateway_token() -> Optional[str]:
    """从openclaw.json读取gateway auth token"""
    cfg_path = os.environ.get("OPENCLAW_CONFIG", "/root/.openclaw/openclaw.json")
    cfg = safe_load_json(Path(cfg_path), {})
    try:
        return cfg["gateway"]["auth"]["token"]
    except Exception:
        return None


def wake_openclaw_for_buy(message: str, logger: logging.Logger) -> bool:
    """唤醒OpenClaw处理买入信号"""
    try:
        token = load_openclaw_gateway_token()
        if not token:
            logger.warning("OpenClaw gateway token not found")
            return False
        
        resp = requests.post(
            "http://localhost:18789/api/cron/wake",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            },
            json={
                "text": message,
                "mode": "now"
            },
            timeout=10
        )
        
        if resp.status_code == 200:
            logger.info(f"OpenClaw wake success: {resp.text[:100]}")
            return True
        else:
            logger.warning(f"OpenClaw wake failed: {resp.status_code} {resp.text[:200]}")
            return False
    except Exception as e:
        logger.warning(f"OpenClaw wake request failed: {e}")
        return False


def execute_auto_sell(
    account: Dict[str, Any],
    signal: Dict[str, Any],
    sell_pct: float,
    logger: logging.Logger
) -> Optional[Dict[str, Any]]:
    """
    执行自动卖出交易
    
    Args:
        account: 账户信息
        signal: 交易信号
        sell_pct: 卖出比例 (0-1), 1.0表示全部卖出
        logger: 日志器
    
    Returns:
        交易结果 or None if failed
    """
    try:
        code = signal.get("code", "")
        name = signal.get("name", code)
        
        # 检查每日交易限制
        if get_daily_auto_trade_count() >= MAX_DAILY_AUTO_TRADES:
            logger.warning(f"Daily auto trade limit reached ({MAX_DAILY_AUTO_TRADES}), skip sell {code}")
            return None
        
        # 找到持仓
        holding = None
        for h in account.get("holdings", []):
            if str(h.get("code", "")).zfill(6) == code:
                holding = h
                break
        
        if not holding:
            logger.warning(f"No holding found for {code}")
            return None
        
        # 计算可卖数量
        sellable = can_sell_today(account, code)
        if sellable <= 0:
            logger.warning(f"No sellable quantity for {code} (T+1)")
            return None
        
        # 计算卖出数量
        if sell_pct >= 1.0:
            sell_qty = sellable
        else:
            sell_qty = int(sellable * sell_pct / 100) * 100
            if sell_qty < 100:
                sell_qty = 100  # 最少卖100股
            sell_qty = min(sell_qty, sellable)
        
        if sell_qty <= 0:
            logger.warning(f"Calculated sell quantity is 0 for {code}")
            return None
        
        # 获取当前价格
        current_price = float(holding.get("current_price", 0) or holding.get("cost_price", 0))
        if current_price <= 0:
            logger.warning(f"Invalid price for {code}")
            return None
        
        # 构造交易决策
        decision = {
            "code": code,
            "name": name,
            "price": current_price,
            "trade_type": "sell",
            "quantity": sell_qty,
            "reasons": [signal.get("reason", "monitor_daemon auto sell")],
            "source": "monitor_daemon"
        }
        
        # 执行交易
        result = execute_trade(account, decision)
        
        if result.get("success"):
            trade = result.get("trade", {})
            trade["source"] = "monitor_daemon"
            increment_daily_auto_trade_count(logger)
            logger.info(f"Auto sell executed: {code} {sell_qty}股 @ ¥{current_price:.2f}")
            return trade
        else:
            reason = result.get("reason", "unknown")
            logger.warning(f"Auto sell failed for {code}: {reason}")
            return None
            
    except Exception as e:
        logger.exception(f"Auto sell error for {signal.get('code', '?')}: {e}")
        return None


def save_pending_buy_signals(signals: List[Dict[str, Any]], logger: logging.Logger) -> None:
    """保存待买入信号到文件"""
    try:
        payload = {
            "timestamp": now_ts(),
            "signals": signals
        }
        safe_write_json(PENDING_BUY_SIGNALS_FILE, payload)
        logger.info(f"Saved {len(signals)} pending buy signals")
    except Exception as e:
        logger.warning(f"Failed to save pending buy signals: {e}")


# ------------------------- core loop logic -------------------------


def compute_account_totals(account: Dict[str, Any]) -> Tuple[float, float]:
    cash = float(account.get("current_cash", 0) or 0)
    holdings_value = 0.0
    for h in account.get("holdings", []) or []:
        try:
            holdings_value += float(h.get("market_value", 0) or 0)
        except Exception:
            pass
    total_value = cash + holdings_value
    return cash, total_value


def update_holdings_with_realtime(
    account: Dict[str, Any],
    realtime: Dict[str, Dict[str, Any]],
    logger: logging.Logger,
) -> None:
    """原地更新 account.holdings 的 current_price/market_value/pnl_pct/high_since_entry。"""
    holdings_value = 0.0

    for h in account.get("holdings", []) or []:
        code = str(h.get("code", "")).zfill(6)
        rt = realtime.get(code, {})

        cost = float(h.get("cost_price", 0) or 0)
        price = float(rt.get("price", 0) or 0)
        if price <= 0:
            price = float(h.get("current_price", 0) or cost)

        qty = int(h.get("quantity", 0) or 0)
        mv = round(price * qty, 2)
        pnl_pct = round(((price - cost) / cost * 100), 2) if cost > 0 else 0.0

        h["current_price"] = price
        h["market_value"] = mv
        h["pnl_pct"] = pnl_pct

        # update high_since_entry
        try:
            high_since = float(h.get("high_since_entry", 0) or 0)
            if high_since <= 0:
                high_since = max(price, cost)
            if price > high_since:
                high_since = price
            h["high_since_entry"] = round(high_since, 3)
        except Exception:
            pass

        holdings_value += mv

    cash = float(account.get("current_cash", 0) or 0)
    # 加上可转债市值
    cb_value = sum(float(cb.get("market_value", 0) or 0) for cb in account.get("cb_holdings", []))
    account["total_value"] = round(cash + holdings_value + cb_value, 2)

    # 同步更新总盈亏
    initial_capital = float(account.get("initial_capital", 0) or 0)
    if initial_capital > 0:
        account["total_pnl"] = round(account["total_value"] - initial_capital, 2)
        account["total_pnl_pct"] = round((account["total_value"] - initial_capital) / initial_capital * 100, 2)

    account["last_updated"] = datetime.now().isoformat()


# ATR 缓存（1小时有效，避免每10秒循环调用K线API）
_atr_cache: Dict[str, Tuple[float, float]] = {}  # code -> (atr_abs, timestamp)
_ATR_CACHE_TTL = 3600  # 1小时


def _calc_atr_abs(code: str, rt: Dict[str, Any], sp: StrategyParams, logger: logging.Logger) -> float:
    """返回 ATR 绝对价格（元），带1小时缓存，失败则返回0。"""
    now = time.time()
    cached = _atr_cache.get(code)
    if cached and (now - cached[1]) < _ATR_CACHE_TTL:
        return cached[0]
    try:
        klines = fetch_kline(code, period="101", limit=max(60, sp.atr_period + 5))
        if not klines:
            return 0.0
        atr_pct = calculate_hybrid_atr(klines, rt) if sp.atr_use_hybrid else 0.0
        if atr_pct <= 0:
            return 0.0
        price = float(rt.get("price", 0) or 0)
        if price <= 0:
            price = float(klines[-1].get("close", 0) or 0)
        result = float(price) * float(atr_pct)
        _atr_cache[code] = (result, now)
        return result
    except Exception as e:
        logger.info(f"ATR calc failed for {code}: {e}")
        return 0.0


def generate_trade_signals(
    account: Dict[str, Any],
    watchlist: Dict[str, Any],
    realtime: Dict[str, Dict[str, Any]],
    sp: StrategyParams,
    logger: logging.Logger,
) -> List[Dict[str, Any]]:
    signals: List[Dict[str, Any]] = []

    holdings = account.get("holdings", []) or []
    holding_codes = {str(h.get("code", "")).zfill(6) for h in holdings}

    # --- sells ---
    for h in holdings:
        code = str(h.get("code", "")).zfill(6)
        name = h.get("name", code)
        rt = realtime.get(code, {})

        cost = float(h.get("cost_price", 0) or 0)
        price = float(rt.get("price", 0) or h.get("current_price", cost) or cost)
        if cost <= 0 or price <= 0:
            continue

        pnl_pct = (price - cost) / cost  # ratio

        # a) hard stop loss
        if pnl_pct <= sp.stop_loss_pct:
            signals.append({
                "type": "sell",
                "code": code,
                "name": name,
                "reason": f"止损触发 {pnl_pct * 100:.2f}% (<= {sp.stop_loss_pct * 100:.2f}%)",
                "urgency": "high",
                "suggested_action": "立即卖出全部",
            })
            continue

        # b) ATR trailing take profit (reduce)
        high_since = h.get("high_since_entry")
        if high_since is not None:
            try:
                high_since_f = float(high_since)
                if high_since_f > 0 and price > 0 and high_since_f > price:
                    atr_abs = _calc_atr_abs(code, rt, sp, logger)
                    if atr_abs > 0:
                        drawdown = high_since_f - price
                        if drawdown >= sp.trailing_stop_atr_multiplier * atr_abs:
                            sell_pct = sp.trailing_stop_sell_pct
                            signals.append({
                                "type": "sell",
                                "code": code,
                                "name": name,
                                "reason": (
                                    f"ATR追踪止盈: 从最高{high_since_f:.2f}回撤{drawdown:.2f}元"
                                    f" >= {sp.trailing_stop_atr_multiplier:.1f}×ATR({atr_abs:.2f})"
                                ),
                                "urgency": "medium",
                                "suggested_action": f"立即减仓约{int(sell_pct * 100)}%",
                            })
            except Exception:
                pass

        # c) fixed take profit
        if pnl_pct >= sp.take_profit_pct:
            signals.append({
                "type": "sell",
                "code": code,
                "name": name,
                "reason": f"止盈触发 {pnl_pct * 100:.2f}% (>= {sp.take_profit_pct * 100:.2f}%)",
                "urgency": "medium",
                "suggested_action": "立即卖出全部",
            })

    # --- buys (watchlist) ---
    try:
        cash = float(account.get("current_cash", 0) or 0)
        total_value = float(account.get("total_value", 0) or 0)
        if total_value <= 0:
            total_value = cash
        current_pos_pct = 1.0 - (cash / total_value) if total_value > 0 else 1.0

        # 超仓位硬阻断
        if cash < sp.min_buy_amount or current_pos_pct >= sp.max_total_position:
            return signals

        candidates = []
        for s in (watchlist.get("stocks", []) or []):
            code = str(s.get("code", "")).zfill(6)
            if code in holding_codes:
                continue
            score = s.get("score")
            if score is None:
                continue
            try:
                if float(score) >= sp.min_score:
                    candidates.append(s)
            except Exception:
                continue

        for s in candidates:
            code = str(s.get("code", "")).zfill(6)
            rt = realtime.get(code, {})
            price = float(rt.get("price", 0) or 0)
            pre_close = float(rt.get("pre_close", 0) or 0)
            if price <= 0 or pre_close <= 0:
                continue
            change_pct = (price - pre_close) / pre_close * 100

            if not (-1.0 < change_pct < 5.0):
                continue

            # 计算可买金额（单只不超 max_position_pct；同时不超 max_total_position）
            remaining_pos_value = max(0.0, total_value * (sp.max_total_position - current_pos_pct))
            max_single_value = total_value * sp.max_position_pct
            max_amount = min(cash * 0.25, remaining_pos_value, max_single_value)
            if max_amount < sp.min_buy_amount:
                continue

            qty = int(max_amount / price / 100) * 100
            if qty < 100:
                continue

            name = rt.get("name") or s.get("name") or code
            signals.append({
                "type": "buy",
                "code": code,
                "name": name,
                "reason": f"watchlist高分股(score={s.get('score')}) 涨幅{change_pct:+.2f}%",
                "urgency": "low",
                "suggested_action": f"建议分批买入{qty}股(不超仓位限制)",
            })
            break  # 一次只提示一只，避免刷屏

    except Exception as e:
        logger.info(f"buy scan failed: {e}")

    return signals


def append_intraday_snapshot(
    account: Dict[str, Any],
    realtime: Dict[str, Dict[str, Any]],
    logger: logging.Logger,
) -> None:
    dt = datetime.now()
    today = dt.strftime("%Y-%m-%d")
    snapshot_file = SNAPSHOT_DIR / f"{today}.json"

    holdings_snapshot = []
    for h in account.get("holdings", []) or []:
        code = str(h.get("code", "")).zfill(6)
        rt = realtime.get(code, {})
        holdings_snapshot.append({
            "code": code,
            "name": h.get("name", rt.get("name", code)),
            "price": float(rt.get("price", 0) or h.get("current_price", h.get("cost_price", 0)) or 0),
            "change_pct": float(rt.get("change_pct", 0) or 0),
            "quantity": int(h.get("quantity", 0) or 0),
            "cost_price": float(h.get("cost_price", 0) or 0),
            "pnl_pct": float(h.get("pnl_pct", 0) or 0),
            "market_value": float(h.get("market_value", 0) or 0),
        })

    snapshot = {
        "timestamp": dt.isoformat(timespec="seconds"),
        "holdings": holdings_snapshot,
        "cash": float(account.get("current_cash", 0) or 0),
        "total_value": float(account.get("total_value", 0) or 0),
    }

    try:
        snapshots = safe_load_json(snapshot_file, [])
        if not isinstance(snapshots, list):
            snapshots = []
        snapshots.append(snapshot)
        safe_write_json(snapshot_file, snapshots)
    except Exception as e:
        logger.info(f"snapshot append failed: {e}")


def persist_trade_signals(signals: List[Dict[str, Any]], logger: logging.Logger) -> None:
    payload = {"timestamp": now_ts(), "signals": signals}
    try:
        safe_write_json(TRADE_SIGNALS_FILE, payload)
    except Exception as e:
        logger.info(f"write trade_signals failed: {e}")


# ------------------------- daemon main -------------------------

STOP = False


def _handle_sigterm(signum, frame):  # noqa: ARG001
    global STOP
    STOP = True


def main() -> int:
    global STOP

    logger = setup_logging()
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    logger.info("monitor_daemon started")

    # 可转债扫描节流：每5分钟全量扫描一次，其余循环仅更新已持有转债报价
    last_cb_full_scan_ts = 0.0
    cached_cb_list: list[dict[str, Any]] = []
    cached_cb_opps: list[dict[str, Any]] = []

    while not STOP:
        dt = datetime.now()

        if not in_monitoring_time(dt):
            nxt = next_trading_start(dt)
            wait_sec = max(5, int((nxt - dt).total_seconds()))
            logger.info(f"非交易时间，等待... next={nxt.strftime('%Y-%m-%d %H:%M:%S')} sleep={wait_sec}s")
            # 可被 SIGTERM 中断
            for _ in range(wait_sec):
                if STOP:
                    break
                time.sleep(1)
            continue

        # trading loop
        loop_start = time.time()
        try:
            account = safe_load_json(ACCOUNT_FILE, {})
            if not isinstance(account, dict):
                account = {}
            account.setdefault("holdings", [])
            watchlist = safe_load_json(WATCHLIST_FILE, {"stocks": []})
            if not isinstance(watchlist, dict):
                watchlist = {"stocks": []}

            sp = load_strategy_params()

            # prepare quote codes
            holdings_codes = [str(h.get("code", "")).zfill(6) for h in (account.get("holdings", []) or [])]
            wl_codes = [str(s.get("code", "")).zfill(6) for s in (watchlist.get("stocks", []) or [])]
            quote_codes = sorted(list({c for c in holdings_codes + wl_codes if c and c != "000000"}))

            realtime = fetch_realtime_sina(quote_codes) if quote_codes else {}

            # update account with latest prices (use locked_update for TOCTOU safety)
            try:
                from file_lock import locked_update_json
                def _update_fn(acc):
                    update_holdings_with_realtime(acc, realtime, logger)
                    return acc
                account = locked_update_json(ACCOUNT_FILE, _update_fn)
            except ImportError:
                update_holdings_with_realtime(account, realtime, logger)
                safe_write_json(ACCOUNT_FILE, account)

            # append snapshots
            append_intraday_snapshot(account, realtime, logger)

            # generate signals
            signals = generate_trade_signals(account, watchlist, realtime, sp, logger)

            # ========== AUTO TRADING EXECUTION ==========
            executed_trades = []
            buy_signals_for_llm = []
            MAX_SELLS_PER_LOOP = 2  # 防止级联止损，每轮最多自动卖出2笔
            sells_this_loop = 0
            
            if signals:
                for sig in signals:
                    signal_type = sig.get("type", "")
                    code = sig.get("code", "")
                    reason = sig.get("reason", "")
                    
                    try:
                        if signal_type == "sell":
                            # 级联止损保护：每轮最多执行 MAX_SELLS_PER_LOOP 笔卖出
                            if sells_this_loop >= MAX_SELLS_PER_LOOP:
                                logger.warning(f"⚠️ 级联保护: 本轮已执行{sells_this_loop}笔卖出，{code}延后到下一轮")
                                continue
                            
                            # 交易时间检查：只在连续竞价时段执行
                            if not in_trading_time(datetime.now()):
                                logger.warning(f"⚠️ 非连续竞价时段，{code}卖出信号延后")
                                continue
                            
                            # 判断信号类型决定卖出比例
                            if "止损" in reason:
                                # 止损：全部卖出
                                trade = execute_auto_sell(account, sig, 1.0, logger)
                                if trade:
                                    executed_trades.append(trade)
                                    sells_this_loop += 1
                                    account = safe_load_json(ACCOUNT_FILE, {})
                                    logger.info(f"🔴 止损卖出完成: {code} - {reason}")
                            
                            elif "ATR追踪止盈" in reason:
                                # ATR追踪止盈：减仓55%
                                trade = execute_auto_sell(account, sig, sp.trailing_stop_sell_pct, logger)
                                if trade:
                                    executed_trades.append(trade)
                                    sells_this_loop += 1
                                    account = safe_load_json(ACCOUNT_FILE, {})
                                    logger.info(f"🟡 ATR追踪止盈完成: {code} - {reason}")
                            
                            elif "止盈" in reason:
                                # 固定止盈：全部卖出
                                trade = execute_auto_sell(account, sig, 1.0, logger)
                                if trade:
                                    executed_trades.append(trade)
                                    sells_this_loop += 1
                                    account = safe_load_json(ACCOUNT_FILE, {})
                                    logger.info(f"🟢 止盈卖出完成: {code} - {reason}")
                            
                            else:
                                # 其他卖出信号：记录但不自动执行
                                logger.info(f"卖出信号(未自动执行): {code} - {reason}")
                        
                        elif signal_type == "buy":
                            # 买入信号：检查止损禁买，然后交给LLM
                            if check_stop_loss_rebuy_ban(code):
                                logger.warning(f"⛔ 止损后同日禁买: {code}")
                            else:
                                buy_signals_for_llm.append(sig)
                    
                    except Exception as e:
                        logger.exception(f"Auto trade error for {code}: {e}")
            
            # 如果有买入信号，保存并唤醒OpenClaw
            if buy_signals_for_llm:
                save_pending_buy_signals(buy_signals_for_llm, logger)
                wake_msg = f"盘中监控发现买入信号，请查看 stock-trading/data/pending_buy_signals.json 并决策是否买入"
                wake_openclaw_for_buy(wake_msg, logger)
            
            # 构建并发送飞书通知
            if signals or executed_trades:
                persist_trade_signals(signals, logger)

                # 重新加载最新账户数据用于通知
                latest_account = safe_load_json(ACCOUNT_FILE, {})
                
                # 判断是单笔还是多笔交易
                total_items = len(executed_trades) + len(buy_signals_for_llm)
                
                if total_items == 1:
                    # 单笔交易：使用详细格式
                    if executed_trades:
                        trade = executed_trades[0]
                        # 找到对应的信号
                        sig = next((s for s in signals if s.get("code") == trade.get("code")), {})
                        msg = format_sell_alert(trade, sig, latest_account)
                    elif buy_signals_for_llm:
                        sig = buy_signals_for_llm[0]
                        msg = format_buy_signal_alert(sig, latest_account)
                    else:
                        # 只有未执行的信号
                        sig = signals[0] if signals else {}
                        if sig.get("type") == "buy":
                            msg = format_buy_signal_alert(sig, latest_account)
                        else:
                            # 未自动执行的卖出信号，用简单格式
                            msg = f"📤 交易信号\n\n{sig.get('name','')} ({sig.get('code','')})\n{sig.get('reason','')}\n建议: {sig.get('suggested_action','')}"
                else:
                    # 多笔交易：使用摘要格式
                    msg = format_batch_trade_summary(executed_trades, buy_signals_for_llm, latest_account)
                
                if should_send_alert(signals, logger):
                    ok = send_feishu_alert(msg, logger)
                    logger.info(f"Feishu alert sent={ok} signals={len(signals)}")
                else:
                    logger.info(f"signals generated but alert throttled, signals={len(signals)}")
            else:
                # no signals: do not overwrite trade_signals.json频繁（保留上次信号）
                logger.info("no trade signals")

            # ========== CB AUTO TRADING (ignored on failure) ==========
            try:
                # 用最新账户（股票自动交易后可能发生变更）
                cb_account = safe_load_json(ACCOUNT_FILE, {})
                if not isinstance(cb_account, dict):
                    cb_account = {}
                cb_account.setdefault("cb_holdings", [])

                now_ts_sec = time.time()
                need_full = (now_ts_sec - last_cb_full_scan_ts) >= 300 or not cached_cb_opps

                held_ops: list[dict[str, Any]] = []
                # 5分钟内：仅刷新已持有转债的债券/正股报价，并计算溢价率用于卖出/转股判断
                if not need_full and (cb_account.get("cb_holdings") or []):
                    codes: list[str] = []
                    bond_map: dict[str, dict[str, Any]] = {}
                    for h in cb_account.get("cb_holdings", []) or []:
                        bcode = str(h.get("bond_code") or "").strip()
                        if not bcode:
                            continue
                        # 兜底：按 11/12/123/127/128 判断市场
                        mkt = "CNSESH" if bcode.startswith("11") else "CNSESZ"
                        sina_b = cb_get_sina_bond_code(bcode, mkt)
                        bond_map[sina_b] = h
                        codes.append(sina_b)
                        stk = str(h.get("target_stock_code") or "").strip()
                        if stk:
                            codes.append(cb_get_sina_stock_code(stk))

                    quotes = cb_fetch_sina_batch(list({c for c in codes if c}))

                    for sina_b, h in bond_map.items():
                        bq = quotes.get(sina_b)
                        bond_price = None
                        if bq and len(bq) > 3:
                            try:
                                p = float(bq[3])
                                if p > 0:
                                    bond_price = p
                                elif float(bq[2]) > 0:
                                    bond_price = float(bq[2])
                            except Exception:
                                bond_price = None

                        stk_code = str(h.get("target_stock_code") or "").strip()
                        stock_price = None
                        if stk_code:
                            sq = quotes.get(cb_get_sina_stock_code(stk_code))
                            if sq and len(sq) > 3:
                                try:
                                    p = float(sq[3])
                                    if p > 0:
                                        stock_price = p
                                    elif float(sq[2]) > 0:
                                        stock_price = float(sq[2])
                                except Exception:
                                    stock_price = None

                        try:
                            tp = float(h.get("transfer_price") or 0)
                        except Exception:
                            tp = 0.0

                        premium_rate = None
                        convert_value = None
                        if bond_price and stock_price and tp > 0:
                            convert_value = (100.0 / tp) * stock_price
                            if convert_value > 0:
                                premium_rate = ((bond_price - convert_value) / convert_value) * 100.0

                        if bond_price:
                            # 更新持仓实时字段
                            try:
                                shares = float(h.get("shares", 0) or 0)
                            except Exception:
                                shares = 0.0
                            h["current_price"] = round(float(bond_price), 4)
                            h["market_value"] = round(float(bond_price) * shares, 2)
                            try:
                                cost_price = float(h.get("cost_price") or 0)
                            except Exception:
                                cost_price = 0.0
                            h["pnl_pct"] = round(((float(bond_price) - cost_price) / cost_price * 100.0) if cost_price > 0 else 0.0, 4)

                        held_ops.append({
                            "bond_code": str(h.get("bond_code") or ""),
                            "bond_name": str(h.get("bond_name") or ""),
                            "bond_price": round(float(bond_price), 4) if bond_price else float(h.get("current_price") or 0),
                            "stock_code": stk_code,
                            "stock_price": round(float(stock_price), 4) if stock_price else None,
                            "transfer_price": round(float(tp), 4) if tp else None,
                            "convert_value": round(float(convert_value), 4) if convert_value else None,
                            "premium_rate": round(float(premium_rate), 4) if premium_rate is not None else None,
                            "can_convert": True,
                            "strategy": str(h.get("strategy") or ""),
                            "score": 0,
                        })

                    # 写回刷新后的 cb_holdings
                    safe_write_json(ACCOUNT_FILE, cb_account)

                if need_full:
                    try:
                        cached_cb_list = cb_fetch_cb_list() or []
                        cached_cb_opps = cb_scan(cached_cb_list) if cached_cb_list else []
                        last_cb_full_scan_ts = now_ts_sec
                        logger.info(f"CB full scan ok opps={len(cached_cb_opps)}")
                    except Exception as e:
                        logger.info(f"CB full scan failed: {e}")

                # 合并机会：用最新持仓计算的 held_ops 覆盖同 code 的 cached
                merged: dict[str, dict[str, Any]] = {}
                for op in cached_cb_opps or []:
                    if isinstance(op, dict) and op.get("bond_code"):
                        merged[str(op["bond_code"]).strip()] = op
                for op in held_ops:
                    if isinstance(op, dict) and op.get("bond_code"):
                        merged[str(op["bond_code"]).strip()] = op

                executed_cb = process_cb_trading(cb_account, list(merged.values()))

                if executed_cb:
                    # 可转债交易发飞书通知，使用优化后的格式
                    if len(executed_cb) == 1:
                        # 单笔：详细格式
                        msg = format_cb_trade_alert(executed_cb[0])
                    else:
                        # 多笔：摘要格式
                        lines = [f"💳 转债交易 | {datetime.now().strftime('%H:%M')}", ""]
                        for t in executed_cb:
                            trade_type = t.get("type", "").upper()
                            emoji = "🔴" if trade_type == "SELL" else "🟢" if trade_type == "BUY" else "🔄"
                            lines.append(
                                f"{emoji} {t.get('bond_name','')} {t.get('quantity',0)}张 "
                                f"@¥{float(t.get('price',0) or 0):.2f}"
                            )
                        msg = "\n".join(lines)
                    
                    send_feishu_alert(msg, logger)

            except Exception as e:
                logger.info(f"CB auto trading failed (ignored): {e}")

            # ========== 盘中定时快报（每30分钟） ==========
            global _last_periodic_report
            periodic_now_ts = time.time()
            if periodic_now_ts - _last_periodic_report >= 1800:  # 30分钟
                try:
                    report = format_intraday_report()
                    if report:
                        send_feishu_alert(report, logger)
                        _last_periodic_report = periodic_now_ts
                        logger.info("sent periodic intraday report")
                except Exception as e:
                    logger.error(f"periodic report error: {e}")

        except Exception as e:
            logger.exception(f"loop failed (ignored): {e}")

        # sleep to 10s cadence
        elapsed = time.time() - loop_start
        sleep_sec = max(0.5, 10.0 - elapsed)
        for _ in range(int(sleep_sec * 10)):
            if STOP:
                break
            time.sleep(0.1)

    logger.info("monitor_daemon exiting gracefully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
