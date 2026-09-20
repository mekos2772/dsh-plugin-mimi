"""Playback-bar layout, default state and close-affordance tests (Qt-gated).

The bar is a hand-painted widget, so these tests exercise the real geometry and
the real mouse routing rather than a mock. ``offscreen`` is only a default: an
explicitly set ``QT_QPA_PLATFORM`` still wins.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mimi_app" / "src"))

try:
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication

    HAS_QT = True
except ImportError:
    HAS_QT = False

if HAS_QT:
    from mimi_pet import media_bar as bar_module
    from mimi_pet.media import MediaTrack
    from mimi_pet.media_bar import (
        BTN_CLOSE,
        BTN_NEXT,
        BTN_PREV,
        BTN_TOGGLE,
        MediaBar,
    )

# A plausible pet rectangle: 256x384 sitting at (600, 400).
PET = (600.0, 400.0, 256.0, 384.0)


@unittest.skipUnless(HAS_QT, "PySide6 is not installed; Qt tests skipped")
class MediaBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.bar = MediaBar()
        self.addCleanup(self.bar.deleteLater)

    @staticmethod
    def _track(title: str, status: str = "playing") -> MediaTrack:
        return MediaTrack(title, "歌手", status, "spotify", 0, 240000)

    def _open_with_pet(self, title: str = "晴天") -> None:
        """The realistic sequence: a track arrives, the pet reports its rect,
        then the user opens the bar from the menu."""
        self.bar.set_track(self._track(title))
        self.bar.position_near(*PET)
        self.bar.open_bar()

    def _click(self, index: int) -> None:
        point = self.bar._button_rects()[index].center()
        self.bar.mousePressEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                QPointF(point),
                QPointF(point),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )

    # ------------------------------------------------------------- geometry

    def test_buttons_stay_inside_the_card_and_do_not_overlap(self) -> None:
        rects = self.bar._button_rects()
        self.assertEqual(len(rects), bar_module.BTN_COUNT)
        for rect in rects:
            self.assertGreaterEqual(rect.left(), 0.0)
            self.assertLessEqual(rect.right(), bar_module.BAR_W)
        for earlier, later in zip(rects, rects[1:]):
            self.assertLess(earlier.right(), later.left())

    def test_every_click_target_maps_to_its_button(self) -> None:
        for index in (BTN_PREV, BTN_TOGGLE, BTN_NEXT, BTN_CLOSE):
            with self.subTest(index=index):
                point = self.bar._button_rects()[index].center().toPoint()
                self.assertEqual(self.bar._hit_button(point), index)

    def test_title_keeps_usable_width(self) -> None:
        # The fourth button eats into the title column. The floor is set just
        # under the real 136px so narrowing the card again fails here.
        note_w = 20.0
        text_width = (
            self.bar._button_rects()[BTN_PREV].left()
            - (bar_module.PAD + note_w)
            - 8.0
        )
        self.assertGreater(text_width, 130.0)

    # -------------------------------------------------------- default state

    def test_starts_closed(self) -> None:
        self.assertFalse(self.bar.is_open)
        self.assertFalse(self.bar.isVisible())

    def test_detecting_a_track_does_not_open_the_bar(self) -> None:
        self.bar.set_track(self._track("晴天"))
        self.bar.position_near(*PET)
        self.assertFalse(self.bar.is_open)
        self.assertFalse(self.bar.isVisible())

    def test_opening_before_any_track_shows_nothing(self) -> None:
        self.bar.open_bar()
        self.assertTrue(self.bar.is_open)
        self.assertFalse(self.bar.isVisible())

    def test_opening_with_a_known_track_shows_it(self) -> None:
        self._open_with_pet()
        self.assertTrue(self.bar.isVisible())

    def test_opening_before_the_pet_is_known_waits_instead_of_flashing(self) -> None:
        # Without an anchor the card can only be shown in the screen corner,
        # so it waits for the first position_near().
        self.bar.set_track(self._track("晴天"))
        self.bar.open_bar()
        self.assertFalse(self.bar.isVisible())

        self.bar.position_near(*PET)
        self.assertTrue(self.bar.isVisible())

    def test_toggle_flips_the_open_state(self) -> None:
        self.bar.toggle()
        self.assertTrue(self.bar.is_open)
        self.bar.toggle()
        self.assertFalse(self.bar.is_open)
        self.assertFalse(self.bar.isVisible())

    def test_losing_the_track_hides_an_open_bar(self) -> None:
        self._open_with_pet()
        self.bar.set_track(None)
        self.assertFalse(self.bar.isVisible())

    def test_a_bar_that_was_never_opened_stays_closed(self) -> None:
        self.bar.set_track(self._track("晴天"))
        self.bar.position_near(*PET)
        self.bar.set_track(self._track("夜曲"))
        self.assertFalse(self.bar.isVisible())

    # ----------------------------------------------------------- placement

    def test_an_unpinned_bar_reopens_beside_the_pet(self) -> None:
        self._open_with_pet()
        self.bar.close_bar()
        self.bar.move(1300, 900)  # wherever it happened to be left
        self.bar.open_bar()
        self.assertLess(self.bar.x(), PET[0], "should sit left of the pet again")

    def test_a_pinned_bar_reopens_where_the_user_left_it(self) -> None:
        self._open_with_pet()
        self.bar.move(1200, 700)
        self.bar._pinned = True  # what a completed drag sets
        self.bar.close_bar()

        self.bar.position_near(*PET)
        self.bar.open_bar()

        self.assertEqual((self.bar.x(), self.bar.y()), (1200, 700))

    # ------------------------------------------------------------- closing

    def test_close_button_closes_the_bar(self) -> None:
        self._open_with_pet()
        self._click(BTN_CLOSE)
        self.assertFalse(self.bar.is_open)
        self.assertFalse(self.bar.isVisible())

    def test_close_button_does_not_start_a_drag(self) -> None:
        self._open_with_pet()
        self._click(BTN_CLOSE)
        self.assertIsNone(self.bar._press_global)

    def test_a_closed_bar_stays_closed_when_the_track_changes(self) -> None:
        self._open_with_pet()
        self._click(BTN_CLOSE)
        self.bar.set_track(self._track("夜曲"))
        self.assertFalse(self.bar.is_open)
        self.assertFalse(self.bar.isVisible())

    def test_transport_clicks_do_not_close(self) -> None:
        self._open_with_pet()
        for index in (BTN_PREV, BTN_TOGGLE, BTN_NEXT):
            with self.subTest(index=index):
                self._click(index)
        self.assertTrue(self.bar.is_open)

    # --------------------------------------------------------------- paint

    def test_glyphs_paint_without_error(self) -> None:
        self._open_with_pet()
        pixmap = QPixmap(int(bar_module.BAR_W), int(bar_module.BAR_H))
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        try:
            self.bar._draw_buttons(painter)
        finally:
            painter.end()
        self.assertFalse(pixmap.isNull())


if __name__ == "__main__":
    unittest.main()
