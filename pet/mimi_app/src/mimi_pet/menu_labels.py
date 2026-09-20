"""Shared label shaping for the pet's right-click menus.

The context menu sizes itself to its widest row, so a single unbounded
runtime string — a media session title, a focus label, a DSH error — used to
stretch the popup across the whole screen and make the pet unreadable while
it was open.

Every row that carries runtime text is added through this module instead of
``menu.addAction``:

* the label is capped to ``LIMIT`` characters with an ellipsis, and
* the full text is kept as the row's tooltip, so nothing is lost.

``MAX_WIDTH`` is the second guard: it is a hard ceiling on the popup, so a
row that still slips through (a submenu, a model label) cannot widen the
menu either. Call :func:`cap_width` on every menu and submenu that is shown.

Qt-free on purpose: ``media.py`` and ``companion_ui.py`` build their menu
sections without importing the window module.
"""

from __future__ import annotations

#: Characters kept in a visible label. 28 is the length of the longest
#: label that must stay readable in one line ("DeepSeek · deepseek-reasoner");
#: 28 CJK glyphs at the menu's 12px font is ~336px, still narrow enough to
#: sit beside the pet. Anything longer is cut, with the full text in the
#: tooltip.
LIMIT = 28

#: Hard ceiling on a popup's width. Sized so a fully capped row is *narrower*
#: than the ceiling — 28 glyphs at the menu's 12px font, plus row and menu
#: padding, is ~400px — so in normal use the ceiling never clips anything: it
#: only bites on a row that escaped the label cap.
MAX_WIDTH = 460


def short_label(text: str, limit: int = LIMIT) -> str:
    """Return ``text`` shortened to ``limit`` characters with an ellipsis.

    The cut happens on a character boundary and never leaves a trailing
    space, so "标题十个字" becomes "标题十个字…" rather than "标题十个 …".
    """
    text = str(text or "").strip()
    if limit <= 1:
        return "…"
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def add_item(
    menu,
    text: str,
    *,
    limit: int = LIMIT,
    enabled: bool = True,
    checkable: bool = False,
    checked: bool = False,
    tooltip: str | None = None,
):
    """Add one menu row whose label never widens the popup.

    ``enabled=False`` marks a read-only status row (the media/focus text).
    The full text always survives in the tooltip, so nothing the label
    truncates becomes unreachable — hover the row to read all of it.
    """
    full = str(text or "").strip()
    action = menu.addAction(short_label(full, limit))
    if len(full) > limit or (tooltip and tooltip != full):
        action.setToolTip(tooltip or full)
    action.setEnabled(enabled)
    if checkable:
        action.setCheckable(True)
        action.setChecked(bool(checked))
    return action


def cap_width(menu) -> None:
    """Apply the hard width ceiling to a menu or submenu popup."""
    menu.setMaximumWidth(MAX_WIDTH)
