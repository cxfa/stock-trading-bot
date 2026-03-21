#!/usr/bin/env python3
"""
多日股票跟踪器 — 跨交易日追踪候选股和持仓股的演变趋势

核心功能:
1. 跟踪候选股连续多日出现在选股列表中的频率（持续热度）
2. 跟踪持仓股的日度表现演变（支撑/压力位变化、量能趋势等）
3. 计算科学指标: 动量持续性、相对强度(RS)、成交量趋势、波动率变化
4. 输出 tracking_state.json 供选股系统和复盘系统读取

设计原则:
- 每个交易日收盘后由复盘系统调用 update()
- 数据保留最近30个交易日
- 候选股出现频率 >= 3天 → 标记为"持续关注"
- 候选股消失 >= 5天 → 自动移除
"""

import json
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
TRACKING_FILE = DATA_DIR / "tracking_state.json"
TRACKING_HISTORY_DIR = DATA_DIR / "tracking_history"
TRACKING_HISTORY_DIR.mkdir(parents=True, exist_ok=True)

MAX_TRACK_DAYS = 30  # 最多保留30天数据
MIN_CONSECUTIVE_DAYS = 3  # 至少连续出现3天才标记为"持续关注"
STALE_THRESHOLD_DAYS = 5  # 连续5天未出现则移除


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
    import os
    os.replace(tmp, path)


class StockTracker:
    """单只股票的多日跟踪状态"""

    def __init__(self, code: str, name: str = ""):
        self.code = code
        self.name = name
        self.daily_records: List[Dict] = []  # 每日快照
        self.first_seen: str = ""
        self.last_seen: str = ""
        self.appearance_count: int = 0  # 在选股列表中出现的次数
        self.consecutive_days: int = 0  # 连续出现天数
        self.tags: List[str] = []  # 标签: "持续关注", "新热点", "动量衰退" 等

    def to_dict(self) -> Dict:
        return {
            "code": self.code,
            "name": self.name,
            "daily_records": self.daily_records[-MAX_TRACK_DAYS:],
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "appearance_count": self.appearance_count,
            "consecutive_days": self.consecutive_days,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "StockTracker":
        t = cls(d.get("code", ""), d.get("name", ""))
        t.daily_records = d.get("daily_records", [])
        t.first_seen = d.get("first_seen", "")
        t.last_seen = d.get("last_seen", "")
        t.appearance_count = d.get("appearance_count", 0)
        t.consecutive_days = d.get("consecutive_days", 0)
        t.tags = d.get("tags", [])
        return t


class MultiDayTracker:
    """多日跟踪引擎"""

    def __init__(self):
        self.trackers: Dict[str, StockTracker] = {}
        self._load_state()

    def _load_state(self):
        state = _load_json(TRACKING_FILE, {"stocks": {}})
        for code, data in state.get("stocks", {}).items():
            self.trackers[code] = StockTracker.from_dict(data)

    def _save_state(self):
        state = {
            "last_updated": datetime.now().isoformat(),
            "stock_count": len(self.trackers),
            "stocks": {code: t.to_dict() for code, t in self.trackers.items()},
        }
        _save_json(TRACKING_FILE, state)
        # 保存每日快照
        today = datetime.now().strftime("%Y-%m-%d")
        _save_json(TRACKING_HISTORY_DIR / f"{today}.json", state)

    def update(
        self,
        discovered_stocks: List[Dict],
        holdings: List[Dict],
        kline_fetcher=None,
        date: str = None,
    ) -> Dict:
        """
        每日更新跟踪状态。

        Args:
            discovered_stocks: 今日选股结果 [{code, name, discovery_score, ...}]
            holdings: 当前持仓 [{code, name, cost_price, quantity, pnl_pct, ...}]
            kline_fetcher: 可选的K线获取函数 fetch_kline(code, period, limit)
            date: 日期，默认今天

        Returns:
            跟踪摘要 dict
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        discovered_codes = set()
        holding_codes = {str(h.get("code", "")).zfill(6) for h in holdings}

        # 1. 更新选股候选
        for stock in discovered_stocks:
            code = str(stock.get("code", "")).zfill(6)
            name = stock.get("name", code)
            discovered_codes.add(code)

            if code not in self.trackers:
                self.trackers[code] = StockTracker(code, name)
                self.trackers[code].first_seen = date

            tracker = self.trackers[code]
            tracker.name = name or tracker.name
            tracker.last_seen = date
            tracker.appearance_count += 1

            # 连续天数计算
            if tracker.daily_records:
                last_date = tracker.daily_records[-1].get("date", "")
                if last_date == date:
                    pass  # 同一天重复更新
                elif self._is_consecutive_trading_day(last_date, date):
                    tracker.consecutive_days += 1
                else:
                    tracker.consecutive_days = 1
            else:
                tracker.consecutive_days = 1

            # 记录日度数据
            record = {
                "date": date,
                "source": "discovery",
                "discovery_score": stock.get("discovery_score", 0),
                "change_pct": stock.get("change_pct", 0),
                "price": stock.get("price", 0),
            }

            # 获取K线计算科学指标
            if kline_fetcher:
                try:
                    klines = kline_fetcher(code, period="101", limit=30)
                    if klines and len(klines) >= 5:
                        record.update(self._calculate_indicators(klines))
                except Exception:
                    pass

            # 避免同日重复
            if not tracker.daily_records or tracker.daily_records[-1].get("date") != date:
                tracker.daily_records.append(record)

        # 2. 更新持仓跟踪
        for h in holdings:
            code = str(h.get("code", "")).zfill(6)
            name = h.get("name", code)

            if code not in self.trackers:
                self.trackers[code] = StockTracker(code, name)
                self.trackers[code].first_seen = date

            tracker = self.trackers[code]
            tracker.last_seen = date

            record = {
                "date": date,
                "source": "holding",
                "cost_price": h.get("cost_price", 0),
                "current_price": h.get("current_price", 0),
                "pnl_pct": h.get("pnl_pct", 0),
                "quantity": h.get("quantity", 0),
            }

            if kline_fetcher:
                try:
                    klines = kline_fetcher(code, period="101", limit=30)
                    if klines and len(klines) >= 5:
                        record.update(self._calculate_indicators(klines))
                except Exception:
                    pass

            if not tracker.daily_records or tracker.daily_records[-1].get("date") != date:
                tracker.daily_records.append(record)

        # 3. 更新标签
        self._update_tags(date, discovered_codes, holding_codes)

        # 4. 清理过期数据
        self._cleanup(date)

        # 5. 保存
        self._save_state()

        return self.get_summary()

    def _calculate_indicators(self, klines: List[Dict]) -> Dict:
        """从K线数据计算科学指标"""
        closes = [float(k.get("close", 0)) for k in klines if float(k.get("close", 0)) > 0]
        volumes = [float(k.get("volume", 0)) for k in klines]
        highs = [float(k.get("high", 0)) for k in klines]
        lows = [float(k.get("low", 0)) for k in klines]

        if len(closes) < 5:
            return {}

        indicators = {}

        # 1. MA5 / MA10 / MA20
        if len(closes) >= 5:
            indicators["ma5"] = round(sum(closes[-5:]) / 5, 3)
        if len(closes) >= 10:
            indicators["ma10"] = round(sum(closes[-10:]) / 10, 3)
        if len(closes) >= 20:
            indicators["ma20"] = round(sum(closes[-20:]) / 20, 3)

        # 2. RSI(14)
        if len(closes) >= 15:
            gains, losses = [], []
            for i in range(-14, 0):
                diff = closes[i] - closes[i - 1]
                gains.append(max(diff, 0))
                losses.append(max(-diff, 0))
            avg_gain = sum(gains) / 14
            avg_loss = sum(losses) / 14
            if avg_loss > 0:
                rs = avg_gain / avg_loss
                indicators["rsi14"] = round(100 - 100 / (1 + rs), 1)

        # 3. 动量(5日和10日收益率)
        if len(closes) >= 6:
            indicators["momentum_5d"] = round((closes[-1] / closes[-6] - 1) * 100, 2)
        if len(closes) >= 11:
            indicators["momentum_10d"] = round((closes[-1] / closes[-11] - 1) * 100, 2)

        # 4. 相对波动率 (5日ATR / 价格)
        if len(highs) >= 5 and len(lows) >= 5 and closes[-1] > 0:
            tr_list = []
            for i in range(-5, 0):
                tr = max(highs[i] - lows[i],
                         abs(highs[i] - closes[i - 1]),
                         abs(lows[i] - closes[i - 1]))
                tr_list.append(tr)
            atr5 = sum(tr_list) / len(tr_list)
            indicators["volatility_5d"] = round(atr5 / closes[-1] * 100, 2)

        # 5. 成交量趋势 (近5日均量 / 前10日均量)
        if len(volumes) >= 15:
            vol_recent = sum(volumes[-5:]) / 5
            vol_earlier = sum(volumes[-15:-5]) / 10
            if vol_earlier > 0:
                indicators["volume_trend"] = round(vol_recent / vol_earlier, 2)

        # 6. 价格相对强度 (相对于20日高低点的位置)
        if len(closes) >= 20:
            high_20 = max(closes[-20:])
            low_20 = min(closes[-20:])
            if high_20 > low_20:
                indicators["price_position_20d"] = round(
                    (closes[-1] - low_20) / (high_20 - low_20) * 100, 1
                )

        return indicators

    def _update_tags(self, date: str, discovered_codes: set, holding_codes: set):
        """根据跟踪数据更新标签"""
        for code, tracker in self.trackers.items():
            tags = []

            if code in holding_codes:
                tags.append("持仓中")

            if tracker.consecutive_days >= MIN_CONSECUTIVE_DAYS:
                tags.append("持续关注")
            elif tracker.consecutive_days >= 2:
                tags.append("连续出现")

            if tracker.appearance_count == 1 and code in discovered_codes:
                tags.append("新热点")

            # 动量分析
            recent = tracker.daily_records[-3:]
            if len(recent) >= 2:
                scores = [r.get("discovery_score", 0) for r in recent if r.get("source") == "discovery"]
                if len(scores) >= 2 and scores[-1] < scores[0] * 0.7:
                    tags.append("热度衰退")
                elif len(scores) >= 2 and scores[-1] > scores[0] * 1.3:
                    tags.append("热度上升")

            # 技术指标分析
            latest = tracker.daily_records[-1] if tracker.daily_records else {}
            rsi = latest.get("rsi14", 50)
            if rsi > 70:
                tags.append("RSI超买")
            elif rsi < 30:
                tags.append("RSI超卖")

            vol_trend = latest.get("volume_trend", 1.0)
            if vol_trend > 1.5:
                tags.append("放量")
            elif vol_trend < 0.6:
                tags.append("缩量")

            tracker.tags = tags

    def _cleanup(self, date: str):
        """清理过期跟踪数据"""
        to_remove = []
        for code, tracker in self.trackers.items():
            # 清理超过30天的记录
            if len(tracker.daily_records) > MAX_TRACK_DAYS:
                tracker.daily_records = tracker.daily_records[-MAX_TRACK_DAYS:]

            # 连续5天未出现且非持仓 → 移除
            if tracker.last_seen and "持仓中" not in tracker.tags:
                try:
                    last = datetime.strptime(tracker.last_seen, "%Y-%m-%d")
                    now = datetime.strptime(date, "%Y-%m-%d")
                    if (now - last).days >= STALE_THRESHOLD_DAYS:
                        to_remove.append(code)
                except ValueError:
                    pass

        for code in to_remove:
            del self.trackers[code]

    def _is_consecutive_trading_day(self, date1: str, date2: str) -> bool:
        """判断是否为连续交易日（简化: 差<=3天视为连续，跳过周末）"""
        try:
            d1 = datetime.strptime(date1, "%Y-%m-%d")
            d2 = datetime.strptime(date2, "%Y-%m-%d")
            diff = (d2 - d1).days
            return 1 <= diff <= 3
        except ValueError:
            return False

    def get_summary(self) -> Dict:
        """获取跟踪摘要"""
        sustained = []  # 持续关注
        new_hot = []  # 新热点
        declining = []  # 热度衰退
        holdings = []  # 持仓跟踪

        for code, tracker in self.trackers.items():
            info = {
                "code": code,
                "name": tracker.name,
                "consecutive_days": tracker.consecutive_days,
                "total_appearances": tracker.appearance_count,
                "tags": tracker.tags,
            }
            # 附加最新指标
            if tracker.daily_records:
                latest = tracker.daily_records[-1]
                info["latest_score"] = latest.get("discovery_score", 0)
                info["latest_price"] = latest.get("price", latest.get("current_price", 0))
                info["momentum_5d"] = latest.get("momentum_5d", 0)
                info["rsi14"] = latest.get("rsi14", 50)
                info["volume_trend"] = latest.get("volume_trend", 1.0)

            if "持续关注" in tracker.tags:
                sustained.append(info)
            if "新热点" in tracker.tags:
                new_hot.append(info)
            if "热度衰退" in tracker.tags:
                declining.append(info)
            if "持仓中" in tracker.tags:
                holdings.append(info)

        # 按连续天数排序
        sustained.sort(key=lambda x: x["consecutive_days"], reverse=True)

        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "total_tracked": len(self.trackers),
            "sustained_focus": sustained,
            "new_hot": new_hot,
            "declining": declining,
            "holdings_tracked": holdings,
        }

    def get_stock_history(self, code: str) -> Optional[Dict]:
        """获取单只股票的完整跟踪历史"""
        tracker = self.trackers.get(code)
        if not tracker:
            return None
        return tracker.to_dict()

    def get_discovery_boost(self) -> Dict[str, float]:
        """
        返回选股系统的加分字典。
        连续出现天数越多，加分越高。供 stock_discovery.py 使用。

        Returns:
            {code: bonus_score}
        """
        boosts = {}
        for code, tracker in self.trackers.items():
            if "持续关注" in tracker.tags:
                # 连续3天+5分，每多1天+2分，上限+15
                bonus = min(15, 5 + (tracker.consecutive_days - MIN_CONSECUTIVE_DAYS) * 2)
                boosts[code] = bonus
            elif "连续出现" in tracker.tags:
                boosts[code] = 3
            if "热度衰退" in tracker.tags:
                boosts[code] = boosts.get(code, 0) - 5  # 衰退扣分
            if "RSI超买" in tracker.tags:
                boosts[code] = boosts.get(code, 0) - 3  # 超买风险扣分
        return boosts

    def format_for_llm(self) -> str:
        """格式化跟踪数据为LLM可读的文本，供复盘系统使用"""
        summary = self.get_summary()
        lines = [f"## 多日跟踪报告 ({summary['date']})", f"跟踪股票数: {summary['total_tracked']}只", ""]

        if summary["sustained_focus"]:
            lines.append("### 🔥 持续关注 (连续出现≥3天)")
            for s in summary["sustained_focus"][:10]:
                lines.append(
                    f"- {s['name']}({s['code']}): 连续{s['consecutive_days']}天, "
                    f"总出现{s['total_appearances']}次, "
                    f"5日动量{s.get('momentum_5d', 0):+.1f}%, "
                    f"RSI={s.get('rsi14', 50):.0f}, "
                    f"量能{s.get('volume_trend', 1.0):.1f}x"
                )

        if summary["new_hot"]:
            lines.append("\n### ⚡ 今日新热点")
            for s in summary["new_hot"][:5]:
                lines.append(f"- {s['name']}({s['code']}): 首次出现, 标签={s['tags']}")

        if summary["declining"]:
            lines.append("\n### 📉 热度衰退")
            for s in summary["declining"][:5]:
                lines.append(f"- {s['name']}({s['code']}): 热度下降, 标签={s['tags']}")

        if summary["holdings_tracked"]:
            lines.append("\n### 📊 持仓跟踪")
            for s in summary["holdings_tracked"]:
                lines.append(
                    f"- {s['name']}({s['code']}): "
                    f"5日动量{s.get('momentum_5d', 0):+.1f}%, "
                    f"RSI={s.get('rsi14', 50):.0f}, "
                    f"量能{s.get('volume_trend', 1.0):.1f}x, "
                    f"标签={s['tags']}"
                )

        return "\n".join(lines)
