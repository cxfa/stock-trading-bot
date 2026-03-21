#!/bin/bash
set -euo pipefail
# 自动定位项目目录（脚本所在目录的父目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"
python3 main.py cycle 2>&1 | tee -a /tmp/trading-$(date +%Y%m%d).log
