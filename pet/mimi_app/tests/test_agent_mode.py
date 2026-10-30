"""桌宠 agent 模式（和 Mimi 聊天）的测试。

用假 bridge 代替真实 DSH，不需要起服务。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "pet" / "mimi_app" / "src"))

from mimi_pet.dsh_integration import DshIntegration


class FakeBridge:
    def __init__(self) -> None:
        self.sessions: list = []
        self.prompts: list[tuple[str, str]] = []
        self.archived: list[str] = []
        self.renamed: list[tuple[str, str]] = []
        self._next = 0

    def list_sessions(self):
        return list(self.sessions)

    def settings_permission_preset(self):
        return ("ask", 1)

    def settings_set_permission_preset(self, preset, revision):
        return revision + 1

    def create_session(self):
        self._next += 1
        return f"session-{self._next}"

    def archive_session(self, session_id):
        self.archived.append(session_id)

    def rename_session(self, session_id, title):
        self.renamed.append((session_id, title))

    def prompt(self, session_id, text):
        self.prompts.append((session_id, text))
        return "ok"

    def auth(self):
        return None


class FakeEngine:
    def __init__(self) -> None:
        self.talking = False
        self.bubbles: list[str] = []

    def affection_snapshot(self):
        return SimpleNamespace(score=50, level_name="朋友", last_interaction=None)

    def set_external_bubble(self, text: str | None, duration_s: float = 6.0) -> None:
        if text:
            self.bubbles.append(text)

    def set_talking(self, value: bool) -> None:
        self.talking = value

    def note_chat_affection(self, text: str) -> None:
        self.last_chat = text

    def force_perform(self, action_id: str) -> bool:
        return True


class AgentSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.integration = DshIntegration(FakeEngine())
        self.integration.bridge = FakeBridge()
        self.integration._agent_session_file = Path(self.tmp.name) / "agent_session.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_creates_and_archives_shadow_session(self):
        ok = self.integration.ensure_agent_session()
        self.assertTrue(ok)
        sid = self.integration._agent_session_id
        self.assertIsNotNone(sid)
        self.assertIn(sid, self.integration.bridge.archived)
        # 人设prompt已发一次
        self.assertTrue(any(text for s, text in self.integration.bridge.prompts if s == sid))

    def test_session_id_persisted(self):
        self.integration.ensure_agent_session()
        self.assertTrue(self.integration._agent_session_file.is_file())
        second = DshIntegration(FakeEngine())
        second.bridge = FakeBridge()
        second._agent_session_file = self.integration._agent_session_file
        self.assertTrue(second.ensure_agent_session())
        self.assertEqual(second._agent_session_id, self.integration._agent_session_id)

    def test_set_mode_agent(self):
        self.assertTrue(self.integration.set_mode("agent"))
        self.assertEqual(self.integration.mode, "agent")

    def test_invalid_mode_rejected(self):
        self.assertFalse(self.integration.set_mode("nope"))

    def test_prompt_routes_to_agent_session(self):
        self.integration.set_mode("agent")
        ok = self.integration.prompt_active("在吗")
        self.assertTrue(ok)
        sid = self.integration._agent_session_id
        targets = [text for s, text in self.integration.bridge.prompts if s == sid]
        self.assertTrue(any("在吗" in text for text in targets), targets[-1:] )

    def test_resolve_target_in_agent_mode(self):
        self.integration.set_mode("agent")
        self.assertEqual(self.integration.resolve_prompt_target(), self.integration._agent_session_id)


class ReplyDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.integration = DshIntegration(FakeEngine())
        self.integration.bridge = FakeBridge()
        self.integration._agent_session_file = Path(self.tmp.name) / "agent_session.json"
        self.bubbles: list[tuple[str, str]] = []
        self.integration._bubble = lambda text, duration=4.0, kind="info", force=False: self.bubbles.append((text, kind))
        self.replies: list[str] = []
        self.integration.on_reply = self.replies.append

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_reply_is_bubbled_in_agent_mode(self):
        self.integration.set_mode("agent")
        self.integration._deliver_reply("今天也要加油呀")
        self.assertTrue(self.bubbles)
        self.assertEqual(self.bubbles[-1][1], "reply")

    def test_reply_notifies_callback(self):
        self.integration.set_mode("agent")
        self.integration._deliver_reply("收到")
        self.assertEqual(self.replies, ["收到"])

    def test_empty_reply_ignored(self):
        self.integration.set_mode("agent")
        self.integration._deliver_reply("   ")
        self.assertFalse(self.bubbles)

    def test_long_reply_truncated(self):
        self.integration.set_mode("agent")
        self.integration._deliver_reply("很" * 400)
        self.assertLessEqual(len(self.bubbles[-1][0]), 181)

    def test_link_mode_does_not_bubble_reply(self):
        self.integration.mode = "link"
        self.integration._deliver_reply("只是工作模式")
        self.assertFalse(self.bubbles)
        self.assertEqual(self.replies, ["只是工作模式"])


if __name__ == "__main__":
    unittest.main()


class PanelBusyTests(unittest.TestCase):
    """输入框的"思考中"状态。"""

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest("需要 PySide6")
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def _panel(self):
        from mimi_pet.dsh_panel import DshPanel
        return DshPanel()

    def test_busy_disables_send(self):
        panel = self._panel()
        panel.set_busy(True, "思考中…")
        self.assertFalse(panel.send_btn.isEnabled())
        self.assertFalse(panel.quick_input.isEnabled())
        self.assertEqual(panel.quick_input.placeholderText(), "思考中…")
        self.assertTrue(panel.is_busy())

    def test_busy_clears(self):
        panel = self._panel()
        panel.set_busy(True, "思考中…")
        panel.set_busy(False)
        self.assertTrue(panel.send_btn.isEnabled())
        self.assertTrue(panel.quick_input.isEnabled())
        self.assertFalse(panel.is_busy())

    def test_busy_blocks_duplicate_send(self):
        panel = self._panel()
        sent = []
        panel.send_requested.connect(sent.append)
        panel.set_busy(True, "思考中…")
        panel._quick_send("第二条")
        self.assertEqual(sent, [], "忙碌时不该再发出请求")

    def test_idle_send_emits(self):
        panel = self._panel()
        sent = []
        panel.send_requested.connect(sent.append)
        panel._quick_send("在吗")
        self.assertEqual(sent, ["在吗"])


class PollIntervalTests(unittest.TestCase):
    def test_uses_fast_poll_when_event_stream_missing(self):
        integration = DshIntegration(FakeEngine())
        integration.connection_error  # 触发属性
        integration.link_error = "缺少 websocket-client 依赖"
        self.assertEqual(integration.poll_interval_s(), DshIntegration.POLL_INTERVAL_FALLBACK_S)

    def test_uses_normal_poll_when_healthy(self):
        integration = DshIntegration(FakeEngine())
        integration.link_error = ""
        self.assertEqual(integration.poll_interval_s(), DshIntegration.POLL_INTERVAL_S)


class ReplayGuardTests(unittest.TestCase):
    """重连重放保护：一秒 134 条历史事件会把主线程压死。"""

    def _integration(self):
        from types import SimpleNamespace
        integration = DshIntegration(FakeEngine())
        integration.bridge = FakeBridge()
        self.handled = []
        integration._handle_session_event = lambda payload: self.handled.append(payload)
        integration._accept_event_session = lambda payload: True
        return integration

    def _event(self, session="s1", seq=1, kind="assistant/message"):
        from mimi_pet.dsh_bridge import DshEvent
        return DshEvent(method="session/event", rpc_id="", seq=seq,
                        payload={"sessionId": session, "event": {"type": kind, "data": {}}})

    def test_replay_window_drops_history(self):
        integration = self._integration()
        integration._replay_guard_until = __import__("time").perf_counter() + 5
        integration._handle_event(self._event())
        self.assertEqual(self.handled, [])

    def test_after_window_event_passes(self):
        integration = self._integration()
        integration._replay_guard_until = 0.0
        integration._handle_event(self._event(seq=1))
        self.assertEqual(len(self.handled), 1)

    def test_duplicate_seq_dropped(self):
        integration = self._integration()
        integration._replay_guard_until = 0.0
        integration._handle_event(self._event(seq=7))
        integration._handle_event(self._event(seq=7))
        self.assertEqual(len(self.handled), 1, "同一 seq 只处理一次")

    def test_lower_seq_dropped(self):
        integration = self._integration()
        integration._replay_guard_until = 0.0
        integration._handle_event(self._event(seq=9))
        integration._handle_event(self._event(seq=3))
        self.assertEqual(len(self.handled), 1)

    def test_other_event_types_unaffected(self):
        integration = self._integration()
        integration._replay_guard_until = __import__("time").perf_counter() + 5
        integration._handle_event(self._event(kind="turn/end"))
        self.assertEqual(len(self.handled), 1, "turn/end 不属于重放保护范围")
