# Skill: 系统维护与代码改进

## 触发条件
- AI 在复盘中发现代码 bug
- hypothesis.json 中有待实施的改进
- 用户要求修改策略逻辑
- 系统运行出错（daemon 日志有异常）

## 执行步骤

### 1. 问题识别
来源优先级：
1. `hypothesis.json` 中 priority=1 的假设
2. daemon 日志中的错误 (`LOG_DIR` 下的日志文件)
3. 复盘中发现的系统性问题
4. 用户反馈

### 2. 回测验证（修改前）
```bash
# 保存当前回测基线
python3 scripts/backtest.py > /tmp/backtest_before.txt

# 记录关键指标
# Sharpe ratio, max drawdown, win rate, profit factor
```

### 3. 实施修改
- 读取 hypothesis.json 中的 patch 建议
- 修改目标文件
- 确保不破坏现有功能

### 4. 回测验证（修改后）
```bash
python3 scripts/backtest.py > /tmp/backtest_after.txt
```
对比前后指标：
- Sharpe ratio 必须不下降
- Win rate 至少持平
- Max drawdown 不能恶化

### 5. 结果处理
**通过**：
- 提交代码修改
- 将假设从 hypothesis.json 移除
- 在 copilot-memory.md 记录改进

**失败**：
- 回滚代码修改
- 将假设移至 dead_ends.json，记录失败原因
- 在 copilot-memory.md 记录

### 6. Daemon 维护
```bash
# 检查 daemon 状态
python3 scripts/scheduler_daemon.py status

# 查看最近日志
tail -100 /tmp/scheduler_daemon.log

# 如需重启
python3 scripts/scheduler_daemon.py restart
```

## 代码修改规范
1. **不在交易时间修改核心交易逻辑**
2. 每次修改必须有对应的回测验证
3. 修改后运行 `python3 -m py_compile scripts/<file>.py` 确保语法正确
4. 重大修改先备份到 `code_backup/`

## 常见维护任务
- **数据源故障**: 检查 fetch_stock_data.py 的降级链
- **Daemon 崩溃**: 查看日志，修复后重启
- **磁盘空间**: 清理旧的 data/report_*.txt 和日志
- **参数回退**: 从 git 历史恢复 strategy_params.json
