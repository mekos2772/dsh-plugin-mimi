"""主动交互：让 Mimi 自己决定什么时候做点什么。

设计原则（为什么不是"随机播动画"）：

1. **先看场合**。用户在敲键盘时突然转圈跳舞是打扰；连续两小时没动时
   还在发呆才是"没活着"。所以每个行为都带前提条件。
2. **成套而不是单发**。一次"念头"由 2–4 个动作加停顿组成（抬头 → 伸懒腰 →
   挥手），比随机单帧更像有意图。
3. **有注意力预算**。半小时最多主动几次，且同一套不连续出现；宁可少做，
   也不要让人觉得"这破宠物一直在动"。
4. **永远让位于人**。用户点它、拖它、DSH 有任务时，主动行为立刻退出，
   不抢、不排队、不补播。

Qt-free：这里只产出"下一步该播哪个动作"，播放交给引擎。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable, Iterable

#: 特殊步骤：不以动作库 id 表示，由接入层翻译成引擎调用。
SPECIAL_PREFIX = "@"


def is_special(action: str) -> bool:
    return action.startswith(SPECIAL_PREFIX)


#: 两次主动行为之间至少隔这么久（秒）。
MIN_GAP_S = 90.0
#: 半小时窗口内最多主动几次。
BUDGET_WINDOW_S = 30 * 60
BUDGET_MAX = 4


@dataclass(frozen=True)
class Step:
    """一套行为里的一步。"""

    action: str
    #: 这一步播完后停多久再做下一步（秒）
    pause_s: float = 1.2


@dataclass(frozen=True)
class Routine:
    """一套完整的主动行为。"""

    id: str
    name: str
    steps: tuple[Step, ...]
    #: 触发所需的最短静止时间（秒）；0 表示不要求
    idle_at_least_s: float = 0.0
    #: 该行为自己的冷却（秒）
    cooldown_s: float = 45 * 60
    weight: float = 1.0
    #: 只在这些时段出现（24 小时制，None 表示不限）
    hours: tuple[int, int] | None = None
    #: 需要专注计时进行中
    needs_focus: bool = False
    #: 需要音乐正在播放
    needs_music: bool = False
    #: 只在某些时段出现（见 time_sense.PHASES）
    phases: tuple[str, ...] = ()
    #: 只在周末出现
    weekend_only: bool = False
    #: 需要某个时间信号（first_seen / welcome_back / hour_changed）
    signal: str = ""
    #: 勿扰时是否仍可出现（默认否）
    allow_quiet: bool = False


#: 行为目录。刻意覆盖尽量多的动作，但每套都要求"读起来像一个动作序列"，
#: 而不是把不相关的姿势硬拼在一起。
ROUTINES: tuple[Routine, ...] = (
    Routine(
        id="morning_greet",
        name="早上打招呼",
        hours=(6, 11),
        idle_at_least_s=8,
        weight=1.4,
        steps=(
            Step("look_up_slow", 0.8),
            Step("stretch_hold", 0.6),
            Step("stretch", 0.8),
            Step("wave_both_slow", 0.4),
        ),
    ),
    Routine(
        id="late_night",
        name="犯困",
        hours=(22, 29),       # 22:00–05:00（跨午夜用 >24 表示）
        idle_at_least_s=10,
        weight=1.2,
        steps=(
            Step("yawn", 1.0),
            Step("sleepy_nod_pingpong", 2.0),
            Step("stretch_back", 0.5),
        ),
    ),
    Routine(
        id="sit_too_long",
        name="久坐伸展",
        idle_at_least_s=6,
        cooldown_s=40 * 60,
        weight=1.5,
        steps=(
            Step("sit_stretch_hold", 0.6),
            Step("sit_stretch", 1.0),
            Step("stretch_hold", 0.5),
            Step("stretch", 0.4),
        ),
    ),
    Routine(
        id="curious",
        name="好奇张望",
        idle_at_least_s=5,
        weight=1.0,
        steps=(
            Step("peek_left", 0.9),
            Step("peek_right_reverse", 0.7),
            Step("look_up_slow", 0.8),
            Step("shrug", 0.4),
        ),
    ),
    Routine(
        id="self_amuse",
        name="自娱自乐",
        idle_at_least_s=14,
        cooldown_s=60 * 60,
        weight=0.9,
        steps=(
            Step("spin_cheer", 0.8),
            Step("spin_cheer_slow", 0.9),
            Step("happy_sway", 1.0),
            Step("clap_happy", 0.3),
        ),
    ),
    Routine(
        id="tidy_self",
        name="整理自己",
        idle_at_least_s=7,
        weight=1.1,
        steps=(
            Step("tidy_hair_pingpong", 1.4),
            Step("ribbon_adjust", 0.8),
            Step("tidy_desk_reverse", 0.4),
        ),
    ),
    Routine(
        id="tea_break",
        name="喝口茶",
        idle_at_least_s=9,
        cooldown_s=70 * 60,
        weight=1.0,
        steps=(
            Step("search_pocket", 0.7),
            Step("drink_tea_hold", 0.6),
            Step("drink_tea", 0.5),
        ),
    ),
    Routine(
        id="listen_music",
        name="跟着听",
        needs_music=True,
        idle_at_least_s=3,
        cooldown_s=35 * 60,
        weight=1.6,
        steps=(
            Step("headphone_hold", 0.5),
            Step("headphone", 1.2),
            Step("happy_sway_pingpong", 1.0),
        ),
    ),
    Routine(
        id="greet_user",
        name="打个招呼",
        idle_at_least_s=3,
        cooldown_s=25 * 60,
        weight=1.7,
        steps=(
            Step("wave", 0.7),
            Step("bow", 0.6),
            Step("nod_slow", 0.3),
        ),
    ),
    Routine(
        id="shy",
        name="害羞",
        idle_at_least_s=6,
        cooldown_s=55 * 60,
        weight=0.8,
        steps=(
            Step("shy_look", 0.9),
            Step("cover_mouth_slow", 0.8),
            Step("hug_self", 0.9),
            Step("hug_self_pingpong", 1.0),
        ),
    ),
    Routine(
        id="proud",
        name="得意一下",
        idle_at_least_s=8,
        cooldown_s=50 * 60,
        weight=0.9,
        steps=(
            Step("salute_hold", 0.5),
            Step("salute", 0.7),
            Step("cheer_jump", 0.4),
        ),
    ),
    Routine(
        id="thinking",
        name="想事情",
        idle_at_least_s=4,
        weight=1.2,
        steps=(
            Step("think_pencil", 1.0),
            Step("look_up_slow", 0.9),
            Step("nod_slow", 0.3),
        ),
    ),
    Routine(
        id="wave_gentle",
        name="轻轻挥手",
        idle_at_least_s=5,
        weight=1.0,
        steps=(
            Step("wave_slow", 0.6),
            Step("wave_both_slow", 0.5),
            Step("nod_slow", 0.3),
        ),
    ),
    Routine(
        id="play_ball",
        name="推球玩",
        idle_at_least_s=11,
        cooldown_s=45 * 60,
        weight=1.0,
        steps=(
            Step("push_ball", 0.8),
            Step("spin_cheer_slow", 0.5),
        ),
    ),
    Routine(
        id="catch_star",
        name="接星星",
        idle_at_least_s=13,
        cooldown_s=50 * 60,
        weight=0.9,
        steps=(
            Step("catch_star", 0.8),
            Step("clap_happy", 0.4),
        ),
    ),
    Routine(
        id="oops",
        name="有点尴尬",
        idle_at_least_s=7,
        cooldown_s=65 * 60,
        weight=0.7,
        steps=(
            Step("fun_facepalm", 1.0),
            Step("shrug_pingpong", 1.2),
        ),
    ),
    Routine(
        id="point_out",
        name="指给你看",
        idle_at_least_s=6,
        cooldown_s=40 * 60,
        weight=0.9,
        steps=(
            Step("point_forward", 0.9),
            Step("look_up", 0.7),
            Step("nod_yes", 0.3),
        ),
    ),
    Routine(
        id="disagree",
        name="不太同意",
        idle_at_least_s=8,
        cooldown_s=60 * 60,
        weight=0.6,
        steps=(
            Step("shake_head", 0.7),
            Step("look_down_sad", 0.8),
            Step("pat_chest_hold", 0.6),
            Step("pat_own_chest", 0.4),
        ),
    ),
    Routine(
        id="focus_company",
        name="专注陪伴",
        needs_focus=True,
        idle_at_least_s=20,
        cooldown_s=30 * 60,
        weight=1.8,
        steps=(
            Step("listen_pingpong", 2.5),
            Step("nod_slow", 0.4),
        ),
    ),
    Routine(
        id="listen_carefully",
        name="认真听",
        idle_at_least_s=4,
        weight=1.0,
        steps=(
            Step("listen_tilt", 1.2),
            Step("nod_yes", 0.3),
        ),
    ),
    Routine(
        id="wait_patiently",
        name="等着你",
        idle_at_least_s=16,
        weight=1.1,
        steps=(
            Step("pat_chest_hold", 0.8),
            Step("happy_sway_pingpong", 1.5),
        ),
    ),
    Routine(
        id="doze",
        name="打盹",
        idle_at_least_s=45,
        cooldown_s=90 * 60,
        weight=1.2,
        steps=(
            Step("sleepy_nod", 1.0),
            Step("yawn", 0.8),
        ),
    ),
    Routine(
        id="wave_hello_big",
        name="热情招手",
        idle_at_least_s=25,
        cooldown_s=80 * 60,
        weight=0.8,
        steps=(
            Step("wave", 0.6),
            Step("wave_both", 0.6),
            Step("cheer_jump", 0.3),
        ),
    ),
    Routine(
        id="water_break",
        name="喝口水",
        idle_at_least_s=12,
        cooldown_s=55 * 60,
        weight=1.1,
        steps=(
            Step("drink_water_hold", 0.6),
            Step("drink_water", 0.6),
            Step("nod", 0.3),
        ),
    ),
    Routine(
        id="show_outfit",
        name="展示新衣服",
        idle_at_least_s=10,
        cooldown_s=75 * 60,
        weight=1.0,
        steps=(
            Step("turn_show", 0.8),
            Step("spin_cheer", 0.6),
            Step("clap_happy", 0.3),
        ),
    ),
    Routine(
        id="gentle_wave",
        name="招招手",
        idle_at_least_s=4,
        cooldown_s=30 * 60,
        weight=1.3,
        steps=(
            Step("wave", 0.6),
            Step("nod", 0.3),
        ),
    ),
    Routine(
        id="note_peek",
        name="看看便签",
        idle_at_least_s=15,
        cooldown_s=65 * 60,
        weight=0.9,
        steps=(
            Step("peek_note", 1.0),
            Step("look_up_slow", 0.7),
            Step("nod_slow", 0.3),
        ),
    ),
    Routine(
        id="surprise",
        name="有点惊讶",
        idle_at_least_s=9,
        cooldown_s=70 * 60,
        weight=0.7,
        steps=(
            Step("cover_mouth", 0.9),
            Step("look_up", 0.7),
            Step("nod_yes", 0.3),
        ),
    ),
    Routine(
        id="kneel_watch",
        name="跪坐着看",
        idle_at_least_s=18,
        cooldown_s=85 * 60,
        weight=0.8,
        steps=(
            Step("kneel_look", 1.2),
            Step("peek_right", 0.7),
            Step("peek_left_reverse", 0.6),
        ),
    ),
    Routine(
        id="tidy_corner",
        name="收拾角落",
        idle_at_least_s=11,
        cooldown_s=70 * 60,
        weight=1.0,
        steps=(
            Step("tidy_desk", 0.8),
            Step("tidy_hair", 0.7),
            Step("shrug", 0.3),
        ),
    ),
    Routine(
        id="spring_play",
        name="蹦一下",
        idle_at_least_s=16,
        cooldown_s=60 * 60,
        weight=0.9,
        steps=(
            Step("spring_reverse", 0.6),
            Step("cheer_jump", 0.5),
            Step("happy_sway", 0.6),
        ),
    ),
    Routine(
        id="sad_moment",
        name="有点低落",
        idle_at_least_s=25,
        cooldown_s=120 * 60,
        weight=0.5,
        steps=(
            Step("look_down_hold", 1.0),
            Step("look_down_sad", 0.8),
            Step("pat_chest_hold", 0.6),
        ),
    ),
    # ---- 时间专属 ----
    Routine(
        id="dawn_wake",
        name="清晨醒来",
        phases=("dawn",),
        idle_at_least_s=5,
        weight=1.5,
        steps=(
            Step("look_up_slow", 0.9),
            Step("stretch_hold", 0.6),
            Step("stretch", 0.7),
        ),
    ),
    Routine(
        id="noon_break",
        name="午间休息",
        phases=("noon",),
        idle_at_least_s=12,
        cooldown_s=90 * 60,
        weight=1.3,
        steps=(
            Step("stretch_back", 0.8),
            Step("drink_water_hold", 0.5),
            Step("drink_water", 0.6),
        ),
    ),
    Routine(
        id="evening_wind_down",
        name="傍晚收工",
        phases=("evening",),
        idle_at_least_s=15,
        cooldown_s=100 * 60,
        weight=1.2,
        steps=(
            Step("yawn", 0.9),
            Step("stretch_back", 0.7),
            Step("tidy_desk", 0.6),
        ),
    ),
    Routine(
        id="weekend_lazy",
        name="周末懒散",
        weekend_only=True,
        phases=("morning", "afternoon"),
        idle_at_least_s=20,
        cooldown_s=120 * 60,
        weight=1.1,
        steps=(
            Step("happy_sway_pingpong", 1.8),
            Step("kneel_look", 1.2),
        ),
    ),
    Routine(
        id="greet_first_seen",
        name="今天第一次见",
        signal="first_seen",
        idle_at_least_s=0,
        cooldown_s=0,
        weight=2.0,
        steps=(
            Step("wave_both", 0.6),
            Step("bow", 0.6),
            Step("cheer_jump", 0.3),
        ),
    ),
    Routine(
        id="welcome_back",
        name="欢迎回来",
        signal="welcome_back",
        idle_at_least_s=0,
        cooldown_s=20 * 60,
        weight=2.2,
        steps=(
            Step("look_up_slow", 0.5),
            Step("wave", 0.6),
            Step("happy_sway", 0.6),
        ),
    ),
    Routine(
        id="hour_chime",
        name="整点提醒",
        signal="hour_changed",
        idle_at_least_s=8,
        cooldown_s=50 * 60,
        weight=0.7,
        steps=(
            Step("look_up", 0.6),
            Step("nod", 0.3),
        ),
    ),
    # ---- 桌面交互 ----
    Routine(
        id="edge_peek_left",
        name="走到左边探头",
        idle_at_least_s=9,
        cooldown_s=40 * 60,
        weight=1.1,
        steps=(
            Step("@walk:left:0.06", 0.4),
            Step("peek_left", 1.0),
            Step("peek_left_reverse", 0.5),
            Step("@walk:home", 0.3),
        ),
    ),
    Routine(
        id="edge_peek_right",
        name="走到右边探头",
        idle_at_least_s=9,
        cooldown_s=40 * 60,
        weight=1.1,
        steps=(
            Step("@walk:right:0.88", 0.4),
            Step("peek_right", 1.0),
            Step("peek_right_reverse", 0.5),
            Step("@walk:home", 0.3),
        ),
    ),
    Routine(
        id="wander",
        name="到处走走",
        idle_at_least_s=22,
        cooldown_s=35 * 60,
        weight=1.2,
        steps=(
            Step("@walk:random", 0.4),
            Step("look_up_slow", 0.9),
            Step("@walk:home", 0.3),
        ),
    ),
    Routine(
        id="sit_at_edge",
        name="坐在边上晃腿",
        idle_at_least_s=30,
        cooldown_s=75 * 60,
        weight=1.0,
        steps=(
            Step("@walk:right:0.8", 0.4),
            Step("@sit", 0.5),
            Step("sit_stretch", 1.0),
            Step("@stand", 0.4),
            Step("@walk:home", 0.3),
        ),
    ),
    Routine(
        id="corner_doze",
        name="角落里打盹",
        idle_at_least_s=120,
        cooldown_s=150 * 60,
        weight=0.9,
        steps=(
            Step("@walk:left:0.1", 0.4),
            Step("@sleep", 0.6),
        ),
    ),
    Routine(
        id="perch_window",
        name="坐在窗口边上",
        idle_at_least_s=25,
        cooldown_s=55 * 60,
        weight=1.2,
        steps=(
            Step("@walk:window_right", 0.5),
            Step("@sit", 0.6),
            Step("sit_stretch", 1.0),
            Step("@stand", 0.4),
        ),
    ),
    Routine(
        id="peek_window",
        name="从窗口边探头",
        idle_at_least_s=18,
        cooldown_s=45 * 60,
        weight=1.1,
        steps=(
            Step("@walk:window_left", 0.5),
            Step("peek_left", 1.0),
            Step("peek_left_reverse", 0.5),
        ),
    ),
    Routine(
        id="follow_cursor",
        name="凑过去看看",
        idle_at_least_s=6,
        cooldown_s=25 * 60,
        weight=1.4,
        steps=(
            Step("@walk:cursor", 0.4),
            Step("listen_tilt", 1.0),
            Step("nod_yes", 0.3),
        ),
    ),
)


@dataclass
class Context:
    """做出决定所需的环境快照。"""

    now: float
    idle_s: float
    hour: int
    quiet: bool = False
    focus_running: bool = False
    music_playing: bool = False
    dsh_busy: bool = False
    dragging: bool = False
    #: 时间信号（来自 TimeSense）
    phase: str = ""
    is_weekend: bool = False
    first_seen_today: bool = False
    welcome_back: bool = False
    hour_changed: bool = False
    #: 桌面几何（来自 desktop_sense）；None 表示不可用
    desktop: object | None = None
    #: 宠物当前所在的屏幕工作区，用于走动目标
    screen_width: float = 0.0
    #: 可以播放的动作（造型可能不支持某些动作）
    available: Callable[[str], bool] | None = None


@dataclass
class DirectorState:
    last_act_at: float = -1e9
    busy_until: float = 0.0
    history: list[tuple[float, str]] = field(default_factory=list)
    current: Routine | None = None
    step_index: int = 0
    next_step_at: float = 0.0
    last_routine_id: str = ""
    last_routine_at: float = -1e9


class ProactiveDirector:
    """决定"现在要不要主动做点什么、做什么"。

    调用方每帧问 ``next_action(now, context)``：
    * 返回动作 id → 播放它；
    * 返回 None → 什么都不做。
    一套行为播完一步后要等 pause_s，由 ``notify_idle`` 推进。
    """

    def __init__(self, routines: Iterable[Routine] | None = None, *, rng: random.Random | None = None) -> None:
        self.routines = tuple(routines if routines is not None else ROUTINES)
        self.rng = rng or random.Random()
        self.state = DirectorState()

    # ---------------------------------------------------------------- 外部事件

    def note_user_input(self, now: float) -> None:
        """用户动了：立刻放弃当前行为，回到"不打扰"状态。"""
        self.state.current = None
        self.state.step_index = 0
        self.state.busy_until = now + 6.0

    def notify_action_started(self, now: float) -> None:
        self.state.busy_until = float("inf")

    def notify_idle(self, now: float) -> None:
        """角色回到待机：安排下一步的等待时间。"""
        if self.state.current is None:
            self.state.busy_until = now
            return
        routine = self.state.current
        step = routine.steps[self.state.step_index - 1] if self.state.step_index else None
        pause = step.pause_s if step is not None else 1.0
        self.state.busy_until = now + pause
        if self.state.step_index >= len(routine.steps):
            self._finish(now)

    # ---------------------------------------------------------------- 决策

    def _finish(self, now: float) -> None:
        routine = self.state.current
        if routine is not None:
            self.state.history.append((now, routine.id))
            self.state.last_routine_id = routine.id
            self.state.last_routine_at = now
        self.state.current = None
        self.state.step_index = 0
        # 一套结束后多歇一会儿，避免连着演。
        self.state.busy_until = now + MIN_GAP_S

    def _recent_count(self, now: float) -> int:
        cutoff = now - BUDGET_WINDOW_S
        self.state.history = [(t, rid) for t, rid in self.state.history if t >= cutoff]
        return len(self.state.history)

    def _hour_ok(self, routine: Routine, hour: int) -> bool:
        if routine.hours is None:
            return True
        start, end = routine.hours
        if end > 24:
            # 跨午夜：例如 (22, 29) 表示 22:00–05:00
            return hour >= start or hour < (end - 24)
        return start <= hour < end

    def _eligible(self, routine: Routine, ctx: Context) -> bool:
        if ctx.dragging or ctx.dsh_busy:
            return False
        if ctx.quiet and not routine.allow_quiet:
            return False
        if routine.needs_focus and not ctx.focus_running:
            return False
        if routine.needs_music and not ctx.music_playing:
            return False
        if routine.phases and ctx.phase and ctx.phase not in routine.phases:
            return False
        if routine.weekend_only and not ctx.is_weekend:
            return False
        if routine.signal:
            flag = {
                "first_seen": ctx.first_seen_today,
                "welcome_back": ctx.welcome_back,
                "hour_changed": ctx.hour_changed,
            }.get(routine.signal, False)
            if not flag:
                return False
        if ctx.idle_s < routine.idle_at_least_s:
            return False
        if not self._hour_ok(routine, ctx.hour):
            return False
        if ctx.available is not None:
            # 至少要有一半的步骤能播，否则这套动作在这套衣服上会残废。
            playable = sum(
                1 for step in routine.steps
                if is_special(step.action) or ctx.available(step.action)
            )
            if playable < max(1, math.ceil(len(routine.steps) / 2)):
                return False
        return True

    def _routine_cooldown_ok(self, routine: Routine, now: float) -> bool:
        for at, rid in reversed(self.state.history):
            if rid == routine.id:
                return now - at >= routine.cooldown_s
        return True

    def next_action(self, now: float, ctx: Context) -> str | None:
        state = self.state

        # 正在演一套：按节奏推进
        if state.current is not None:
            if now < state.busy_until:
                return None
            routine = state.current
            while state.step_index < len(routine.steps):
                step = routine.steps[state.step_index]
                state.step_index += 1
                if is_special(step.action) or ctx.available is None or ctx.available(step.action):
                    state.busy_until = float("inf")
                    return step.action
            self._finish(now)
            return None

        if now < state.busy_until:
            return None
        if self._recent_count(now) >= BUDGET_MAX:
            return None

        candidates = [
            routine for routine in self.routines
            if self._eligible(routine, ctx) and self._routine_cooldown_ok(routine, now)
        ]
        if not candidates:
            return None

        # 不连续两次同一套：把上一次的从候选里剔除（除非只剩它）
        filtered = [r for r in candidates if r.id != state.last_routine_id]
        if filtered:
            candidates = filtered

        # 越久没主动过，越容易触发：基础概率随静止时间上升，封顶 0.55
        quiet_for = now - state.last_act_at
        base = min(0.55, 0.06 + quiet_for / 3600.0 * 0.5)
        if self.rng.random() > base:
            return None

        weights = [max(0.01, r.weight) for r in candidates]
        chosen = self.rng.choices(candidates, weights=weights, k=1)[0]
        state.current = chosen
        state.step_index = 0
        state.last_act_at = now
        # 立刻给出第一步，不要递归回 next_action（那会被 busy_until 挡住）。
        while state.step_index < len(chosen.steps):
            step = chosen.steps[state.step_index]
            state.step_index += 1
            if is_special(step.action) or ctx.available is None or ctx.available(step.action):
                state.busy_until = float("inf")
                return step.action
        self._finish(now)
        return None

    # ---------------------------------------------------------------- 观察

    def pending_routine(self) -> str:
        return self.state.current.id if self.state.current else ""

    def stats(self) -> dict:
        return {
            "recent": len(self.state.history),
            "current": self.pending_routine(),
            "last": self.state.last_routine_id,
        }
