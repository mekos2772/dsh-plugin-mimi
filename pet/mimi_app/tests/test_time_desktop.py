"""时间感知 / 桌面感知 / 任务状态条的测试。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "pet" / "mimi_app" / "src"))

from mimi_pet.time_sense import TimeSense, phase_of


class PhaseTests(unittest.TestCase):
    def test_phase_boundaries(self):
        self.assertEqual(phase_of(5), "dawn")
        self.assertEqual(phase_of(7), "dawn")
        self.assertEqual(phase_of(8), "morning")
        self.assertEqual(phase_of(11), "noon")
        self.assertEqual(phase_of(14), "afternoon")
        self.assertEqual(phase_of(18), "evening")
        self.assertEqual(phase_of(22), "night")
        self.assertEqual(phase_of(2), "night")

    def test_every_hour_has_phase(self):
        for hour in range(24):
            self.assertTrue(phase_of(hour))


class TimeSenseTests(unittest.TestCase):
    def _at(self, y, m, d, hh, mm=0):
        from datetime import datetime
        return datetime(y, m, d, hh, mm).timestamp()

    def test_first_tick_is_silent(self):
        sense = TimeSense()
        signals = sense.tick(self._at(2026, 10, 29, 10))
        self.assertFalse(signals.first_seen_today, "启动瞬间不该立刻问候")

    def test_greeting_once_per_day(self):
        sense = TimeSense()
        day = self._at(2026, 10, 29, 9)
        sense.tick(day)
        first = sense.tick(day + 5)
        self.assertTrue(first.first_seen_today)
        second = sense.tick(day + 60)
        self.assertFalse(second.first_seen_today)

    def test_no_greeting_at_night(self):
        sense = TimeSense()
        night = self._at(2026, 10, 29, 2)
        sense.tick(night)
        signals = sense.tick(night + 5)
        self.assertFalse(signals.first_seen_today, "深夜不该问候早上好")

    def test_welcome_back_after_threshold(self):
        sense = TimeSense()
        start = self._at(2026, 10, 29, 9)
        sense.tick(start)
        signals = sense.tick(start + 46 * 60)
        self.assertTrue(signals.welcome_back)

    def test_no_welcome_back_for_short_gap(self):
        sense = TimeSense()
        start = self._at(2026, 10, 29, 9)
        sense.tick(start)
        signals = sense.tick(start + 60)
        self.assertFalse(signals.welcome_back)

    def test_hour_changed(self):
        sense = TimeSense()
        start = self._at(2026, 10, 29, 9, 59)
        sense.tick(start)
        signals = sense.tick(self._at(2026, 10, 29, 10, 0))
        self.assertTrue(signals.hour_changed)

    def test_weekend_flag(self):
        sense = TimeSense()
        # 2026-10-31 是周六
        signals = sense.tick(self._at(2026, 10, 31, 10))
        self.assertTrue(signals.is_weekend)

    def test_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "time.json"
            first = TimeSense(state_path=str(path))
            day = self._at(2026, 10, 29, 9)
            first.tick(day)
            first.tick(day + 5)
            self.assertTrue(path.is_file())
            saved = json.loads(path.read_text())
            self.assertIn("2026-10-29", saved["last_greeting"])

            # 新实例读回来：当天不再问候
            second = TimeSense(state_path=str(path))
            second.tick(day + 10)
            signals = second.tick(day + 60)
            self.assertFalse(signals.first_seen_today)


class DesktopSenseTests(unittest.TestCase):
    def test_snapshot_never_raises(self):
        from mimi_pet import desktop_sense
        info = desktop_sense.snapshot()
        self.assertIsNotNone(info)

    def test_cursor_near(self):
        from mimi_pet.desktop_sense import DesktopInfo, is_cursor_near
        info = DesktopInfo(cursor=(100, 100))
        self.assertTrue(is_cursor_near(info, (80, 80, 200, 200), margin=10))
        self.assertFalse(is_cursor_near(info, (900, 900, 100, 100), margin=10))

    def test_cursor_near_without_cursor(self):
        from mimi_pet.desktop_sense import DesktopInfo, is_cursor_near
        self.assertFalse(is_cursor_near(DesktopInfo(), (0, 0, 10, 10)))


class TaskBarStateTests(unittest.TestCase):
    def test_visibility_rules(self):
        from mimi_pet.task_bar import BarState
        self.assertFalse(BarState().visible)
        self.assertTrue(BarState(title="写文档").visible)
        self.assertTrue(BarState(focus_label="专注 12:00").visible)

    def test_progress_ratio_bounds(self):
        from mimi_pet.task_bar import BarState
        state = BarState(title="x", done=5, total=3)
        self.assertGreater(state.done, state.total)  # 渲染时会被 clamp


if __name__ == "__main__":
    unittest.main()
