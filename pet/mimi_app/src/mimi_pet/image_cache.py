"""QPixmap cache with a decoded-byte budget.

Qt-only module; imported exclusively by the GUI adapter.

为什么需要预算：动作从 23 套涨到 87 套后，全部帧解码约为
    5200 帧 × 512×768×4B ≈ 7.8 GB
无上限缓存会在长时间运行后把进程拖到换页甚至被杀。这里按**解码后字节**
计费（不是磁盘文件大小，webp 压缩比很高，两者差一个数量级），超出预算
按最近最少使用淘汰。
"""

from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication, QPixmap

from .asset_prefetch import AssetPrefetcher

#: 默认预算 192 MB：够放当前动作的一小部分工作集，又不会挤压系统内存。
DEFAULT_BUDGET_BYTES = 192 * 1024 * 1024


def _pixmap_bytes(pixmap: QPixmap) -> int:
    """解码后的近似占用：宽 × 高 × 4 字节。"""
    try:
        return max(0, pixmap.width()) * max(0, pixmap.height()) * 4
    except Exception:  # noqa: BLE001 - 已销毁的 QPixmap 不应让缓存崩
        return 0


def _screen_dpr() -> float:
    """当前屏幕的设备像素比；没有 QApplication 时退回 1.0。"""
    try:
        screen = QGuiApplication.primaryScreen()
        return float(screen.devicePixelRatio()) if screen is not None else 1.0
    except Exception:  # noqa: BLE001
        return 1.0


class ImageCache:
    def __init__(
        self,
        budget_bytes: int = DEFAULT_BUDGET_BYTES,
        dpr: float | None = None,
        prefetcher: AssetPrefetcher | None = None,
    ) -> None:
        self.budget_bytes = max(16 * 1024 * 1024, int(budget_bytes))
        # 2 倍屏上必须按物理像素出图：否则 256×384 的位图会被合成器放大到
        # 512×768，细结构（比如呆毛的圈洞）在插值里直接糊死。
        override = os.environ.get("MIMI_DPR", "")
        if dpr is not None:
            self.dpr = float(dpr)
        elif override:
            self.dpr = max(1.0, float(override))
        else:
            self.dpr = _screen_dpr()
        # 素材在外置卷上冷读 41–252 ms/件（见 asset_prefetch 的实测），
        # 主线程直接 QPixmap(path) 会把整只桌宠冻住。预取线程先把字节读进
        # 内存，这里只做解码。没预取到就先记下来让后台补，本帧跳过不阻塞。
        self.prefetch = prefetcher if prefetcher is not None else AssetPrefetcher()
        self._pixmaps: "OrderedDict[str, QPixmap]" = OrderedDict()
        self._scaled: "OrderedDict[tuple[str, int, int], QPixmap]" = OrderedDict()
        self._generic: dict[Any, Any] = {}
        self._bytes = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._prefetch_misses = 0

    # ---------------------------------------------------------------- 通用

    def get_or_load(self, key: Any, loader: Callable[[], Any]) -> Any:
        if key not in self._generic:
            self._generic[key] = loader()
        return self._generic[key]

    # ---------------------------------------------------------------- 解码

    def _decode(self, path: Path) -> QPixmap:
        """Decode from prefetched bytes when possible.

        外置卷冷读是 41–252 ms/件（实测），所以主线程**优先**只解内存里的
        字节。没预取到才退回磁盘直读，同时立刻排队让后台补上——同一个动作
        第二轮播放就全是内存命中了。
        """
        blob = self.prefetch.get(path)
        if blob is None:
            self._prefetch_misses += 1
            self.prefetch.touch(path)
            pixmap = QPixmap(str(path))
        else:
            pixmap = QPixmap()
            # 素材里既有 webp（动作帧），也有 png（rig 底板、表情补丁、
            # 布景和玩具物件）。以前这里写死 "WEBP"：png 会被解成空图，
            # 然后抛出 FileNotFoundError。rig 底板是每一帧都要用的，
            # 它一旦解不出来，on_tick 里的 render() 就每帧抛异常，
            # 画面永远停在最后一次渲染成功的那一帧——正是"桌宠一动不动、
            # 右键菜单一开就像刷新了一下"的样子。
            # 不指定格式，让 Qt 按文件头自己判断（webp/png 都能解）。
            if not pixmap.loadFromData(blob):
                # 内存字节不可解时退回磁盘直读，别把整只桌宠冻住。
                pixmap = QPixmap(str(path))
        if pixmap.isNull():
            raise FileNotFoundError(f"cannot decode image: {path}")
        return pixmap

    def _evict_one(self) -> bool:
        """淘汰最久未用的一帧；连带清掉它的所有缩放版本。"""
        if not self._pixmaps:
            return False
        key, pixmap = self._pixmaps.popitem(last=False)
        self._bytes -= _pixmap_bytes(pixmap)
        for scaled_key in [k for k in self._scaled if k[0] == key]:
            scaled = self._scaled.pop(scaled_key, None)
            if scaled is not None:
                self._bytes -= _pixmap_bytes(scaled)
        self._evictions += 1
        return True

    def _trim(self) -> None:
        while self._bytes > self.budget_bytes and self._evict_one():
            pass

    def pixmap(self, path: Path) -> QPixmap:
        key = str(path.resolve())
        cached = self._pixmaps.get(key)
        if cached is not None:
            self._hits += 1
            self._pixmaps.move_to_end(key)
            return cached
        self._misses += 1
        pixmap = self._decode(path)
        self._pixmaps[key] = pixmap
        self._bytes += _pixmap_bytes(pixmap)
        self._trim()
        # 刚插入的帧理论上可能被立即淘汰（预算小于单帧时），返回局部引用。
        return self._pixmaps.get(key, pixmap)

    def scaled_pixmap(self, path: Path, size: QSize) -> QPixmap:
        """Decode once, scale once, then reuse the scaled result."""
        dpr = self.dpr
        target = QSize(max(1, int(round(size.width() * dpr))),
                       max(1, int(round(size.height() * dpr))))
        key = (str(path.resolve()), target.width(), target.height())
        cached = self._scaled.get(key)
        if cached is not None:
            self._hits += 1
            self._scaled.move_to_end(key)
            return cached
        source = self.pixmap(path)
        scaled = source.scaled(
            target,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if dpr != 1.0:
            scaled.setDevicePixelRatio(dpr)
        self._scaled[key] = scaled
        self._bytes += _pixmap_bytes(scaled)
        self._trim()
        return scaled

    # ---------------------------------------------------------------- 运维

    def clear(self) -> None:
        self._pixmaps.clear()
        self._scaled.clear()
        self._generic.clear()
        self._bytes = 0

    def stats(self) -> dict:
        return {
            "frames": len(self._pixmaps),
            "scaled": len(self._scaled),
            "bytes": self._bytes,
            "budget_bytes": self.budget_bytes,
            "hits": self._hits,
            "misses": self._misses,
            "evictions": self._evictions,
            "prefetch_misses": self._prefetch_misses,
            "prefetch": self.prefetch.stats(),
        }
