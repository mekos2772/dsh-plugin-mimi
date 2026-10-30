"""背景预热素材字节，保证主线程永远不碰冷盘。

为什么需要这个模块（实测数据，2026-10-29）：

    素材放在外置 APFS 卷 `/Volumes/isci肥牛`。单个 17 KB 的 webp：
        冷读 41–252 ms（文件系统层，与 Qt 无关，Python 的 read_bytes 同样慢）
        解码   3 ms
        热读   0.02 ms
    一个 62 帧的动作要 62 次冷读 -> 2.5–15 秒。主线程按 16 ms 节拍渲染，
    于是整只桌宠冻住，画面停在上一帧（看上去像"动作做完了卡在不该停的姿势"）。

做法：一个低优先级后台线程把文件**原始字节**读进内存（不碰 Qt 对象，
所以可以安全地跑在非 GUI 线程），渲染线程只从内存里取。

两级优先级：
    * 高优先级（priority=0）：正在播的动作帧、鼠标悬停时下一帧要用到的帧。
    * 低优先级（priority=9）：整库扫描预热，一次一件，慢慢铺完。

字节账用**磁盘字节**计（webp 很小，全部 5621 帧约 95 MB），预算默认 160 MB，
够整库常驻；超预算按 LRU 淘汰。
"""

from __future__ import annotations

import os
import queue
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Iterable

#: 整库字节预算。5621 帧 webp 合计约 95 MB，160 MB 能把整库常驻还留余量。
DEFAULT_BUDGET_BYTES = 160 * 1024 * 1024

#: 并发读线程数。实测外置 APFS 卷单线程 74 ms/件、4 线程 12 ms/件，
#: 瓶颈是每文件的延迟而不是带宽，所以并发几乎线性收益。8 线程足够把
#: 一个 62 帧动作的首次预热压到几十毫秒量级。
DEFAULT_WORKERS = 8

#: 低优先级扫描每读一件就歇一下，别把盘喂满。
SWEEP_PAUSE_S = 0.004

#: 高优先级（正在播的动作）不留停顿，下一帧就要用。
URGENT_PAUSE_S = 0.0

#: 关掉预热的开关（排查用）。
DISABLE_ENV = "MIMI_PREFETCH"


def _key(path: Path | str) -> str:
    return str(path)


class AssetPrefetcher:
    """Threaded byte cache. GUI-thread safe for get/request/sweep."""

    def __init__(
        self,
        budget_bytes: int = DEFAULT_BUDGET_BYTES,
        *,
        enabled: bool = True,
        workers: int = DEFAULT_WORKERS,
    ) -> None:
        self.budget_bytes = max(8 * 1024 * 1024, int(budget_bytes))
        self.workers = max(1, int(workers))
        self._lock = threading.Lock()
        self._cache: "OrderedDict[str, bytes]" = OrderedDict()
        self._bytes = 0
        self._queued: set[str] = set()
        self._queue: "queue.PriorityQueue[tuple[int, int, str]]" = queue.PriorityQueue()
        self._seq = 0
        self._stop = threading.Event()
        self._started = False
        self._threads: list[threading.Thread] = []
        self._reads = 0
        self._read_s = 0.0
        self._hits = 0
        self._misses = 0
        self._swept = 0
        self._failed = 0
        self._inflight = 0
        self._idle = threading.Condition(self._lock)
        self.enabled = enabled and os.environ.get(DISABLE_ENV, "1") != "0"

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        if self._started or not self.enabled:
            return
        self._started = True
        for index in range(self.workers):
            thread = threading.Thread(
                target=self._run, name=f"mimi-prefetch-{index}", daemon=True
            )
            thread.start()
            self._threads.append(thread)

    def stop(self, timeout_s: float = 1.0) -> None:
        self._stop.set()
        for thread in self._threads:
            if thread.is_alive():
                thread.join(timeout=timeout_s)
        self._threads.clear()

    def wait_idle(self, timeout_s: float = 30.0) -> bool:
        """Block until the queue is drained (tests and the startup sweep)."""
        deadline = time.monotonic() + timeout_s
        with self._idle:
            while self._queued and time.monotonic() < deadline:
                self._idle.wait(min(0.05, max(0.0, deadline - time.monotonic())))
            return not self._queued

    # ------------------------------------------------------------ 主线程 API

    def get(self, path: Path | str) -> bytes | None:
        """Return cached bytes, or None. Never touches the filesystem."""
        if not self.enabled:
            return None
        key = _key(path)
        with self._lock:
            blob = self._cache.get(key)
            if blob is None:
                self._misses += 1
                return None
            self._cache.move_to_end(key)
            self._hits += 1
            return blob

    def request(self, paths: Iterable[Path | str], priority: int = 0) -> int:
        """Queue paths for reading. Deduplicated against cache and pending work."""
        if not self.enabled:
            return 0
        added = 0
        with self._lock:
            for raw in paths:
                key = _key(raw)
                if key in self._cache or key in self._queued:
                    continue
                self._queued.add(key)
                self._seq += 1
                self._queue.put((priority, self._seq, key))
                added += 1
        return added

    def sweep(self, paths: Iterable[Path | str]) -> int:
        """Low-priority full-library warm-up: the whole set, one item at a time."""
        return self.request(paths, priority=9)

    def touch(self, path: Path | str) -> None:
        """Record a miss for stats and ask the worker to read it next."""
        self.request([path], priority=0)

    def stats(self) -> dict:
        with self._lock:
            return {
                "enabled": self.enabled,
                "cached": len(self._cache),
                "bytes": self._bytes,
                "budget_bytes": self.budget_bytes,
                "pending": len(self._queued),
                "hits": self._hits,
                "misses": self._misses,
                "reads": self._reads,
                "read_ms_avg": round(1000.0 * self._read_s / self._reads, 2) if self._reads else 0.0,
                "swept": self._swept,
                "failed": self._failed,
            }

    # ------------------------------------------------------------ 工作线程

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                priority, _seq, key = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                blob = self._read(key)
                if blob is not None:
                    self._insert(key, blob)
                if priority >= 9:
                    with self._lock:
                        self._swept += 1
            finally:
                with self._idle:
                    self._queued.discard(key)
                    self._idle.notify_all()
            if priority >= 9 and SWEEP_PAUSE_S:
                self._stop.wait(SWEEP_PAUSE_S)
            elif priority < 9 and URGENT_PAUSE_S:
                self._stop.wait(URGENT_PAUSE_S)

    def _read(self, key: str) -> bytes | None:
        if self._stop.is_set():
            return None
        started = time.perf_counter()
        try:
            blob = Path(key).read_bytes()
        except OSError:
            with self._lock:
                self._failed += 1
            return None
        finally:
            elapsed = time.perf_counter() - started
            with self._lock:
                self._reads += 1
                self._read_s += elapsed
        return blob if blob else None

    def _insert(self, key: str, blob: bytes) -> None:
        size = len(blob)
        with self._lock:
            if key in self._cache:
                return
            self._cache[key] = blob
            self._bytes += size
            while self._bytes > self.budget_bytes and len(self._cache) > 1:
                _, evicted = self._cache.popitem(last=False)
                self._bytes -= len(evicted)
