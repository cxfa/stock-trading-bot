# Skill: 环境搭建与系统启动

## 触发条件
用户说类似以下的话时触发此技能：
- "帮我运行这个系统"
- "初始化这个项目"
- "搭建环境"
- "启动交易系统"

## 执行步骤

### 1. 环境检测
```bash
python3 --version  # 需要 3.9+
pip3 --version
```
如果 Python 版本不足，提示用户安装。

### 2. 安装依赖
```bash
cd <项目根目录>
pip3 install -r requirements.txt
```

### 3. 配置环境
```bash
# 如果 .env 不存在
cp .env.example .env
```
然后提示用户：
- 飞书通知是可选的，不配也能运行
- LLM 配置是可选的，不配则跳过 AI 辩论
- 如果用户在 Copilot 环境中，LLM 可自动使用 Copilot 的模型

### 4. 初始化账户
```bash
python3 scripts/setup_account.py
```
如果 account.json 已存在，跳过此步。

### 5. 验证数据源
```bash
python3 -c "
import sys; sys.path.insert(0, 'scripts')
from fetch_stock_data import fetch_market_overview
data = fetch_market_overview()
print('数据源测试:', '✅ 成功' if data else '❌ 失败')
for k, v in data.items():
    print(f'  {v.get(\"name\", k)}: {v.get(\"price\", \"N/A\")}')
"
```

### 6. 启动 Scheduler Daemon
```bash
python3 scripts/scheduler_daemon.py install
python3 scripts/scheduler_daemon.py start
```

### 7. 验证运行
```bash
python3 scripts/scheduler_daemon.py status
```

### 8. 更新 AI Memory
在 `.github/copilot-memory.md` 中记录：
- daemon 启动时间
- 环境配置状态
- 首次运行验证结果

## 完成标志
- [ ] 依赖已安装
- [ ] .env 已配置
- [ ] account.json 存在且有效
- [ ] 数据源可访问
- [ ] daemon 已启动并运行
- [ ] AI Memory 已更新
