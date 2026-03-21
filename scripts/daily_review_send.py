#!/usr/bin/env python3
"""Daily review runner (no LLM) + Feishu card sender.

- Generates daily review markdown via ReviewEngine (rule-based)
- Persists review json + appends to daily-log if present
- Sends a Feishu card (template=orange)

Usage:
  python3 daily_review_send.py [YYYY-MM-DD]
  python3 daily_review_send.py --latest

This is meant to be invoked by system crontab to avoid OpenClaw agent-cron LLM flakiness.
"""

import sys
import json
import os
import subprocess
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).parent.parent
FEISHU_CARD = Path(os.environ.get("FEISHU_CARD_SCRIPT", "/root/.openclaw/workspace/scripts/feishu_card.py"))

# local import
sys.path.insert(0, str(Path(__file__).parent))
from review_engine import ReviewEngine  # noqa: E402


def _latest_tx_date(transactions_file: Path) -> Optional[str]:
    if not transactions_file.exists():
        return None
    try:
        tx = json.loads(transactions_file.read_text(encoding="utf-8"))
        dates = []
        for t in tx:
            ts = str(t.get("timestamp", ""))
            if len(ts) >= 10:
                dates.append(ts[:10])
        return max(dates) if dates else None
    except Exception:
        return None


def _send_feishu_card(title: str, content_md: str, template: str = "orange", note: str = "小豆豆") -> bool:
    if not FEISHU_CARD.exists():
        print(f"⚠️ Feishu card script not found: {FEISHU_CARD}")
        return False

    cp = subprocess.run(
        [
            sys.executable,
            str(FEISHU_CARD),
            "--title",
            title,
            "--content",
            content_md,
            "--template",
            template,
            "--note",
            note,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if cp.returncode != 0:
        print(f"⚠️ Feishu card send failed rc={cp.returncode}: {cp.stderr.strip() or cp.stdout.strip()}")
        return False

    print(f"✅ Feishu card sent: {title}")
    return True


def main():
    engine = ReviewEngine()

    arg = sys.argv[1] if len(sys.argv) > 1 else None
    date = None

    if arg in ("--latest", "latest") or arg is None:
        date = _latest_tx_date(engine.transactions_file)
        if date is None:
            # fallback: today (engine handles empty trades)
            date = None
    else:
        date = arg

    report_md = engine.run_daily_review(date)

    final_date = date or report_md.split("|")[-1].strip() if "|" in report_md.splitlines()[0] else ""
    title = f"📊 每日复盘 | {final_date or (date or '今日')}"

    # Keep card content not too long
    content = report_md
    if len(content) > 12000:
        content = content[:11800] + "\n\n...(内容过长已截断)"

    _send_feishu_card(title=title, content_md=content, template="orange", note="小豆豆")


if __name__ == "__main__":
    main()
