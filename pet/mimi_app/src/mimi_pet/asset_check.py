"""Startup asset check: is every file the renderer will decode actually usable?

``ActionLibrary`` and ``load_rig_model`` only verify that their files *exist*,
so a 0-byte or truncated image passes every loader and instead fails the first
time the renderer asks for it — by which point the pet is already running. On
the full-frame path that is one dropped frame; on the rig path the failure
lands inside an active QPainter transaction, which is how one broken image used
to take the whole process down. Checking up front turns both into a single line
naming the file.

Qt-only module: it decodes through QPixmap, so it sits beside the GUI adapter
and must never be imported by the domain layer.

Decoding is not free — measured at roughly 4 s for this project's 1103 action
frames — so callers are expected to pass ``decode=False`` for the wide sweep
and decode the small set of every-frame assets immediately.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PySide6.QtGui import QPixmap


def find_broken_assets(
    paths: Iterable[Path], *, decode: bool = True
) -> list[tuple[Path, str]]:
    """Report assets that are missing, not a plain file, empty, or undecodable.

    ``decode=False`` limits the sweep to stat calls, which is the cheap way to
    cover every referenced frame. Returns one ``(path, reason)`` pair per
    problem, in the order given; an empty list means everything checked out.
    """
    problems: list[tuple[Path, str]] = []
    for path in paths:
        try:
            size = path.stat().st_size
        except OSError as exc:
            problems.append((path, f"无法访问（{type(exc).__name__}）"))
            continue
        if not path.is_file():
            problems.append((path, "不是普通文件"))
            continue
        if size == 0:
            problems.append((path, "文件为 0 字节"))
            continue
        if decode and QPixmap(str(path)).isNull():
            problems.append((path, "无法解码（文件已损坏）"))
    return problems
