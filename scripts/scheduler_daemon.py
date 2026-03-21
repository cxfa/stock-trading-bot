#!/usr/bin/env python3
"""
scheduler_daemon.py - 统一调度守护进程

替代分散的 cron 任务，自动管理交易系统的全生命周期：
- 盘前准备（09:15）
- 盘中交易周期（09:35-14:35, 每30分钟）
- 盘中实时监控（09:30-15:00, 每10秒）
- 收盘报告（15:05）
- 自动复盘（15:30）

用法:
    python3 scheduler_daemon.py install   # 安装为系统服务
    python3 scheduler_daemon.py start     # 前台启动（调试用）
    python3 scheduler_daemon.py start -d  # 后台启动
    python3 scheduler_daemon.py stop      # 停止
    python3 scheduler_daemon.py restart   # 重启
    python3 scheduler_daemon.py status    # 查看状态
    python3 scheduler_daemon.py next      # 查看下一个计划任务
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional, Dict, List, Tuple

# 项目路径
BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
DATA_DIR = BASE_DIR / "data"
LOG_DIR = Path(os.environ.get("LOG_DIR", "/tmp"))
PID_FILE = BASE_DIR / ".scheduler.pid"
STATE_FILE = BASE_DIR / "data" / "scheduler_state.json"

# 确保目录存在
DATA_DIR.mkdir(exist_ok=True)

# 日志配置
def setup_logging(level: str = "INFO") -> logging.Logger:
    log_file = LOG_DIR / "scheduler_daemon.log"
    logger = logging.getLogger("scheduler")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    fh = logging.FileHandler(str(log_file), encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger


# ─── 交易日历 ───

# 中国A股休市日（需要每年更新，这里预设2026年主要节假日）
# AI 应在每年初更新此列表
HOLIDAYS_2026 = {
    # 元旦
    date(2026, 1, 1), date(2026, 1, 2),
    # 春节
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
    date(2026, 2, 19), date(2026, 2, 20),
    # 清明
    date(2026, 4, 5), date(2026, 4, 6), date(2026, 4, 7),
    # 劳动节
    date(2026, 5, 1), date(2026, 5, 4), date(2026, 5, 5),
    # 端午
    date(2026, 5, 31), date(2026, 6, 1),
    # 中秋+国庆
    date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5),
    date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 8),
}

# 调休补班日（周末需要上班的日子）
WORKDAYS_2026 = {
    date(2026, 2, 14),  # 春节调休
    date(2026, 2, 15),  # 春节调休
    date(2026, 10, 10), # 国庆调休
}


def is_trading_day(d: Optional[date] = None) -> bool:
    """判断是否为交易日"""
    if d is None:
        d = date.today()

    # 调休补班日视为交易日
    if d in WORKDAYS_2026:
        return True

    # 周末不交易
    if d.weekday() >= 5:
        return False

    # 节假日不交易
    if d in HOLIDAYS_2026:
        return False

    return True


def next_trading_day(d: Optional[date] = None) -> date:
    """获取下一个交易日"""
    if d is None:
        d = date.today()
    d = d + timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def is_in_trading_hours(now: Optional[datetime] = None) -> bool:
    """是否在交易时间内 (9:30-11:30, 13:00-15:00)"""
    if now is None:
        now = datetime.now()
    t = now.time()
    morning = (t >= datetime.strptime("09:30", "%H:%M").time() and
               t <= datetime.strptime("11:30", "%H:%M").time())
    afternoon = (t >= datetime.strptime("13:00", "%H:%M").time() and
                 t <= datetime.strptime("15:00", "%H:%M").time())
    return morning or afternoon


# ─── 任务定义 ───

class Task:
    """调度任务"""
    def __init__(self, name: str, time_str: str, command: List[str],
                 description: str = "", repeat_interval: int = 0,
                 repeat_until: str = "", only_trading_hours: bool = False):
        self.name = name
        self.time_str = time_str  # HH:MM 格式
        self.command = command
        self.description = description
        self.repeat_interval = repeat_interval  # 秒，0=不重复
        self.repeat_until = repeat_until  # HH:MM 格式
        self.only_trading_hours = only_trading_hours

    @property
    def scheduled_time(self) -> datetime:
        today = date.today()
        h, m = map(int, self.time_str.split(":"))
        return datetime(today.year, today.month, today.day, h, m)


def get_daily_tasks() -> List[Task]:
    """获取每日任务列表（新架构：monitor_v2 处理盘中，scheduler 只管定时任务）"""
    py = sys.executable
    main_py = str(SCRIPTS_DIR / "main.py")
    llm_review_py = str(SCRIPTS_DIR / "llm_review_engine.py")

    return [
        # ─── 盘前 (09:15-09:25) ───
        Task("morning_sentiment", "09:20",
             [py, main_py, "sentiment"],
             "盘前情绪: 获取市场情绪"),

        # ─── 盘中: monitor_v2 自动处理（每1分钟循环 + 每30分钟策略快调） ───
        # 不再需要 cycle_xxxx 任务，monitor_v2.py 会自行:
        #   - 每1分钟获取行情、调用策略判断、执行交易
        #   - 每30分钟飞书快报 + 策略快速调整
        #   - 交易触发后立即飞书通知

        # ─── 收盘报告 (15:05) ───
        Task("close_report", "15:05",
             [py, main_py, "report"],
             "收盘报告"),

        # ─── 盘后流水线: 复盘 → 选股 (15:30-16:00) ───
        # 1) LLM 深度复盘（含: 系统健康检查 + 全面策略调整 + 买入计划生成）
        Task("llm_review", "15:30",
             [py, llm_review_py],
             "LLM增强复盘 + 策略全面调整 + 买入计划"),

        # 2) 旧版规则复盘（作为备份/对比）
        Task("rule_review", "15:35",
             [py, str(SCRIPTS_DIR / "daily_backup_review.py")],
             "规则复盘 + 数据备份"),

        # 3) 多日跟踪更新 + 选股（复盘完成后运行，读取复盘输出）
        Task("post_review_discover", "15:45",
             [py, main_py, "discover"],
             "盘后选股(读取复盘输出) + watchlist更新"),
    ]


# ─── Monitor Daemon 管理 ───

class MonitorManager:
    """管理 monitor_v2.py 子进程（新架构：1分钟循环 + 策略驱动）"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.process: Optional[subprocess.Popen] = None
        self.monitor_script = str(SCRIPTS_DIR / "monitor_v2.py")

    def start(self):
        """启动 monitor_v2"""
        if self.process and self.process.poll() is None:
            self.logger.info("Monitor v2 已在运行 (PID: %d)", self.process.pid)
            return

        self.logger.info("启动 monitor_v2 (1分钟循环+策略驱动)...")
        try:
            log_file = LOG_DIR / f"monitor_v2_{date.today().isoformat()}.log"
            with open(log_file, "a") as lf:
                self.process = subprocess.Popen(
                    [sys.executable, self.monitor_script],
                    cwd=str(BASE_DIR),
                    stdout=lf,
                    stderr=subprocess.STDOUT,
                    env={**os.environ, "PYTHONPATH": str(SCRIPTS_DIR)},
                )
            self.logger.info("Monitor v2 启动成功 (PID: %d)", self.process.pid)
        except Exception as e:
            self.logger.error("Monitor v2 启动失败: %s", e)

    def stop(self):
        """停止 monitor_v2"""
        if self.process and self.process.poll() is None:
            self.logger.info("停止 monitor_v2 (PID: %d)...", self.process.pid)
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.logger.info("Monitor v2 已停止")
        self.process = None

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None


# ─── 调度器 ───

class Scheduler:
    """主调度器"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.monitor = MonitorManager(logger)
        self.running = False
        self.executed_today: Dict[str, datetime] = {}
        self.last_repeat: Dict[str, datetime] = {}
        self.current_date: Optional[date] = None

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum, frame):
        self.logger.info("收到信号 %d，正在优雅退出...", signum)
        self.running = False

    def _save_state(self):
        """保存调度器状态"""
        state = {
            "last_run": datetime.now().isoformat(),
            "current_date": str(self.current_date),
            "executed_today": {k: v.isoformat() for k, v in self.executed_today.items()},
        }
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.warning("保存状态失败: %s", e)

    def _run_task(self, task: Task) -> bool:
        """执行单个任务"""
        self.logger.info("▶ 执行任务: %s (%s)", task.name, task.description)
        try:
            log_file = LOG_DIR / f"task_{task.name}_{date.today().isoformat()}.log"
            with open(log_file, "a") as lf:
                lf.write(f"\n{'='*60}\n")
                lf.write(f"任务: {task.name} | 时间: {datetime.now().isoformat()}\n")
                lf.write(f"{'='*60}\n")
                lf.flush()

                result = subprocess.run(
                    task.command,
                    cwd=str(BASE_DIR),
                    stdout=lf,
                    stderr=subprocess.STDOUT,
                    timeout=300,  # 5分钟超时
                    env={**os.environ, "PYTHONPATH": str(SCRIPTS_DIR)},
                )

            if result.returncode == 0:
                self.logger.info("✅ 任务完成: %s", task.name)
                return True
            else:
                self.logger.warning("⚠️ 任务异常退出: %s (code=%d)", task.name, result.returncode)
                return False

        except subprocess.TimeoutExpired:
            self.logger.error("⏰ 任务超时: %s", task.name)
            return False
        except Exception as e:
            self.logger.error("❌ 任务执行失败: %s - %s", task.name, e)
            return False

    def _check_and_run_tasks(self, now: datetime, tasks: List[Task]):
        """检查并执行到期任务"""
        for task in tasks:
            # 已执行的非重复任务跳过
            if task.name in self.executed_today and task.repeat_interval == 0:
                continue

            scheduled = task.scheduled_time
            # 任务窗口：计划时间 ~ 计划时间+5分钟
            if now < scheduled or now > scheduled + timedelta(minutes=5):
                # 非重复任务窗口外跳过
                if task.repeat_interval == 0:
                    continue

            # 重复任务处理
            if task.repeat_interval > 0:
                if task.repeat_until:
                    h, m = map(int, task.repeat_until.split(":"))
                    end_time = datetime(now.year, now.month, now.day, h, m)
                    if now > end_time:
                        continue

                if now < scheduled:
                    continue

                if task.only_trading_hours and not is_in_trading_hours(now):
                    continue

                last = self.last_repeat.get(task.name)
                if last and (now - last).total_seconds() < task.repeat_interval:
                    continue

                self._run_task(task)
                self.last_repeat[task.name] = now
                continue

            # 一次性任务
            if task.name not in self.executed_today:
                self._run_task(task)
                self.executed_today[task.name] = now

    def run(self):
        """主运行循环"""
        self.running = True
        self.logger.info("=" * 60)
        self.logger.info("🚀 Scheduler Daemon 启动")
        self.logger.info("   项目目录: %s", BASE_DIR)
        self.logger.info("   Python: %s", sys.executable)
        self.logger.info("   PID: %d", os.getpid())
        self.logger.info("=" * 60)

        # 写 PID 文件
        PID_FILE.write_text(str(os.getpid()))

        tasks = get_daily_tasks()

        while self.running:
            now = datetime.now()
            today = now.date()

            # 新的一天：重置状态
            if today != self.current_date:
                self.current_date = today
                self.executed_today.clear()
                self.last_repeat.clear()
                self.logger.info("📅 新的一天: %s (交易日: %s)",
                                 today.isoformat(),
                                 "是" if is_trading_day(today) else "否")

                if is_trading_day(today):
                    tasks = get_daily_tasks()  # 刷新任务列表
                else:
                    self.logger.info("⏸️  非交易日，休眠中...")

            # 非交易日：每分钟检查一次日期变化
            if not is_trading_day(today):
                time.sleep(60)
                continue

            # 交易日：在工作时间段内（09:00-17:00）密集检查
            t = now.time()
            if t < datetime.strptime("09:00", "%H:%M").time():
                # 早于09:00，等到09:00
                wake_time = datetime(today.year, today.month, today.day, 9, 0)
                sleep_secs = (wake_time - now).total_seconds()
                self.logger.info("⏰ 等待开盘前准备时间... (%.0f秒后)", sleep_secs)
                # 分段休眠以响应信号
                while self.running and datetime.now() < wake_time:
                    time.sleep(min(30, max(1, (wake_time - datetime.now()).total_seconds())))
                continue

            if t > datetime.strptime("17:00", "%H:%M").time():
                # 晚于17:00，等到明天
                self.logger.info("📴 今日任务完成，等待明天...")
                # 停止 monitor daemon
                self.monitor.stop()
                self._save_state()

                # 休眠到明天 08:55
                tomorrow = today + timedelta(days=1)
                wake_time = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 8, 55)
                while self.running and datetime.now() < wake_time:
                    time.sleep(60)
                continue

            # 在工作时间段内：管理 monitor daemon
            if is_in_trading_hours(now):
                if not self.monitor.is_running():
                    self.monitor.start()
            else:
                # 午间休市，停止 monitor
                if t > datetime.strptime("11:35", "%H:%M").time() and \
                   t < datetime.strptime("12:55", "%H:%M").time():
                    if self.monitor.is_running():
                        self.monitor.stop()

            # 检查并执行到期任务
            self._check_and_run_tasks(now, tasks)

            # 保存状态
            self._save_state()

            # 每30秒检查一次（足够精确调度分钟级任务）
            time.sleep(30)

        # 清理
        self.monitor.stop()
        if PID_FILE.exists():
            PID_FILE.unlink()
        self.logger.info("Scheduler Daemon 已停止")


# ─── CLI 命令 ───

def cmd_start(args):
    """启动 daemon"""
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        try:
            os.kill(pid, 0)  # 检查进程是否存在
            print(f"⚠️  Scheduler 已在运行 (PID: {pid})")
            return
        except OSError:
            PID_FILE.unlink()  # 清理过期 PID 文件

    if args.daemon:
        # 后台启动
        log_file = LOG_DIR / "scheduler_daemon.log"
        print(f"🚀 启动 Scheduler Daemon (后台模式)")
        print(f"   日志: {log_file}")
        with open(log_file, "a") as lf:
            proc = subprocess.Popen(
                [sys.executable, __file__, "start"],
                cwd=str(BASE_DIR),
                stdout=lf,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        print(f"   PID: {proc.pid}")
        print("✅ Daemon 已启动")
    else:
        # 前台启动
        logger = setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
        scheduler = Scheduler(logger)
        scheduler.run()


def cmd_stop(args):
    """停止 daemon"""
    if not PID_FILE.exists():
        print("⚠️  Scheduler 未在运行")
        return

    pid = int(PID_FILE.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"✅ 已发送停止信号 (PID: {pid})")
        # 等待进程退出
        for _ in range(10):
            try:
                os.kill(pid, 0)
                time.sleep(1)
            except OSError:
                break
        print("✅ Scheduler 已停止")
    except OSError:
        print("⚠️  进程不存在，清理 PID 文件")
    finally:
        if PID_FILE.exists():
            PID_FILE.unlink()


def cmd_restart(args):
    """重启"""
    cmd_stop(args)
    time.sleep(2)
    args.daemon = True
    cmd_start(args)


def cmd_status(args):
    """查看状态"""
    print("=" * 50)
    print("📊 Scheduler Daemon 状态")
    print("=" * 50)

    # PID
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        try:
            os.kill(pid, 0)
            print(f"🟢 运行中 (PID: {pid})")
        except OSError:
            print("🔴 已停止 (残留PID文件)")
    else:
        print("🔴 未运行")

    # 今日是否交易日
    today = date.today()
    print(f"\n📅 今日: {today.isoformat()} ({'交易日' if is_trading_day(today) else '非交易日'})")
    print(f"📅 下一交易日: {next_trading_day(today).isoformat()}")

    # 上次运行状态
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
            print(f"\n⏱️  上次运行: {state.get('last_run', 'N/A')}")
            executed = state.get("executed_today", {})
            if executed:
                print(f"📋 今日已执行 {len(executed)} 个任务:")
                for name, ts in sorted(executed.items(), key=lambda x: x[1]):
                    print(f"   ✅ {name} @ {ts[11:16]}")
        except Exception:
            pass

    print()


def cmd_next(args):
    """查看下一个计划任务"""
    now = datetime.now()
    today = now.date()

    if not is_trading_day(today):
        nd = next_trading_day(today)
        print(f"⏸️  今日非交易日，下一交易日: {nd.isoformat()}")
        return

    tasks = get_daily_tasks()
    upcoming = []
    for task in tasks:
        if task.repeat_interval > 0:
            continue  # 跳过重复任务
        if now < task.scheduled_time:
            upcoming.append(task)

    if not upcoming:
        print("📴 今日所有任务已过计划时间")
    else:
        upcoming.sort(key=lambda t: t.time_str)
        print("📋 今日待执行任务:")
        for t in upcoming:
            print(f"   ⏰ {t.time_str} | {t.name} - {t.description}")


def _detect_environment() -> str:
    """检测运行环境: openclaw / docker / macos / linux"""
    if Path("/root/.openclaw/openclaw-source").exists():
        return "openclaw"
    if Path("/.dockerenv").exists():
        return "docker"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def cmd_install(args):
    """安装 daemon（根据环境自动选择安装方式）"""
    print("🔧 安装 Scheduler Daemon")
    print()

    env = _detect_environment()
    print(f"📍 检测到环境: {env}")
    print()

    if env == "openclaw":
        # OpenClaw 容器: 使用 nohup + setsid（与 OpenClaw Gateway 相同模式）
        start_script = BASE_DIR / "scripts" / "start_scheduler.sh"
        start_content = f"""#!/bin/bash
# Stock Trading Bot Scheduler Daemon - OpenClaw Container Startup
# 由 scheduler_daemon.py install 自动生成

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PID_FILE="$PROJECT_DIR/.scheduler.pid"
LOG_FILE="/root/.openclaw/logs/scheduler_daemon.log"

# 检查是否已在运行
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "⚠️  Scheduler 已在运行 (PID: $PID)"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

# 确保日志目录存在
mkdir -p "$(dirname "$LOG_FILE")"

# 激活 Python 虚拟环境（如果存在）
if [ -f "/root/.venv/bin/activate" ]; then
    source /root/.venv/bin/activate
fi

# 使用 setsid + nohup 启动（与 OpenClaw Gateway Watchdog 相同模式）
echo "🚀 启动 Scheduler Daemon (OpenClaw 容器模式)"
setsid nohup python3 "$SCRIPT_DIR/scheduler_daemon.py" start \\
    >> "$LOG_FILE" 2>&1 &

echo "   PID: $!"
echo "   日志: $LOG_FILE"
echo "✅ Daemon 已启动"
"""
        start_script.write_text(start_content)
        os.chmod(str(start_script), 0o755)
        print(f"✅ 启动脚本已生成: {start_script}")
        print()
        print("启动 daemon:")
        print(f"  bash {start_script}")
        print()
        print("或者:")
        print(f"  python3 {Path(__file__).resolve()} start -d")
        print()
        print("查看日志:")
        print("  tail -f /root/.openclaw/logs/scheduler_daemon.log")
        print()
        print("💡 提示: 容器重启后需要重新启动 daemon")
        print("   可在 /root/.openclaw/cron/jobs.json 中添加自启动任务")

    elif env == "docker":
        # 普通 Docker 容器
        print("📦 Docker 容器环境")
        print()
        print("启动 daemon:")
        print(f"  python3 {Path(__file__).resolve()} start -d")
        print()
        print("💡 提示: 如需容器启动时自动运行，请在 Dockerfile 或 entrypoint 中添加:")
        print(f"  python3 {Path(__file__).resolve()} start -d")

    elif env == "macos":
        # macOS: launchd
        plist_name = "com.stock-trading-bot.scheduler"
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{plist_name}.plist"
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{plist_name}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>{Path(__file__).resolve()}</string>
        <string>start</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{BASE_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{LOG_DIR}/scheduler_daemon.log</string>
    <key>StandardErrorPath</key>
    <string>{LOG_DIR}/scheduler_daemon_err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>{SCRIPTS_DIR}</string>
    </dict>
</dict>
</plist>"""

        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist_content)
        print(f"✅ LaunchAgent 配置已写入: {plist_path}")
        print()
        print("启动 daemon:")
        print(f"  launchctl load {plist_path}")
        print()
        print("停止 daemon:")
        print(f"  launchctl unload {plist_path}")
        print()
        print("或者使用内置命令:")
        print(f"  python3 {Path(__file__).resolve()} start -d")

    elif system == "linux":
        # Linux: systemd
        service_name = "stock-trading-scheduler"
        service_path = Path.home() / ".config" / "systemd" / "user" / f"{service_name}.service"
        service_content = f"""[Unit]
Description=Stock Trading Bot Scheduler Daemon
After=network.target

[Service]
Type=simple
WorkingDirectory={BASE_DIR}
ExecStart={sys.executable} {Path(__file__).resolve()} start
Restart=on-failure
RestartSec=30
Environment=PYTHONPATH={SCRIPTS_DIR}
StandardOutput=append:{LOG_DIR}/scheduler_daemon.log
StandardError=append:{LOG_DIR}/scheduler_daemon_err.log

[Install]
WantedBy=default.target
"""
        service_path.parent.mkdir(parents=True, exist_ok=True)
        service_path.write_text(service_content)
        print(f"✅ systemd service 配置已写入: {service_path}")
        print()
        print("启动 daemon:")
        print(f"  systemctl --user daemon-reload")
        print(f"  systemctl --user enable {service_name}")
        print(f"  systemctl --user start {service_name}")
        print()
        print("查看状态:")
        print(f"  systemctl --user status {service_name}")
        print()
        print("或者使用内置命令:")
        print(f"  python3 {Path(__file__).resolve()} start -d")
    else:
        print(f"⚠️  不支持的系统: {system}")
        print("请手动配置守护进程，运行命令:")
        print(f"  python3 {Path(__file__).resolve()} start -d")


def main():
    parser = argparse.ArgumentParser(description="Stock Trading Bot Scheduler Daemon")
    subparsers = parser.add_subparsers(dest="command")

    # start
    start_parser = subparsers.add_parser("start", help="启动 daemon")
    start_parser.add_argument("-d", "--daemon", action="store_true", help="后台运行")

    # stop
    subparsers.add_parser("stop", help="停止 daemon")

    # restart
    subparsers.add_parser("restart", help="重启 daemon")

    # status
    subparsers.add_parser("status", help="查看状态")

    # next
    subparsers.add_parser("next", help="查看下一个计划任务")

    # install
    subparsers.add_parser("install", help="安装为系统服务")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "start": cmd_start,
        "stop": cmd_stop,
        "restart": cmd_restart,
        "status": cmd_status,
        "next": cmd_next,
        "install": cmd_install,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
