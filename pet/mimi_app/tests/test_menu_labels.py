"""Right-click menu label shaping: capping, tooltips and popup width.

A pet menu must stay narrow beside the character. The rows it builds carry
unbounded runtime text — a media session title, a focus label, a DSH session
label — and one long row used to stretch the popup across the whole screen
while the menu was open (the user's report: 右键面板过大).

These tests pin the three guarantees that fix it:

1. a label is capped and never longer than ``LIMIT`` characters;
2. truncating a label never loses the text — it moves to the row's tooltip;
3. the popup itself has a hard width ceiling, so a row that slips through
   cannot widen the menu either.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mimi_app" / "src"))

from mimi_pet import menu_labels  # noqa: E402
from mimi_pet.menu_labels import LIMIT, MAX_WIDTH  # noqa: E402

try:
    from PySide6.QtWidgets import QApplication, QMenu

    HAS_QT = True
except ImportError:  # pragma: no cover - PySide6 missing
    HAS_QT = False

# The shape that actually reported the bug: a real track title from the
# pet's own menu, long enough to push a popup past a whole screen.
LONG_TRACK = (
    "♪ 什么叫打赢BO8才能拿冠军？玩机器看决赛日赛程，载物依旧一带四僵尸，"
    "进攻方打的太变态了。只能先赢下决赛BO3，才能再进决赛BO5复仇欧若拉（已暂停）"
)


class ShortLabelTest(unittest.TestCase):
    def test_short_text_is_returned_unchanged(self) -> None:
        self.assertEqual(menu_labels.short_label("晴天 - 周杰伦"), "晴天 - 周杰伦")

    def test_long_text_is_capped_with_an_ellipsis(self) -> None:
        label = menu_labels.short_label(LONG_TRACK)
        self.assertLessEqual(len(label), LIMIT)
        self.assertTrue(label.endswith("…"))
        self.assertIn("…", label)

    def test_cap_never_leaves_a_trailing_space(self) -> None:
        # A cut inside a CJK/ASCII run must not leave "十个字…" looking like
        # "十个 …"; the char before the ellipsis is trimmed.
        for text in ("标题 后面还有很多很多很多的文字", "a b c d e f g h i j k l m n"):
            with self.subTest(text=text):
                label = menu_labels.short_label(text, limit=10)
                self.assertFalse(label[:-1].endswith(" "))

    def test_empty_and_none_become_empty(self) -> None:
        self.assertEqual(menu_labels.short_label(""), "")
        self.assertEqual(menu_labels.short_label(None), "")

    def test_degenerate_limit_still_returns_one_char(self) -> None:
        self.assertEqual(menu_labels.short_label(LONG_TRACK, limit=1), "…")

    def test_the_cap_is_wide_enough_to_read_but_narrow_enough_to_behave(self) -> None:
        # Sanity on the constants themselves: a CJK glyph advances ~12px at
        # the menu's font, and the row plus menu padding adds ~60px. A fully
        # capped row must stay inside the ceiling, so the clamp can never be
        # what cuts text off.
        self.assertLessEqual(LIMIT * 14 + 60, MAX_WIDTH)

    def test_the_ceiling_is_far_narrower_than_the_bug_report(self) -> None:
        # The reported menu spanned ~1100px; the ceiling must be a fraction
        # of that, or the "panel too large" complaint comes straight back.
        self.assertLessEqual(MAX_WIDTH, 480)


@unittest.skipUnless(HAS_QT, "PySide6 is not installed; Qt tests skipped")
class MenuRowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.menu = QMenu()
        self.addCleanup(self.menu.close)

    def test_a_long_row_is_capped_and_keeps_the_full_text_in_the_tooltip(self) -> None:
        action = menu_labels.add_item(self.menu, LONG_TRACK)
        self.assertLessEqual(len(action.text()), LIMIT)
        self.assertEqual(action.toolTip(), LONG_TRACK)

    def test_a_short_row_carries_no_extra_tooltip_text(self) -> None:
        # Qt reports an action's own text when no tooltip was set, so a row
        # that already fits shows its label on hover and nothing more.
        action = menu_labels.add_item(self.menu, "♪ 晴天 - 周杰伦")
        self.assertEqual(action.text(), "♪ 晴天 - 周杰伦")
        self.assertIn(action.toolTip(), ("", action.text()))

    def test_an_explicit_tooltip_wins_over_the_label_text(self) -> None:
        action = menu_labels.add_item(self.menu, "播放中", tooltip="完整状态：播放中")
        self.assertEqual(action.toolTip(), "完整状态：播放中")

    def test_status_rows_are_disabled_but_still_show_the_tooltip(self) -> None:
        action = menu_labels.add_item(self.menu, LONG_TRACK, enabled=False)
        self.assertFalse(action.isEnabled())
        self.assertEqual(action.toolTip(), LONG_TRACK)

    def test_checkable_rows_report_their_checked_state(self) -> None:
        action = menu_labels.add_item(
            self.menu, "跟随当前活动", checkable=True, checked=True
        )
        self.assertTrue(action.isCheckable())
        self.assertTrue(action.isChecked())


@unittest.skipUnless(HAS_QT, "PySide6 is not installed; Qt tests skipped")
class MenuWidthTest(unittest.TestCase):
    """The popup must never grow past ``MAX_WIDTH``.

    ``QMenu.sizeHint()`` reports the content's natural width and ignores
    ``maximumWidth``, so the assertion has to run against the popup Qt
    actually shows (``show()`` then read the widget size), not the hint.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _shown_menu(self) -> QMenu:
        menu = QMenu()
        self.addCleanup(menu.close)
        menu.addAction(LONG_TRACK)
        menu.addAction("继续播放")
        menu_labels.cap_width(menu)
        menu.show()
        self.app.processEvents()
        return menu

    def test_a_menu_with_an_uncapped_long_row_stays_within_the_ceiling(self) -> None:
        menu = self._shown_menu()
        self.assertGreater(menu.width(), 0)
        self.assertLessEqual(menu.width(), MAX_WIDTH)

    def test_the_ceiling_is_applied_to_submenus_too(self) -> None:
        sub = QMenu()
        self.addCleanup(sub.close)
        menu_labels.cap_width(sub)
        self.assertEqual(sub.maximumWidth(), MAX_WIDTH)

    def test_capped_labels_fit_inside_the_ceiling_without_clipping(self) -> None:
        # The clamp is only a backstop: with labels shaped, the natural width
        # of the popup is already narrow enough that nothing is cut off.
        menu = QMenu()
        self.addCleanup(menu.close)
        for text in (LONG_TRACK, "♪ 晴天 - 周杰伦", "在 DSH 中管理专注与提醒"):
            menu_labels.add_item(menu, text)
        menu.show()
        self.app.processEvents()
        self.assertLessEqual(menu.width(), MAX_WIDTH)


if __name__ == "__main__":
    unittest.main()
