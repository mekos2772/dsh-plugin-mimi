"""Music control CLI for the pet agent — called from its PowerShell tool.

Standalone by design: talks to Windows SMTC (the system media card) directly
and prints one-line results the agent can relay in chat. No Qt, no IPC with
the running pet, works even when the pet window is closed. Read + transport
control only: it never fetches or plays any content itself.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# The agent invokes this file by path (python media_cmd.py …) and never
# imports the package, so a relative import cannot work here.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from smtc_core import STATUS_TEXT, pick_session  # noqa: E402

USAGE = "用法: media_cmd.py <status|play|pause|toggle|next|prev>"

try:
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _SmtcManager,
    )

    HAVE_WINSDK = True
except ImportError:
    HAVE_WINSDK = False

_STATUS_CN = {"playing": "播放中", "paused": "已暂停", "stopped": "已停止"}


async def _status() -> str:
    mgr = await _SmtcManager.request_async()
    session, props = await pick_session(mgr)
    if session is None or props is None or not props.title:
        return "当前没有在播放音乐。"
    status = STATUS_TEXT.get(int(session.get_playback_info().playback_status), "")
    suffix = f"（{_STATUS_CN.get(status, status)}）" if status else ""
    return f"♪ {props.title} - {props.artist or '未知歌手'}{suffix}"


async def _send(action: str) -> str:
    mgr = await _SmtcManager.request_async()
    session, props = await pick_session(mgr)
    if session is None:
        return "没有找到可控制的播放器。"
    ops = {
        "play": session.try_play_async,
        "pause": session.try_pause_async,
        "toggle": session.try_toggle_play_pause_async,
        "next": session.try_skip_next_async,
        "prev": session.try_skip_previous_async,
    }
    ok = bool(await ops[action]())
    if not ok:
        return "没有正在播放的音乐。让用户在任意播放器里放点什么（Apple Music、浏览器都行），我就能控制了。"
    verb = {"play": "已继续播放", "pause": "已暂停", "toggle": "已切换播放状态",
            "next": "已切到下一首", "prev": "已切到上一首"}[action]
    if props and props.title:
        return f"{verb}：{props.title} - {props.artist or '未知歌手'}"
    return verb + "。"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(USAGE)
        return 2
    command = argv[1].lower()
    if command == "status":
        if not HAVE_WINSDK:
            print("音乐控制不可用：缺少 winsdk 依赖。")
            return 1
        print(asyncio.run(_status()))
        return 0
    if command in ("play", "pause", "toggle", "next", "prev"):
        if not HAVE_WINSDK:
            print("音乐控制不可用：缺少 winsdk 依赖。")
            return 1
        print(asyncio.run(_send(command)))
        return 0
    print(USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
