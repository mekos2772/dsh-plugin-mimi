"""SMTC session picking and media title cleanup tests (pure logic, no Qt)."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mimi_app" / "src"))

from mimi_pet.smtc_core import STATUS_TEXT, pick_session, pretty_title


class FakeSession:
    """Stand-in for an SMTC session; only what pick_session touches."""

    def __init__(self, aumid: str, title: str, *, raises: bool = False) -> None:
        self.source_app_user_model_id = aumid
        self._props = SimpleNamespace(title=title, artist="artist") if title else None
        self._raises = raises
        self.probe_count = 0

    async def try_get_media_properties_async(self):
        self.probe_count += 1
        if self._raises:
            raise RuntimeError("session died mid-enumeration")
        return self._props


class FakeManager:
    def __init__(self, current, sessions) -> None:
        self._current = current
        self._sessions = list(sessions)

    def get_current_session(self):
        return self._current

    def get_sessions(self):
        return list(self._sessions)


class PickSessionTest(unittest.IsolatedAsyncioTestCase):
    async def test_titled_current_session_wins(self) -> None:
        current = FakeSession("spotify", "晴天")
        other = FakeSession("itunes", "起风了")
        session, props = await pick_session(FakeManager(current, [current, other]))
        self.assertIs(session, current)
        self.assertEqual(props.title, "晴天")

    async def test_titled_other_beats_untitled_current(self) -> None:
        # The zombie case: a paused client with no queue holds the current
        # slot, but what is actually audible must win.
        zombie = FakeSession("stale", "")
        audible = FakeSession("browser", "夜曲")
        session, props = await pick_session(FakeManager(zombie, [zombie, audible]))
        self.assertIs(session, audible)
        self.assertEqual(props.title, "夜曲")

    async def test_untitled_current_is_last_resort(self) -> None:
        zombie = FakeSession("stale", "")
        session, props = await pick_session(FakeManager(zombie, [zombie]))
        self.assertIs(session, zombie)
        self.assertIsNone(props)

    async def test_no_sessions(self) -> None:
        session, props = await pick_session(FakeManager(None, []))
        self.assertIsNone(session)
        self.assertIsNone(props)

    async def test_current_session_is_probed_once(self) -> None:
        # get_sessions() normally repeats the current session; probing it twice
        # would double the cost of every poll.
        current = FakeSession("same", "")
        duplicate = FakeSession("same", "重复会话")
        other = FakeSession("other", "歌")
        session, _ = await pick_session(
            FakeManager(current, [current, duplicate, other])
        )
        self.assertIs(session, other)
        self.assertEqual(current.probe_count, 1)
        self.assertEqual(duplicate.probe_count, 0)

    async def test_a_dead_session_does_not_hide_a_live_one(self) -> None:
        # A player can exit mid-enumeration. That used to raise straight out of
        # the poll loop and freeze the bar on a dead track forever.
        dead = FakeSession("dead", "", raises=True)
        alive = FakeSession("alive", "晴天")
        session, props = await pick_session(FakeManager(dead, [dead, alive]))
        self.assertIs(session, alive)
        self.assertEqual(props.title, "晴天")

    async def test_all_sessions_dead_falls_back_to_the_first(self) -> None:
        dead = FakeSession("dead", "", raises=True)
        session, props = await pick_session(FakeManager(dead, [dead]))
        self.assertIs(session, dead)
        self.assertIsNone(props)


class StatusTextTest(unittest.TestCase):
    def test_playback_status_names_are_stable(self) -> None:
        # media_bar.py branches on the literal "playing" to pick its glyph.
        self.assertEqual(STATUS_TEXT[4], "playing")
        self.assertEqual(STATUS_TEXT[5], "paused")
        self.assertEqual(STATUS_TEXT[3], "stopped")


class PrettyTitleTest(unittest.TestCase):
    def test_strips_player_suffixes(self) -> None:
        cases = {
            "晴天_哔哩哔哩_bilibili": "晴天",
            "晴天 - 网易云音乐": "晴天",
            "晴天 | QQ音乐-千万正版音乐": "晴天",
            "晴天": "晴天",
            "": "",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(pretty_title(raw), expected)


if __name__ == "__main__":
    unittest.main()
