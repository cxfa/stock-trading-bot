#!/usr/bin/env python3
"""
setup_account.py - 初始化交易账户

用于新用户首次运行时创建 account.json。
如果 account.json 已存在，不会覆盖。

用法:
    python3 scripts/setup_account.py
    python3 scripts/setup_account.py --capital 500000
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ACCOUNT_FILE = BASE_DIR / "account.json"
TRANSACTIONS_FILE = BASE_DIR / "transactions.json"
WATCHLIST_FILE = BASE_DIR / "watchlist.json"
DATA_DIR = BASE_DIR / "data"
REVIEWS_DIR = BASE_DIR / "reviews"
DAILY_LOG_DIR = BASE_DIR / "daily-log"

# 从 .env 加载
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

DEFAULT_CAPITAL = float(os.environ.get("INITIAL_CAPITAL", "1000000"))


def setup_account(capital: float = DEFAULT_CAPITAL, force: bool = False):
    """初始化交易账户"""

    # 创建必要目录
    for d in [DATA_DIR, REVIEWS_DIR, DAILY_LOG_DIR,
              DATA_DIR / "intraday_snapshots"]:
        d.mkdir(parents=True, exist_ok=True)
        print(f"📁 目录就绪: {d.relative_to(BASE_DIR)}")

    # account.json
    if ACCOUNT_FILE.exists() and not force:
        print(f"⚠️  account.json 已存在，跳过（使用 --force 覆盖）")
        with open(ACCOUNT_FILE) as f:
            acct = json.load(f)
        print(f"   现金: ¥{acct.get('current_cash', 0):,.2f}")
        print(f"   持仓: {len(acct.get('holdings', []))} 只")
    else:
        account = {
            "initial_capital": capital,
            "current_cash": capital,
            "total_value": capital,
            "total_pnl": 0,
            "total_pnl_pct": 0,
            "holdings": [],
            "cb_holdings": [],
            "start_date": datetime.now().strftime("%Y-%m-%d"),
            "last_updated": datetime.now().isoformat(),
        }
        with open(ACCOUNT_FILE, "w") as f:
            json.dump(account, f, indent=2, ensure_ascii=False)
        print(f"✅ account.json 已创建 (初始资金: ¥{capital:,.2f})")

    # transactions.json
    if not TRANSACTIONS_FILE.exists():
        with open(TRANSACTIONS_FILE, "w") as f:
            json.dump([], f)
        print("✅ transactions.json 已创建")
    else:
        print("⚠️  transactions.json 已存在，跳过")

    # watchlist.json
    if not WATCHLIST_FILE.exists():
        watchlist = {
            "stocks": [],
            "last_updated": datetime.now().isoformat(),
        }
        with open(WATCHLIST_FILE, "w") as f:
            json.dump(watchlist, f, indent=2, ensure_ascii=False)
        print("✅ watchlist.json 已创建")
    else:
        print("⚠️  watchlist.json 已存在，跳过")

    # .env
    env_file = BASE_DIR / ".env"
    env_example = BASE_DIR / ".env.example"
    if not env_file.exists() and env_example.exists():
        import shutil
        shutil.copy(env_example, env_file)
        print("✅ .env 已从 .env.example 复制（请编辑配置）")
    elif not env_file.exists():
        print("⚠️  .env 不存在且无 .env.example")

    print()
    print("=" * 50)
    print("🎉 账户初始化完成！")
    print()
    print("下一步:")
    print("  1. 编辑 .env 配置文件（可选）")
    print("  2. 验证: python3 main.py report")
    print("  3. 启动: python3 scripts/scheduler_daemon.py start -d")
    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="初始化交易账户")
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL,
                        help=f"初始资金（默认: {DEFAULT_CAPITAL:,.0f}）")
    parser.add_argument("--force", action="store_true",
                        help="强制覆盖现有 account.json")
    args = parser.parse_args()

    setup_account(capital=args.capital, force=args.force)


if __name__ == "__main__":
    main()
