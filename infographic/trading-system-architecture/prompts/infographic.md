Create a professional infographic following these specifications:

## Image Specifications

- **Type**: Infographic
- **Layout**: circular-flow
- **Style**: technical-schematic
- **Aspect Ratio**: 16:9
- **Language**: Chinese (zh)

## Core Principles

- Follow the layout structure precisely for information architecture
- Apply style aesthetics consistently throughout
- Keep information concise, highlight keywords and core concepts
- Use ample whitespace for visual clarity
- Maintain clear visual hierarchy

## Layout Guidelines (circular-flow)

- Circular arrangement with central hub
- 6 module nodes evenly spaced around the circle
- Directional arrows showing data flow
- Center holds the core concept (Trading Strategy / 交易策略)
- Bottom arc shows daily timeline

## Style Guidelines (technical-schematic)

- Primary colors: Blues (#2563EB), teals, grays, white lines
- Background: Deep blue (#1E3A5F) with subtle grid pattern
- Accents: Amber highlights (#F59E0B) for key data, cyan (#06B6D4) callouts
- Geometric precision throughout
- Technical stencil or clean sans-serif typography
- Dimension lines and annotations
- Clean vector shapes with consistent stroke weights
- Blueprint/engineering aesthetic

## Text Requirements

- All text must be in Chinese
- Main title prominent and readable
- Key concepts visually emphasized with amber highlights
- Module labels clear and appropriately sized

---

Generate the infographic based on the content below:

# A股智能交易系统 v2.0 架构全景

## Central Hub (largest element, center)
🧠 交易策略 (Trading Strategy)
- 多因子决策引擎
- Signal输出: 买入/卖出/持有
- 快速调整: 每30min
- 全面调整: 每日复盘后

## 6 Nodes Around the Circle (clockwise from top-right)

### Node 1 (top-right): 👁️ 盘中监控
- 每1分钟循环
- 实时行情获取
- 30min飞书快报
→ Arrow TO center (获取Signal)
→ Arrow TO Node 2 (发送交易指令)

### Node 2 (right): 🤲 交易执行
- 纯买卖执行
- 原子文件锁
- T+1冻结
→ Arrow TO Node 3 (交易记录)

### Node 3 (bottom-right): 📊 交易记录/数据
- transactions.json
- account.json
- daily-log/
→ Arrow TO Node 4 (复盘读取)

### Node 4 (bottom-left): 🔍 LLM复盘
- 系统健康检查
- Sharpe/胜率/回撤
- LLM深度分析
- 生成 buy_plan.json
→ Arrow TO center (全面策略调整)
→ Arrow TO Node 5 (复盘输出)

### Node 5 (left): 🔭 选股系统
- 7源筛选
- 质量评分
- 多日跟踪 RSI/动量
→ Arrow TO Node 4 (候选股)

### Node 6 (top-left): ⏰ 调度器
- 统一调度daemon
- 交易日历
- 进程生命周期管理
→ Dashed arrows TO all nodes (调度管理)

## Outer Annotations (two domains)

### Left Side Domain (dashed border): 🤖 AI 离线辅助
- 环境搭建
- 复盘分析 (LLM)
- 参数调优
- 代码改进
Connected to: Node 4 (LLM复盘), Center (策略调整)

### Right Side Domain (solid border): ⚙️ 系统自主运行
- 实时监控
- 策略判断
- 交易执行
- 飞书通知
Connected to: Node 1, Node 2, Center

## Bottom Timeline Arc
09:20 盘前情绪 → 09:30-15:00 盘中监控(1min) → 15:05 收盘报告 → 15:30 LLM复盘 → 15:45 选股

## Key Statistics (amber highlights)
- ¥1,000,000 初始资金
- 6 大子系统
- 1min 监控频率
- 30min 快报/快调
- 7源 选股
- 57项 策略参数

## Data Flow Labels on Arrows
- Signal (策略→监控)
- 交易指令 (监控→执行)
- buy_plan.json (复盘→监控)
- review_output.json (复盘→选股)
- strategy_params.json (复盘→策略)
- discovered_stocks.json (选股→复盘)
- strategy_state.json (策略盘中状态)

Text labels (in Chinese):
- Title: "A股智能交易系统 v2.0 架构全景"
- Subtitle: "模块化 · 策略驱动 · AI辅助 · 自主运行"
- Center: "🧠 交易策略 — 多因子决策引擎"
- Nodes: "👁️ 盘中监控", "🤲 交易执行", "📊 数据层", "🔍 LLM复盘", "🔭 选股系统", "⏰ 调度器"
- Domains: "🤖 AI 离线辅助", "⚙️ 系统自主运行"
- Timeline: "09:20 情绪", "09:30 监控", "15:05 报告", "15:30 复盘", "15:45 选股"
