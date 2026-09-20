"""User-visible time, recovery, notification and command-routing contracts."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mimi_app" / "src"))

from mimi_pet.companion import CompanionService, MAX_HISTORY, MAX_REMINDERS
from mimi_pet.companion_commands import parse_command
from mimi_pet.companion_store import CompanionStore


class Clock:
    def __init__(self) -> None:
        self.wall = datetime(2026, 9, 14, 12).timestamp()
        self.mono = 1000.0

    def jump(self, seconds: float) -> None:
        self.wall += seconds
        self.mono += seconds


class CompanionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.service = self.make_service()

    def make_service(self, store=None) -> CompanionService:
        return CompanionService(store, wall_clock=lambda: self.clock.wall, monotonic=lambda: self.clock.mono)

    def advance(self, seconds: int) -> None:
        for _ in range(seconds):
            self.clock.jump(1)
            self.service.tick()

    def test_pause_resume_counts_only_running_time(self) -> None:
        self.service.start_focus(25)
        self.advance(65)
        self.service.pause_focus()
        remaining = self.service.focus.remaining_s
        self.clock.jump(7 * 3600)
        self.service.tick()
        self.assertEqual(self.service.focus.remaining_s, remaining)
        self.service.resume_focus()
        self.advance(10)
        self.assertEqual(self.service.focus.remaining_s, 25 * 60 - 75)

    def test_completion_is_single_and_offers_a_separate_break(self) -> None:
        self.service.start_focus(1)
        self.assertTrue(self.service.quiet)
        self.advance(62)
        self.assertEqual(self.service.focus.status, "finished")
        self.assertFalse(self.service.quiet)
        self.assertEqual(len(self.service.notices), 1)
        self.service.start_focus(5, kind="break")
        self.assertEqual(self.service.focus.kind, "break")
        self.assertEqual(self.service.focus.remaining_s, 300)

    def test_cancel_does_not_produce_completion(self) -> None:
        self.service.start_focus(1)
        self.advance(5)
        self.service.cancel_focus()
        self.advance(70)
        self.assertEqual(self.service.notices, [])
        self.assertEqual(self.service.focus.status, "cancelled")

    def test_duration_bounds_and_replacing_an_active_timer(self) -> None:
        for minutes in (0, 241, -1, True, 2.5):
            with self.subTest(minutes=minutes), self.assertRaises(ValueError):
                self.service.start_focus(minutes)
        self.service.start_focus(1)
        with self.assertRaises(ValueError):
            self.service.start_focus(2)
        self.service.pause_focus()
        with self.assertRaises(ValueError):
            self.service.start_focus(2)

    def test_quiet_setting_restores_after_focus(self) -> None:
        self.service.set_quiet(True)
        self.service.start_focus(1)
        self.service.set_quiet(False)
        self.assertFalse(self.service.quiet)
        self.service.pause_focus()
        self.assertTrue(self.service.quiet)  # original manual setting
        self.service.resume_focus()
        self.assertFalse(self.service.quiet)  # this timer's override
        self.advance(60)
        self.assertTrue(self.service.quiet)

    def test_lock_and_suspend_are_independent_and_never_auto_resume(self) -> None:
        self.service.start_focus(25)
        self.advance(10)
        self.service.system_event("lock", True)
        self.service.system_event("suspend", True)
        remaining = self.service.focus.remaining_s
        self.clock.jump(3600)
        self.service.system_event("suspend", False)
        self.assertTrue(self.service.system_blocked)
        with self.assertRaises(ValueError):
            self.service.resume_focus()
        self.service.system_event("lock", False)
        self.assertEqual(self.service.focus.status, "paused")
        self.assertEqual(self.service.focus.remaining_s, remaining)
        self.assertEqual(len(self.service.notices), 1)
        self.service.resume_focus()
        self.assertTrue(self.service.running)

    def test_missed_suspend_message_pauses_instead_of_counting_gap(self) -> None:
        self.service.start_focus(1)
        self.advance(10)
        self.clock.jump(3600)
        self.service.tick()
        self.assertEqual(self.service.focus.status, "paused")
        self.assertEqual(self.service.focus.remaining_s, 50)
        self.assertEqual(self.service.notices[0].kind, "pause")

    def test_wall_clock_correction_does_not_complete_focus(self) -> None:
        self.service.start_focus(1)
        self.clock.wall += 86400
        self.service.tick()
        self.assertEqual(self.service.focus.remaining_s, 60)
        self.clock.wall -= 2 * 86400
        self.advance(3)
        self.assertEqual(self.service.focus.remaining_s, 57)

    def test_running_timer_restores_paused_without_offline_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CompanionStore(Path(directory) / "companion.json")
            self.service = self.make_service(store)
            self.service.start_focus(25)
            self.advance(16)
            self.service.save()
            self.clock.jump(86400)
            restored = self.make_service(store)
            self.assertEqual(restored.focus.status, "paused")
            self.assertEqual(restored.focus.remaining_s, 25 * 60 - 16)
            restored.tick()
            self.assertFalse(any(n.kind == "focus" for n in restored.notices))

    def test_reminders_batch_after_quiet_and_persist_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CompanionStore(Path(directory) / "companion.json")
            self.service = self.make_service(store)
            self.service.set_quiet(True)
            self.service.add_reminder("喝水", self.clock.wall + 60)
            self.service.add_reminder("伸展", self.clock.wall + 90)
            self.clock.jump(120)
            self.service.tick()
            self.assertIsNone(self.service.notification_batch())
            self.assertEqual(len(self.service.pending_notices), 2)
            self.service.set_quiet(False)
            text, ids = self.service.notification_batch()
            self.assertIn("2 条", text)
            self.assertEqual(len(ids), 2)
            self.service.mark_delivered(ids)
            restored = self.make_service(store)
            restored.tick()
            self.assertIsNone(restored.notification_batch())
            self.assertEqual(len(restored.active_reminders), 2)

    def test_restart_recovers_overdue_once_and_keeps_original_due_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CompanionStore(Path(directory) / "companion.json")
            self.service = self.make_service(store)
            self.service.add_reminder("午饭", self.clock.wall + 60)
            self.clock.jump(3600)
            self.service = self.make_service(store)
            self.service.tick()
            self.service.tick()
            self.assertEqual(len(self.service.notices), 1)
            self.assertIn("12:01", self.service.notices[0].text)

    def test_presentation_waits_for_typing_work_and_unlock(self) -> None:
        self.service.add_reminder("喝水", self.clock.wall + 60)
        self.clock.jump(60)
        self.service.tick()
        self.assertIsNone(self.service.notification_batch(presentation_blocked=True))
        self.assertEqual(len(self.service.pending_notices), 1)
        self.service.system_event("lock", True)
        self.assertIsNone(self.service.notification_batch())
        self.service.system_event("lock", False)
        self.assertIsNotNone(self.service.notification_batch())

    def test_snooze_and_edit_retire_stale_notifications(self) -> None:
        self.service.add_reminder("喝水", self.clock.wall + 60)
        reminder = self.service.active_reminders[0]
        self.clock.jump(60)
        self.service.tick()
        self.service.update_reminder(reminder.id, due_at=self.clock.wall + 600, text="喝茶")
        self.assertEqual(self.service.pending_notices, [])
        self.assertEqual(reminder.occurrence, 1)
        self.clock.jump(600)
        self.service.tick()
        self.assertEqual(len(self.service.pending_notices), 1)
        self.assertIn("喝茶", self.service.pending_notices[0].text)
        self.service.update_reminder(reminder.id, status="done")
        self.assertEqual(self.service.active_reminders, [])
        self.assertEqual(self.service.pending_notices, [])

    def test_cancel_never_fires_and_validation_does_not_partially_edit(self) -> None:
        self.service.add_reminder("取消我", self.clock.wall + 60)
        reminder = self.service.active_reminders[0]
        old_due = reminder.due_at
        with self.assertRaises(ValueError):
            self.service.update_reminder(reminder.id, due_at=self.clock.wall + 100, text="")
        self.assertEqual(reminder.due_at, old_due)
        self.service.update_reminder(reminder.id, status="cancelled")
        self.clock.jump(600)
        self.service.tick()
        self.assertEqual(self.service.notices, [])

    def test_history_clear_does_not_cancel_due_reminder(self) -> None:
        self.service.add_reminder("仍待处理", self.clock.wall + 60)
        self.clock.jump(60)
        self.service.tick()
        self.service.clear_history()
        self.service.tick()
        self.assertEqual(self.service.notices, [])
        self.assertEqual(self.service.active_reminders[0].status, "due")

    def test_capacity_and_same_title_require_explicit_selection(self) -> None:
        for _ in range(MAX_REMINDERS):
            self.service.add_reminder("喝水", self.clock.wall + 60)
        with self.assertRaises(ValueError):
            self.service.add_reminder("更多", self.clock.wall + 60)
        with self.assertRaises(ValueError):
            self.service.find_reminder("喝水")
        self.clock.jump(60)
        self.service.tick()
        self.assertEqual(len(self.service.notices), MAX_HISTORY)

    def test_quiet_keeps_questions_approvals_replies_and_user_feedback(self) -> None:
        self.service.set_quiet(True)
        self.assertFalse(self.service.allows_bubble("tool"))
        for kind in ("question", "assistant", "summary", "companion", "info"):
            self.assertTrue(self.service.allows_bubble(kind), kind)

    def test_invalid_reminder_times_and_text(self) -> None:
        for value in (float("inf"), float("nan"), self.clock.wall - 1, self.clock.wall + 400 * 86400):
            with self.subTest(due_at=value), self.assertRaises(ValueError):
                self.service.add_reminder("喝水", value)
        for text in ("", "  ", "水" * 201):
            with self.assertRaises(ValueError):
                self.service.add_reminder(text, self.clock.wall + 60)
        self.assertEqual(self.service.reminders, [])


class StoreTests(unittest.TestCase):
    def test_bad_data_is_backed_up_without_partial_restore(self) -> None:
        for raw in ("{bad", '{"schema_version":9}', '{"schema_version":1,"reminders":[{}]}'):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "companion.json"
                path.write_text(raw, encoding="utf-8")
                service = CompanionService(CompanionStore(path))
                self.assertEqual(service.reminders, [])
                backups = list(path.parent.glob("*.corrupt-*.json"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_text(encoding="utf-8"), raw)

    def test_failed_atomic_write_preserves_last_valid_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "companion.json"
            store = CompanionStore(path)
            service = CompanionService(store)
            service.set_quiet(True)
            previous = path.read_bytes()
            with patch("mimi_pet.companion_store.os.replace", side_effect=OSError("disk error")):
                service.set_quiet(False)
            self.assertEqual(path.read_bytes(), previous)
            self.assertFalse(service.quiet)
            self.assertIn("保存失败", store.error)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])
            self.assertTrue(service.save())
            self.assertEqual(store.error, "")
            self.assertFalse(json.loads(path.read_text(encoding="utf-8"))["manual_quiet"])

    def test_failed_read_does_not_overwrite_unknown_existing_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "companion.json"
            path.write_text("private original", encoding="utf-8")
            store = CompanionStore(path)
            with patch.object(Path, "read_text", side_effect=PermissionError):
                service = CompanionService(store)
            service.set_quiet(True)
            self.assertEqual(path.read_text(encoding="utf-8"), "private original")
            self.assertFalse(service.save())

    def test_a_transient_read_lock_is_ridden_out(self) -> None:
        # A momentary lock (antivirus, a sync client) used to latch the store
        # shut for the rest of the process: nothing was saved again, including
        # the save on shutdown.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "companion.json"
            store = CompanionStore(path)
            service = CompanionService(store)
            service.set_quiet(True)
            self.assertTrue(service.save())

            original = Path.read_text
            calls = {"count": 0}

            def flaky(self, *args, **kwargs):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise PermissionError("held by another process")
                return original(self, *args, **kwargs)

            with patch.object(Path, "read_text", flaky):
                restored = CompanionService(CompanionStore(path))
            self.assertEqual(calls["count"], 2)
            self.assertTrue(restored.quiet)
            self.assertEqual(restored.store.error, "")
            self.assertFalse(restored.store._write_blocked)

    def test_a_failed_quarantine_is_retried_before_giving_up(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "companion.json"
            path.write_text("{bad", encoding="utf-8")
            store = CompanionStore(path)
            with patch("mimi_pet.companion_store.os.replace", side_effect=OSError("locked")):
                service = CompanionService(store)
                # The known-bad record could not be moved aside, so writing over
                # it would destroy the only copy.
                self.assertTrue(store._write_blocked)
                self.assertFalse(service.save())
                self.assertEqual(path.read_text(encoding="utf-8"), "{bad")

            # Lock gone: the move is retried and saving resumes on its own.
            self.assertTrue(service.save())
            self.assertFalse(store._write_blocked)
            self.assertEqual(store.error, "")
            backups = list(path.parent.glob("*.corrupt-*.json"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "{bad")


class CommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 14, 12).timestamp()

    def parse(self, text):
        return parse_command(text, now=self.now)

    def test_focus_commands_support_digits_and_chinese(self) -> None:
        for text, minutes in (
            ("陪我专注 25 分钟", 25), ("专注二十五分钟", 25),
            ("开始专注１小时！", 60), ("专注", 25), ("休息一下", 5),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.parse(text).minutes, minutes)

    def test_relative_and_absolute_time(self) -> None:
        self.assertEqual(self.parse("20 分钟后提醒我喝水").due_at, self.now + 1200)
        self.assertEqual(self.parse("半小时后提醒我站起来").due_at, self.now + 1800)
        self.assertEqual(self.parse("今天 18:00 提醒我下班").due_at, self.now + 6 * 3600)
        self.assertEqual(self.parse("明天18：00提醒我下班").due_at, self.now + 30 * 3600)
        self.assertEqual(self.parse("11:00提醒我喝水").due_at, self.now + 23 * 3600)
        self.assertEqual(
            self.parse("2026-09-16 18:00 提醒我开会").due_at,
            datetime(2026, 9, 16, 18).timestamp(),
        )

    def test_ambiguous_or_invalid_time_does_not_create_a_reminder(self) -> None:
        for text in (
            "下午提醒我喝水", "提醒我喝水", "今天11:00提醒我喝水",
            "明天25:00提醒我喝水", "2026-02-30 18:00提醒我喝水",
            "0分钟后提醒我喝水", "专注2.5分钟", "专注-5分钟",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.parse(text).kind, "error")

    def test_ordinary_chat_and_work_are_not_local_commands(self) -> None:
        for text in ("你好", "写一个专注25分钟的功能", "帮我解释提醒工具怎么用", "谢谢你陪我专注"):
            self.assertIsNone(self.parse(text), text)


if __name__ == "__main__":
    unittest.main()
