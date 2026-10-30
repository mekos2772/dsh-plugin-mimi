"""今日统计、系统状态、状态栏交互的测试。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "pet" / "mimi_app" / "src"))

from mimi_pet.daily_stats import DayStats, StatsTracker, format_duration
from mimi_pet.system_sense import SystemState, TransitionDetector


class DurationTests(unittest.TestCase):
    def test_format(self):
        self.assertEqual(format_duration(0), "0m")
        self.assertEqual(format_duration(59), "0m")
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(3600), "1h")
        self.assertEqual(format_duration(3600 + 15 * 60), "1h15m")


class StatsTests(unittest.TestCase):
    def test_tick_accumulates(self):
        tracker = StatsTracker()
        tracker.tick(60, active=True, focusing=False)
        tracker.tick(30, active=False, focusing=True)
        self.assertAlmostEqual(tracker.today.pet_seconds, 90)
        self.assertAlmostEqual(tracker.today.active_seconds, 60)
        self.assertAlmostEqual(tracker.today.focus_seconds, 30)

    def test_sentence(self):
        tracker = StatsTracker()
        tracker.tick(3600, active=True, focusing=False)
        tracker.note_task_done(3)
        text = tracker.sentence()
        self.assertIn("今天陪了你 1h", text)
        self.assertIn("完成 1 个任务", text)

    def test_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "stats.json")
            first = StatsTracker(path=path)
            first.tick(120, active=True, focusing=False)
            first.note_note()
            first.flush()
            second = StatsTracker(path=path)
            self.assertAlmostEqual(second.today.pet_seconds, 120)
            self.assertEqual(second.today.notes_added, 1)

    def test_streak_counts_consecutive_days(self):
        tracker = StatsTracker()
        tracker.history = [
            DayStats(day="2026-10-27", pet_seconds=100),
            DayStats(day="2026-10-28", pet_seconds=100),
        ]
        tracker.today = DayStats(day="2026-10-29", pet_seconds=100)
        self.assertEqual(tracker.streak_days(), 3)

    def test_streak_breaks_on_gap(self):
        tracker = StatsTracker()
        tracker.history = [DayStats(day="2026-10-20", pet_seconds=100)]
        tracker.today = DayStats(day="2026-10-29", pet_seconds=100)
        self.assertEqual(tracker.streak_days(), 1)

    def test_short_sessions_do_not_count_for_streak(self):
        tracker = StatsTracker()
        tracker.history = [DayStats(day="2026-10-28", pet_seconds=5)]
        tracker.today = DayStats(day="2026-10-29", pet_seconds=100)
        self.assertEqual(tracker.streak_days(), 1)


class SystemSenseTests(unittest.TestCase):
    def test_low_battery_requires_battery_power(self):
        self.assertFalse(SystemState(battery_percent=10, on_battery=False).low_battery)
        self.assertTrue(SystemState(battery_percent=10, on_battery=True).low_battery)
        self.assertFalse(SystemState(battery_percent=80, on_battery=True).low_battery)

    def test_transitions_fire_once(self):
        detector = TransitionDetector()
        detector.update(SystemState(battery_percent=80, on_battery=True, network_online=True))
        events = detector.update(SystemState(battery_percent=15, on_battery=True, network_online=True))
        self.assertIn("low_battery", events)
        again = detector.update(SystemState(battery_percent=14, on_battery=True, network_online=True))
        self.assertNotIn("low_battery", again)

    def test_network_transitions(self):
        detector = TransitionDetector()
        detector.update(SystemState(network_online=True))
        offline = detector.update(SystemState(network_online=False))
        self.assertIn("offline", offline)
        online = detector.update(SystemState(network_online=True))
        self.assertIn("online", online)

    def test_sense_poll_never_raises(self):
        from mimi_pet.system_sense import SystemSense
        state = SystemSense().poll()
        self.assertIsInstance(state, SystemState)


class TaskBarInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest("需要 PySide6")
        cls.app = QApplication.instance() or QApplication([])

    def test_minimal_toggle_changes_height(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from mimi_pet.task_bar import BarState, TaskStatusBar
        bar = TaskStatusBar()
        bar.set_state(BarState(title="写文档", step="写正文", done=1, total=3))
        normal = bar.height()
        bar.set_minimal(True)
        self.assertLess(bar.height(), normal)
        bar.set_minimal(False)
        self.assertEqual(bar.height(), normal)

    def test_position_persistence(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MIMI_STATUSBAR_POS"] = str(Path(tmp) / "pos.json")
            try:
                from mimi_pet.task_bar import BarState, TaskStatusBar
                bar = TaskStatusBar()
                bar.set_state(BarState(title="x"))
                bar.move(321, 123)
                bar._save_pos()
                again = TaskStatusBar()
                self.assertIsNotNone(again._saved_pos)
                self.assertEqual((again._saved_pos.x(), again._saved_pos.y()), (321, 123))
            finally:
                os.environ.pop("MIMI_STATUSBAR_POS", None)


if __name__ == "__main__":
    unittest.main()
