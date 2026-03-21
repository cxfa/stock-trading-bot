#!/usr/bin/env python3
"""
交易策略模块 — 独立的策略决策中心

职责:
1. 盘中快速评估 (assess_intraday): 每30分钟，基于实时行情快速调整持仓策略
2. 盘后全面调整 (full_review_adjust): 复盘后，LLM深度分析+确定买入目标+制定完整策略
3. 实时信号判断 (evaluate_position / evaluate_buy_candidate): 每分钟由监控系统调用

因子体系:
- 大盘指数 (上证/深成/创业板走势、状态)
- 新闻情绪 (东财/新浪快讯关键词分析)
- 技术面 (MACD/RSI/KDJ/布林/均线/量价)
- 资金流 (北向/主力/机构)
- 市场状态 (牛市/震荡/熊市 — 马尔可夫模型)
- 风控 (回撤熔断/仓位限制/集中度)
- LLM辩论 (多空辩论置信度)

设计原则:
- 本模块不执行交易，只输出决策
- 快速评估 (<1秒): 纯规则，不调LLM
- 全面调整 (~30秒): 调LLM，深度分析
- 决策输出标准化: {action, code, quantity, urgency, reasons}
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
STRATEGY_STATE_FILE = DATA_DIR / "strategy_state.json"
BUY_PLAN_FILE = DATA_DIR / "buy_plan.json"

# ─── 数据结构 ───


@dataclass
class Signal:
    """策略输出的交易信号"""
    action: str         # "buy" | "sell" | "reduce" | "hold" | "skip"
    code: str
    name: str = ""
    quantity: int = 0   # 建议数量 (0=由执行器决定)
    urgency: str = "normal"  # "immediate" | "normal" | "low"
    reasons: List[str] = field(default_factory=list)
    confidence: float = 0.5  # 0~1
    price_limit: float = 0   # 限价 (0=市价)
    extra: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class MarketContext:
    """市场环境快照"""
    timestamp: str = ""
    # 大盘
    index_sh: float = 0       # 上证指数
    index_sh_pct: float = 0   # 上证涨跌幅%
    index_sz: float = 0       # 深成指
    index_sz_pct: float = 0
    index_cy: float = 0       # 创业板
    index_cy_pct: float = 0
    market_regime: str = "range"  # bull/range/bear
    regime_confidence: float = 0.5
    # 情绪
    news_sentiment: float = 0    # -10 ~ +10
    news_label: str = "neutral"
    hot_sectors: List = field(default_factory=list)
    # 风控
    risk_level: str = "low"      # low/medium/high
    drawdown_pct: float = 0
    circuit_breaker: bool = False
    # 仓位
    total_value: float = 0
    cash: float = 0
    position_pct: float = 0
    holdings_count: int = 0

    def to_dict(self) -> Dict:
        return asdict(self)


# ─── 工具函数 ───


def _load_json(path: Path, default: Any = None) -> Any:
    if default is None:
        default = {}
    try:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return default


def _save_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_strategy_params() -> Dict:
    return _load_json(BASE_DIR / "strategy_params.json", {})


# ─── 市场环境收集 ───


def gather_market_context(account: Dict = None) -> MarketContext:
    """
    收集当前市场环境 (快速，<0.5秒)。
    会被监控系统每分钟调用。
    """
    ctx = MarketContext(timestamp=datetime.now().isoformat())

    # 1. 大盘指数
    try:
        from fetch_stock_data import fetch_market_overview
        overview = fetch_market_overview()
        if overview:
            for idx in overview:
                code = idx.get("code", "")
                pct = float(idx.get("change_pct", 0))
                price = float(idx.get("price", 0))
                if "000001" in code:  # 上证
                    ctx.index_sh = price
                    ctx.index_sh_pct = pct
                elif "399001" in code:  # 深成
                    ctx.index_sz = price
                    ctx.index_sz_pct = pct
                elif "399006" in code:  # 创业板
                    ctx.index_cy = price
                    ctx.index_cy_pct = pct
    except Exception as e:
        logger.debug(f"获取大盘失败: {e}")

    # 2. 市场状态 (缓存1小时)
    try:
        from market_regime import detect_market_regime
        regime = detect_market_regime()
        ctx.market_regime = regime.get("current_regime", "range")
        ctx.regime_confidence = regime.get("confidence", 0.5)
    except Exception as e:
        logger.debug(f"市场状态检测失败: {e}")

    # 3. 新闻情绪 (缓存30分钟)
    try:
        sentiment_file = BASE_DIR / "news" / f"sentiment_{datetime.now().strftime('%Y%m%d')}.json"
        if sentiment_file.exists():
            sdata = _load_json(sentiment_file)
            ctx.news_sentiment = sdata.get("overall_sentiment", 0)
            ctx.news_label = sdata.get("overall_label", "neutral")
            ctx.hot_sectors = sdata.get("hot_sectors", [])[:5]
    except Exception:
        pass

    # 4. 风控
    if account:
        try:
            from risk_manager import calculate_portfolio_risk, check_drawdown_circuit_breaker
            risk = calculate_portfolio_risk(account)
            ctx.risk_level = risk.get("risk_level", "low")
            ctx.drawdown_pct = risk.get("max_drawdown", 0)
            cb = check_drawdown_circuit_breaker(account, max_dd=0.10)
            ctx.circuit_breaker = cb.get("triggered", False)
        except Exception:
            pass

        ctx.total_value = account.get("total_value", 0)
        ctx.cash = account.get("current_cash", 0)
        tv = ctx.total_value or 1
        ctx.position_pct = (1 - ctx.cash / tv) * 100 if tv > 0 else 0
        ctx.holdings_count = len(account.get("holdings", []))

    return ctx


# ─── 持仓评估 (每分钟) ───


def evaluate_position(
    holding: Dict,
    realtime: Dict,
    context: MarketContext,
    params: Dict,
) -> Optional[Signal]:
    """
    评估单个持仓是否需要操作 (快速, <10ms)。
    每分钟由监控系统对每个持仓调用。

    Returns:
        Signal if action needed, None if hold
    """
    code = str(holding.get("code", "")).zfill(6)
    name = holding.get("name", code)
    cost = float(holding.get("cost_price", 0) or 0)
    qty = int(holding.get("quantity", 0) or 0)
    rt = realtime.get(code, {})
    price = float(rt.get("price", 0) or 0)

    if cost <= 0 or price <= 0 or qty <= 0:
        return None

    pnl_pct = (price - cost) / cost
    reasons = []

    # ── 止损 ──
    stop_loss = float(params.get("stop_loss_pct", -0.03))
    if pnl_pct <= stop_loss:
        return Signal(
            action="sell", code=code, name=name, quantity=qty,
            urgency="immediate",
            reasons=[f"止损触发: {pnl_pct:.1%} <= {stop_loss:.1%}"],
            confidence=0.95,
        )

    # ── 熔断 ──
    if context.circuit_breaker:
        return Signal(
            action="sell", code=code, name=name, quantity=qty,
            urgency="immediate",
            reasons=["回撤熔断触发: 清仓保护"],
            confidence=0.9,
        )

    # ── 止盈 ──
    tp = float(params.get("take_profit_pct", 0.04))
    tp_full = float(params.get("take_profit_full_pct", 0.08))
    if pnl_pct >= tp_full:
        return Signal(
            action="sell", code=code, name=name, quantity=qty,
            urgency="immediate",
            reasons=[f"全止盈触发: {pnl_pct:.1%} >= {tp_full:.1%}"],
            confidence=0.9,
        )
    if pnl_pct >= tp:
        sell_qty = max(100, (qty // 2 // 100) * 100)
        return Signal(
            action="reduce", code=code, name=name, quantity=sell_qty,
            urgency="normal",
            reasons=[f"止盈减仓: {pnl_pct:.1%} >= {tp:.1%}"],
            confidence=0.8,
        )

    # ── ATR 追踪止损 ──
    high_since = float(holding.get("high_since_entry", 0) or 0)
    if high_since > 0 and price < high_since:
        try:
            from technical_analysis import calculate_hybrid_atr
            from fetch_stock_data import fetch_kline
            # 使用缓存的 ATR (由监控系统提供)
            atr_pct = holding.get("_cached_atr_pct", 0)
            if atr_pct > 0:
                trail_mult = float(params.get("trailing_stop_atr_multiplier", 1.5))
                drawdown_pct = (high_since - price) / high_since
                if drawdown_pct >= atr_pct * trail_mult:
                    sell_pct = float(params.get("trailing_stop_sell_pct", 0.55))
                    sell_qty = max(100, int(qty * sell_pct / 100) * 100)
                    return Signal(
                        action="reduce", code=code, name=name, quantity=sell_qty,
                        urgency="normal",
                        reasons=[f"ATR追踪止损: 从最高{high_since:.2f}回撤{drawdown_pct:.1%} >= {trail_mult}×ATR"],
                        confidence=0.75,
                    )
        except Exception:
            pass

    # ── 大盘联动风控 ──
    if context.index_sh_pct < -2.0 and pnl_pct < 0:
        reasons.append(f"大盘跌{context.index_sh_pct:.1f}%+浮亏{pnl_pct:.1%}")
        # 不立即卖出，但标记为需关注
        if pnl_pct < -0.02:
            sell_qty = max(100, (qty // 3 // 100) * 100)
            return Signal(
                action="reduce", code=code, name=name, quantity=sell_qty,
                urgency="normal",
                reasons=[f"大盘联动减仓: 大盘{context.index_sh_pct:.1f}%+浮亏{pnl_pct:.1%}"],
                confidence=0.6,
            )

    return None  # hold


# ── 买入候选评估 (每分钟) ──


def evaluate_buy_candidate(
    candidate: Dict,
    realtime: Dict,
    context: MarketContext,
    params: Dict,
    account: Dict,
) -> Optional[Signal]:
    """
    评估候选股是否值得买入 (快速, <10ms)。
    只做初步筛选，不调LLM。

    candidate: 来自 buy_plan.json 的买入目标
    """
    code = str(candidate.get("code", "")).zfill(6)
    name = candidate.get("name", code)
    rt = realtime.get(code, {})
    price = float(rt.get("price", 0) or 0)

    if price <= 0:
        return None

    # 检查市场环境
    if context.circuit_breaker:
        return None  # 熔断期不买入
    if context.risk_level == "high":
        return None  # 高风险不买入

    # 检查仓位限制
    max_total = float(params.get("max_total_position", 0.5))
    if context.position_pct / 100 >= max_total:
        return None  # 仓位已满

    # 检查当日限制
    max_daily = int(params.get("max_daily_buys", 2))
    today_buys = candidate.get("_today_buy_count", 0)
    if today_buys >= max_daily:
        return None

    # 检查买入条件
    target_price = candidate.get("target_price", 0)
    strategy = candidate.get("strategy", "")
    reasons = candidate.get("reasons", [])

    # 价格条件
    if target_price > 0 and price > target_price * 1.02:
        return None  # 超过目标价2%不追

    # 大盘联动
    if context.index_sh_pct < -1.5:
        return None  # 大盘大跌不买

    # 技术面快速检查
    change_pct = float(rt.get("change_pct", 0) or 0)
    if change_pct > 7:
        return None  # 涨太多不追
    if change_pct < -5:
        return None  # 跌太多不抄底(可能有利空)

    # 量价检查
    volume_ratio = float(rt.get("volume_ratio", 0) or 0)
    min_vol = float(params.get("volume_ratio_min", 1.2))
    # 放量才买(但不要求太严格)

    # 计算买入数量
    max_pct = float(params.get("first_buy_max_pct", 0.07))
    max_amount = context.cash * 0.3  # 单次最多用30%可用现金
    max_amount = min(max_amount, context.total_value * max_pct)
    if max_amount < 5000:
        return None  # 资金不足

    quantity = int(max_amount / price / 100) * 100
    if quantity < 100:
        return None

    return Signal(
        action="buy", code=code, name=name, quantity=quantity,
        urgency="normal",
        reasons=reasons or [f"买入计划: {strategy}"],
        confidence=candidate.get("confidence", 0.6),
        price_limit=target_price if target_price > 0 else 0,
        extra={"strategy": strategy, "plan_source": "review"},
    )


# ─── 盘中快速调整 (每30分钟) ───


def assess_intraday(account: Dict, context: MarketContext) -> Dict:
    """
    盘中快速策略评估。每30分钟由监控系统调用。
    不调LLM，纯规则+数据驱动，必须<1秒完成。

    Returns:
        {
            "adjustments": {param: new_value},  # 临时参数调整
            "alerts": [str],                    # 预警信息
            "market_summary": str,              # 市场简况
        }
    """
    params = _load_strategy_params()
    adjustments = {}
    alerts = []

    # 1. 大盘联动调整
    sh_pct = context.index_sh_pct
    if sh_pct < -3:
        # 大盘暴跌: 收紧止损，降低仓位上限
        adjustments["stop_loss_pct"] = max(params.get("stop_loss_pct", -0.03), -0.02)
        adjustments["max_total_position"] = min(params.get("max_total_position", 0.5), 0.3)
        alerts.append(f"⚠️大盘暴跌{sh_pct:.1f}%: 收紧止损到-2%, 仓位上限降至30%")
    elif sh_pct < -1.5:
        adjustments["max_daily_buys"] = 1
        alerts.append(f"⚠️大盘调整{sh_pct:.1f}%: 今日限买1笔")
    elif sh_pct > 2:
        # 大盘大涨: 适当放宽
        adjustments["take_profit_pct"] = min(params.get("take_profit_pct", 0.04) + 0.01, 0.08)
        alerts.append(f"📈大盘上涨{sh_pct:.1f}%: 适当提高止盈线")

    # 2. 市场状态调整
    if context.market_regime == "bear" and params.get("max_total_position", 0.5) > 0.35:
        adjustments["max_total_position"] = 0.30
        alerts.append("🐻熊市状态: 仓位上限降至30%")
    elif context.market_regime == "bull" and params.get("max_total_position", 0.5) < 0.55:
        adjustments["max_total_position"] = 0.55
        alerts.append("🐂牛市状态: 仓位上限提至55%")

    # 3. 情绪异常
    if context.news_sentiment < -5:
        adjustments["min_score"] = max(params.get("min_score", 65), 75)
        alerts.append(f"😨市场恐慌(情绪={context.news_sentiment:.0f}): 提高选股门槛")
    elif context.news_sentiment > 5:
        alerts.append(f"🎉市场亢奋(情绪={context.news_sentiment:.0f}): 注意追高风险")

    # 4. 风控预警
    if context.risk_level == "high":
        alerts.append("🚨组合风险高: 建议减仓")
    if context.circuit_breaker:
        alerts.append("🔴回撤熔断: 禁止买入，仅允许卖出")

    # 5. 生成市场简况
    regime_cn = {"bull": "牛市", "range": "震荡", "bear": "熊市"}.get(context.market_regime, "未知")
    market_summary = (
        f"上证{context.index_sh:.0f}({context.index_sh_pct:+.1f}%) "
        f"深成({context.index_sz_pct:+.1f}%) "
        f"创业板({context.index_cy_pct:+.1f}%) | "
        f"状态:{regime_cn} | 情绪:{context.news_label} | 风险:{context.risk_level}"
    )

    # 保存临时调整
    if adjustments:
        state = _load_json(STRATEGY_STATE_FILE)
        state["intraday_adjustments"] = adjustments
        state["intraday_adjusted_at"] = datetime.now().isoformat()
        state["intraday_alerts"] = alerts
        _save_json(STRATEGY_STATE_FILE, state)

    return {
        "adjustments": adjustments,
        "alerts": alerts,
        "market_summary": market_summary,
    }


def get_effective_params() -> Dict:
    """
    获取当前生效的策略参数 = 基础参数 + 盘中临时调整。
    被监控系统每分钟调用。
    """
    base = _load_strategy_params()
    state = _load_json(STRATEGY_STATE_FILE)
    adjustments = state.get("intraday_adjustments", {})

    # 检查调整是否过期 (只在当日有效)
    adj_time = state.get("intraday_adjusted_at", "")
    if adj_time and not adj_time.startswith(datetime.now().strftime("%Y-%m-%d")):
        adjustments = {}  # 跨日失效

    # 合并
    effective = {**base, **adjustments}
    return effective


# ─── 盘后全面调整 (复盘后调用) ───


def generate_buy_plan(
    candidates: List[Dict],
    account: Dict,
    context: MarketContext,
    llm_insights: Dict = None,
) -> List[Dict]:
    """
    从候选股中确定买入目标，制定买入策略。
    由复盘系统调用，支持LLM辅助。

    Args:
        candidates: 选股系统提供的候选股 [{code, name, discovery_score, ...}]
        account: 账户状态
        context: 市场环境
        llm_insights: LLM复盘分析结果

    Returns:
        买入计划列表 [{code, name, strategy, target_price, reasons, confidence}]
    """
    params = _load_strategy_params()
    plans = []

    # 可用资金
    cash = account.get("current_cash", 0)
    total = account.get("total_value", 1000000)
    max_position = float(params.get("max_total_position", 0.5))
    current_pct = (1 - cash / total) if total > 0 else 1

    if current_pct >= max_position:
        logger.info("仓位已满，不生成买入计划")
        return []

    # LLM推荐 & 回避
    llm_watch = set()
    llm_avoid = set()
    if llm_insights:
        reco = llm_insights.get("stock_recommendations", {})
        llm_watch = set(reco.get("watch_list", []))
        llm_avoid = set(reco.get("avoid_list", []))

    # 多日跟踪加成
    tracker_boost = {}
    try:
        from multi_day_tracker import MultiDayTracker
        tracker = MultiDayTracker()
        tracker_boost = tracker.get_discovery_boost()
    except Exception:
        pass

    # 评估每个候选
    scored_candidates = []
    for c in candidates:
        code = str(c.get("code", "")).zfill(6)
        name = c.get("name", code)
        base_score = c.get("discovery_score", 0)

        if code in llm_avoid:
            continue  # LLM建议回避

        # 综合评分
        final_score = base_score
        reasons = []

        # LLM推荐加分
        if code in llm_watch:
            final_score += 15
            reasons.append("LLM复盘推荐")

        # 多日跟踪加分
        boost = tracker_boost.get(code, 0)
        if boost != 0:
            final_score += boost
            if boost > 0:
                reasons.append(f"多日跟踪+{boost}")

        # 市场状态调整
        if context.market_regime == "bear":
            final_score -= 10  # 熊市提高门槛
        elif context.market_regime == "bull":
            final_score += 5

        scored_candidates.append({
            "code": code,
            "name": name,
            "score": final_score,
            "original_score": base_score,
            "reasons": reasons,
            "source_data": c,
        })

    # 排序取Top
    scored_candidates.sort(key=lambda x: x["score"], reverse=True)
    min_score = int(params.get("min_score", 65))

    # 最多选 max_daily_buys 个
    max_targets = int(params.get("max_daily_buys", 2))
    for sc in scored_candidates[:max_targets * 2]:  # 多选一些备选
        if sc["score"] < min_score:
            continue
        if len(plans) >= max_targets:
            break

        code = sc["code"]
        name = sc["name"]

        # 确定买入策略
        strategy = "均线支撑买入"  # 默认
        target_price = 0
        confidence = min(0.9, sc["score"] / 100)

        if "多日跟踪" in " ".join(sc["reasons"]):
            strategy = "持续强势追踪买入"
            confidence = min(0.85, confidence + 0.1)
        if "LLM复盘推荐" in " ".join(sc["reasons"]):
            strategy = "LLM深度分析买入"
            confidence = min(0.9, confidence + 0.1)

        plans.append({
            "code": code,
            "name": name,
            "strategy": strategy,
            "target_price": target_price,
            "max_amount": min(cash * 0.3, total * float(params.get("first_buy_max_pct", 0.07))),
            "confidence": round(confidence, 2),
            "reasons": sc["reasons"] + [f"选股评分{sc['score']}"],
            "score": sc["score"],
            "created_at": datetime.now().isoformat(),
        })

    # 保存买入计划
    plan_data = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now().isoformat(),
        "market_regime": context.market_regime,
        "plans": plans,
        "total_candidates": len(candidates),
        "filtered_candidates": len(scored_candidates),
    }
    _save_json(BUY_PLAN_FILE, plan_data)
    logger.info(f"买入计划已生成: {len(plans)}个目标")

    return plans


def load_buy_plan() -> List[Dict]:
    """加载当日买入计划 (供监控系统使用)"""
    plan = _load_json(BUY_PLAN_FILE)
    today = datetime.now().strftime("%Y-%m-%d")
    if plan.get("date") != today:
        return []  # 过期计划不使用
    return plan.get("plans", [])


def full_review_adjust(
    review_result: Dict,
    account: Dict,
    candidates: List[Dict],
) -> Dict:
    """
    复盘后全面策略调整。由复盘系统调用。

    Args:
        review_result: LLM复盘输出
        account: 账户状态
        candidates: 选股候选

    Returns:
        {
            "param_changes": [{param, old, new, reason}],
            "buy_plans": [买入计划],
            "system_alerts": [str],
        }
    """
    context = gather_market_context(account)
    params = _load_strategy_params()

    # 1. 从复盘建议中提取参数调整
    param_changes = []
    suggestions = review_result.get("param_suggestions", [])
    for s in suggestions:
        param = s.get("param", "")
        suggested = s.get("suggested")
        if param in params and suggested is not None:
            old = params[param]
            if old != suggested:
                param_changes.append({
                    "param": param,
                    "old": old,
                    "new": suggested,
                    "reason": s.get("reason", ""),
                })
                params[param] = suggested

    # 2. 保存参数变更
    if param_changes:
        params["version"] = params.get("version", 0) + 1
        params["last_review_update"] = datetime.now().isoformat()
        _save_json(BASE_DIR / "strategy_params.json", params)
        logger.info(f"策略参数已更新: {len(param_changes)}项变更")

    # 3. 生成买入计划
    llm_analysis = review_result.get("llm_analysis", {})
    buy_plans = generate_buy_plan(candidates, account, context, llm_analysis)

    # 4. 清理盘中临时调整 (复盘后重置)
    state = _load_json(STRATEGY_STATE_FILE)
    state["intraday_adjustments"] = {}
    state["last_full_review"] = datetime.now().isoformat()
    _save_json(STRATEGY_STATE_FILE, state)

    return {
        "param_changes": param_changes,
        "buy_plans": buy_plans,
        "system_alerts": review_result.get("issues", []),
    }
