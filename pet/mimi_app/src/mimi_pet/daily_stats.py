"""今日陪伴统计：陪了多久、一起完成了什么。

只累计聚合数字（时长、计数），不记录任何内容文本。
按天保存，保留最近 30 天，供状态栏、悬停气泡和右键菜单使用。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

KEEP_DAYS = 30


@dataclass
class DayStats:
    day: str
    pet_seconds: float = 0.0
    active_seconds: float = 0.0
    focus_seconds: float = 0.0
    tasks_done: int = 0
    steps_done: int = 0
    notes_added: int = 0
    proactive_count: int = 0
    health_done: int = 0


def format_duration(seconds: float) -> str:
    total = int(max(0, seconds))
    minutes = total // 60
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours}h{minutes:02d}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


@dataclass
class StatsTracker:
    path: str = ""
    today: DayStats = field(default_factory=lambda: DayStats(day=date.today().isoformat()))
    history: list[DayStats] = field(default_factory=list)
    _pending_save: float = 0.0
    save_every_s: float = 60.0

    def __post_init__(self) -> None:
        if not self.path:
            self.path = os.environ.get("MIMI_STATS_PATH", "")
        if self.path:
            self._load()

    # ------------------------------------------------------------ 持久化

    def _load(self) -> None:
        try:
            raw = json.loads(Path(self.path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        entries = raw.get("days")
        if not isinstance(entries, list):
            return
        parsed: list[DayStats] = []
        for item in entries:
            try:
                parsed.append(DayStats(**item))
            except (TypeError, ValueError):
                continue
        key = self.today.day
        for stats in parsed:
            if stats.day == key:
                self.today = stats
            else:
                self.history.append(stats)
        self.history = sorted(self.history, key=lambda s: s.day)[-KEEP_DAYS:]

    def _save(self) -> None:
        if not self.path:
            return
        try:
            path = Path(self.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            days = [asdict(s) for s in (self.history + [self.today])]
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps({"days": days}, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, path)
        except OSError:
            pass

    def flush(self) -> None:
        self._save()

    # ------------------------------------------------------------ 累计

    def _roll(self) -> None:
        key = date.today().isoformat()
        if self.today.day == key:
            return
        self.history.append(self.today)
        self.history = sorted(self.history, key=lambda s: s.day)[-KEEP_DAYS:]
        self.today = DayStats(day=key)

    def tick(self, dt_s: float, *, active: bool, focusing: bool) -> None:
        if dt_s <= 0:
            return
        self._roll()
        self.today.pet_seconds += dt_s
        if active:
            self.today.active_seconds += dt_s
        if focusing:
            self.today.focus_seconds += dt_s
        self._pending_save += dt_s
        if self._pending_save >= self.save_every_s:
            self._pending_save = 0.0
            self._save()

    def note_task_done(self, steps: int = 0) -> None:
        self.today.tasks_done += 1
        self.today.steps_done += max(0, steps)
        self._save()

    def note_step_done(self) -> None:
        self.today.steps_done += 1
        self._save()

    def note_note(self) -> None:
        self.today.notes_added += 1
        self._save()

    def note_proactive(self) -> None:
        self.today.proactive_count += 1

    def note_health_done(self) -> None:
        self.today.health_done += 1
        self._save()

    # ------------------------------------------------------------ 展示

    def summary(self) -> dict:
        d = self.today
        return {
            "day": d.day,
            "pet": format_duration(d.pet_seconds),
            "active": format_duration(d.active_seconds),
            "focus": format_duration(d.focus_seconds),
            "tasks": d.tasks_done,
            "steps": d.steps_done,
            "notes": d.notes_added,
            "proactive": d.proactive_count,
            "health": d.health_done,
            "streak": self.streak_days(),
        }

    def sentence(self) -> str:
        s = self.summary()
        parts = [f"今天陪了你 {s['pet']}"]
        if s["focus"] != "0m":
            parts.append(f"专注 {s['focus']}")
        if s["tasks"]:
            parts.append(f"完成 {s['tasks']} 个任务")
        elif s["steps"]:
            parts.append(f"推进 {s['steps']} 步")
        if s["notes"]:
            parts.append(f"记了 {s['notes']} 条便签")
        if s["streak"] > 1:
            parts.append(f"连续 {s['streak']} 天")
        return " · ".join(parts)

    def streak_days(self) -> int:
        days = {s.day for s in self.history if s.pet_seconds > 60}
        days.add(self.today.day)
        ordered = sorted(days)
        streak = 1
        for a, b in zip(ordered, ordered[1:]):
            if _next_day(a) == b:
                streak += 1
            else:
                streak = 1
        return streak


def _next_day(day: str) -> str:
    try:
        return (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    except ValueError:
        return ""
