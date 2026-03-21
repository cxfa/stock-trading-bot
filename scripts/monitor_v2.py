#!/usr/bin/env python3
"""
盘中监控守护进程 v2 — 基于新架构的监控系统

架构:
  trading_strategy.py (决策) → trade_executor.py (执行)
  本模块 = 数据收集 + 调度 + 通知

循环:
  每1分钟: 获取行情 → 策略评估持仓 → 策略评估买入 → 执行交易
  每30分钟: 飞书快报 + 策略盘中快速调整 + 刷新新闻情绪
  有交易时: 立即飞书通知

依赖:
  - trading_strategy.py: evaluate_position, evaluate_buy_candidate, assess_intraday, gather_market_context
  - trade_executor.py: execute_buy, execute_sell, load_account, can_sell_today
  - fetch_stock_data.py: fetch_realtime_sina
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 确保能导入同目录模块
sys.path.insert(0, str(Path(__file__).parent))

from fetch_stock_data import fetch_realtime_sina, fetch_kline
from trade_executor import (
    load_account, save_account, execute_buy, execute_sell,
    can_sell_today, get_today_buy_count, clean_frozen_sells,
)
from trading_strategy import (
    Signal, MarketContext, gather_market_context,
    evaluate_position, evaluate_buy_candidate,
    assess_intraday, load_buy_plan, get_effective_params,
)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ─── 配置 ───
LOOP_INTERVAL = 60          # 主循环: 1分钟
REPORT_INTERVAL = 1800      # 飞书快报: 30分钟
STRATEGY_ADJUST_INTERVAL = 1800  # 策略快速调整: 30分钟
SENTIMENT_REFRESH_INTERVAL = 1800  # 新闻刷新: 30分钟
MAX_SELLS_PER_LOOP = 2      # 每轮最多卖出2笔 (防止级联)
ATR_CACHE_TTL = 3600         # ATR缓存: 1小时

# ─── 全局状态 ───
STOP = False
_last_report_time = 0.0
_last_strategy_adjust_time = 0.0
_last_sentiment_refresh_time = 0.0
_atr_cache: Dict[str, tuple] = {}  # code -> (atr_pct, timestamp)


def _signal_handler(signum, frame):
    global STOP
    STOP = True


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


# ─── 日志 ───

def setup_logger() -> logging.Logger:
    log = logging.getLogger("monitor_v2")
    log.setLevel(logging.INFO)
    if not log.handlers:
        today = datetime.now().strftime("%Y-%m-%d")
        fh = logging.FileHandler(LOG_DIR / f"monitor_v2_{today}.log", encoding="utf-8")
        sh = logging.StreamHandler(sys.stdout)
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
        fh.setFormatter(fmt)
        sh.setFormatter(fmt)
        log.addHandler(fh)
        log.addHandler(sh)
    return log


# ─── 时间判断 ───

def is_trading_time() -> bool:
    """连续竞价时段: 09:30-11:30, 13:00-15:00"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    morning = (9 * 60 + 30) <= t <= (11 * 60 + 30)
    afternoon = (13 * 60) <= t <= (15 * 60)
    return morning or afternoon


def is_monitoring_time() -> bool:
    """监控时段: 09:25-15:05"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return (9 * 60 + 25) <= t <= (15 * 60 + 5)


# ─── 飞书通知 ───

def _send_feishu(message: str, log: logging.Logger):
    """发送飞书通知 (复用旧模块)"""
    try:
        # 尝试导入旧的飞书模块
        from monitor_daemon import send_feishu_alert
        send_feishu_alert(message, log)
    except ImportError:
        log.warning("飞书通知模块不可用")
    except Exception as e:
        log.error(f"飞书通知失败: {e}")


def _send_trade_notification(trade: Dict, log: logging.Logger):
    """交易发生时立即发送飞书通知"""
    t = trade.get("type", "?")
    code = trade.get("code", "?")
    name = trade.get("name", code)
    price = trade.get("price", 0)
    qty = trade.get("quantity", 0)
    pnl = trade.get("pnl", 0)
    reasons = trade.get("reasons", [])

    if t == "buy":
        emoji = "🟢"
        msg = f"{emoji} **买入** {name}({code})\n价格: {price} × {qty}股\n原因: {'; '.join(reasons[:2])}"
    else:
        emoji = "🟢" if pnl >= 0 else "🔴"
        msg = f"{emoji} **卖出** {name}({code})\n价格: {price} × {qty}股\n盈亏: {pnl:+,.0f}元\n原因: {'; '.join(reasons[:2])}"

    _send_feishu(msg, log)


def _send_periodic_report(account: Dict, context: MarketContext, log: logging.Logger):
    """30分钟定期快报"""
    holdings = account.get("holdings", [])
    total = account.get("total_value", 0)
    cash = account.get("current_cash", 0)
    pnl = account.get("total_pnl", 0)

    lines = [
        f"📊 **盘中快报** | {datetime.now().strftime('%H:%M')}",
        "",
        f"💰 总资产: ¥{total:,.0f} | 现金: ¥{cash:,.0f} | 累计: {pnl:+,.0f}",
        f"📈 {context.market_summary if hasattr(context, 'market_summary') else ''}",
        "",
    ]

    if holdings:
        lines.append(f"📋 持仓 ({len(holdings)}只):")
        for h in holdings:
            name = h.get("name", h.get("code", "?"))
            pnl_pct = h.get("pnl_pct", 0)
            emoji = "🟢" if pnl_pct >= 0 else "🔴"
            lines.append(f"  {emoji} {name}: {pnl_pct:+.1f}%")

    _send_feishu("\n".join(lines), log)


# ─── ATR 缓存 ───

def _get_cached_atr(code: str) -> float:
    """获取缓存的 ATR 百分比"""
    cached = _atr_cache.get(code)
    if cached and (time.time() - cached[1]) < ATR_CACHE_TTL:
        return cached[0]
    try:
        from technical_analysis import calculate_hybrid_atr
        klines = fetch_kline(code, period="101", limit=30)
        if klines and len(klines) >= 10:
            atr_pct = calculate_hybrid_atr(klines, {})
            _atr_cache[code] = (atr_pct, time.time())
            return atr_pct
    except Exception:
        pass
    return 0.0


# ─── 主循环 ───

def run_monitor(log: logging.Logger):
    """主监控循环"""
    global _last_report_time, _last_strategy_adjust_time, _last_sentiment_refresh_time

    log.info("=" * 50)
    log.info("盘中监控 v2 启动")
    log.info("=" * 50)

    while not STOP:
        now = datetime.now()

        # 非监控时段 → 等待
        if not is_monitoring_time():
            log.info(f"非监控时段({now.strftime('%H:%M')}), 等待...")
            for _ in range(60):
                if STOP:
                    break
                time.sleep(1)
            continue

        loop_start = time.time()

        try:
            # 1. 加载账户
            account = load_account()
            account = clean_frozen_sells(account)
            params = get_effective_params()

            # 2. 收集行情
            holdings = account.get("holdings", [])
            holding_codes = [str(h.get("code", "")).zfill(6) for h in holdings]

            buy_plans = load_buy_plan()
            buy_codes = [str(p.get("code", "")).zfill(6) for p in buy_plans]

            all_codes = sorted(set(holding_codes + buy_codes))
            if not all_codes:
                log.debug("无持仓无买入计划，跳过")
                _sleep_loop(loop_start)
                continue

            try:
                realtime = fetch_realtime_sina(all_codes)
            except Exception as e:
                log.error(f"获取行情失败: {e}")
                _sleep_loop(loop_start)
                continue

            # 3. 收集市场环境
            context = gather_market_context(account)

            # 4. 更新持仓信息 (价格、市值、最高价)
            for h in holdings:
                code = str(h.get("code", "")).zfill(6)
                rt = realtime.get(code, {})
                price = float(rt.get("price", 0) or 0)
                if price > 0:
                    h["current_price"] = price
                    h["market_value"] = round(price * int(h.get("quantity", 0)), 2)
                    cost = float(h.get("cost_price", 0) or 0)
                    h["pnl_pct"] = round((price - cost) / cost * 100, 2) if cost > 0 else 0
                    # 更新最高价
                    high = float(h.get("high_since_entry", 0) or 0)
                    if price > high:
                        h["high_since_entry"] = price
                # 注入缓存 ATR
                h["_cached_atr_pct"] = _get_cached_atr(code)

            # 更新总资产
            holdings_mv = sum(float(h.get("market_value", 0)) for h in holdings)
            cb_mv = sum(float(cb.get("market_value", 0)) for cb in account.get("cb_holdings", []))
            account["total_value"] = round(account.get("current_cash", 0) + holdings_mv + cb_mv, 2)
            save_account(account)

            # ========== 5. 策略评估 + 执行 ==========
            executed_trades = []

            if is_trading_time():
                # 5a. 评估持仓 → 卖出信号
                sells_this_loop = 0
                for h in holdings:
                    if sells_this_loop >= MAX_SELLS_PER_LOOP:
                        break
                    signal = evaluate_position(h, realtime, context, params)
                    if signal and signal.action in ("sell", "reduce"):
                        code = signal.code
                        name = signal.name
                        price = float(realtime.get(code, {}).get("price", 0))
                        qty = signal.quantity

                        result = execute_sell(code, name, price, qty, signal.reasons)
                        if result.get("success"):
                            executed_trades.append(result["trade"])
                            sells_this_loop += 1
                            log.info(f"🔴 {signal.action}: {name}({code}) {qty}股 - {'; '.join(signal.reasons)}")
                            # 重新加载账户
                            account = load_account()

                # 5b. 评估买入计划 → 买入信号
                today_buys = get_today_buy_count()
                for plan in buy_plans:
                    if today_buys >= int(params.get("max_daily_buys", 2)):
                        break
                    plan["_today_buy_count"] = today_buys
                    signal = evaluate_buy_candidate(plan, realtime, context, params, account)
                    if signal and signal.action == "buy":
                        code = signal.code
                        name = signal.name
                        price = float(realtime.get(code, {}).get("price", 0))
                        qty = signal.quantity

                        result = execute_buy(code, name, price, qty, signal.reasons, signal.extra.get("strategy", ""))
                        if result.get("success"):
                            executed_trades.append(result["trade"])
                            today_buys += 1
                            log.info(f"🟢 买入: {name}({code}) {qty}股 - {'; '.join(signal.reasons)}")
                            account = load_account()

            # 6. 交易立即通知
            for trade in executed_trades:
                try:
                    _send_trade_notification(trade, log)
                except Exception as e:
                    log.error(f"发送交易通知失败: {e}")

            # 7. 30分钟任务
            now_ts = time.time()

            # 7a. 策略快速调整
            if now_ts - _last_strategy_adjust_time >= STRATEGY_ADJUST_INTERVAL:
                try:
                    result = assess_intraday(account, context)
                    for alert in result.get("alerts", []):
                        log.info(f"📢 策略调整: {alert}")
                    _last_strategy_adjust_time = now_ts
                except Exception as e:
                    log.error(f"策略调整失败: {e}")

            # 7b. 新闻情绪刷新
            if now_ts - _last_sentiment_refresh_time >= SENTIMENT_REFRESH_INTERVAL:
                try:
                    from news_sentiment import get_market_sentiment
                    get_market_sentiment()
                    _last_sentiment_refresh_time = now_ts
                except Exception as e:
                    log.debug(f"新闻刷新失败: {e}")

            # 7c. 飞书定期快报
            if now_ts - _last_report_time >= REPORT_INTERVAL:
                try:
                    _send_periodic_report(account, context, log)
                    _last_report_time = now_ts
                except Exception as e:
                    log.error(f"发送快报失败: {e}")

        except Exception as e:
            log.exception(f"主循环异常: {e}")

        _sleep_loop(loop_start)

    log.info("盘中监控 v2 停止")


def _sleep_loop(loop_start: float):
    """等待到下一个循环"""
    elapsed = time.time() - loop_start
    sleep_time = max(1, LOOP_INTERVAL - elapsed)
    for _ in range(int(sleep_time)):
        if STOP:
            break
        time.sleep(1)


# ─── 入口 ───

def main():
    log = setup_logger()
    try:
        run_monitor(log)
    except KeyboardInterrupt:
        log.info("收到中断信号")
    except Exception as e:
        log.exception(f"致命错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
