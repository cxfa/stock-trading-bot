# Copilot Instructions - 股票交易系统 AI 运维指南

## 🎯 项目概述

这是一个 **A股模拟交易系统**，初始资金 ¥1,000,000。系统通过技术分析、多因子模型、AI辩论（多空对决）等手段，自动进行股票发现、交易执行、风险管理和策略复盘。

**核心理念：系统本身自主运行，不需要 AI 实时介入。AI 的职责是：**
1. **环境搭建** — 安装依赖、配置环境、启动 daemon
2. **复盘分析** — 审阅交易结果、分析盈亏原因
3. **参数调优** — 根据复盘结果调整 strategy_params.json
4. **代码改进** — 修复 bug、实现 hypothesis.json 中的改进建议

---

## 🏗️ 系统架构

```
stock-trading-bot/
├── main.py                        # 入口（shim → scripts/main.py）
├── scripts/
│   ├── main.py                    # CLI入口：discover/report/sentiment
│   ├── trading_strategy.py        # ⭐ 交易策略（大脑）: 多因子决策+快调/全调
│   ├── trade_executor.py          # ⭐ 交易执行器（手）: 纯买卖执行，原子锁
│   ├── monitor_v2.py              # ⭐ 盘中监控v2（眼）: 1分钟循环+策略驱动
│   ├── llm_review_engine.py       # ⭐ LLM复盘引擎（脑后）: 全面复盘+策略调整+买入计划
│   ├── multi_day_tracker.py       # ⭐ 多日跟踪器: RSI/动量/成交量趋势
│   ├── scheduler_daemon.py        # 统一调度daemon（替代cron）
│   ├── stock_discovery.py         # 股票发现: 7源筛选+质量评分
│   ├── trading_engine.py          # [遗留] 旧交易引擎
│   ├── monitor_daemon.py          # [遗留] 旧监控（保留Feishu函数）
│   ├── factor_model.py            # 多因子评分模型
│   ├── bull_bear_debate.py        # LLM多空辩论决策
│   ├── technical_analysis.py      # 技术指标: MA/MACD/RSI/KDJ/布林/ATR
│   ├── fetch_stock_data.py        # 数据源: 新浪实时/东财K线/BaoStock
│   ├── news_sentiment.py          # 新闻情绪分析
│   ├── market_regime.py           # 市场状态检测（牛/熊/震荡）
│   ├── risk_manager.py            # 风险管理器
│   ├── file_lock.py               # 文件锁（fcntl）
│   └── setup_account.py           # 账户初始化脚本
├── strategy_params.json           # 策略参数（动态调整）
├── data/
│   ├── buy_plan.json              # 复盘生成的次日买入计划
│   ├── review_output.json         # 复盘输出（选股/策略读取）
│   ├── strategy_state.json        # 策略状态（盘中快调结果）
│   └── tracking_state.json        # 多日跟踪状态
├── account.json                   # 账户状态 -- gitignored
├── transactions.json              # 交易记录 -- gitignored
└── .github/
    ├── copilot-instructions.md    # 本文件（AI指令）
    ├── copilot-memory.md          # AI记忆
    └── skills/                    # AI技能定义
```

### 核心数据流

```
选股(stock_discovery) → 候选股池(discovered_stocks.json)
                          ↓
复盘(llm_review) → 系统健康检查
                 → 从候选确定买入目标 → buy_plan.json
                 → 全面策略调整 → strategy_params.json
                          ↓
盘中监控(monitor_v2) ← 每1分钟循环
  → 获取行情 → 调用策略(trading_strategy)判断 → 执行器(trade_executor)买卖
  → 每30分钟: 飞书快报 + 策略快速调整(assess_intraday)
  → 交易触发: 飞书立即通知
```

---

## 🤖 AI 首次任务：当用户说"帮我运行这个系统"

当新用户 clone 本项目后要求 AI 帮忙运行，AI 应按以下顺序自动执行：

### Step 0: 环境检测
```bash
# 检测运行环境
python3 scripts/scheduler_daemon.py install
# 此命令会自动检测: openclaw容器 / docker / macos / linux
```

**OpenClaw 容器环境**（检测到 `/root/.openclaw/openclaw-source` 存在）：
- Python venv 已在 `/root/.venv`，直接 `source /root/.venv/bin/activate`
- 飞书配置从 `/root/.openclaw/openclaw.json` 读取
- LLM 通过 OpenClaw Gateway 的 Copilot token 调用
- 日志写入 `/root/.openclaw/logs/`
- daemon 用 `setsid nohup` 启动（与 Gateway Watchdog 相同模式）

**非容器环境**（macOS/Linux）：
- 需要自行安装 Python 和依赖
- 使用 launchd (macOS) 或 systemd (Linux) 管理 daemon

### Step 1: 环境检测与安装
```bash
# 检测 Python 版本（需要 3.9+）
python3 --version

# 安装依赖（OpenClaw 容器中先激活 venv）
# source /root/.venv/bin/activate  # OpenClaw 容器
pip3 install -r requirements.txt

# 复制环境配置
cp .env.example .env
# 然后提示用户填写必要的配置项
```

### Step 2: 初始化账户
```bash
# 如果 account.json 不存在，从模板创建
python3 scripts/setup_account.py
```

### Step 3: 验证系统可运行
```bash
# 测试数据获取
python3 -c "from scripts.fetch_stock_data import fetch_market_overview; print(fetch_market_overview())"

# 测试报告生成
python3 main.py report
```

### Step 4: 启动调度 daemon
```bash
# 安装并启动 scheduler daemon
python3 scripts/scheduler_daemon.py install
python3 scripts/scheduler_daemon.py start
```

### Step 5: 确认运行状态
```bash
python3 scripts/scheduler_daemon.py status
```

---

## 📋 AI 日常职责

### 每个交易日盘后（15:30后）—— 复盘
1. 读取 `reviews/` 下最新复盘报告
2. 读取 `transactions.json` 当日交易
3. 读取 `account.json` 账户状态
4. 分析：
   - 今日胜率、盈亏比
   - 止损是否及时执行
   - 是否有未预期的大幅亏损
   - 策略参数是否需要调整
5. 如需调参，修改 `strategy_params.json`（递增 version，写 notes）
6. 如发现代码 bug 或改进点，写入 `hypothesis.json`

### 每周末 —— 深度复盘
1. 运行 `python3 scripts/deep_review_v2.py`
2. 审阅 `hypothesis.json` 中的待验证假设
3. 运行回测验证：`python3 scripts/backtest.py`
4. 对验证通过的假设，实施代码修改
5. 将失败假设移至 `dead_ends.json`

### 参数调整规则
- 止损执行不及时 → 收紧 `stop_loss_pct`
- 盈利过早卖出 → 放宽 `take_profit_pct`
- 胜率低于 40% → 提高 `min_score`
- 仓位过重 → 降低 `max_position_pct`
- 每次调参必须：递增 version，更新 last_updated，写清 notes

---

## ⚙️ 策略参数说明（strategy_params.json）

| 参数 | 当前值 | 含义 |
|------|--------|------|
| stop_loss_pct | -0.03 | 硬止损线（-3%） |
| take_profit_pct | 0.04 | 首次止盈（+4%减仓） |
| take_profit_full_pct | 0.08 | 全部止盈（+8%清仓） |
| take_profit_atr_multiplier | 2.0 | ATR止盈倍数 |
| trailing_stop_atr_multiplier | 1.5 | ATR追踪止损 |
| min_score | 65 | 最低买入评分 |
| max_position_pct | 0.12 | 单只最大仓位12% |
| max_total_position | 0.5 | 最大总仓位50% |
| rebuy_cooldown_days | 12 | 止损后冷却期 |
| max_daily_buys | 2 | 每日最多买入笔数 |
| debate_llm | openclaw/auto | LLM辩论配置（自动读取OpenClaw当前模型） |
| qlib_enabled | false | Qlib ML打分（见下方说明） |

### Qlib ML 打分模块（当前禁用）

系统包含一个可选的 Qlib (微软量化投资框架) + LightGBM ML 打分模块，与规则评分混合使用。

**当前状态**: `qlib_enabled: false` — 纯影子模式，不影响交易决策。

**相关文件**（保留但不启用）:
- `scripts/qlib_train.py` — 模型训练
- `scripts/qlib_scorer.py` — 预测打分
- `scripts/custom_alpha_handler.py` — 自定义 A股因子
- `scripts/compare_train.py` — 新旧模型对比
- `scripts/weekly_retrain.sh` — 每周重训练流水线

**启用条件**（依赖较重，非必需）:
1. 安装 Qlib: `pip install qlib lightgbm` (需要独立 venv `qlib-env/`)
2. 准备数据: 运行 BaoStock 数据采集 + `qlib dump_bin` 转换
3. 训练模型: `python3 scripts/qlib_train.py`
4. 设置 `qlib_enabled: true`, `qlib_weight: 0.4`（ML 占比 40%）

**建议**: 系统已有多因子评分 + LLM 辩论，ML 打分是锦上添花。新用户无需启用。

---

## 🔧 关键文件说明

### hypothesis.json — 改进假设追踪
格式：每个假设包含 5-Why 根因、建议的代码修改 patch、目标文件和函数。
AI 应优先处理 priority=1 的假设。

### dead_ends.json — 已证伪假设
记录尝试过但回测未通过的改进方案，避免重复尝试。

### strategy_params.json — 动态参数
系统运行时实时读取，AI 调整后立即生效。修改时必须保持 JSON 格式正确。

### account.json — 账户状态（不在 git 中）
包含 current_cash、holdings、cb_holdings。系统运行时持续更新。

---

## 🚨 注意事项

1. **交易时间**：周一至周五 9:30-11:30, 13:00-15:00（中国A股）
2. **T+1规则**：当天买入次日才能卖出
3. **不要在交易时间修改核心交易逻辑**，等盘后再改
4. **account.json 和 transactions.json 是系统运行时的状态文件**，不要随意修改
5. **飞书通知是可选的**，缺少飞书配置不影响系统运行
6. **LLM 辩论是可选的**，如果 LLM 不可用，系统会跳过辩论直接使用多因子评分
7. **所有数据源都有降级策略**：新浪 → 东方财富 → BaoStock

---

## 📊 Daemon 调度时间表

| 时间 | 任务 | 说明 |
|------|------|------|
| 09:15 | 盘前准备 | 新闻情绪、股票发现、watchlist更新 |
| 09:30-15:00 | 盘中监控 | 每10秒止损止盈检查（monitor_daemon） |
| 09:35, 10:05, 10:35, 11:05, 13:05, 13:35, 14:05, 14:35 | 交易周期 | 完整 cycle（发现+评分+辩论+交易） |
| 15:05 | 收盘报告 | 生成日报、推送通知 |
| 15:30 | 自动复盘 | review_engine 分析 + 自动调参 |
| 16:00 | 深度复盘 | deep_review 5-Why 分析（需要 AI 审阅） |

---

## 🔌 环境变量（.env）

```
# 必需
TRADING_BOT_HOME=/path/to/stock-trading-bot

# 可选 - 飞书通知
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_WEBHOOK_URL=

# 可选 - LLM 配置（用于多空辩论）
LLM_PROVIDER=openclaw          # openclaw / openai / gemini
LLM_MODEL=gemini-2.0-flash
LLM_API_KEY=

# 可选 - 日志
LOG_LEVEL=INFO
LOG_DIR=/tmp
```

---

## 🐳 OpenClaw 容器部署说明

本项目最初在 OpenClaw (ClawNook) 容器中开发，完全兼容该环境。

### 容器环境特点
- **Python**: `/root/.venv/bin/python3`（持久化虚拟环境）
- **Node.js 22**: 用于 OpenClaw Gateway
- **无 systemd**: 使用 `setsid nohup` 管理后台进程
- **持久化存储**: `/root/.openclaw/` (Docker Volume)
- **飞书配置**: 从 `/root/.openclaw/openclaw.json` 读取
- **LLM 调用**: 自动读取 OpenClaw 当前配置的模型和 provider（`openclaw.json` → `agents.defaults.model.primary`），无需额外配置 API key

### OpenClaw 中的安装步骤
```bash
# 1. 在容器 workspace 中 clone
cd /root/.openclaw/workspace
git clone <repo-url> stock-trading

# 2. 激活 venv 并安装依赖
source /root/.venv/bin/activate
cd stock-trading
pip install -r requirements.txt

# 3. 初始化
python3 scripts/setup_account.py

# 4. 安装并启动 daemon
python3 scripts/scheduler_daemon.py install  # 自动检测 OpenClaw 环境
bash scripts/start_scheduler.sh              # 用生成的启动脚本

# 5. 容器重启后自动恢复（可选）
# 在 start-services.sh 或通过 cron/jobs.json 添加自启动
```

### 容器重启后恢复
OpenClaw 容器没有 systemd，重启后 daemon 不会自动启动。两种方案：

1. **手动启动**:
```bash
bash /root/.openclaw/workspace/stock-trading/scripts/start_scheduler.sh
```

2. **添加到 OpenClaw 启动流程**: AI 可检测 daemon 是否运行，未运行则启动
