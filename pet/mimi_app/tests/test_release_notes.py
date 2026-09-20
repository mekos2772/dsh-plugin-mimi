"""Release announcement: shown once per update, then kept in the notice history."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mimi_app" / "src"))

from mimi_pet.companion import CompanionService  # noqa: E402
from mimi_pet.companion_store import CompanionStore  # noqa: E402
from mimi_pet.release_notes import (  # noqa: E402
    RELEASE_NOTES,
    announcement_bubble,
    announcement_for,
    load_last_seen,
    notes_between,
    save_last_seen,
    take_announcement,
)

LATEST = RELEASE_NOTES[-1][0]
OLDEST = RELEASE_NOTES[0][0]


class AnnouncementTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "app_state.json"

    def test_first_run_says_nothing_but_remembers_the_version(self) -> None:
        self.assertIsNone(take_announcement(LATEST, self.path))
        self.assertEqual(load_last_seen(self.path), LATEST)

    def test_same_version_does_not_announce_twice(self) -> None:
        take_announcement(LATEST, self.path)
        self.assertIsNone(take_announcement(LATEST, self.path))

    def test_update_announces_what_changed(self) -> None:
        save_last_seen(OLDEST, self.path)
        text = take_announcement(LATEST, self.path)
        self.assertIsNotNone(text)
        assert text is not None
        self.assertIn(f"v{LATEST}", text)
        for line in RELEASE_NOTES[-1][1]:
            self.assertIn(line, text)
        # The version just shown is remembered, so it only appears once.
        self.assertEqual(load_last_seen(self.path), LATEST)

    def test_skipped_releases_are_all_listed(self) -> None:
        save_last_seen("0.0.1", self.path)
        text = take_announcement(LATEST, self.path)
        assert text is not None
        for _, notes in RELEASE_NOTES:
            for line in notes:
                self.assertIn(line, text)

    def test_unknown_version_still_reports_the_new_number(self) -> None:
        # Nothing in the table sits between the newest entry and 9.9.9, so the
        # announcement falls back to the headline alone.
        save_last_seen(LATEST, self.path)
        text = take_announcement("9.9.9", self.path)
        self.assertEqual(text, "Mimi 已更新到 v9.9.9。")

    def test_unreadable_state_is_treated_as_a_first_run(self) -> None:
        self.path.write_text("{ not json", encoding="utf-8")
        self.assertIsNone(take_announcement(LATEST, self.path))
        self.assertEqual(load_last_seen(self.path), LATEST)

    def test_notes_between_ignores_future_entries(self) -> None:
        self.assertEqual(notes_between(LATEST, OLDEST), [])

    def test_bubble_points_at_the_panel(self) -> None:
        line = announcement_bubble(LATEST)
        self.assertIn(f"v{LATEST}", line)
        self.assertIn("最近通知", line)

    def test_announcement_lists_every_bullet(self) -> None:
        text = announcement_for("0.0.1", LATEST)
        self.assertEqual(text.count("·"), sum(len(notes) for _, notes in RELEASE_NOTES))


class AnnouncementNoticeTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = CompanionStore(Path(directory.name) / "companion.json")
        self.service = CompanionService(self.store, wall_clock=lambda: 1000.0, monotonic=lambda: 5.0)

    def test_announcement_is_history_only(self) -> None:
        self.assertTrue(self.service.add_announcement("Mimi 已更新到 v9.9.9。", "update-9.9.9"))
        notice = self.service.notices[-1]
        self.assertEqual(notice.kind, "update")
        self.assertTrue(notice.delivered)
        # Never re-bubbled by the reminder path.
        self.assertEqual(self.service.pending_notices, [])
        self.assertIsNone(self.service.notification_batch())

    def test_same_version_never_duplicates(self) -> None:
        self.service.add_announcement("一次", "update-9.9.9")
        self.service.add_announcement("一次", "update-9.9.9")
        self.assertEqual(len(self.service.notices), 1)

    def test_empty_text_is_ignored(self) -> None:
        self.assertFalse(self.service.add_announcement("   "))
        self.assertEqual(self.service.notices, [])

    def test_update_notice_survives_a_restart(self) -> None:
        self.service.add_announcement("Mimi 已更新到 v9.9.9。", "update-9.9.9")
        self.assertTrue(self.service.save())
        reloaded = CompanionService(CompanionStore(self.store.path))
        self.assertEqual([item.kind for item in reloaded.notices], ["update"])
        self.assertEqual(reloaded.notices[0].text, "Mimi 已更新到 v9.9.9。")
        # A malformed kind is still rejected, so the extension did not widen too far.
        self.store.path.write_text(
            json.dumps({"schema_version": 1, "notices": [
                {"id": "x", "text": "t", "created_at": 1.0, "kind": "bogus", "delivered": True}
            ]}), encoding="utf-8",
        )
        quarantined = CompanionService(CompanionStore(self.store.path))
        self.assertEqual(quarantined.notices, [])


if __name__ == "__main__":
    unittest.main()
