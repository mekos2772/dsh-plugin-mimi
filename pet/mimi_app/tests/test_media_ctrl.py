"""SMTC controller dispatch and menu tests.

``control()`` is called from menu items and playback-bar clicks, and
``_launch_apple_music()`` from a menu item. Both used to wait synchronously on
an external round-trip (an SMTC call, a PowerShell probe), which froze the pet
for up to 5 and 10 seconds respectively.

The dispatch tests need no window; the menu tests do build widgets.
"""

from __future__ import annotations

import concurrent.futures
import os
import sys
import threading
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mimi_app" / "src"))

try:
    from PySide6.QtWidgets import QApplication, QMenu

    import mimi_pet.media as media_module
    import mimi_pet.menu_labels as menu_labels
    from mimi_pet.media import MediaTrack, SmtcController
    from mimi_pet.media_bar import MediaBar

    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "PySide6 is not installed; Qt tests skipped")
class _ControllerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        # Construct while winsdk is reported absent, so no real SMTC polling
        # thread starts and nothing touches the machine's media sessions.
        original = media_module.HAVE_WINSDK
        media_module.HAVE_WINSDK = False
        try:
            self.media = SmtcController()
        finally:
            media_module.HAVE_WINSDK = original
        self.media.available = True
        self.bubbles: list[str] = []
        self.media.bubble_requested.connect(
            lambda text, _kind: self.bubbles.append(text)
        )
        self.addCleanup(self.media.shutdown)


class ControlDispatchTests(_ControllerTestCase):
    def test_control_does_not_wait_on_the_session(self) -> None:
        submitted: list = []
        callbacks: list = []

        class NeverFinishes:
            """Stands in for a hung SMTC call; waiting on it must not happen."""

            def add_done_callback(self, callback) -> None:
                callbacks.append(callback)

            def result(self, timeout=None):
                raise AssertionError("control() must not wait on the future")

        self.media._submit = lambda factory: (
            submitted.append(factory),
            NeverFinishes(),
        )[1]

        self.media.control("next")

        self.assertEqual(len(submitted), 1)
        self.assertEqual(callbacks, [self.media._report_control])

    def test_control_reports_when_the_session_refuses(self) -> None:
        future = concurrent.futures.Future()
        future.set_result(False)
        self.media._report_control(future)
        self.assertEqual(len(self.bubbles), 1)

    def test_control_stays_quiet_on_success(self) -> None:
        future = concurrent.futures.Future()
        future.set_result(True)
        self.media._report_control(future)
        self.assertEqual(self.bubbles, [])

    def test_control_reports_a_raised_error_instead_of_crashing(self) -> None:
        future = concurrent.futures.Future()
        future.set_exception(RuntimeError("SMTC blew up"))
        self.media._report_control(future)
        self.assertEqual(len(self.bubbles), 1)

    def test_unavailable_controller_reports_immediately(self) -> None:
        self.media.available = False
        self.media.control("next")
        self.assertEqual(len(self.bubbles), 1)

    def test_control_reports_when_the_loop_is_not_ready(self) -> None:
        self.media._submit = lambda factory: None
        self.media.control("next")
        self.assertEqual(len(self.bubbles), 1)


class AvailabilityTests(_ControllerTestCase):
    def test_control_surfaces_the_unavailable_reason(self) -> None:
        self.media._mark_unavailable("音乐控制不可用（TimeoutError）")
        self.bubbles.clear()
        self.media.control("next")
        self.assertEqual(self.bubbles, ["音乐控制不可用（TimeoutError）"])

    def test_unavailable_is_reported_only_once(self) -> None:
        self.media._mark_unavailable("first")
        self.media._mark_unavailable("second")
        self.assertEqual(self.bubbles, ["first"])
        self.assertEqual(self.media.unavailable_reason, "first")

    def test_status_text_surfaces_the_real_reason(self) -> None:
        # It used to always claim "缺少 winsdk", even when winsdk was present
        # and SMTC itself was the thing that failed.
        self.media._mark_unavailable("音乐控制不可用（RuntimeError）")
        self.assertEqual(self.media.status_text(), "音乐控制不可用（RuntimeError）")


class PollLoopTests(_ControllerTestCase):
    def _run_loop_raising(self, error: Exception) -> None:
        async def failing():
            raise error

        self.media._poll_forever = failing
        self.media._run_loop()

    def test_a_poll_failure_is_reported_not_swallowed(self) -> None:
        self._run_loop_raising(RuntimeError("COM init failed"))
        self.assertFalse(self.media.available)
        self.assertIn("RuntimeError", self.media.unavailable_reason)
        self.assertEqual(len(self.bubbles), 1)

    def test_a_poll_failure_during_shutdown_stays_quiet(self) -> None:
        self.media._stop.set()
        self._run_loop_raising(RuntimeError("loop closing"))
        self.assertTrue(self.media.available)
        self.assertEqual(self.bubbles, [])

    def test_submit_refuses_a_closed_loop(self) -> None:
        self._run_loop_raising(RuntimeError("COM init failed"))
        self.assertIsNone(self.media._submit(lambda: None))
        self.bubbles.clear()
        self.media.control("next")
        self.assertEqual(len(self.bubbles), 1)


class AppleMusicLaunchTests(_ControllerTestCase):
    def test_launch_apple_music_runs_off_the_calling_thread(self) -> None:
        seen: dict = {}
        done = threading.Event()

        def worker() -> None:
            seen["thread"] = threading.current_thread()
            done.set()

        self.media._launch_apple_music_worker = worker
        self.media._launch_apple_music()

        self.assertTrue(done.wait(2.0), "the probe worker never ran")
        self.assertIsNot(seen["thread"], threading.current_thread())


class MediaMenuTests(_ControllerTestCase):
    def _bar_toggle(self, menu: QMenu):
        items = [a for a in menu.actions() if "音乐栏" in a.text()]
        self.assertEqual(len(items), 1, "exactly one bar toggle expected")
        return items[0]

    def test_no_bar_toggle_without_a_bar(self) -> None:
        menu = QMenu()
        self.media.build_menu(menu)
        self.assertEqual([a for a in menu.actions() if "音乐栏" in a.text()], [])

    def test_bar_toggle_opens_and_closes_the_bar(self) -> None:
        bar = MediaBar()
        self.addCleanup(bar.deleteLater)
        self.media.bar = bar

        menu = QMenu()
        self.media.build_menu(menu)
        toggle = self._bar_toggle(menu)
        self.assertEqual(toggle.text(), "显示音乐栏")

        toggle.trigger()
        self.assertTrue(bar.is_open)

        menu2 = QMenu()
        self.media.build_menu(menu2)
        self.assertEqual(self._bar_toggle(menu2).text(), "隐藏音乐栏")

        self._bar_toggle(menu2).trigger()
        self.assertFalse(bar.is_open)

    def test_separator_is_present_even_when_unavailable(self) -> None:
        # The early return used to skip addSeparator(), shifting the menus
        # that follow depending on whether winsdk happened to be installed.
        self.media.available = False
        menu = QMenu()
        self.media.build_menu(menu)
        self.assertTrue(any(action.isSeparator() for action in menu.actions()))

    def test_no_bar_toggle_when_unavailable(self) -> None:
        bar = MediaBar()
        self.addCleanup(bar.deleteLater)
        self.media.bar = bar
        self.media.available = False
        menu = QMenu()
        self.media.build_menu(menu)
        self.assertEqual([a for a in menu.actions() if "音乐栏" in a.text()], [])


class MediaMenuShapeTests(_ControllerTestCase):
    """The reported menu was ~1100px wide: one unshaped track title did it."""

    # The exact string from the bug report — long enough on its own to blow
    # the popup up to screen width.
    LONG_TITLE = (
        "♪ 什么叫打赢BO8才能拿冠军？玩机器看决赛日赛程，载物依旧一带四僵尸，"
        "进攻方打的太变态了。只能先赢下决赛BO3，才能再进决赛BO5复仇欧若拉（已暂停）"
    )

    def setUp(self) -> None:
        super().setUp()
        bar = MediaBar()
        self.addCleanup(bar.deleteLater)
        self.media.bar = bar
        self.media.track = MediaTrack(
            self.LONG_TITLE, "歌手", "paused", "app", 1000, 240000
        )
        # What the status row shows: title, artist and playback state.
        self.expected_status = self.media.status_text()

    def test_status_row_is_capped_and_the_full_title_is_in_the_tooltip(self) -> None:
        menu = QMenu()
        self.addCleanup(menu.close)
        self.media.build_menu(menu)
        status = menu.actions()[0]
        self.assertIn(self.LONG_TITLE, self.expected_status)
        self.assertLessEqual(len(status.text()), menu_labels.LIMIT)
        self.assertTrue(status.text().endswith("…"))
        self.assertEqual(status.toolTip(), self.expected_status)

    def test_no_row_can_widen_the_menu_past_the_ceiling(self) -> None:
        # A popup's sizeHint ignores maximumWidth, so the width has to be
        # read back from the popup Qt actually shows.
        menu = QMenu()
        self.addCleanup(menu.close)
        self.media.build_menu(menu)
        menu_labels.cap_width(menu)
        menu.show()
        self.app.processEvents()
        self.assertLessEqual(menu.width(), menu_labels.MAX_WIDTH)
        self.assertGreater(menu.width(), 0)

    def test_the_bar_toggle_row_is_not_affected_by_the_cap(self) -> None:
        bar = self.media.bar
        menu = QMenu()
        self.addCleanup(menu.close)
        self.media.build_menu(menu)
        toggle = next(a for a in menu.actions() if "音乐栏" in a.text())
        self.assertEqual(toggle.text(), "显示音乐栏")
        toggle.trigger()
        self.assertTrue(bar.is_open)


if __name__ == "__main__":
    unittest.main()
