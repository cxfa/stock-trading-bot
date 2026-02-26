#!/bin/bash
set -euo pipefail
cd /root/.openclaw/workspace/stock-trading
python3 main.py cycle 2>&1 | tee -a /tmp/trading-$(date +%Y%m%d).log
