"""One-time "what's new" announcement after Mimi updates.

The installed plugin version comes from :mod:`plugin_update`; the version the
user last opened is remembered in a small state file beside the companion
state, so the announcement shows exactly once per update. A user who skipped
releases sees every entry newer than the version they last opened.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .plugin_update import parse_version

SCHEMA_VERSION = 1

# Oldest first. Each entry is (version, bullet lines); the announcement lists
# every entry newer than the last opened version.
RELEASE_NOTES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "0.7.1",
        (
            "头顶气泡按文字自适应：短消息更窄，长消息换行变高，不再一律截断。",
            "右键菜单不再一移开就消失。",
        ),
    ),
    (
        "0.7.3",
        (
            "新增专注悬浮时钟：开始专注后桌宠旁边出现表盘，显示倒计时和状态。",
            "时钟可以拖动，拖走后固定在那里；双击回到桌宠旁边。",
            "专注时 Mimi 会偶尔说一句。",
        ),
    ),
    (
        "0.7.4",
        (
            "更新后第一次打开会显示这样一条更新公告，说明新版本改了什么。",
            "右键菜单新增「检查更新」，可以随时手动检查并直接看到结果。",
        ),
    ),
    (
        "0.7.5",
        (
            "内置的 Computer Use 升到 0.2.2：修正窗口定位，界面动作可以被正确取消。",
        ),
    ),
)


def state_path() -> Path:
    override = os.environ.get("MIMI_APP_STATE_PATH")
    if override:
        return Path(override).expanduser()
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) / "MimiDesktopPet" if appdata else Path.home() / ".mimi-desktop-pet"
    return base / "app_state.json"


def load_last_seen(path: Path | None = None) -> str:
    target = path or state_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        return ""
    return str(data.get("last_seen_version") or "").strip()


def save_last_seen(version: str, path: Path | None = None) -> None:
    # Losing the marker only costs one repeated announcement, so an unwritable
    # state file must never interrupt startup.
    target = path or state_path()
    payload = {"schema_version": SCHEMA_VERSION, "last_seen_version": version}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        pass


def notes_between(last_seen: str, current: str) -> list[str]:
    """Bullet lines for every release newer than ``last_seen``, oldest first."""
    if not last_seen or not current:
        return []
    last_key = parse_version(last_seen)[0]
    current_key = parse_version(current)[0]
    lines: list[str] = []
    for version, notes in RELEASE_NOTES:
        key = parse_version(version)[0]
        if last_key < key <= current_key:
            lines.extend(notes)
    return lines


def announcement_for(last_seen: str, current: str) -> str:
    """The announcement text, or "" when there is nothing to show."""
    if not current:
        return ""
    lines = notes_between(last_seen, current)
    header = f"Mimi 已更新到 v{current}"
    if not lines:
        return header + "。"
    return "\n".join([header + "：", *(f"·  {line}" for line in lines)])


def take_announcement(current: str, path: Path | None = None) -> str | None:
    """Announcement to show once, or None on a first run or an unchanged version."""
    if not current:
        return None
    target = path or state_path()
    last_seen = load_last_seen(target)
    text = announcement_for(last_seen, current) if last_seen and last_seen != current else None
    save_last_seen(current, target)
    return text


def announcement_bubble(current: str) -> str:
    """The short line the pet says when it has just updated."""
    return f"我更新好啦～ v{current} 的新功能在「最近通知」里。"
