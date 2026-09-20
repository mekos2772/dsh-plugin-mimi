"""Pet feedback and DSH navigation must not create a management window."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mimi_app" / "src"))

from mimi_pet.action_library import ActionLibrary
from mimi_pet.companion import CompanionService
from mimi_pet.config import load_config
from mimi_pet.dsh_integration import DshIntegration
from mimi_pet.engine import PetEngine
from mimi_pet.state_machine import PetState

try:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMenu
    from mimi_pet.bubble_layer import BubbleLayer
    from mimi_pet.companion_ui import CompanionController
    from mimi_pet.dsh_panel import DshPanel
    from mimi_pet.qt_window import MimiWindow
    from mimi_pet.session_monitor import WindowsSessionMonitor
    HAS_QT = True
except ImportError:
    HAS_QT = False


def make_engine():
    config = load_config()
    return PetEngine(ActionLibrary(Path(config["asset_manifest"])), config)


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class CompanionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.now = datetime(2026, 9, 14, 12).timestamp()
        self.mono = 1000.0
        self.service = CompanionService(wall_clock=lambda: self.now, monotonic=lambda: self.mono)
        self.engine = make_engine()
        self.window = MimiWindow(self.engine, None, lambda *_: None, lambda: None)
        self.controller = CompanionController(self.window, self.service)
        self.addCleanup(self.window.close)
        self.bubbles = []
        self.controller.bubble_requested.connect(lambda text, kind: self.bubbles.append((text, kind)))

    def test_dsh_companion_command_does_not_create_a_chat_session(self) -> None:
        integration = DshIntegration(self.engine)
        integration.local_command_handler = self.controller.handle_command
        integration.ensure_agent_session = Mock(side_effect=AssertionError("must stay local"))
        integration.connected = True
        self.assertTrue(integration.set_mode("agent"))
        self.assertTrue(integration.should_show_panel(head_hovered=True))
        self.assertTrue(integration.prompt_active("陪我专注25分钟"))
        self.assertTrue(self.service.running)
        self.assertTrue(self.bubbles)
        integration.connected = False
        self.assertFalse(integration.should_show_panel(head_hovered=True))
        self.assertFalse(integration.prompt_active("你好"))
        integration.ensure_agent_session.assert_not_called()

    def test_work_mode_keeps_local_looking_text_in_project_session(self) -> None:
        integration = DshIntegration(self.engine)
        integration.local_command_handler = self.controller.handle_command
        integration.bridge = Mock()
        integration._active_session = "work-project"
        self.assertTrue(integration.prompt_active("陪我专注25分钟"))
        integration.bridge.prompt.assert_called_once_with("work-project", "陪我专注25分钟")
        self.assertIsNone(self.service.focus)

    def test_quiet_suppresses_tool_but_keeps_question_reply_and_input(self) -> None:
        integration = DshIntegration(self.engine)
        integration.local_command_handler = self.controller.handle_command
        integration.bubble_allowed = self.service.allows_bubble
        integration.sink = Mock()
        self.service.set_quiet(True)
        self.controller.refresh()
        integration._bubble("正在搜索", kind="tool")
        integration.sink.show_bubble.assert_not_called()
        integration._bubble("请批准", kind="question")
        integration.sink.show_bubble.assert_called_once_with("请批准", "question")
        integration._last_bubble_at = 0
        integration._bubble("工作结果", kind="assistant")
        self.assertEqual(integration.sink.show_bubble.call_count, 2)
        integration.connected = True
        integration._waiting_text = "请批准"
        self.assertTrue(integration.should_show_panel())

    def test_focus_seats_after_touch_without_changing_affection(self) -> None:
        before = self.engine.affection_score
        self.engine.perform("head_pat")
        self.service.start_focus(25)
        self.controller.tick()
        self.assertEqual(self.engine.player.action.id, "head_pat")
        for tick in range(100):
            self.engine.tick((tick + 1) / 10, 0.1)
        self.controller.tick()
        self.assertEqual(self.engine.player.action.id, "sit_down")
        for tick in range(100):
            self.engine.tick(10 + (tick + 1) / 10, 0.1)
        self.assertTrue(self.engine.is_sitting)
        self.engine.scenario_check(4000, 0)
        self.assertTrue(self.engine.is_sitting)  # no sleep during focus
        self.assertEqual(self.engine.affection_score, before)
        self.controller.invoke(self.service.cancel_focus)
        self.assertEqual(self.engine.player.action.id, "stand_up")

    def test_quiet_suppresses_welcome_and_autonomous_walk(self) -> None:
        self.engine.note_input(0)
        self.engine.set_companion_mode(False, True)
        self.assertFalse(self.engine.scenario_check(20, 0))
        self.assertFalse(self.engine.trigger_welcome_back(100))
        self.assertEqual(self.engine.states.state, PetState.IDLE)
        self.engine.trigger_touch("head")
        self.assertEqual(self.engine.player.action.id, "head_pat")

    def test_notifications_wait_and_click_requests_dsh_without_a_new_window(self) -> None:
        self.service.add_reminder("喝水", self.now + 60)
        self.now += 61
        self.mono += 61
        self.controller.can_present = lambda: False
        self.controller.tick()
        self.assertEqual(self.bubbles, [])
        self.assertEqual(len(self.service.pending_notices), 1)
        self.controller.can_present = lambda: True
        self.controller.tick()
        self.assertEqual(len(self.bubbles), 1)
        self.assertEqual(self.service.pending_notices, [])
        layer = BubbleLayer()
        self.addCleanup(layer.close)
        local = []
        chat = []
        self.controller.panel_requested.connect(lambda section: local.append(section))
        layer.companion_clicked.connect(lambda: self.controller.request_panel("notices"))
        layer.message_clicked.connect(lambda: chat.append(True))
        layer.add_bubble(*self.bubbles[0])
        windows_before = set(self.app.topLevelWidgets())
        layer.bubbles()[0].clicked.emit()
        self.assertEqual(local, ["notices"])
        self.assertEqual(chat, [])
        self.assertEqual(set(self.app.topLevelWidgets()), windows_before)

    def test_list_command_only_navigates_to_dsh(self) -> None:
        requested = []
        self.controller.panel_requested.connect(requested.append)
        windows_before = set(self.app.topLevelWidgets())
        self.assertTrue(self.controller.handle_command("查看提醒"))
        self.assertEqual(requested, ["reminders"])
        self.assertEqual(set(self.app.topLevelWidgets()), windows_before)

    def test_menu_points_to_dsh_and_offline_input_stays_hidden(self) -> None:
        menu = QMenu()
        self.addCleanup(menu.close)
        self.controller.build_menu(menu)
        requested = []
        self.controller.panel_requested.connect(requested.append)
        next(a for a in menu.actions() if a.text() == "在 DSH 中管理专注与提醒").trigger()
        self.assertEqual(requested, ["reminders"])
        panel = DshPanel()
        self.addCleanup(panel.close)
        integration = DshIntegration(self.engine)
        integration.local_command_handler = self.controller.handle_command
        integration.sink = panel
        integration.set_mode("agent")
        self.assertIn("未连接 DSH", panel.quick_input.placeholderText())
        self.assertFalse(integration.should_show_panel(head_hovered=True))
        self.assertFalse(integration.prompt_active("陪我专注25分钟"))
        self.assertIsNone(self.service.focus)
        integration.connected = True
        self.service.start_focus(25)
        panel.send_requested.connect(integration.prompt_active)
        panel.quick_input.setText("暂停专注")
        panel.quick_input._do_send()
        self.assertEqual(self.service.focus.status, "paused")
        self.assertEqual(panel.quick_input.text(), "")

    def test_context_menu_opens_on_release_not_on_press(self) -> None:
        from PySide6.QtCore import QPoint

        self.assertFalse(self.window.menu_open)
        opened: list = []
        original = self.window._show_context_menu
        self.window._show_context_menu = lambda pos: opened.append(pos)
        self.addCleanup(lambda: setattr(self.window, "_show_context_menu", original))
        QTest.mousePress(
            self.window, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, QPoint(12, 12)
        )
        # A popup opened while the button is still held is dismissed by the
        # button-up that follows, so nothing must happen on press.
        self.assertEqual(opened, [])
        QTest.mouseRelease(
            self.window, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, QPoint(12, 12)
        )
        self.assertEqual(len(opened), 1)

    def test_long_runtime_rows_never_widen_the_context_menu(self) -> None:
        """右键面板过大：一条长文本曾把菜单拉到整屏宽。

        The whole menu is exercised here — companion rows, media rows, the
        尺寸与位置 submenu with its width slider — because the popup is
        built by all three sections, not just one.
        """
        from PySide6.QtCore import QPoint

        from mimi_pet import menu_labels
        from mimi_pet.media import MediaTrack, SmtcController
        from mimi_pet.media_bar import MediaBar

        # A running focus so the companion menu section has real content.
        self.service.start_focus(25)
        self.controller.refresh()

        long_track = MediaTrack(
            "什么叫打赢BO8才能拿冠军？玩机器看决赛日赛程，载物依旧一带四僵尸，"
            "进攻方打的太变态了。只能先赢下决赛BO3，才能再进决赛BO5复仇欧若拉",
            "歌手", "paused", "app", 1000, 240000,
        )
        media = SmtcController(self.window)
        self.addCleanup(media.shutdown)
        media.available = True
        media.track = long_track
        bar = MediaBar()
        self.addCleanup(bar.deleteLater)
        media.bar = bar
        self.window.media = media
        self.addCleanup(setattr, self.window, "media", None)

        # Build (not exec): the popup loop only exits on a real dismissal.
        menu = self.window._build_context_menu()
        self.addCleanup(menu.close)
        menu.show()
        self.app.processEvents()

        # The reported popup was ~1100px; it must now stay in a narrow band
        # beside the pet, and the width cap must be on every menu/submenu.
        self.assertGreater(menu.width(), 0)
        self.assertLessEqual(menu.width(), menu_labels.MAX_WIDTH)
        for row in menu.actions():
            if row.menu() is not None:
                self.assertEqual(row.menu().maximumWidth(), menu_labels.MAX_WIDTH)
        # The full text survives in the tooltip, so nothing is unreadable.
        media_status = next(
            a for a in menu.actions() if a.text() and a.text().startswith("♪")
        )
        self.assertLessEqual(len(media_status.text()), menu_labels.LIMIT)
        self.assertIn(long_track.title, media_status.toolTip())

    def test_native_lock_resume_notifications_pause_without_auto_restart(self) -> None:
        monitor = WindowsSessionMonitor(self.app, self.window, self.service.system_event)
        self.addCleanup(monitor.close)
        self.service.start_focus(1)
        monitor.handle_message(0x02B1, 7)
        self.assertEqual(self.service.focus.status, "paused")
        self.assertTrue(self.service.system_blocked)
        monitor.handle_message(0x0218, 4)
        monitor.handle_message(0x0218, 18)
        self.assertTrue(self.service.system_blocked)
        monitor.handle_message(0x02B1, 8)
        self.assertFalse(self.service.system_blocked)
        self.assertEqual(self.service.focus.status, "paused")

    @unittest.skipUnless(sys.platform == "win32", "Windows native message layout")
    def test_native_message_pointer_is_decoded_and_filtered_by_window(self) -> None:
        import ctypes
        from ctypes import wintypes

        monitor = WindowsSessionMonitor(self.app, self.window, self.service.system_event)
        self.addCleanup(monitor.close)
        self.service.start_focus(1)
        message = wintypes.MSG()
        message.hWnd = monitor.hwnd + 1
        message.message = 0x02B1
        message.wParam = 7
        pointer = ctypes.addressof(message)
        self.assertEqual(monitor.nativeEventFilter(b"windows_generic_MSG", pointer), (False, 0))
        self.assertTrue(self.service.running)
        message.hWnd = monitor.hwnd
        monitor.nativeEventFilter(b"windows_generic_MSG", pointer)
        self.assertEqual(self.service.focus.status, "paused")
        message.wParam = 8
        monitor.nativeEventFilter(b"windows_generic_MSG", pointer)
        self.assertFalse(self.service.system_blocked)

    def test_input_desktop_fallback_handles_missed_unlock_without_resuming(self) -> None:
        monitor = WindowsSessionMonitor(self.app, self.window, self.service.system_event)
        self.addCleanup(monitor.close)
        monitor.native_enabled = True
        self.service.start_focus(1)
        with patch("mimi_pet.session_monitor._input_desktop_available", return_value=False):
            monitor.poll(force=True)
        self.assertTrue(self.service.system_blocked)
        self.assertEqual(self.service.focus.status, "paused")
        monitor.handle_message(0x02B1, 7)
        with patch("mimi_pet.session_monitor._input_desktop_available", return_value=True):
            monitor.poll(force=True)
        self.assertFalse(self.service.system_blocked)
        self.assertEqual(self.service.focus.status, "paused")


if __name__ == "__main__":
    unittest.main()
