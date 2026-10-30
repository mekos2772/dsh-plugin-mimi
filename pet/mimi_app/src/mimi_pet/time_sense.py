"""时间感知：让 Mimi 知道"现在是什么时候"。

纯函数 + 一个轻量状态机，不依赖 Qt。产出的是**语义信号**，不是具体动作：

* 今天的第一次见面（用于早晨问候，每天只出现一次）
* 离开很久之后的回来（用于"欢迎回来"）
* 当前时段（清晨 / 上午 / 中午 / 下午 / 傍晚 / 深夜）
* 是否周末
* 是否刚跨过整点

把这些和时间写死的行为分开，是为了让行为目录只关心"在什么语义下做什么"，
而不是自己去算小时。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from dataclasses import dataclass, field
from datetime import date, datetime

#: 时段划分（左闭右开）
PHASES = (
    ("dawn", 5, 8),        # 清晨
    ("morning", 8, 11),    # 上午
    ("noon", 11, 14),      # 中午
    ("afternoon", 14, 18), # 下午
    ("evening", 18, 22),   # 傍晚
    ("night", 22, 29),     # 深夜 22:00–05:00
)

PHASE_NAMES = {
    "dawn": "清晨", "morning": "上午", "noon": "中午",
    "afternoon": "下午", "evening": "傍晚", "night": "深夜",
}

#: 离开超过这么久算"回来"，会触发欢迎。
AWAY_THRESHOLD_S = 45 * 60
#: 同一天内两次"见面问候"至少隔这么久。
GREETING_GAP_S = 6 * 3600


def phase_of(hour: int) -> str:
    for name, start, end in PHASES:
        if end > 24:
            if hour >= start or hour < end - 24:
                return name
        elif start <= hour < end:
            return name
    return "night"


@dataclass
class TimeSignals:
    """一次时间判断的输出。"""

    phase: str = "morning"
    is_weekend: bool = False
    first_seen_today: bool = False
    welcome_back: bool = False
    away_s: float = 0.0
    hour_changed: bool = False
    hour: int = 0
    minute: int = 0


@dataclass
class TimeSense:
    """跟踪"上次见到用户是什么时候"，产出时间语义信号。"""

    clock: callable = time.time
    #: 本地日期 → 上次问候时间戳
    _last_greeting: dict[str, float] = field(default_factory=dict)
    _last_seen: float = 0.0
    _last_hour: int = -1
    _started: bool = False
    #: 可选：把状态写到这里，避免每次启动都重新"今天第一次见"
    state_path: str = ""

    def __post_init__(self) -> None:
        if not self.state_path:
            self.state_path = os.environ.get("MIMI_TIME_PATH", "")
        if self.state_path:
            self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(Path(self.state_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        greetings = raw.get("last_greeting")
        if isinstance(greetings, dict):
            self._last_greeting = {
                str(k): float(v) for k, v in greetings.items()
                if isinstance(v, (int, float))
            }
        last_seen = raw.get("last_seen")
        if isinstance(last_seen, (int, float)):
            self._last_seen = float(last_seen)

    def _save(self) -> None:
        if not self.state_path:
            return
        try:
            path = Path(self.state_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps({
                "last_greeting": self._last_greeting,
                "last_seen": self._last_seen,
            }, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, path)
        except OSError:
            pass

    def _local(self, ts: float) -> datetime:
        return datetime.fromtimestamp(ts)

    def tick(self, now: float | None = None) -> TimeSignals:
        now = self.clock() if now is None else now
        moment = self._local(now)
        today = moment.date().isoformat()

        signals = TimeSignals(
            phase=phase_of(moment.hour),
            is_weekend=moment.weekday() >= 5,
            hour=moment.hour,
            minute=moment.minute,
        )

        # 第一次运行：不问候，先记下时间，避免一启动就"早上好"。
        if not self._started:
            self._started = True
            self._last_seen = now
            self._last_hour = moment.hour
            return signals

        away = max(0.0, now - self._last_seen)
        signals.away_s = away
        if away >= AWAY_THRESHOLD_S:
            signals.welcome_back = True

        last_today = self._last_greeting.get(today)
        if last_today is None or now - last_today >= GREETING_GAP_S:
            # 只有白天才问候；深夜"早上好"很怪。
            if 6 <= moment.hour < 23:
                signals.first_seen_today = True
                self._last_greeting[today] = now

        if moment.hour != self._last_hour:
            signals.hour_changed = True
            self._last_hour = moment.hour

        self._last_seen = now
        self._save()
        # 只保留最近 7 天的问候记录，避免字典无限增长。
        if len(self._last_greeting) > 7:
            keep = sorted(self._last_greeting)[-7:]
            self._last_greeting = {k: self._last_greeting[k] for k in keep}
        return signals

    def note_activity(self, now: float | None = None) -> None:
        """用户有交互时调用，用来算"离开多久"。"""
        self._last_seen = self.clock() if now is None else now
