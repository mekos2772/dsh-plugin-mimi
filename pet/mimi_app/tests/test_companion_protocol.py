"""DSH panel actions, IPC deduplication and shutdown without a Qt window."""

import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mimi_pet.companion import CompanionService
from mimi_pet.companion_ipc import MAX_LINE, MAX_OUTBOX, CompanionPipe
from mimi_pet.companion_protocol import CompanionActions


class CompanionProtocolTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_800_000_000.0
        self.mono = 100.0
        self.service = CompanionService(wall_clock=lambda: self.now, monotonic=lambda: self.mono)
        self.actions = CompanionActions(self.service, default_minutes=40)

    def test_panel_can_start_pause_resume_and_restore_quiet(self):
        self.actions.execute({"op": "focus.start"})
        self.assertEqual(self.service.focus.total_s, 2400)
        self.assertTrue(self.actions.snapshot()["quiet"])
        self.mono += 5
        self.actions.execute({"op": "focus.pause"})
        self.assertEqual(self.service.focus.remaining_s, 2395)
        self.assertFalse(self.actions.snapshot()["quiet"])
        self.actions.execute({"op": "focus.resume"})
        self.actions.execute({"op": "focus.cancel"})
        self.assertEqual(self.service.focus.status, "cancelled")

    def test_panel_reminder_lifecycle_uses_ids(self):
        self.actions.execute({"op": "reminder.add", "text": "喝水", "due_at": self.now + 60})
        identifier = self.service.active_reminders[0].id
        self.actions.execute({"op": "reminder.update", "id": identifier, "text": "补水"})
        self.actions.execute({"op": "reminder.snooze", "id": identifier})
        self.assertEqual(self.service.active_reminders[0].due_at, self.now + 600)
        self.actions.execute({"op": "reminder.update", "id": identifier, "status": "done"})
        self.assertEqual(self.actions.snapshot()["reminders"], [])

    def test_bad_structured_values_do_not_mutate_state(self):
        self.actions.execute({"op": "reminder.add", "text": "喝水", "due_at": self.now + 60})
        identifier = self.service.active_reminders[0].id
        before = self.service.to_payload()
        for action in (
            {"op": "focus.start", "minutes": True},
            {"op": "focus.start", "minutes": 1.5},
            {"op": "focus.start", "minutes": 241},
            {"op": "quiet.set", "enabled": "false"},
            {"op": "reminder.add", "text": [], "due_at": self.now + 60},
            {"op": "reminder.add", "text": "foo", "due_at": float("nan")},
            {"op": "reminder.update", "id": identifier, "due_at": None},
            {"op": "reminder.update", "id": identifier, "status": []},
            {"op": "reminder.update", "id": identifier, "text": None},
            {"op": "reminder.update", "id": identifier},
            {"op": "notices.clear", "extra": "ignored?"},
            {"op": ["focus.start"]},
            {"op": "__dict__"},
        ):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.actions.execute(action)
            self.assertEqual(self.service.to_payload(), before)

    def test_command_input_clarifies_ambiguous_time(self):
        with self.assertRaisesRegex(ValueError, "明确时间"):
            self.actions.execute({"op": "command", "text": "下午提醒我喝水"})
        self.assertEqual(self.service.reminders, [])
        self.actions.execute({"op": "command", "text": "开始专注"})
        self.assertEqual(self.service.focus.total_s, 2400)

    def test_snapshot_is_a_copy_and_show_is_navigation_only(self):
        self.actions.execute({"op": "focus.start"})
        snapshot = self.actions.snapshot()
        snapshot["focus"]["remaining_s"] = 0
        self.assertEqual(self.service.focus.remaining_s, 2400)
        result = self.actions.execute({"op": "command", "text": "查看提醒"})
        self.assertEqual(result["section"], "reminders")

    def test_disabled_actions_reject_without_a_timer(self):
        disabled = CompanionActions(self.service, enabled=False)
        with self.assertRaises(ValueError):
            disabled.execute({"op": "focus.start"})
        self.assertIsNone(self.service.focus)
        self.assertFalse(disabled.snapshot()["enabled"])

    def test_pipe_replay_does_not_create_duplicate_reminders(self):
        pipe = CompanionPipe(self.actions, lambda: None, reader=io.StringIO(), writer=io.StringIO())
        command = {"type": "action", "id": "request-reminder-1", "action": {
            "op": "reminder.add", "text": "喝水", "due_at": self.now + 60,
        }}
        pipe.commands.put(command)
        pipe.commands.put(command)
        self.assertEqual(self.service.reminders, [])  # reader never runs domain actions
        pipe.drain()
        self.assertEqual(len(self.service.reminders), 1)
        responses = list(pipe._outbox)
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0], responses[1])
        self.assertTrue(responses[0]["result"]["ok"])

    def test_pipe_validation_failure_does_not_block_following_action(self):
        pipe = CompanionPipe(self.actions, lambda: None, reader=io.StringIO(), writer=io.StringIO())
        pipe.commands.put({"type": "action", "id": "request-bad-1", "action": {"op": "quiet.set", "enabled": None}})
        pipe.commands.put({"type": "action", "id": "request-good-1", "action": {"op": "focus.start", "minutes": 25}})
        pipe.drain()
        responses = list(pipe._outbox)
        self.assertFalse(responses[0]["result"]["ok"])
        self.assertTrue(responses[1]["result"]["ok"])
        self.assertTrue(self.service.running)

    def test_host_shutdown_or_pipe_loss_requests_app_exit(self):
        exits = []
        pipe = CompanionPipe(self.actions, lambda: exits.append(True), reader=io.StringIO(), writer=io.StringIO())
        pipe.commands.put({"type": "shutdown"})
        pipe.drain()
        self.assertEqual(exits, [True])
        pipe._parent_gone.set()
        pipe.drain()
        self.assertEqual(exits, [True, True])


class PipeResilienceTests(unittest.TestCase):
    """Back-pressure and one-off protocol errors must not kill the pet.

    ``_parent_gone`` means "the host closed the pipe", and ``drain`` turns it
    into ``app.quit``. A slow reader and an oversized frame are neither, so
    both used to make the pet exit itself — the first on nothing worse than the
    host falling behind for a moment.
    """

    def setUp(self) -> None:
        self.service = CompanionService(
            wall_clock=lambda: 1_800_000_000.0, monotonic=lambda: 100.0
        )
        self.actions = CompanionActions(self.service, default_minutes=40)

    def _pipe(self, reader=None, writer=None, exits=None):
        return CompanionPipe(
            self.actions,
            (lambda: exits.append(True)) if exits is not None else (lambda: None),
            reader=reader if reader is not None else io.StringIO(),
            writer=writer if writer is not None else io.StringIO(),
        )

    def test_a_full_outbox_drops_frames_instead_of_quitting(self) -> None:
        exits: list[bool] = []
        pipe = self._pipe(exits=exits)
        for sequence in range(MAX_OUTBOX * 3):
            pipe._enqueue({"type": "state", "sequence": sequence})
        pipe.drain()
        self.assertEqual(exits, [])
        self.assertFalse(pipe._parent_gone.is_set())
        # Bounded, and the newest frame survived: stale snapshots go first.
        self.assertEqual(len(pipe._outbox), MAX_OUTBOX)
        self.assertEqual(pipe._outbox[-1]["sequence"], MAX_OUTBOX * 3 - 1)
        self.assertEqual(pipe._outbox[0]["sequence"], MAX_OUTBOX * 2)

    def test_an_oversized_frame_is_skipped_not_fatal(self) -> None:
        huge = "MIMI/1 " + "x" * (MAX_LINE + 10) + "\n"
        good = "MIMI/1 " + json.dumps(
            {"type": "action", "id": "ok-1", "action": {"op": "focus.cancel"}}
        ) + "\n"
        pipe = self._pipe(reader=io.StringIO(huge + good))
        pipe._read()
        self.assertEqual(pipe.commands.qsize(), 1)
        self.assertEqual(pipe.commands.get_nowait()["id"], "ok-1")

    def test_an_oversized_line_that_ends_cleanly_does_not_eat_the_next_frame(self) -> None:
        # Exactly one character past the cap, and terminated. readline stops at
        # the cap, so "did it end with a newline" is what tells a truncated line
        # apart from a complete one — get this wrong and the tail discard
        # swallows the frame that follows.
        oversized = "MIMI/1 " + "x" * (MAX_LINE - 7) + "\n"
        self.assertEqual(len(oversized), MAX_LINE + 1)
        good = "MIMI/1 " + json.dumps({"type": "shutdown"}) + "\n"
        pipe = self._pipe(reader=io.StringIO(oversized + good))
        pipe._read()
        self.assertEqual(pipe.commands.qsize(), 1)
        self.assertEqual(pipe.commands.get_nowait()["type"], "shutdown")

    def test_an_unserialisable_frame_does_not_kill_the_writer(self) -> None:
        writer = io.StringIO()
        pipe = self._pipe(writer=writer)
        pipe._closed.set()  # the writer returns once the backlog is drained
        pipe._outbox.append({"type": "state", "bad_set": {1, 2}})
        pipe._outbox.append({"type": "state", "bad_nan": float("nan")})
        pipe._outbox.append({"type": "state", "good": 1})
        pipe._write()
        self.assertFalse(pipe._parent_gone.is_set())
        # The two bad frames were dropped and the healthy one still went out.
        self.assertIn('"good"', writer.getvalue())
        self.assertNotIn("bad_", writer.getvalue())

    def test_a_broken_pipe_still_requests_exit(self) -> None:
        class BrokenWriter(io.StringIO):
            def write(self, _text: str) -> int:
                raise OSError("pipe closed")

        exits: list[bool] = []
        pipe = self._pipe(writer=BrokenWriter(), exits=exits)
        pipe._outbox.append({"type": "state"})
        pipe._write()  # the OSError ends the loop on its own
        self.assertTrue(pipe._parent_gone.is_set())
        pipe.drain()
        self.assertEqual(exits, [True])

    def test_stdin_eof_still_requests_exit(self) -> None:
        pipe = self._pipe(reader=io.StringIO(""))
        pipe._read()
        self.assertTrue(pipe._parent_gone.is_set())


if __name__ == "__main__":
    unittest.main()
