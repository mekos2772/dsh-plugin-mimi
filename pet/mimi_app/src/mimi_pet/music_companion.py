"""F04 陪我听音乐：在现有媒体控制之上增加「陪着听」这一层。

设计边界（对应 docs/expansion/FEATURE-SPEC.md）：
* 不录音、不做节拍分析、不抓歌词。只读 SMTC 已经给出的标题与播放状态。
* 开关默认关闭，且只在当前桌宠生命周期内有效；重启不会静默重新进入。
* 默认使用静态陪听（不叠耳机、不做摆动），动画素材是否可用由造型决定。
* 关闭跟听不会暂停用户的播放器。

Qt-free：SMTC 读取由 media.py 注入，这里只做状态机与节流。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

STATE_OFF = "off"
STATE_WAITING = "waiting"
STATE_ENTERING = "entering"
STATE_LISTENING = "listening"
STATE_EXITING = "exiting"

#: 播放状态需要稳定这么久才重新进入，避免暂停/播放抖动反复触发。
STABLE_S = 2.0
#: 暂停多久后停止摆动并收起临时装饰。
PAUSE_GRACE_S = 5.0
#: 曲目变化合并窗口：频繁换歌不重复播入场动作。
TITLE_MERGE_S = 2.0

STRENGTH_GENTLE = "gentle"
STRENGTH_STATIC = "static"
STRENGTHS = (STRENGTH_GENTLE, STRENGTH_STATIC)


def _now() -> float:
    return time.monotonic()


@dataclass
class MusicState:
    enabled: bool = False
    state: str = STATE_OFF
    strength: str = STRENGTH_STATIC
    title: str = ""
    playing: bool = False
    source: str = ""
    entered_at: float = 0.0
    last_change_at: float = 0.0
    paused_at: float = 0.0


class MusicCompanion:
    """跟听状态机。

    调用方每帧喂入 SMTC 的当前快照（``update``），再读 ``motion`` 决定
    要不要让角色轻摆。没有素材时把 ``strength`` 保持 static 即可。
    """

    def __init__(self, *, clock=_now) -> None:
        self.clock = clock
        self.state = MusicState()

    # ------------------------------------------------------------ 开关

    @property
    def enabled(self) -> bool:
        return self.state.enabled

    def toggle(self, on: bool | None = None) -> tuple[bool, str]:
        target = (not self.state.enabled) if on is None else bool(on)
        if target == self.state.enabled:
            return True, ""
        self.state.enabled = target
        self.state.last_change_at = self.clock()
        if not target:
            self.state.state = STATE_EXITING
            self.state.entered_at = 0.0
            self.state.state = STATE_OFF
            return True, "好，先不跟着听了。"
        self.state.state = STATE_WAITING
        return True, "陪你听一会儿。放首歌吧。"

    def set_strength(self, strength: str) -> tuple[bool, str]:
        if strength not in STRENGTHS:
            return False, "没有这个强度。"
        self.state.strength = strength
        return True, ("轻轻陪听。" if strength == STRENGTH_GENTLE else "静态陪听，不动。")

    # ------------------------------------------------------------ 每帧

    def update(self, *, title: str, playing: bool, source: str = "") -> None:
        """喂入播放器快照。任何异常都视为“没有在播放”。"""
        now = self.clock()
        title = (title or "").strip()
        playing = bool(playing)

        if not self.state.enabled:
            self.state.state = STATE_OFF
            return

        if not playing or not title:
            if self.state.state in (STATE_LISTENING, STATE_ENTERING):
                if not self.state.paused_at:
                    self.state.paused_at = now
                elif now - self.state.paused_at >= PAUSE_GRACE_S:
                    # 暂停够久：停止摆动、收起临时装饰，但保留用户"想跟听"的偏好。
                    self.state.state = STATE_WAITING
                    self.state.entered_at = 0.0
            else:
                self.state.state = STATE_WAITING
            self.state.playing = False
            self.state.title = title
            return

        self.state.paused_at = 0.0
        self.state.playing = True
        self.state.source = source

        if title != self.state.title:
            # 换曲只更新标题；入场动作不重播。
            self.state.title = title
            self.state.last_change_at = now

        if self.state.state in (STATE_WAITING, STATE_EXITING):
            if now - self.state.last_change_at >= STABLE_S:
                self.state.state = STATE_ENTERING
                self.state.entered_at = now
        elif self.state.state == STATE_ENTERING:
            if now - self.state.entered_at >= 1.0:
                self.state.state = STATE_LISTENING
        elif self.state.state == STATE_LISTENING:
            pass

    # ------------------------------------------------------------ 表现

    @property
    def listening(self) -> bool:
        return self.state.state == STATE_LISTENING

    def motion(self, reduced_motion: bool = False) -> dict:
        """给渲染层的轻摆参数。没有素材时返回 ``enabled=False``。"""
        gentle = (
            self.listening
            and self.state.strength == STRENGTH_GENTLE
            and not reduced_motion
        )
        return {
            "enabled": gentle,
            "amplitude_deg": 1.6 if gentle else 0.0,
            "period_s": 2.4,
        }

    def summary(self) -> dict:
        return {
            "enabled": self.state.enabled,
            "state": self.state.state,
            "strength": self.state.strength,
            "title": self.state.title,
            "playing": self.state.playing,
        }
