# Skill: 每日复盘与参数调优

## 触发条件
- 每个交易日 16:00 后 AI 应主动执行
- 用户说 "复盘"、"review"、"看看今天表现"
- 用户说 "调参"、"优化参数"

## 执行步骤

### 1. 收集数据
读取以下文件：
- `account.json` — 当前账户状态
- `transactions.json` — 交易记录（筛选当日）
- `reviews/` — 最新复盘报告
- `strategy_params.json` — 当前参数
- `data/report_*.txt` — 当日报告

### 2. 分析当日表现
计算：
- 当日盈亏金额和百分比
- 胜率（盈利交易 / 总交易）
- 盈亏比（平均盈利 / 平均亏损）
- 最大单笔亏损
- 止损是否及时执行

### 3. 诊断问题
检查以下模式：
- **止损不及时**: 实际亏损 > stop_loss_pct → 收紧止损
- **过早止盈**: 卖出后继续上涨 > 5% → 放宽止盈
- **选股质量差**: 胜率 < 40% → 提高 min_score
- **仓位过重**: 持仓集中度 > 30% → 降低 max_position_pct
- **频繁交易**: 日交易 > 4笔 → 降低 max_daily_buys

### 4. 调整参数
如果诊断出问题，修改 `strategy_params.json`：
```python
# 每次必须：
params["version"] = round(params["version"] + 0.1, 1)
params["last_updated"] = datetime.now().isoformat()
params["notes"] = f"v{params['version']}: 具体变更描述"
```

### 5. 处理 hypothesis.json
检查是否有新的改进假设：
- 读取 hypothesis.json
- 对 priority=1 的假设，运行回测验证
- 回测通过则实施代码修改
- 回测失败则移至 dead_ends.json

### 6. 更新 Memory
在 `.github/copilot-memory.md` 中：
- 更新参数调整历史表
- 追加 AI 操作日志
- 记录新发现的规律到复盘洞察

## 参数调整边界（安全护栏）
| 参数 | 最小值 | 最大值 | 说明 |
|------|--------|--------|------|
| stop_loss_pct | -0.08 | -0.02 | 不能太宽也不能太紧 |
| take_profit_pct | 0.02 | 0.10 | 至少2%才有意义 |
| min_score | 50 | 85 | 太高会没有标的 |
| max_position_pct | 0.05 | 0.20 | 单只不超过20% |
| max_total_position | 0.3 | 0.8 | 至少留20%现金 |
| rebuy_cooldown_days | 5 | 30 | 冷却期 |

## 注意事项
- 不要在交易时间（9:30-15:00）修改参数
- 每次只调整 1-2 个参数，避免多变量混淆
- 调参后下一周观察效果，不要频繁改动
