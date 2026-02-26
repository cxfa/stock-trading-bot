#!/bin/bash
set -euo pipefail
cd /root/.openclaw/workspace/stock-trading
python3 scripts/intraday_monitor.py 2>&1 | tee -a /tmp/monitor-$(date +%Y%m%d).log
