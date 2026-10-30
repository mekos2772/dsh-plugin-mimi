from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .action_library import ActionSpec


@dataclass(frozen=True)
class FrameSample:
    path: Path
    index: int
    finished: bool


class FramePlayer:
    """Time-based player that preserves every authored frame duration."""

    def __init__(self) -> None:
        self.action: ActionSpec | None = None
        self.elapsed_ms = 0.0
        self._speed = 1.0

    #: 一次性动作的兜底上限。素材尾部静止帧已裁掉（动作主体结束就切），
    #: 所以正常情况下**一律按素材标称时长原速播放**——不同动作本来长短
    #: 不一，强行拉齐反而让短的拖、长的抽搐。
    #: 这里只是防御：真出现异常长的动作（>8s）才整体加速，避免桌宠像死机。
    MAX_ONESHOT_MS = 8000.0

    def play(self, action: ActionSpec) -> None:
        self.action = action
        self.elapsed_ms = 0.0
        total = float(action.total_duration_ms or 0.0)
        self._speed = 1.0
        if not action.loop and total > self.MAX_ONESHOT_MS > 0:
            self._speed = total / self.MAX_ONESHOT_MS

    def stop(self) -> None:
        self.action = None
        self.elapsed_ms = 0.0

    def advance(self, delta_ms: float) -> FrameSample | None:
        action = self.action
        if action is None:
            return None
        self.elapsed_ms += max(0.0, delta_ms) * self._speed
        total = action.total_duration_ms
        finished = False
        if action.loop:
            self.elapsed_ms %= total
        elif self.elapsed_ms >= total:
            self.elapsed_ms = float(total)
            finished = True

        cursor = 0
        index = len(action.frames) - 1
        for candidate, duration in enumerate(action.duration_ms):
            cursor += duration
            if self.elapsed_ms < cursor:
                index = candidate
                break
        return FrameSample(action.frames[index], index, finished)
