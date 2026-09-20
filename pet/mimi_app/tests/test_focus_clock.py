"""Floating focus dial: visibility, countdown text, chatter and screen clamping."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mimi_app" / "src"))

from mimi_pet.action_library import ActionLibrary  # noqa: E402
from mimi_pet.companion import CompanionService, FocusSession  # noqa: E402
from mimi_pet.config import load_config  # noqa: E402
from mimi_pet.engine import PetEngine  # noqa: E402

try:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from mimi_pet.companion_ui import CompanionController
    from mimi_pet.focus_clock import (
        CAPTION_INTERVAL_S,
        FINISHED_LINE,
        FOCUS_LINES,
        PAUSED_LINE,
        FocusClock,
        format_remaining,
        status_label,
    )
    from mimi_pet.qt_window import MimiWindow

    HAS_QT = True
except ImportError:
    HAS_QT = False


def session(kind: str = "focus", total: float = 1500.0, remaining: float = 1500.0, status: str = "running"):
    return FocusSession(id="s1", kind=kind, total_s=total, remaining_s=remaining, status=status)


@unittest.skipUnless(HAS_QT, "PySide6 is not installed; focus clock test skipped")
class FocusClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _clock(self) -> FocusClock:
        clock = FocusClock()
        self.addCleanup(clock.close)
        self.addCleanup(clock.hide)
        return clock

    def test_stays_hidden_without_a_session(self) -> None:
        clock = self._clock()
        clock.set_session(None, "准备好时，陪你专注一会儿。")
        clock.position_near(500.0, 400.0, 256.0)
        self.assertIsNone(clock.focus)
        self.assertFalse(clock.isVisible())

    def test_running_session_shows_the_countdown(self) -> None:
        clock = self._clock()
        focus = session(total=1500.0, remaining=1499.4)
        clock.set_session(focus, "专注中 · 剩余 24:59")
        clock.position_near(500.0, 400.0, 256.0)
        self.assertTrue(clock.isVisible())
        self.assertEqual(format_remaining(focus), "25:00")
        self.assertEqual(status_label(focus), "专注")
        self.assertEqual(clock.caption(), FOCUS_LINES[0])

    def test_cancelled_session_hides_the_dial(self) -> None:
        clock = self._clock()
        clock.set_session(session(), "")
        clock.position_near(500.0, 400.0, 256.0)
        self.assertTrue(clock.isVisible())
        clock.set_session(session(status="cancelled"), "")
        clock.position_near(500.0, 400.0, 256.0)
        self.assertFalse(clock.isVisible())
        self.assertIsNone(clock.focus)

    def test_paused_and_finished_have_their_own_line(self) -> None:
        clock = self._clock()
        clock.set_session(session(status="paused"), "")
        self.assertEqual(clock.caption(), PAUSED_LINE)
        self.assertEqual(status_label(session(status="paused")), "已暂停")
        clock.set_session(session(status="finished", remaining=0.0), "")
        self.assertEqual(clock.caption(), FINISHED_LINE)
        self.assertEqual(status_label(session(status="finished")), "已完成")

    def test_break_session_counts_as_rest(self) -> None:
        clock = self._clock()
        clock.set_session(session(kind="break", total=300.0, remaining=300.0), "")
        self.assertEqual(status_label(session(kind="break")), "休息")
        self.assertNotEqual(clock.caption(), "")

    def test_chatter_rotates_as_the_session_runs(self) -> None:
        clock = self._clock()
        total = CAPTION_INTERVAL_S * len(FOCUS_LINES) + 30.0
        seen = []
        for elapsed in (0.0, CAPTION_INTERVAL_S, CAPTION_INTERVAL_S * 2):
            clock.set_session(session(total=total, remaining=total - elapsed), "")
            seen.append(clock.caption())
        self.assertEqual(seen[0], FOCUS_LINES[0])
        self.assertEqual(seen[1], FOCUS_LINES[1])
        self.assertEqual(seen[2], FOCUS_LINES[2])

    def _drag(self, clock: FocusClock, target: QPoint) -> None:
        """Press, move and release so the dial lands on ``target``."""
        grab = QPoint(20, 20)
        QTest.mousePress(clock, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, grab)
        # The offset is captured on press, so the move is aimed at
        # (global - offset); the widget has not moved yet, so global is pos+local.
        local = QPoint(target.x() + grab.x() - clock.x(), target.y() + grab.y() - clock.y())
        QTest.mouseMove(clock, local)
        QTest.mouseRelease(clock, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, local)

    def test_dial_is_draggable_without_stealing_activation(self) -> None:
        clock = self._clock()
        self.assertFalse(clock.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertTrue(clock.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        self.assertEqual(clock.cursor().shape(), Qt.CursorShape.SizeAllCursor)

    def test_dragging_pins_the_dial_where_the_user_left_it(self) -> None:
        clock = self._clock()
        clock.set_session(session(), "")
        clock.position_near(500.0, 400.0, 256.0)
        self.assertFalse(clock.pinned)
        geo = QGuiApplication.primaryScreen().availableGeometry()
        target = QPoint(geo.left() + 30, geo.top() + 40)
        self._drag(clock, target)
        self.assertTrue(clock.pinned)
        self.assertEqual(clock.pos(), target)
        # The automatic trailing must leave a pinned dial alone.
        clock.position_near(geo.right() - 300.0, geo.top() + 120.0, 256.0)
        self.assertEqual(clock.pos(), target)

    def test_double_click_hands_the_dial_back_to_the_pet(self) -> None:
        clock = self._clock()
        clock.set_session(session(), "")
        clock.position_near(500.0, 400.0, 256.0)
        geo = QGuiApplication.primaryScreen().availableGeometry()
        target = QPoint(geo.left() + 30, geo.top() + 40)
        self._drag(clock, target)
        QTest.mouseDClick(clock, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(8, 8))
        self.assertFalse(clock.pinned)
        clock.position_near(500.0, 400.0, 256.0)
        self.assertNotEqual(clock.pos(), target)

    def test_dial_flips_to_the_other_side_and_stays_on_screen(self) -> None:
        clock = self._clock()
        clock.set_session(session(), "")
        geo = QGuiApplication.primaryScreen().availableGeometry()
        # Far right edge: there is no room beside the head, so it flips left.
        clock.position_near(geo.right() - 8.0, geo.top() + 200.0, 256.0)
        self.assertTrue(clock._tail_on_right)
        self.assertGreaterEqual(clock.x(), geo.left())
        self.assertLessEqual(clock.x() + clock.width(), geo.right())
        self.assertGreaterEqual(clock.y(), geo.top())
        self.assertLessEqual(clock.y() + clock.height(), geo.bottom())


@unittest.skipUnless(HAS_QT, "PySide6 is not installed; focus clock wiring test skipped")
class FocusClockWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.now = datetime(2026, 9, 14, 12).timestamp()
        self.mono = 1000.0
        self.service = CompanionService(wall_clock=lambda: self.now, monotonic=lambda: self.mono)
        config = load_config()
        self.engine = PetEngine(ActionLibrary(Path(config["asset_manifest"])), config)
        self.window = MimiWindow(self.engine, None, lambda *_: None, lambda: None)
        self.addCleanup(self.window.close)
        self.controller = CompanionController(self.window, self.service)
        self.clock = FocusClock()
        self.addCleanup(self.clock.close)
        self.addCleanup(self.clock.hide)
        self.controller.clock = self.clock

    def _place(self) -> None:
        self.clock.position_near(500.0, 400.0, 256.0)

    def test_focus_session_shows_the_dial_and_cancel_hides_it(self) -> None:
        self.controller.refresh()
        self._place()
        self.assertFalse(self.clock.isVisible())
        self.service.start_focus(25)
        self.controller.refresh()
        self._place()
        self.assertTrue(self.clock.isVisible())
        self.assertIn("专注", self.clock.toolTip())
        self.service.cancel_focus()
        self.controller.tick()
        self._place()
        self.assertFalse(self.clock.isVisible())

    def test_disabled_companion_clears_the_dial(self) -> None:
        self.service.start_focus(25)
        self.controller.refresh()
        self._place()
        self.assertTrue(self.clock.isVisible())
        self.controller.actions.enabled = False
        self.controller.refresh()
        self._place()
        self.assertFalse(self.clock.isVisible())


if __name__ == "__main__":
    unittest.main()
