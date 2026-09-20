"""Qt-free SMTC helpers shared by the pet runtime and the agent CLI.

``media.py`` (the in-process controller) and ``media_cmd.py`` (the standalone
CLI the agent calls over PowerShell) must agree on WHICH session they are
looking at. When they disagree, the playback bar shows one player while the
transport buttons drive another — pausing looks broken to the user.

``media_cmd.py`` deliberately runs without Qt so it still works while the pet
window is closed, which means it cannot import ``media.py``. One shared
implementation here is the only way the two stay in agreement.

Nothing in this module imports Qt or winsdk; callers pass in an already
obtained session manager.
"""

from __future__ import annotations

import re

# Windows playback_status values (SMTC) → the names the rest of Mimi uses.
STATUS_TEXT = {3: "stopped", 4: "playing", 5: "paused"}

_TITLE_NOISE = re.compile(
    r"\s*[_\-|·]\s*(?:哔哩哔哩|bilibili|网易云音乐|QQ音乐|QQ音乐-千万正版音乐|"
    r"YouTube|Google Chrome|Microsoft Edge).*\Z",
    re.IGNORECASE,
)


def pretty_title(title: str) -> str:
    """Drop the '_哔哩哔哩_bilibili' style suffixes players put in titles.

    Display only: ``MediaTrack.title`` keeps the player's original string, so
    nothing here rewrites the source of truth.
    """
    return _TITLE_NOISE.sub("", title or "").strip()


async def pick_session(manager) -> tuple[object | None, object | None]:
    """Return ``(session, props | None)`` for the session that is really playing.

    Windows' own media-key target comes first: a real title means it is
    genuinely playing. Empty "zombie" sessions (a paused client with no queue)
    hold the current slot but must not hide what is actually audible, so any
    other session carrying a title wins over an untitled current one.

    When nothing carries a title the current session is still returned as a
    last resort, so transport control can at least be attempted on it.
    """
    current = manager.get_current_session()
    others = [
        session
        for session in manager.get_sessions()
        if current is None
        or session.source_app_user_model_id != current.source_app_user_model_id
    ]
    ordered = ([current] if current is not None else []) + others
    for session in ordered:
        try:
            props = await session.try_get_media_properties_async()
        except Exception:  # noqa: BLE001 - a player can exit mid-enumeration
            # A session that throws is dead. Skipping it keeps one dying
            # player from hiding the one that is still audible — and from
            # killing the caller's poll loop.
            continue
        if props and props.title:
            return session, props
    if ordered:
        return ordered[0], None
    return None, None
