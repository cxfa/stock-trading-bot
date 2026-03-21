#!/usr/bin/env python3
"""
交易执行器 — 纯执行层，不做任何决策

职责:
1. execute_buy()  — 执行买入（更新 account.json + transactions.json）
2. execute_sell() — 执行卖出
3. 账户状态管理: load_account, save_account, get_holding, can_sell_today

设计原则:
- 本模块不评分、不判断、不调用LLM
- 所有决策由 trading_strategy.py 提供
- 当前为模拟系统，执行=更新JSON文件
- 未来接入券商API时，只需修改本模块
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
ACCOUNT_FILE = BASE_DIR / "account.json"
TRANSACTIONS_FILE = BASE_DIR / "transactions.json"

# 费率 (模拟)
COMMISSION_RATE = 0.00025     # 万2.5佣金
MIN_COMMISSION = 5.0          # 最低5元
STAMP_TAX_RATE = 0.0005       # 印花税 0.05% (卖出才收)
TRANSFER_FEE_RATE = 0.00001   # 过户费 0.001%


# ─── 文件操作 (带锁) ───

def _locked_read(path: Path, default=None):
    try:
        from file_lock import locked_read_json
        return locked_read_json(path, default or {})
    except ImportError:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return default or {}


def _locked_write(path: Path, data):
    try:
        from file_lock import locked_write_json
        locked_write_json(path, data)
    except ImportError:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + '.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def _locked_update(path: Path, fn):
    try:
        from file_lock import locked_update_json
        return locked_update_json(path, fn)
    except ImportError:
        data = _locked_read(path, {})
        result = fn(data)
        _locked_write(path, result)
        return result


# ─── 账户管理 ───

_DEFAULT_ACCOUNT = {
    "initial_capital": 1000000,
    "current_cash": 1000000,
    "total_value": 1000000,
    "holdings": [],
    "frozen_sells": [],
    "cb_holdings": [],
    "daily_pnl": 0,
    "total_pnl": 0,
    "total_pnl_pct": 0,
}


def load_account() -> Dict:
    """加载账户状态"""
    account = _locked_read(ACCOUNT_FILE, _DEFAULT_ACCOUNT.copy())
    # 确保关键字段存在
    for key, default in _DEFAULT_ACCOUNT.items():
        account.setdefault(key, default)
    return account


def save_account(account: Dict):
    """保存账户状态"""
    account["last_updated"] = datetime.now().isoformat()
    _locked_write(ACCOUNT_FILE, account)


def get_holding(account: Dict, code: str) -> Optional[Dict]:
    """获取指定股票的持仓"""
    code = str(code).zfill(6)
    for h in account.get("holdings", []):
        if str(h.get("code", "")).zfill(6) == code:
            return h
    return None


def can_sell_today(account: Dict, code: str) -> int:
    """
    检查 T+1 规则，返回今日可卖数量。
    今日买入的不可卖。
    """
    code = str(code).zfill(6)
    today = datetime.now().strftime("%Y-%m-%d")
    holding = get_holding(account, code)
    if not holding:
        return 0

    total_qty = int(holding.get("quantity", 0))

    # 冻结的今日买入量
    frozen = account.get("frozen_sells", [])
    frozen_qty = sum(
        int(f.get("quantity", 0))
        for f in frozen
        if str(f.get("code", "")).zfill(6) == code
        and f.get("buy_date", "") == today
    )

    return max(0, total_qty - frozen_qty)


# ─── 费用计算 ───

def calculate_cost(price: float, quantity: int, direction: str) -> float:
    """
    计算交易费用

    Args:
        direction: "buy" or "sell"
    """
    amount = price * quantity
    commission = max(MIN_COMMISSION, amount * COMMISSION_RATE)
    transfer = amount * TRANSFER_FEE_RATE
    stamp = amount * STAMP_TAX_RATE if direction == "sell" else 0
    return round(commission + transfer + stamp, 2)


# ─── 执行买入 ───

def execute_buy(
    code: str,
    name: str,
    price: float,
    quantity: int,
    reasons: List[str] = None,
    strategy: str = "",
) -> Dict:
    """
    执行买入操作（模拟）。

    Returns:
        {"success": bool, "message": str, "trade": dict or None}
    """
    code = str(code).zfill(6)
    if quantity <= 0 or quantity % 100 != 0:
        return {"success": False, "message": f"数量无效: {quantity} (必须是100的整数倍)"}
    if price <= 0:
        return {"success": False, "message": f"价格无效: {price}"}

    amount = price * quantity
    cost = calculate_cost(price, quantity, "buy")
    total_cost = amount + cost

    def _do_buy(account):
        cash = account.get("current_cash", 0)
        if cash < total_cost:
            return None  # 标记失败

        # 扣减现金
        account["current_cash"] = round(cash - total_cost, 2)

        # 更新持仓
        holdings = account.get("holdings", [])
        existing = None
        for h in holdings:
            if str(h.get("code", "")).zfill(6) == code:
                existing = h
                break

        today = datetime.now().strftime("%Y-%m-%d")
        if existing:
            # 加仓 — 计算新均价
            old_qty = int(existing.get("quantity", 0))
            old_cost = float(existing.get("cost_price", 0)) * old_qty
            new_qty = old_qty + quantity
            existing["quantity"] = new_qty
            existing["cost_price"] = round((old_cost + amount) / new_qty, 4)
            existing["last_buy_date"] = today
            existing["market_value"] = round(price * new_qty, 2)
            existing["current_price"] = price
        else:
            # 新建仓位
            holdings.append({
                "code": code,
                "name": name,
                "quantity": quantity,
                "cost_price": round(price, 4),
                "current_price": price,
                "market_value": round(amount, 2),
                "buy_date": today,
                "last_buy_date": today,
                "high_since_entry": price,
                "pnl_pct": 0,
            })

        account["holdings"] = holdings

        # T+1 冻结
        account.setdefault("frozen_sells", []).append({
            "code": code,
            "quantity": quantity,
            "buy_date": today,
        })

        return account

    # 使用 locked_update 保证原子性
    result_account = _locked_update(ACCOUNT_FILE, _do_buy)
    if result_account is None:
        account = load_account()
        return {
            "success": False,
            "message": f"现金不足: 需要{total_cost:,.0f}, 可用{account.get('current_cash', 0):,.0f}",
        }

    # 记录交易
    trade = {
        "type": "buy",
        "code": code,
        "name": name,
        "price": price,
        "quantity": quantity,
        "amount": round(amount, 2),
        "cost": cost,
        "reasons": reasons or [],
        "strategy": strategy,
        "timestamp": datetime.now().isoformat(),
    }
    _append_transaction(trade)

    logger.info(f"✅ 买入 {name}({code}) {quantity}股 @{price} 金额{amount:,.0f}")
    return {"success": True, "message": f"买入成功", "trade": trade}


# ─── 执行卖出 ───

def execute_sell(
    code: str,
    name: str,
    price: float,
    quantity: int,
    reasons: List[str] = None,
) -> Dict:
    """
    执行卖出操作（模拟）。

    Returns:
        {"success": bool, "message": str, "trade": dict or None}
    """
    code = str(code).zfill(6)
    if quantity <= 0:
        return {"success": False, "message": f"数量无效: {quantity}"}
    if price <= 0:
        return {"success": False, "message": f"价格无效: {price}"}

    amount = price * quantity
    cost = calculate_cost(price, quantity, "sell")
    net_amount = amount - cost

    def _do_sell(account):
        # 找到持仓
        holdings = account.get("holdings", [])
        target = None
        for h in holdings:
            if str(h.get("code", "")).zfill(6) == code:
                target = h
                break

        if not target:
            return None  # 无持仓

        available = can_sell_today(account, code)
        sell_qty = min(quantity, available)
        if sell_qty <= 0:
            return None  # T+1 限制

        # 计算盈亏
        cost_price = float(target.get("cost_price", 0))
        pnl = round((price - cost_price) * sell_qty - cost, 2)
        pnl_pct = (price - cost_price) / cost_price if cost_price > 0 else 0

        # 更新持仓
        old_qty = int(target.get("quantity", 0))
        new_qty = old_qty - sell_qty
        if new_qty <= 0:
            holdings.remove(target)
        else:
            target["quantity"] = new_qty
            target["market_value"] = round(price * new_qty, 2)

        # 增加现金
        account["current_cash"] = round(account.get("current_cash", 0) + net_amount, 2)

        # 记录盈亏信息供交易记录使用
        account["_last_sell_pnl"] = pnl
        account["_last_sell_pnl_pct"] = pnl_pct
        account["_last_sell_qty"] = sell_qty

        return account

    result = _locked_update(ACCOUNT_FILE, _do_sell)
    if result is None:
        return {"success": False, "message": f"卖出失败: 无持仓或T+1限制"}

    pnl = result.pop("_last_sell_pnl", 0)
    pnl_pct = result.pop("_last_sell_pnl_pct", 0)
    sell_qty = result.pop("_last_sell_qty", quantity)

    # 记录交易
    trade = {
        "type": "sell",
        "code": code,
        "name": name,
        "price": price,
        "quantity": sell_qty,
        "amount": round(price * sell_qty, 2),
        "cost": cost,
        "pnl": pnl,
        "pnl_pct": round(pnl_pct, 4),
        "reasons": reasons or [],
        "timestamp": datetime.now().isoformat(),
    }
    _append_transaction(trade)

    emoji = "🟢" if pnl >= 0 else "🔴"
    logger.info(f"{emoji} 卖出 {name}({code}) {sell_qty}股 @{price} 盈亏{pnl:+,.0f}")
    return {"success": True, "message": f"卖出成功", "trade": trade}


# ─── 交易记录 ───

def _append_transaction(trade: Dict):
    """追加交易记录到 transactions.json"""
    try:
        txs = _locked_read(TRANSACTIONS_FILE, [])
        if not isinstance(txs, list):
            txs = []
        txs.append(trade)
        _locked_write(TRANSACTIONS_FILE, txs)
    except Exception as e:
        logger.error(f"记录交易失败: {e}")


def get_today_transactions() -> List[Dict]:
    """获取今日交易列表"""
    today = datetime.now().strftime("%Y-%m-%d")
    txs = _locked_read(TRANSACTIONS_FILE, [])
    return [t for t in txs if t.get("timestamp", "").startswith(today)]


def get_today_buy_count() -> int:
    """获取今日买入次数（不同股票去重）"""
    today_txs = get_today_transactions()
    buy_codes = set()
    for t in today_txs:
        if t.get("type") == "buy":
            buy_codes.add(t.get("code", ""))
    return len(buy_codes)


def clean_frozen_sells(account: Dict) -> Dict:
    """清理过期的T+1冻结记录（非今日买入的）"""
    today = datetime.now().strftime("%Y-%m-%d")
    frozen = account.get("frozen_sells", [])
    account["frozen_sells"] = [f for f in frozen if f.get("buy_date") == today]
    return account
