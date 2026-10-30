"""Best-effort diagnostics shared by domain and IO modules.

为什么不是直接 open/append：调试日志写在项目目录里（外置盘），单次
append+close 实测约 0.27 秒。DSH 实时事件一秒能来几十条，逐条写盘会把
主线程完全堵死——表现就是"整个系统卡住"。

所以这里改成：主线程只把行塞进内存队列，后台线程每 2 秒批量落盘一次。
调用方永远不碰磁盘。用 MIMI_DEBUG_LOG=0 可以完全关掉。
"""

from __future__ import annotations

import atexit
import os
import threading
import time

DEBUG_LOG_PATH = os.path.join(os.environ.get("TEMP", "."), "mimi-pet-debug.log")

_FLUSH_INTERVAL_S = 2.0
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_PENDING = 5000          # 积压上限，超过就丢弃，绝不拖慢主线程

_buffer: list[str] = []
_lock = threading.Lock()
_wake = threading.Event()
_thread: threading.Thread | None = None


def _enabled() -> bool:
    return os.environ.get("MIMI_DEBUG_LOG", "1") != "0"


def _flush_locked() -> None:
    """把缓冲写盘；调用方必须持有 _lock 之后取走数据。"""
    try:
        try:
            if os.path.getsize(DEBUG_LOG_PATH) > _MAX_FILE_BYTES:
                os.replace(DEBUG_LOG_PATH, DEBUG_LOG_PATH + ".1")
        except OSError:
            pass
        with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.writelines(_buffer)
    except OSError:
        pass
    _buffer.clear()


def _worker() -> None:
    while True:
        _wake.wait(_FLUSH_INTERVAL_S)
        _wake.clear()
        with _lock:
            if not _buffer:
                continue
            _flush_locked()


def _ensure_thread() -> None:
    global _thread
    if _thread is not None:
        return
    _thread = threading.Thread(target=_worker, name="mimi-debug-log", daemon=True)
    _thread.start()


def flush() -> None:
    """显式落盘（退出时用）。"""
    with _lock:
        if _buffer:
            _flush_locked()


def dbg(msg: str) -> None:
    """记录一行诊断：只进内存队列，磁盘由后台线程负责。"""
    if not _enabled():
        return
    _ensure_thread()
    with _lock:
        if len(_buffer) >= _MAX_PENDING:
            return
        _buffer.append(f"[{time.strftime('%H:%M:%S')}] {msg}\n")


atexit.register(flush)
