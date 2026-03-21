#!/usr/bin/env python3
"""
file_lock.py - 文件级锁，防止 account.json 等共享文件并发写入

使用 fcntl.flock 实现（Unix），保证 monitor_daemon 和 trading_engine
不会同时写同一个 JSON 文件导致数据丢失。

用法:
    from file_lock import locked_read_json, locked_write_json, locked_update_json

    # 原子读
    account = locked_read_json(ACCOUNT_FILE, default={})

    # 原子写
    locked_write_json(ACCOUNT_FILE, account)

    # 原子读-改-写（推荐，锁在整个操作期间持有）
    def update_fn(account):
        account["current_cash"] -= amount
        return account
    locked_update_json(ACCOUNT_FILE, update_fn, default={})
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional


LOCK_TIMEOUT = 10  # 最多等 10 秒


def _lock_path(path: Path) -> Path:
    """锁文件路径"""
    return path.with_suffix(path.suffix + ".lock")


def locked_read_json(path: Path, default: Any = None) -> Any:
    """带锁的 JSON 读取"""
    lock_file = _lock_path(path)
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(lock_file, "w") as lf:
            _acquire_lock(lf)
            try:
                if not path.exists():
                    return default
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return default
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    except Exception:
        # 锁获取失败，降级到无锁读取
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return default


def locked_write_json(path: Path, data: Any) -> None:
    """带锁的 JSON 写入（原子：先写 .tmp 再 rename）"""
    lock_file = _lock_path(path)
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    with open(lock_file, "w") as lf:
        _acquire_lock(lf)
        try:
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp), str(path))
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def locked_update_json(path: Path, update_fn: Callable[[Any], Any],
                       default: Any = None) -> Any:
    """原子的 读-改-写 操作。锁在整个操作期间持有，避免 TOCTOU 竞态。

    Args:
        path: JSON 文件路径
        update_fn: 接收当前数据，返回修改后的数据
        default: 文件不存在时的默认值

    Returns:
        修改后的数据
    """
    lock_file = _lock_path(path)
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    with open(lock_file, "w") as lf:
        _acquire_lock(lf)
        try:
            # 读
            current = default
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        current = json.load(f)
                except (json.JSONDecodeError, IOError):
                    current = default

            # 改
            updated = update_fn(current)

            # 写（原子）
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(updated, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp), str(path))

            return updated
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _acquire_lock(lf, timeout: float = LOCK_TIMEOUT) -> None:
    """获取文件锁，带超时"""
    start = time.monotonic()
    while True:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() - start > timeout:
                raise TimeoutError(f"无法在 {timeout}s 内获取文件锁")
            time.sleep(0.05)
