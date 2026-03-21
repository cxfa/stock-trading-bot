# A股智能交易系统 v2.0 架构全景

## Overview
展示A股模拟交易系统6大核心模块的循环运作机制、每日运行周期、以及AI离线辅助与系统自主运行的分工边界。

## Learning Objectives
The viewer will understand:
1. 系统6大核心模块（策略/监控/执行/复盘/选股/调度）的职责与关系
2. 每个交易日09:20-15:45的完整运行周期
3. AI（离线辅助）与系统（实时自主）的分工

---

## Section 1: 中心 — 交易策略（大脑）

**Key Concept**: 交易策略模块是整个系统的决策中心，所有买卖信号都从这里产生。

**Content**:
- 多因子决策：大盘指数 + 新闻情绪 + 技术指标 + 风控 + 市场状态
- 盘中快速调整：每30分钟，<1秒，无LLM
- 复盘全面调整：每日收盘后，深度分析，调用LLM
- 输出 Signal（买入/卖出/持有 + 信心度 + 原因）

**Visual Element**:
- Type: 中心圆形hub
- Subject: 大脑图标 🧠，标注"交易策略"
- Treatment: 蓝色高亮，最大尺寸

**Text Labels**:
- Headline: "🧠 交易策略"
- Subhead: "多因子决策引擎"
- Labels: "Signal输出", "快调30min", "全调/日"

---

## Section 2: 盘中监控（眼睛）

**Key Concept**: 每1分钟获取行情，调用策略判断，驱动交易执行。

**Content**:
- 每1分钟循环：获取实时行情 → 调用策略 → 执行交易
- 每30分钟：飞书快报 + 策略快速调整
- 交易触发：飞书立即通知

**Visual Element**:
- Type: 环形节点
- Subject: 眼睛图标 👁️
- Arrow: 从策略接收Signal，向执行器发送指令

**Text Labels**:
- Headline: "👁️ 盘中监控"
- Labels: "1min循环", "30min飞书", "实时行情"

---

## Section 3: 交易执行器（双手）

**Key Concept**: 纯执行层，只做买和卖，不做判断。

**Content**:
- 纯买卖执行，不做判断
- 原子文件锁（fcntl），防止TOCTOU竞态
- 佣金计算、T+1冻结、持仓均价

**Visual Element**:
- Type: 环形节点
- Subject: 双手图标 🤲
- Arrow: 从监控接收指令，输出交易记录

**Text Labels**:
- Headline: "🤲 交易执行"
- Labels: "原子锁", "T+1", "佣金计算"

---

## Section 4: LLM复盘引擎（反思）

**Key Concept**: 每日收盘后全面复盘，生成买入计划和策略调整。

**Content**:
- 系统健康检查
- Sharpe比率、胜率、最大回撤统计
- LLM深度分析
- 生成 buy_plan.json（次日买入目标）
- 全面策略调整 strategy_params.json

**Visual Element**:
- Type: 环形节点（较大，强调LLM）
- Subject: 放大镜/灯泡图标 🔍
- Arrow: 输出到策略和选股

**Text Labels**:
- Headline: "🔍 LLM复盘"
- Labels: "健康检查", "Sharpe/胜率", "买入计划", "参数调整"

---

## Section 5: 选股系统（侦察兵）

**Key Concept**: 7源筛选提供候选股，不参与交易策略。

**Content**:
- 7源筛选：涨幅榜/连涨/机构/板块/技术/AI/BaoStock
- 质量评分 + 复盘加成（+10/-15）
- 多日跟踪加成（RSI/动量/成交量）

**Visual Element**:
- Type: 环形节点
- Subject: 望远镜图标 🔭
- Arrow: 输出候选到复盘

**Text Labels**:
- Headline: "🔭 选股系统"
- Labels: "7源筛选", "质量评分", "多日跟踪"

---

## Section 6: 调度器（心脏）

**Key Concept**: 统一调度管理所有子系统的生命周期。

**Content**:
- 替代cron，统一调度
- 交易日历（节假日+周末自动跳过）
- 管理monitor_v2子进程

**Visual Element**:
- Type: 环形底部节点
- Subject: 时钟图标 ⏰
- Arrow: 连接所有节点（管理生命周期）

**Text Labels**:
- Headline: "⏰ 调度器"
- Labels: "交易日历", "进程管理", "生命周期"

---

## Section 7: AI与系统的分工（外圈标注）

**Key Concept**: AI离线辅助搭建环境和优化，系统实时自主运行。

**Content**:
- AI（离线）：环境搭建 → 复盘分析 → 参数调优 → 代码改进
- 系统（实时）：调度 → 监控 → 策略判断 → 执行交易 → 通知

**Visual Element**:
- Type: 外圈分域标注
- Left: AI域（虚线边框）
- Right: 系统域（实线边框）

**Text Labels**:
- Left: "🤖 AI 离线辅助"
- Right: "⚙️ 系统自主运行"

---

## Section 8: 每日时间轴（底部弧线）

**Key Concept**: 每个交易日从09:20到15:45的完整流程。

**Content**:
- 09:20 盘前情绪
- 09:30-15:00 盘中监控（1min循环）
- 15:05 收盘报告
- 15:30 LLM复盘
- 15:45 盘后选股

**Visual Element**:
- Type: 弧线时间轴
- Treatment: 底部半环，时间标记

**Text Labels**:
- "09:20", "09:30", "15:00", "15:05", "15:30", "15:45"

---

## Data Points (Verbatim)

### Statistics
- "初始资金 ¥1,000,000"
- "每1分钟循环"
- "每30分钟飞书快报"
- "7源筛选"
- "6大子系统"
- "39个Python文件"

### Key Terms
- **Signal**: 策略模块输出的买卖信号（方向+信心度+原因）
- **buy_plan.json**: 复盘生成的次日买入计划
- **strategy_params.json**: 57个策略参数
- **assess_intraday**: 盘中快速策略调整

---

## Design Instructions

### Style Preferences
- 蓝色工程风格（technical-schematic）
- 深蓝背景 + 白色/青色线条 + 琥珀色高亮
- 专业、精密、工程图感

### Layout Preferences
- circular-flow: 6个模块围绕中心策略旋转
- 外圈标注AI/System分域
- 底部弧线展示时间轴

### Other Requirements
- 中文文字
- 用于GitHub README展示
- 16:9 横版
