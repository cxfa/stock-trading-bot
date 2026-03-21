# 🤖 A股模拟交易系统 (Stock Trading Bot)

AI 辅助的 A 股自动化模拟交易系统。系统自主运行，AI 负责环境搭建、复盘分析和策略调优。

## ✨ 核心特性

- **多策略交易**: 常规股票 + T+0 日内 + 可转债套利
- **多因子选股**: 动量、技术面、量价、资金流、情绪 五维评分
- **AI 辩论决策**: LLM 多空辩论生成买入信心分（0-100）
- **ATR 动态止损止盈**: 基于波动率自适应风控
- **10秒级盘中监控**: 实时止损止盈执行
- **自动复盘调参**: 5-Why 根因分析 + 策略参数自动优化
- **飞书通知**: 实时交易推送（可选）

## 🚀 快速开始

### 方式一：让 AI 帮你运行（推荐）

> 用 GitHub Copilot 或其他 AI 助手打开此项目，说：
> **"帮我把这个交易系统运行起来"**

AI 会自动读取 `.github/copilot-instructions.md`，然后：
1. 安装 Python 依赖
2. 初始化账户
3. 配置环境
4. 启动调度 daemon
5. 验证系统运行

### 方式二：手动设置

```bash
# 1. 克隆项目
git clone https://github.com/cintia09/stock-trading-bot.git
cd stock-trading-bot

# 2. 安装依赖
pip3 install -r requirements.txt

# 3. 配置环境
cp .env.example .env
# 编辑 .env（飞书和 LLM 配置是可选的）

# 4. 初始化账户
python3 scripts/setup_account.py

# 5. 验证
python3 main.py report

# 6. 启动调度 daemon
python3 scripts/scheduler_daemon.py install  # 安装为系统服务
python3 scripts/scheduler_daemon.py start -d # 后台启动
python3 scripts/scheduler_daemon.py status   # 查看状态
```

## 📐 系统架构

```
┌─────────────────────────────────────────────────────┐
│                  Scheduler Daemon                     │
│  (scheduler_daemon.py - 统一调度，替代 cron)          │
├──────────┬──────────┬──────────┬──────────┬──────────┤
│  09:15   │ 09:30-   │ 09:35-   │  15:05   │  15:30   │
│  盘前    │  15:00   │  14:35   │  收盘    │  复盘    │
│  准备    │  监控    │  交易    │  报告    │  调参    │
├──────────┼──────────┼──────────┼──────────┼──────────┤
│ discover │ monitor  │  cycle   │  report  │  review  │
│ sentiment│ _daemon  │ (30min)  │          │ deep_rev │
└──────┬───┴────┬─────┴────┬─────┴────┬─────┴────┬─────┘
       │        │          │          │          │
  ┌────▼────┐ ┌─▼──────┐ ┌▼────────┐ ┌▼──────┐ ┌▼──────────┐
  │Stock    │ │Monitor │ │Trading  │ │Report │ │Review     │
  │Discovery│ │Daemon  │ │Engine   │ │Gen    │ │Engine     │
  └────┬────┘ └─┬──────┘ └┬────────┘ └───────┘ └┬──────────┘
       │        │         │                      │
  ┌────▼────┐ ┌─▼──────┐ ┌▼────────┐        ┌───▼────────┐
  │Factor   │ │ATR止损  │ │Bull/Bear│        │Auto-Tune   │
  │Model    │ │止盈执行 │ │Debate   │        │Params      │
  └─────────┘ └────────┘ └─────────┘        └────────────┘
```

## 📁 文件结构

| 路径 | 说明 |
|------|------|
| `main.py` | 入口 shim → scripts/main.py |
| `scripts/` | **核心代码** (38 个 Python 模块) |
| `scripts/scheduler_daemon.py` | **统一调度 daemon** |
| `scripts/setup_account.py` | 账户初始化脚本 |
| `strategy_params.json` | 策略参数（AI 动态调整） |
| `strategy.md` | 策略说明文档 |
| `hypothesis.json` | 待验证的改进假设 |
| `dead_ends.json` | 已证伪的假设 |
| `data/` | 报告、发现的股票、盘中快照 |
| `reviews/` | 每日/深度复盘报告 |
| `models/` | ML 模型（LightGBM/FinRL） |
| `.github/copilot-instructions.md` | **AI 运维指令** |
| `.github/copilot-memory.md` | **AI 记忆文件** |
| `.github/skills/` | **AI 技能定义** |

## 🤖 AI 协作模式

本项目设计为 **"系统自主运行 + AI 运维"** 模式：

| 角色 | 职责 |
|------|------|
| **系统 (Daemon)** | 盘中监控、交易执行、数据采集、止损止盈 |
| **AI** | 环境搭建、复盘分析、参数调优、代码改进 |

### AI 文档说明

- **`.github/copilot-instructions.md`** — AI 的"工作手册"，描述系统架构和 AI 应做什么
- **`.github/copilot-memory.md`** — AI 的"记忆"，记录系统状态、操作历史、积累的洞察
- **`.github/skills/setup-and-start.md`** — 环境搭建技能
- **`.github/skills/daily-review.md`** — 复盘调参技能
- **`.github/skills/system-maintenance.md`** — 系统维护技能

## ⚙️ 配置

### 环境变量 (.env)

| 变量 | 必需 | 说明 |
|------|------|------|
| `FEISHU_APP_ID` | 否 | 飞书通知 |
| `FEISHU_APP_SECRET` | 否 | 飞书通知 |
| `LLM_PROVIDER` | 否 | LLM 提供商 (openclaw/openai/gemini) |
| `LLM_API_KEY` | 否 | LLM API Key |
| `LOG_LEVEL` | 否 | 日志级别 (默认 INFO) |
| `LOG_DIR` | 否 | 日志目录 (默认 /tmp) |

> 💡 不配置飞书和 LLM 也能运行，系统会跳过通知和 AI 辩论。

### Scheduler Daemon

```bash
# 管理命令
python3 scripts/scheduler_daemon.py status    # 查看状态
python3 scripts/scheduler_daemon.py next      # 下一个任务
python3 scripts/scheduler_daemon.py start -d  # 后台启动
python3 scripts/scheduler_daemon.py stop      # 停止
python3 scripts/scheduler_daemon.py restart   # 重启
python3 scripts/scheduler_daemon.py install   # 安装为系统服务
```

## 📊 策略概要

- **选股**: 涨幅榜 + 连涨股 + 机构持仓 + AI 基建主题
- **评分**: 多因子模型 (动量25% + 技术25% + 量价20% + 资金流15% + 情绪15%)
- **决策**: 评分 ≥ 65 分 → LLM 多空辩论 → 信心分 ≥ 阈值 → 买入
- **止损**: 硬止损 -3% + ATR 追踪止损
- **止盈**: +4% 减仓 50% → +8% 全部清仓 → ATR 追踪止盈
- **仓位**: 单只 ≤ 12%，总仓位 ≤ 50%

## 📜 License

MIT

