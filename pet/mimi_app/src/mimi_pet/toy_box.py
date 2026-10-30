"""F06 桌边玩具盒：短时、可随时收起的互动。

设计边界（对应 docs/expansion/FEATURE-SPEC.md）：
* 只在用户主动开始时存在，默认不自动弹出。
* 每轮有时长上限，同屏最多一个玩具；不记录成绩、连续天数、鼠标轨迹。
* 不注册全局键盘钩子；Esc 只在玩具窗口有焦点时处理。
* 中断（拾取、锁屏、断屏、问题卡）一律结束本轮并清理。

全部状态只在内存：游戏不跨重启恢复。
Qt-free —— 绘制与命中测试在渲染/窗口层，这里只管会话与物理。
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field

SESSION_IDLE = "idle"
SESSION_PREPARING = "preparing"
SESSION_PLAYING = "playing"
SESSION_ENDING = "ending"

TOY_BALL = "ball"
TOY_STAR = "star"
TOYS = (TOY_BALL, TOY_STAR)

DEFAULT_DURATION_S = 20.0
MAX_STARS_PER_ROUND = 3
#: 球的速度上限与每帧最大积分步数：休眠回来后不追算一大段轨迹。
MAX_BALL_SPEED = 420.0
MAX_STEPS_PER_TICK = 4
BALL_RADIUS = 18.0
GRAVITY = 900.0
RESTITUTION = 0.55


def _now() -> float:
    return time.monotonic()


@dataclass
class ToyObject:
    kind: str
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    spin: float = 0.0


@dataclass
class ToySession:
    id: int = 0
    state: str = SESSION_IDLE
    toy: str = TOY_BALL
    started_at: float = 0.0
    deadline: float = 0.0
    seed: int = 0
    objects: list[ToyObject] = field(default_factory=list)
    interactions: int = 0
    stars_spawned: int = 0


class ToyBox:
    """玩具会话与极简物理；所有入口都不抛异常。"""

    def __init__(self, *, clock=_now, rng: random.Random | None = None) -> None:
        self.clock = clock
        self.rng = rng or random.Random()
        self.session = ToySession()
        self._counter = 0
        #: 由界面注入：返回宠物当前的交互矩形（逻辑像素）。
        self.bounds: tuple[float, float, float, float] | None = None
        self.last_finish_reason = ""

    # ------------------------------------------------------------ 生命周期

    @property
    def active(self) -> bool:
        return self.session.state in (SESSION_PREPARING, SESSION_PLAYING)

    def start(self, toy: str = TOY_BALL, *, duration_s: float = DEFAULT_DURATION_S) -> tuple[bool, str]:
        if toy not in TOYS:
            return False, "没有这个玩具。"
        if self.active:
            return False, "已经在玩了，先收好这一轮。"
        duration = max(5.0, min(60.0, float(duration_s)))
        self._counter += 1
        now = self.clock()
        self.session = ToySession(
            id=self._counter,
            state=SESSION_PREPARING,
            toy=toy,
            started_at=now,
            deadline=now + duration,
            seed=self.rng.randrange(1_000_000),
        )
        self.last_finish_reason = ""
        if toy == TOY_BALL:
            self.session.objects = [self._spawn_ball()]
        self.session.state = SESSION_PLAYING
        label = "小球" if toy == TOY_BALL else "纸星星"
        return True, f"玩一会儿{label}吧，{int(duration)} 秒后自动收起。"

    def _spawn_ball(self) -> ToyObject:
        x, y = self._center()
        return ToyObject(
            kind=TOY_BALL,
            x=x,
            y=y - 60.0,
            vx=self.rng.uniform(-160.0, 160.0),
            vy=-120.0,
        )

    def _center(self) -> tuple[float, float]:
        if self.bounds is None:
            return 160.0, 220.0
        left, top, right, bottom = self.bounds
        return (left + right) / 2.0, (top + bottom) / 2.0

    def finish(self, reason: str = "user") -> tuple[bool, str]:
        """结束本轮并清空对象；重复调用安全。"""
        if not self.active:
            self.session.objects = []
            self.session.state = SESSION_IDLE
            return False, ""
        self.session.state = SESSION_ENDING
        self.session.objects = []
        self.session.state = SESSION_IDLE
        self.last_finish_reason = reason
        return True, "收好啦。"

    # ------------------------------------------------------------ 用户操作

    def push_ball(self) -> tuple[bool, str]:
        if not self.active or self.session.toy != TOY_BALL:
            return False, ""
        ball = next((o for o in self.session.objects if o.kind == TOY_BALL), None)
        if ball is None:
            return False, ""
        self.session.interactions += 1
        ball.vx += self.rng.uniform(-220.0, 220.0)
        ball.vy -= self.rng.uniform(120.0, 260.0)
        self._clamp_speed(ball)
        return True, ""

    def drop_star(self) -> tuple[bool, str]:
        """纸星：同屏最多一颗，每轮最多三颗。"""
        if not self.active or self.session.toy != TOY_STAR:
            return False, ""
        if any(o.kind == TOY_STAR for o in self.session.objects):
            return False, "先接住这一颗。"
        if self.session.stars_spawned >= MAX_STARS_PER_ROUND:
            return False, "这一轮玩够了。"
        x, _ = self._center()
        self.session.objects.append(ToyObject(kind=TOY_STAR, x=x, y=-40.0, vy=180.0))
        self.session.stars_spawned += 1
        return True, ""

    def catch_star(self) -> tuple[bool, str]:
        stars = [o for o in self.session.objects if o.kind == TOY_STAR]
        if not stars:
            return False, ""
        self.session.objects = [o for o in self.session.objects if o.kind != TOY_STAR]
        self.session.interactions += 1
        return True, "接住啦。"

    # ------------------------------------------------------------ 每帧

    def _clamp_speed(self, obj: ToyObject) -> None:
        speed = math.hypot(obj.vx, obj.vy)
        if speed > MAX_BALL_SPEED:
            scale = MAX_BALL_SPEED / speed
            obj.vx *= scale
            obj.vy *= scale

    def tick(self, dt_s: float) -> dict:
        """推进一帧；返回给渲染层的事件摘要。"""
        if not self.active:
            return {"active": False}
        now = self.clock()
        if now >= self.session.deadline:
            self.finish("timeout")
            return {"active": False, "finished": "timeout"}

        dt = max(0.0, min(0.05, float(dt_s)))
        steps = 1 if dt <= 0.02 else min(MAX_STEPS_PER_TICK, int(dt / 0.02) + 1)
        sub = dt / steps
        left, top, right, bottom = self.bounds or (-1e9, -1e9, 1e9, 1e9)

        for obj in self.session.objects:
            for _ in range(steps):
                if obj.kind == TOY_BALL:
                    obj.vy += GRAVITY * sub
                    obj.x += obj.vx * sub
                    obj.y += obj.vy * sub
                    if obj.x - BALL_RADIUS < left:
                        obj.x = left + BALL_RADIUS
                        obj.vx = abs(obj.vx) * RESTITUTION
                    elif obj.x + BALL_RADIUS > right:
                        obj.x = right - BALL_RADIUS
                        obj.vx = -abs(obj.vx) * RESTITUTION
                    if obj.y + BALL_RADIUS > bottom:
                        obj.y = bottom - BALL_RADIUS
                        obj.vy = -abs(obj.vy) * RESTITUTION
                        obj.vx *= 0.92
                    obj.spin += obj.vx * sub * 0.05
                else:
                    obj.y += obj.vy * sub
                    obj.spin += 40.0 * sub
                    if obj.y > bottom - BALL_RADIUS:
                        obj.y = bottom - BALL_RADIUS
            self._clamp_speed(obj)

        return {
            "active": True,
            "toy": self.session.toy,
            "remaining_s": max(0.0, self.session.deadline - now),
            "objects": [
                {"kind": o.kind, "x": o.x, "y": o.y, "spin": o.spin}
                for o in self.session.objects
            ],
        }

    # ------------------------------------------------------------ 中断

    def interrupt(self, reason: str) -> tuple[bool, str]:
        """拾取、锁屏、断屏、问题卡都走这里。"""
        return self.finish(reason)

    def summary(self) -> dict:
        return {
            "active": self.active,
            "toy": self.session.toy if self.active else "",
            "remaining_s": (
                max(0.0, self.session.deadline - self.clock()) if self.active else 0.0
            ),
            "objects": len(self.session.objects),
        }
