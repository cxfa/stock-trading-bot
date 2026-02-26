#!/usr/bin/env python3
"""Compatibility entrypoint.

The project historically used `python3 scripts/main.py ...`.
System crontab wrappers in this repo call `python3 main.py cycle`, so keep this thin shim.

All real logic (including Feishu card push) lives in `scripts/main.py`.
"""

from __future__ import annotations

import runpy
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "scripts" / "main.py"

if __name__ == "__main__":
    runpy.run_path(str(TARGET), run_name="__main__")
