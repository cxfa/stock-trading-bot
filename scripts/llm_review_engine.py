#!/usr/bin/env python3
"""
LLM 增强复盘引擎 — 结合规则分析与大语言模型深度洞察

工作流:
1. 收集数据: 今日交易、持仓、市场行情、多日跟踪
2. 规则分析: 计算统计指标(胜率/盈亏比/Sharpe/回撤)
3. LLM 深度分析: 将结构化数据喂给 LLM，获得深度洞察
4. 参数建议: LLM 输出参数调整建议 → 验证边界 → 安全应用
5. 输出: 结构化复盘报告(JSON + Markdown)，供选股/交易系统读取

设计原则:
- LLM 失败时降级到纯规则分析（不阻塞）
- 参数变更有安全边界，防止极端值
- 输出 review_output.json 作为其他子系统的输入
"""

import json
import math
import os
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
REVIEW_DIR = BASE_DIR / "reviews"
REVIEW_DIR.mkdir(parents=True, exist_ok=True)
REVIEW_OUTPUT = DATA_DIR / "review_output.json"  # 复盘输出，供其他子系统读取

# 参数安全边界
PARAM_BOUNDS = {
    "stop_loss_pct": (-0.12, -0.02),       # 止损: -12% ~ -2%
    "take_profit_pct": (0.03, 0.15),        # 止盈: 3% ~ 15%
    "take_profit_full_pct": (0.06, 0.25),   # 全止盈: 6% ~ 25%
    "min_score": (55, 85),                  # 最低评分: 55 ~ 85
    "max_position_pct": (0.05, 0.25),       # 单股仓位: 5% ~ 25%
    "max_total_position_pct": (0.3, 0.95),  # 总仓位: 30% ~ 95%
    "volume_ratio_min": (0.8, 2.5),         # 量比最低: 0.8 ~ 2.5
    "debate_min_confidence": (30, 70),       # LLM辩论置信度: 30 ~ 70
}


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


def _call_llm(prompt: str) -> Optional[str]:
    """调用 LLM（复用 bull_bear_debate 的 OpenClaw 集成）"""
    try:
        from bull_bear_debate import _call_via_openclaw, _call_direct
        # 优先 OpenClaw
        try:
            return _call_via_openclaw(prompt)
        except Exception:
            pass
        # 回退直连
        try:
            return _call_direct(prompt)
        except Exception:
            pass
    except ImportError:
        pass
    return None


class LLMReviewEngine:
    """LLM 增强复盘引擎"""

    def __init__(self):
        self.transactions_file = BASE_DIR / "transactions.json"
        self.account_file = BASE_DIR / "account.json"
        self.params_file = BASE_DIR / "strategy_params.json"
        self.tracker = None
        try:
            from multi_day_tracker import MultiDayTracker
            self.tracker = MultiDayTracker()
        except ImportError:
            pass

    def run_review(self, date: str = None) -> Dict:
        """
        运行完整复盘流程。

        Returns:
            复盘结果 dict，包含:
            - stats: 统计指标
            - issues: 发现的问题
            - llm_analysis: LLM 深度分析
            - param_suggestions: 参数调整建议
            - stock_insights: 个股洞察（供选股系统用）
            - report_md: Markdown 报告
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        print(f"📊 开始LLM增强复盘 ({date})...")

        # 1. 收集数据
        data = self._collect_data(date)

        # 2. 规则分析
        stats = self._calculate_stats(data)
        issues = self._identify_issues(data, stats)

        # 3. LLM 深度分析
        llm_analysis = self._llm_analyze(data, stats, issues)

        # 4. 生成参数建议
        param_suggestions = self._generate_param_suggestions(stats, issues, llm_analysis)

        # 5. 个股洞察（供选股系统使用）
        stock_insights = self._generate_stock_insights(data, llm_analysis)

        # 6. 系统健康检查
        system_health = self._check_system_health(data, date)

        # 7. 从候选股生成买入计划 (调用策略模块)
        buy_plan = self._generate_buy_plan(data, llm_analysis)

        # 8. 全面策略调整 (调用策略模块)
        strategy_result = self._apply_full_strategy_adjust(result={
            "param_suggestions": param_suggestions,
            "llm_analysis": llm_analysis,
            "issues": issues,
        }, data=data)

        # 9. 生成报告
        report_md = self._generate_report(
            date, stats, issues, llm_analysis, param_suggestions,
            system_health, buy_plan,
        )

        result = {
            "date": date,
            "generated_at": datetime.now().isoformat(),
            "stats": stats,
            "issues": issues,
            "llm_analysis": llm_analysis,
            "param_suggestions": param_suggestions,
            "stock_insights": stock_insights,
            "system_health": system_health,
            "buy_plan": buy_plan,
            "strategy_adjustments": strategy_result,
            "report_md": report_md,
        }

        # 7. 保存
        _save_json(REVIEW_OUTPUT, result)
        _save_json(REVIEW_DIR / f"{date}_llm.json", result)

        # 8. 保存 Markdown 报告
        report_file = REVIEW_DIR / f"{date}_report.md"
        report_file.write_text(report_md, encoding="utf-8")

        print(f"✅ 复盘完成，报告已保存到 {report_file}")
        return result

    # ==================== 数据收集 ====================

    def _collect_data(self, date: str) -> Dict:
        """收集复盘所需的所有数据"""
        transactions = _load_json(self.transactions_file, [])
        account = _load_json(self.account_file, {})
        params = _load_json(self.params_file, {})

        # 今日交易
        today_trades = [t for t in transactions if t.get("timestamp", "").startswith(date)]

        # 近7天交易
        cutoff_7d = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
        recent_trades = [t for t in transactions if t.get("timestamp", "") >= cutoff_7d]

        # 近30天交易（计算长期指标）
        cutoff_30d = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
        month_trades = [t for t in transactions if t.get("timestamp", "") >= cutoff_30d]

        # 多日跟踪数据
        tracking_summary = {}
        tracking_text = ""
        if self.tracker:
            tracking_summary = self.tracker.get_summary()
            tracking_text = self.tracker.format_for_llm()

        # 历史复盘（最近5次）
        recent_reviews = []
        for i in range(1, 6):
            d = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=i)).strftime("%Y-%m-%d")
            r = _load_json(REVIEW_DIR / f"{d}_llm.json")
            if r:
                recent_reviews.append(r)

        return {
            "date": date,
            "account": account,
            "params": params,
            "today_trades": today_trades,
            "recent_trades": recent_trades,
            "month_trades": month_trades,
            "tracking_summary": tracking_summary,
            "tracking_text": tracking_text,
            "recent_reviews": recent_reviews,
        }

    # ==================== 规则分析 ====================

    def _calculate_stats(self, data: Dict) -> Dict:
        """计算统计指标"""
        account = data["account"]
        today = data["today_trades"]
        recent = data["recent_trades"]
        month = data["month_trades"]

        # 今日统计
        today_sells = [t for t in today if t.get("type") == "sell" or t.get("action") == "sell"]
        today_buys = [t for t in today if t.get("type") == "buy" or t.get("action") == "buy"]
        today_pnl = sum(t.get("pnl", 0) for t in today_sells)

        wins = [t for t in today_sells if t.get("pnl", 0) > 0]
        losses = [t for t in today_sells if t.get("pnl", 0) < 0]

        # 持仓统计
        holdings = account.get("holdings", [])
        total_value = account.get("total_value", 1000000)
        cash = account.get("current_cash", 0)
        position_pct = (1 - cash / total_value) * 100 if total_value > 0 else 0

        # 近7天滚动统计
        week_sells = [t for t in recent if t.get("type") == "sell" or t.get("action") == "sell"]
        week_wins = [t for t in week_sells if t.get("pnl", 0) > 0]
        week_pnl = sum(t.get("pnl", 0) for t in week_sells)

        # 近30天统计
        month_sells = [t for t in month if t.get("type") == "sell" or t.get("action") == "sell"]
        month_wins = [t for t in month_sells if t.get("pnl", 0) > 0]
        month_pnl = sum(t.get("pnl", 0) for t in month_sells)

        # Sharpe Ratio (简化: 基于日收益率)
        daily_returns = self._compute_daily_returns(month)
        sharpe = self._calculate_sharpe(daily_returns)

        # 最大回撤
        max_dd = account.get("max_drawdown", 0)

        # 平均持有天数（从交易记录推算）
        avg_hold = self._calculate_avg_hold_days(month)

        stats = {
            "today": {
                "pnl": today_pnl,
                "pnl_pct": today_pnl / total_value * 100 if total_value > 0 else 0,
                "buys": len(today_buys),
                "sells": len(today_sells),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": len(wins) / len(today_sells) * 100 if today_sells else 0,
            },
            "week": {
                "pnl": week_pnl,
                "trades": len(week_sells),
                "win_rate": len(week_wins) / len(week_sells) * 100 if week_sells else 0,
            },
            "month": {
                "pnl": month_pnl,
                "trades": len(month_sells),
                "win_rate": len(month_wins) / len(month_sells) * 100 if month_sells else 0,
                "sharpe": sharpe,
                "avg_hold_days": avg_hold,
            },
            "portfolio": {
                "total_value": total_value,
                "cash": cash,
                "position_pct": position_pct,
                "holdings_count": len(holdings),
                "max_drawdown": max_dd,
                "total_pnl": account.get("total_pnl", 0),
                "total_pnl_pct": account.get("total_pnl_pct", 0),
            },
        }

        # 个股盈亏明细
        stock_pnl = {}
        for t in today_sells:
            code = t.get("code", "")
            pnl = t.get("pnl", 0)
            stock_pnl[code] = stock_pnl.get(code, 0) + pnl
        stats["today"]["stock_pnl"] = stock_pnl

        return stats

    def _compute_daily_returns(self, trades: List[Dict]) -> List[float]:
        """从交易记录推算日收益率序列"""
        by_date = {}
        for t in trades:
            d = t.get("timestamp", "")[:10]
            if d:
                by_date.setdefault(d, 0)
                by_date[d] += t.get("pnl", 0)

        # 转为收益率（假设总资产100万）
        total = 1000000
        return [pnl / total for pnl in sorted(by_date.values())]

    def _calculate_sharpe(self, returns: List[float], risk_free: float = 0.03 / 252) -> float:
        """计算年化 Sharpe Ratio"""
        if len(returns) < 5:
            return 0.0
        mean_r = statistics.mean(returns)
        std_r = statistics.stdev(returns)
        if std_r == 0:
            return 0.0
        return round((mean_r - risk_free) / std_r * math.sqrt(252), 2)

    def _calculate_avg_hold_days(self, trades: List[Dict]) -> float:
        """计算平均持有天数"""
        hold_days = []
        sells = [t for t in trades if t.get("type") == "sell" or t.get("action") == "sell"]
        for t in sells:
            buy_date = t.get("buy_date", "")
            sell_date = t.get("timestamp", "")[:10]
            if buy_date and sell_date:
                try:
                    bd = datetime.strptime(buy_date, "%Y-%m-%d")
                    sd = datetime.strptime(sell_date, "%Y-%m-%d")
                    hold_days.append((sd - bd).days)
                except ValueError:
                    pass
        return round(statistics.mean(hold_days), 1) if hold_days else 0

    # ==================== 问题识别 ====================

    def _identify_issues(self, data: Dict, stats: Dict) -> List[Dict]:
        """识别交易问题（返回结构化问题列表）"""
        issues = []
        account = data["account"]
        today = data["today_trades"]
        params = data["params"]

        # 1. 今日亏损过大
        today_pnl = stats["today"]["pnl"]
        total_value = stats["portfolio"]["total_value"]
        if total_value > 0 and today_pnl / total_value < -0.02:
            issues.append({
                "type": "large_daily_loss",
                "severity": "high",
                "description": f"今日亏损{today_pnl:,.0f}元({today_pnl/total_value*100:.1f}%)，超过2%警戒线",
                "data": {"pnl": today_pnl, "pnl_pct": today_pnl / total_value},
            })

        # 2. 止损执行延迟
        sells = [t for t in today if (t.get("type") == "sell" or t.get("action") == "sell")]
        for t in sells:
            pnl_pct = t.get("pnl_pct", 0)
            if pnl_pct < -0.08:
                issues.append({
                    "type": "late_stop_loss",
                    "severity": "high",
                    "description": f"{t.get('name', t.get('code'))}亏损{pnl_pct:.1%}超过-8%止损线",
                    "data": {"code": t.get("code"), "pnl_pct": pnl_pct},
                })

        # 3. 仓位集中度
        holdings = account.get("holdings", [])
        for h in holdings:
            mv = h.get("market_value", h.get("quantity", 0) * h.get("cost_price", 0))
            if total_value > 0 and mv / total_value > 0.20:
                issues.append({
                    "type": "position_concentration",
                    "severity": "medium",
                    "description": f"{h.get('name', h.get('code'))}仓位{mv/total_value:.1%}超过20%",
                    "data": {"code": h.get("code"), "pct": mv / total_value},
                })

        # 4. 连续亏损
        recent_reviews = data.get("recent_reviews", [])
        consecutive_loss = 0
        for r in recent_reviews:
            if r.get("stats", {}).get("today", {}).get("pnl", 0) < 0:
                consecutive_loss += 1
            else:
                break
        if consecutive_loss >= 3:
            issues.append({
                "type": "consecutive_losses",
                "severity": "high",
                "description": f"已连续{consecutive_loss}个交易日亏损",
                "data": {"days": consecutive_loss},
            })

        # 5. 胜率过低
        week_wr = stats["week"]["win_rate"]
        if stats["week"]["trades"] >= 3 and week_wr < 35:
            issues.append({
                "type": "low_win_rate",
                "severity": "medium",
                "description": f"近7天胜率仅{week_wr:.0f}%，低于35%警戒线",
                "data": {"win_rate": week_wr},
            })

        # 6. 最大回撤
        max_dd = stats["portfolio"]["max_drawdown"]
        if isinstance(max_dd, (int, float)) and max_dd < -0.10:
            issues.append({
                "type": "deep_drawdown",
                "severity": "critical",
                "description": f"最大回撤{max_dd:.1%}超过10%",
                "data": {"drawdown": max_dd},
            })

        return issues

    # ==================== LLM 深度分析 ====================

    def _llm_analyze(self, data: Dict, stats: Dict, issues: List[Dict]) -> Dict:
        """调用 LLM 做深度复盘分析"""
        prompt = self._build_llm_prompt(data, stats, issues)

        print("  🤖 正在调用LLM进行深度分析...")
        raw_response = _call_llm(prompt)

        if not raw_response:
            print("  ⚠️ LLM调用失败，使用纯规则分析")
            return {"llm_available": False, "fallback": True, "analysis": "LLM不可用，仅使用规则分析"}

        # 解析 LLM JSON 响应
        try:
            # 尝试从响应中提取 JSON
            json_start = raw_response.find('{')
            json_end = raw_response.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                result = json.loads(raw_response[json_start:json_end])
                result["llm_available"] = True
                result["raw_response"] = raw_response[:500]  # 保留前500字符用于调试
                return result
        except json.JSONDecodeError:
            pass

        # JSON解析失败，返回纯文本
        return {
            "llm_available": True,
            "parse_failed": True,
            "analysis": raw_response[:2000],
        }

    def _build_llm_prompt(self, data: Dict, stats: Dict, issues: List[Dict]) -> str:
        """构建 LLM 分析 prompt"""
        today = stats["today"]
        week = stats["week"]
        month = stats["month"]
        portfolio = stats["portfolio"]

        issue_text = "\n".join(
            f"- [{i['severity']}] {i['description']}" for i in issues
        ) or "无明显问题"

        # 今日交易明细
        trade_details = []
        for t in data["today_trades"]:
            action = t.get("type", t.get("action", "?"))
            code = t.get("code", "?")
            name = t.get("name", code)
            price = t.get("price", 0)
            pnl = t.get("pnl", 0)
            reasons = t.get("reasons", t.get("reason", ""))
            if isinstance(reasons, list):
                reasons = "; ".join(reasons[:3])
            trade_details.append(f"  {action} {name}({code}) @{price} 盈亏={pnl:+.0f} 原因={reasons}")
        trade_text = "\n".join(trade_details) or "  无交易"

        # 持仓明细
        holding_details = []
        for h in data["account"].get("holdings", []):
            code = h.get("code", "?")
            name = h.get("name", code)
            pnl_pct = h.get("pnl_pct", 0)
            mv = h.get("market_value", 0)
            holding_details.append(f"  {name}({code}) 市值={mv:,.0f} 浮盈={pnl_pct:+.1f}%")
        holding_text = "\n".join(holding_details) or "  空仓"

        # 多日跟踪摘要
        tracking_text = data.get("tracking_text", "无跟踪数据")

        # 当前参数
        params = data["params"]
        param_text = json.dumps({
            k: params.get(k) for k in [
                "stop_loss_pct", "take_profit_pct", "min_score",
                "max_position_pct", "volume_ratio_min", "debate_min_confidence"
            ] if k in params
        }, indent=2)

        return f"""你是一个专业量化交易复盘分析师。请基于以下数据进行深度复盘分析。

## 今日概况 ({data['date']})
- 盈亏: {today['pnl']:+,.0f}元 ({today['pnl_pct']:+.1f}%)
- 买入: {today['buys']}笔, 卖出: {today['sells']}笔
- 胜率: {today['win_rate']:.0f}%

## 近期表现
- 7日盈亏: {week['pnl']:+,.0f}元, 胜率: {week['win_rate']:.0f}%, 交易{week['trades']}笔
- 30日盈亏: {month['pnl']:+,.0f}元, 胜率: {month['win_rate']:.0f}%, Sharpe: {month['sharpe']}
- 平均持有: {month['avg_hold_days']}天

## 持仓状态
- 总资产: {portfolio['total_value']:,.0f}元, 仓位: {portfolio['position_pct']:.0f}%
- 持仓{portfolio['holdings_count']}只, 累计盈亏: {portfolio['total_pnl']:+,.0f}元 ({portfolio['total_pnl_pct']:+.1f}%)
{holding_text}

## 今日交易明细
{trade_text}

## 发现的问题
{issue_text}

## 多日跟踪
{tracking_text}

## 当前策略参数
{param_text}

请以JSON格式返回分析结果，包含以下字段:
{{
  "overall_assessment": "今日整体评价(一句话)",
  "root_causes": ["根本原因1", "根本原因2"],
  "strategy_effectiveness": {{
    "working_well": ["哪些策略有效"],
    "needs_improvement": ["哪些策略需要改进"]
  }},
  "param_adjustments": [
    {{"param": "参数名", "current": 当前值, "suggested": 建议值, "reason": "调整理由"}}
  ],
  "stock_recommendations": {{
    "watch_list": ["值得继续关注的股票代码"],
    "avoid_list": ["建议回避的股票代码"],
    "reasons": {{"代码": "理由"}}
  }},
  "action_items": ["明日具体行动1", "明日具体行动2"],
  "risk_warnings": ["风险提示"]
}}"""

    # ==================== 参数建议 ====================

    def _generate_param_suggestions(
        self, stats: Dict, issues: List[Dict], llm_analysis: Dict
    ) -> List[Dict]:
        """生成参数调整建议（结合规则+LLM，带安全边界验证）"""
        suggestions = []

        # 从 LLM 获取建议
        llm_params = llm_analysis.get("param_adjustments", [])
        for adj in llm_params:
            param = adj.get("param", "")
            suggested = adj.get("suggested")
            reason = adj.get("reason", "")

            if param in PARAM_BOUNDS and suggested is not None:
                lo, hi = PARAM_BOUNDS[param]
                safe_value = max(lo, min(hi, float(suggested)))
                suggestions.append({
                    "param": param,
                    "suggested": safe_value,
                    "original_suggested": suggested,
                    "clamped": safe_value != suggested,
                    "reason": reason,
                    "source": "llm",
                })

        # 规则补充建议
        for issue in issues:
            if issue["type"] == "late_stop_loss" and not any(s["param"] == "stop_loss_pct" for s in suggestions):
                suggestions.append({
                    "param": "stop_loss_pct",
                    "suggested": -0.06,
                    "reason": f"止损执行延迟: {issue['description']}",
                    "source": "rule",
                })
            elif issue["type"] == "low_win_rate" and not any(s["param"] == "min_score" for s in suggestions):
                suggestions.append({
                    "param": "min_score",
                    "suggested": 70,
                    "reason": f"胜率过低: {issue['description']}",
                    "source": "rule",
                })

        return suggestions

    def apply_param_suggestions(self, suggestions: List[Dict], dry_run: bool = True) -> Dict:
        """
        应用参数调整建议。

        Args:
            suggestions: 参数建议列表
            dry_run: 如果True，只返回变更预览，不实际写入

        Returns:
            {"changes": [...], "applied": bool}
        """
        params = _load_json(self.params_file, {})
        changes = []

        for s in suggestions:
            param = s["param"]
            new_val = s["suggested"]

            if param in params:
                old_val = params[param]
                if old_val != new_val:
                    changes.append({
                        "param": param,
                        "old": old_val,
                        "new": new_val,
                        "reason": s.get("reason", ""),
                    })
                    if not dry_run:
                        params[param] = new_val

        if changes and not dry_run:
            params["version"] = params.get("version", 0) + 1
            params["last_review_update"] = datetime.now().isoformat()
            params["last_review_changes"] = changes
            _save_json(self.params_file, params)

        return {"changes": changes, "applied": not dry_run}

    # ==================== 个股洞察 ====================

    def _generate_stock_insights(self, data: Dict, llm_analysis: Dict) -> Dict:
        """
        生成个股洞察，供选股系统使用。

        Returns:
            {
                "watch_list": [{code, name, reason, score_boost}],
                "avoid_list": [{code, name, reason, score_penalty}],
                "holding_assessments": [{code, name, action, reason}]
            }
        """
        insights = {
            "watch_list": [],
            "avoid_list": [],
            "holding_assessments": [],
        }

        # 从 LLM 获取
        llm_reco = llm_analysis.get("stock_recommendations", {})
        reasons = llm_reco.get("reasons", {})

        for code in llm_reco.get("watch_list", []):
            insights["watch_list"].append({
                "code": code,
                "reason": reasons.get(code, "LLM推荐"),
                "score_boost": 10,
                "source": "llm_review",
            })

        for code in llm_reco.get("avoid_list", []):
            insights["avoid_list"].append({
                "code": code,
                "reason": reasons.get(code, "LLM建议回避"),
                "score_penalty": -15,
                "source": "llm_review",
            })

        # 从持仓分析
        for h in data["account"].get("holdings", []):
            code = h.get("code", "")
            pnl_pct = h.get("pnl_pct", 0)
            name = h.get("name", code)

            assessment = {"code": code, "name": name}
            if pnl_pct < -5:
                assessment["action"] = "review_stop_loss"
                assessment["reason"] = f"浮亏{pnl_pct:.1f}%，需评估是否止损"
            elif pnl_pct > 8:
                assessment["action"] = "review_take_profit"
                assessment["reason"] = f"浮盈{pnl_pct:.1f}%，需评估是否止盈"
            else:
                assessment["action"] = "hold"
                assessment["reason"] = "继续持有"
            insights["holding_assessments"].append(assessment)

        return insights

    # ==================== 报告生成 ====================

    def _generate_report(
        self, date: str, stats: Dict, issues: List[Dict],
        llm_analysis: Dict, suggestions: List[Dict],
        system_health: Dict = None, buy_plan: List[Dict] = None,
    ) -> str:
        """生成 Markdown 复盘报告"""
        today = stats["today"]
        week = stats["week"]
        month = stats["month"]
        portfolio = stats["portfolio"]

        lines = [
            f"# 📊 LLM增强复盘报告 | {date}",
            "",
            "## 📈 盈亏概况",
            f"| 指标 | 今日 | 7日 | 30日 |",
            f"|------|------|-----|------|",
            f"| 盈亏 | {today['pnl']:+,.0f}元 | {week['pnl']:+,.0f}元 | {month['pnl']:+,.0f}元 |",
            f"| 胜率 | {today['win_rate']:.0f}% | {week['win_rate']:.0f}% | {month['win_rate']:.0f}% |",
            f"| 交易 | {today['buys']}买/{today['sells']}卖 | {week['trades']}笔 | {month['trades']}笔 |",
            "",
            f"**Sharpe Ratio (30日):** {month['sharpe']}  |  **平均持有:** {month['avg_hold_days']}天",
            f"**总资产:** {portfolio['total_value']:,.0f}元  |  **仓位:** {portfolio['position_pct']:.0f}%  |  **回撤:** {portfolio['max_drawdown']}",
            "",
        ]

        # LLM 分析
        if llm_analysis.get("llm_available"):
            assessment = llm_analysis.get("overall_assessment", "")
            if assessment:
                lines.extend(["## 🤖 AI分析", f"**总评:** {assessment}", ""])

            root_causes = llm_analysis.get("root_causes", [])
            if root_causes:
                lines.append("**根因分析:**")
                for rc in root_causes:
                    lines.append(f"- {rc}")
                lines.append("")

            effectiveness = llm_analysis.get("strategy_effectiveness", {})
            working = effectiveness.get("working_well", [])
            needs_fix = effectiveness.get("needs_improvement", [])
            if working or needs_fix:
                lines.append("**策略评估:**")
                for w in working:
                    lines.append(f"- ✅ {w}")
                for n in needs_fix:
                    lines.append(f"- ⚠️ {n}")
                lines.append("")

            actions = llm_analysis.get("action_items", [])
            if actions:
                lines.append("**明日行动:**")
                for a in actions:
                    lines.append(f"- 📋 {a}")
                lines.append("")

            warnings = llm_analysis.get("risk_warnings", [])
            if warnings:
                lines.append("**风险提示:**")
                for w in warnings:
                    lines.append(f"- ⚠️ {w}")
                lines.append("")
        else:
            lines.extend(["## ⚠️ LLM不可用", "本次复盘使用纯规则分析。", ""])

        # 问题
        if issues:
            lines.append("## ⚠️ 发现问题")
            for i in issues:
                icon = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(i["severity"], "⚪")
                lines.append(f"- {icon} [{i['severity']}] {i['description']}")
            lines.append("")

        # 参数建议
        if suggestions:
            lines.append("## 🔧 参数调整建议")
            lines.append("| 参数 | 建议值 | 理由 | 来源 |")
            lines.append("|------|--------|------|------|")
            for s in suggestions:
                lines.append(f"| {s['param']} | {s['suggested']} | {s.get('reason', '')} | {s.get('source', '')} |")
            lines.append("")

        # 系统健康
        if system_health:
            status_icon = {"healthy": "✅", "warning": "⚠️", "error": "🔴"}.get(system_health.get("status"), "❓")
            lines.append(f"## 🏥 系统健康 {status_icon}")
            for c in system_health.get("checks", []):
                icon = {"ok": "✅", "warning": "⚠️", "error": "🔴", "info": "ℹ️"}.get(c["status"], "")
                lines.append(f"- {icon} {c['check']}")
            lines.append("")

        # 买入计划
        if buy_plan:
            lines.append("## 🎯 次日买入计划")
            for p in buy_plan:
                lines.append(f"- **{p.get('name', '')}**({p.get('code', '')}): "
                             f"策略={p.get('strategy', '')} "
                             f"信心={p.get('confidence', 0):.0%} "
                             f"原因={'; '.join(p.get('reasons', []))}")
            lines.append("")

        return "\n".join(lines)

    # ==================== 系统健康检查 ====================

    def _check_system_health(self, data: Dict, date: str) -> Dict:
        """
        检查整个交易系统的运行健康状况。
        """
        checks = []

        # 1. 数据文件完整性
        required_files = [
            ("account.json", self.account_file),
            ("transactions.json", self.transactions_file),
            ("strategy_params.json", self.params_file),
            ("watchlist.json", BASE_DIR / "watchlist.json"),
        ]
        for name, path in required_files:
            if path.exists():
                try:
                    with open(path, 'r') as f:
                        json.load(f)
                    checks.append({"check": f"{name}可读", "status": "ok"})
                except Exception:
                    checks.append({"check": f"{name}损坏", "status": "error"})
            else:
                checks.append({"check": f"{name}缺失", "status": "warning"})

        # 2. 策略参数版本
        params = data.get("params", {})
        version = params.get("version", 0)
        last_update = params.get("last_updated", "")
        checks.append({
            "check": f"策略参数 v{version} (更新于{last_update[:10] if last_update else '未知'})",
            "status": "ok",
        })

        # 3. 日志检查
        today_log = BASE_DIR / "logs" / f"monitor_v2_{date}.log"
        if today_log.exists():
            size = today_log.stat().st_size
            if size > 10 * 1024 * 1024:  # >10MB
                checks.append({"check": f"日志文件过大({size//1024//1024}MB)", "status": "warning"})
            else:
                checks.append({"check": "监控日志正常", "status": "ok"})
        else:
            checks.append({"check": "今日无监控日志", "status": "warning"})

        # 4. 交易数据一致性
        account = data.get("account", {})
        total = account.get("total_value", 0)
        cash = account.get("current_cash", 0)
        holdings_mv = sum(
            float(h.get("market_value", 0))
            for h in account.get("holdings", [])
        )
        calculated = cash + holdings_mv
        if total > 0 and abs(calculated - total) / total > 0.01:
            checks.append({
                "check": f"资产不一致: total_value={total:.0f} vs calculated={calculated:.0f}",
                "status": "error",
            })
        else:
            checks.append({"check": "资产数据一致", "status": "ok"})

        # 5. 复盘连续性
        recent_reviews = data.get("recent_reviews", [])
        if len(recent_reviews) == 0:
            checks.append({"check": "首次复盘(无历史)", "status": "info"})
        elif len(recent_reviews) < 3:
            checks.append({"check": f"复盘记录{len(recent_reviews)}天(数据积累中)", "status": "info"})
        else:
            checks.append({"check": f"复盘连续{len(recent_reviews)}天", "status": "ok"})

        # 汇总
        errors = [c for c in checks if c["status"] == "error"]
        warnings = [c for c in checks if c["status"] == "warning"]
        status = "error" if errors else ("warning" if warnings else "healthy")

        return {
            "status": status,
            "checks": checks,
            "errors": len(errors),
            "warnings": len(warnings),
        }

    # ==================== 买入计划生成 ====================

    def _generate_buy_plan(self, data: Dict, llm_analysis: Dict) -> List[Dict]:
        """
        从候选股中确定次日买入目标。
        调用 trading_strategy.generate_buy_plan()
        """
        try:
            from trading_strategy import generate_buy_plan, gather_market_context
            account = data.get("account", {})
            context = gather_market_context(account)

            # 加载选股候选
            discovered_file = DATA_DIR / "discovered_stocks.json"
            candidates = []
            if discovered_file.exists():
                disc = _load_json(discovered_file)
                candidates = disc.get("top_picks", [])

            if not candidates:
                return []

            plans = generate_buy_plan(candidates, account, context, llm_analysis)
            return plans
        except Exception as e:
            print(f"  ⚠️ 买入计划生成失败: {e}")
            return []

    # ==================== 全面策略调整 ====================

    def _apply_full_strategy_adjust(self, result: Dict, data: Dict) -> Dict:
        """
        复盘后全面调整策略。
        调用 trading_strategy.full_review_adjust()
        """
        try:
            from trading_strategy import full_review_adjust

            account = data.get("account", {})
            discovered_file = DATA_DIR / "discovered_stocks.json"
            candidates = []
            if discovered_file.exists():
                disc = _load_json(discovered_file)
                candidates = disc.get("top_picks", [])

            adj = full_review_adjust(result, account, candidates)
            if adj.get("param_changes"):
                print(f"  📝 策略参数调整: {len(adj['param_changes'])}项")
                for c in adj["param_changes"]:
                    print(f"     {c['param']}: {c['old']} → {c['new']} ({c['reason']})")
            if adj.get("buy_plans"):
                print(f"  🎯 买入目标: {len(adj['buy_plans'])}只")
                for p in adj["buy_plans"]:
                    print(f"     {p['name']}({p['code']}) 策略={p['strategy']} 信心={p['confidence']}")
            return adj
        except Exception as e:
            print(f"  ⚠️ 策略调整失败: {e}")
            return {}


def main():
    """命令行入口"""
    import sys

    engine = LLMReviewEngine()

    date = None
    apply = False
    for arg in sys.argv[1:]:
        if arg == "--apply":
            apply = True
        elif not arg.startswith("-"):
            date = arg

    result = engine.run_review(date)

    # 打印报告
    print(result.get("report_md", ""))

    # 应用参数
    if apply and result.get("param_suggestions"):
        print("\n📝 应用参数建议...")
        applied = engine.apply_param_suggestions(result["param_suggestions"], dry_run=False)
        for c in applied["changes"]:
            print(f"  ✅ {c['param']}: {c['old']} → {c['new']} ({c['reason']})")


if __name__ == "__main__":
    main()
