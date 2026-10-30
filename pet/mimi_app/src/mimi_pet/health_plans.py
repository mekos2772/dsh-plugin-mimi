"""F08 温柔健康小卡：可配置的间歇提醒。

与已有一次性提醒的区别：重复计划、活跃时段、每日展示上限、今天关闭、执行记录。

设计边界（对应 docs/expansion/FEATURE-SPEC.md）：
* 默认全部关闭；用户显式开启才建立计划。
* 不存饮水量、生理指标、键鼠轨迹；只留最近 30 天的发生记录。
* 休眠醒来不补发一串：旧候选过期，每计划最多保留一条。
* 时钟回拨/前拨用 occurrence id 去重，不重复弹卡。

Qt-free。时间相关的计算全部走传入的 clock，便于测试。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from .debug_log import dbg
from .expansion_store import JsonStore

SCHEMA_VERSION = 1
KINDS = ("water", "eyes", "stretch")
KIND_NAMES = {"water": "喝口水", "eyes": "看看远处", "stretch": "活动一下肩颈"}
INTERVAL_RANGE = (15, 180)
DAILY_CAP_RANGE = (1, 8)
SNOOZE_MINUTES = 10
MAX_SNOOZE = 2
HISTORY_DAYS = 30
#: 超过这个时长的候选说明电脑睡过/锁过屏，直接过期而不是补发。
STALE_AFTER_S = 60 * 60
#: 全局每日自动展示上限，跨计划合计。
GLOBAL_DAILY_CAP = 8

STATUS_SCHEDULED = "scheduled"
STATUS_DUE = "due"
STATUS_DEFERRED = "deferred"
STATUS_COMPLETED = "completed"
STATUS_DISMISSED = "dismissed"
STATUS_EXPIRED = "expired"


class HealthError(ValueError):
    """可以展示给用户的内容错误。"""


@dataclass
class Window:
    """一段活跃时段，分钟数从 0 点算；可跨午夜（start > end）。"""

    start_minute: int = 9 * 60
    end_minute: int = 18 * 60

    def contains(self, minute_of_day: int) -> bool:
        if self.start_minute == self.end_minute:
            return False
        if self.start_minute < self.end_minute:
            return self.start_minute <= minute_of_day < self.end_minute
        # 跨午夜
        return minute_of_day >= self.start_minute or minute_of_day < self.end_minute

    @property
    def label(self) -> str:
        return f"{self.start_minute // 60:02d}:{self.start_minute % 60:02d}–{self.end_minute // 60:02d}:{self.end_minute % 60:02d}"


@dataclass
class Plan:
    id: str
    kind: str = "water"
    enabled: bool = False
    interval_minutes: int = 60
    windows: list[Window] = field(default_factory=list)
    weekdays: list[int] = field(default_factory=lambda: [1, 2, 3, 4, 5])
    timezone: str = ""
    daily_display_cap: int = 4
    muted_until: float = 0.0
    last_fired_at: float = 0.0
    revision: int = 1

    @property
    def name(self) -> str:
        return KIND_NAMES.get(self.kind, self.kind)


@dataclass
class Occurrence:
    id: str
    plan_id: str
    local_date: str
    scheduled_at: float
    status: str = STATUS_SCHEDULED
    snooze_count: int = 0
    displayed_at: float = 0.0
    completed_at: float = 0.0


def _now() -> float:
    return time.time()


def _local(dt: float) -> datetime:
    return datetime.fromtimestamp(dt)


class HealthPlanner:
    """健康计划的调度与记录。

    ``tick(now)`` 返回这一轮应该展示的卡片（0 或 1 条），由界面决定怎么弹。
    """

    def __init__(self, store: JsonStore | None = None, *, clock=_now) -> None:
        self.store = store or JsonStore.for_user_file(
            "health.json", schema_version=SCHEMA_VERSION, override_env="MIMI_HEALTH_PATH"
        )
        self.clock = clock
        self.plans: dict[str, Plan] = {}
        self.occurrences: dict[str, Occurrence] = {}
        self.error = ""
        self._load()

    # ------------------------------------------------------------ 持久化

    def _load(self) -> None:
        payload = self.store.load()
        self.error = self.store.error
        for raw in payload.get("plans", []):
            try:
                windows = [
                    Window(int(w.get("start_minute", 540)), int(w.get("end_minute", 1080)))
                    for w in raw.get("windows", [])
                ] or [Window()]
                plan = Plan(
                    id=str(raw["id"]),
                    kind=str(raw.get("kind", "water")),
                    enabled=bool(raw.get("enabled", False)),
                    interval_minutes=int(raw.get("interval_minutes", 60)),
                    windows=windows,
                    weekdays=[int(d) for d in raw.get("weekdays", [1, 2, 3, 4, 5])],
                    timezone=str(raw.get("timezone", "")),
                    daily_display_cap=int(raw.get("daily_display_cap", 4)),
                    muted_until=float(raw.get("muted_until", 0.0)),
                    last_fired_at=float(raw.get("last_fired_at", 0.0)),
                    revision=int(raw.get("revision", 1)),
                )
            except (KeyError, TypeError, ValueError) as exc:
                dbg(f"health plan rejected: {exc}")
                continue
            if plan.kind not in KINDS:
                plan.kind = "water"
            self.plans[plan.id] = plan

        for raw in payload.get("occurrences", []):
            try:
                occ = Occurrence(
                    id=str(raw["id"]),
                    plan_id=str(raw["plan_id"]),
                    local_date=str(raw["local_date"]),
                    scheduled_at=float(raw.get("scheduled_at", 0.0)),
                    status=str(raw.get("status", STATUS_SCHEDULED)),
                    snooze_count=int(raw.get("snooze_count", 0)),
                    displayed_at=float(raw.get("displayed_at", 0.0)),
                    completed_at=float(raw.get("completed_at", 0.0)),
                )
            except (KeyError, TypeError, ValueError) as exc:
                dbg(f"health occurrence rejected: {exc}")
                continue
            if occ.plan_id in self.plans:
                self.occurrences[occ.id] = occ
        self.prune(HISTORY_DAYS)

    def _save(self) -> bool:
        payload = {
            "plans": [
                {
                    **asdict(p),
                    "windows": [asdict(w) for w in p.windows],
                }
                for p in self.plans.values()
            ],
            "occurrences": [asdict(o) for o in self.occurrences.values()],
        }
        ok = self.store.save(payload)
        self.error = self.store.error
        return ok

    # ------------------------------------------------------------ 计划管理

    def ensure_defaults(self) -> None:
        """补齐三种计划（默认关闭），让设置页有稳定条目可选。"""
        existing = {p.kind for p in self.plans.values()}
        added = False
        for kind in KINDS:
            if kind in existing:
                continue
            # 字典键必须和 Plan.id 是同一个值：两次 uuid4() 会让
            # plans.get(occurrence.plan_id) 永远查不到，进而丢掉动作选择
            # 和面板上的计划类型。
            plan_id = uuid.uuid4().hex[:8]
            self.plans[plan_id] = Plan(
                id=plan_id, kind=kind, enabled=False,
                windows=[Window()], weekdays=[1, 2, 3, 4, 5],
            )
            added = True
        if added:
            self._save()

    def plan_for(self, kind: str) -> Plan | None:
        return next((p for p in self.plans.values() if p.kind == kind), None)

    def configure(
        self,
        kind: str,
        *,
        enabled: bool | None = None,
        interval_minutes: int | None = None,
        windows: list[tuple[int, int]] | None = None,
        weekdays: list[int] | None = None,
        daily_display_cap: int | None = None,
    ) -> tuple[bool, str]:
        plan = self.plan_for(kind)
        if plan is None:
            return False, "没有找到这个计划。"
        if interval_minutes is not None:
            low, high = INTERVAL_RANGE
            if not low <= int(interval_minutes) <= high:
                return False, f"间隔请填 {low}–{high} 分钟。"
            plan.interval_minutes = int(interval_minutes)
        if daily_display_cap is not None:
            low, high = DAILY_CAP_RANGE
            if not low <= int(daily_display_cap) <= high:
                return False, f"每日上限请填 {low}–{high} 次。"
            plan.daily_display_cap = int(daily_display_cap)
        if windows is not None:
            built = []
            for start, end in windows[:2]:
                if not (0 <= start < 1440 and 0 <= end < 1440):
                    return False, "时段必须在一天之内。"
                built.append(Window(int(start), int(end)))
            if not built:
                return False, "至少保留一个时段。"
            plan.windows = built
        if weekdays is not None:
            days = sorted({int(d) for d in weekdays if 1 <= int(d) <= 7})
            if not days:
                return False, "至少选一天。"
            plan.weekdays = days
        if enabled is not None:
            plan.enabled = bool(enabled)
            if plan.enabled:
                plan.muted_until = 0.0
        plan.revision += 1
        self._save()
        return True, ("已开启。" if plan.enabled else "已更新。")

    def mute_today(self, kind: str) -> tuple[bool, str]:
        """今天先关：抑制到计划时区的次日零点。"""
        plan = self.plan_for(kind)
        if plan is None:
            return False, "没有找到这个计划。"
        now = _local(self.clock())
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        plan.muted_until = tomorrow.timestamp()
        plan.revision += 1
        self._save()
        return True, f"{plan.name}今天先不提醒了。"

    # ------------------------------------------------------------ 调度

    def _in_window(self, plan: Plan, moment: datetime) -> bool:
        if moment.isoweekday() not in plan.weekdays:
            return False
        minute = moment.hour * 60 + moment.minute
        return any(w.contains(minute) for w in plan.windows)

    def _displayed_count(self, plan: Plan, day: str) -> int:
        return sum(
            1 for o in self.occurrences.values()
            if o.plan_id == plan.id and o.local_date == day
            and o.status in (STATUS_DEFERRED, STATUS_COMPLETED, STATUS_DISMISSED)
        )

    def _global_displayed(self, day: str) -> int:
        return sum(
            1 for o in self.occurrences.values()
            if o.local_date == day
            and o.status in (STATUS_DEFERRED, STATUS_COMPLETED, STATUS_DISMISSED)
        )

    #: 内部时钟连续调用时的最小扫描间隔。主循环 60Hz，但健康计划只需
    #: 秒级精度；每帧全表扫描在历史累积后会变成可观的浪费。
    SCAN_INTERVAL_S = 0.5

    def due(self, now: float | None = None) -> Occurrence | None:
        """找一个该展示的候选；没有就返回 None。

        传 ``now`` 时不节流（测试与手动推进需要确定性）。
        """
        internal = now is None
        now = self.clock() if internal else now
        if internal and getattr(self, "_scan_none", False):
            if now - getattr(self, "_scan_at", -1e9) < self.SCAN_INTERVAL_S:
                return None
        moment = _local(now)
        day = moment.date().isoformat()

        # 推迟过的卡片到点后重新变成待处理；同样不接受重复展示。
        for occ in self.occurrences.values():
            if occ.status == STATUS_SCHEDULED and occ.snooze_count:
                if now >= occ.scheduled_at + SNOOZE_MINUTES * 60:
                    occ.status = STATUS_DUE
                    self._save()
                    return occ

        # 已经有待处理的卡片就不叠加，避免一次弹好几张。
        for occ in self.occurrences.values():
            if occ.status == STATUS_DUE:
                return occ

        candidates: list[tuple[float, Plan]] = []
        for plan in self.plans.values():
            if not plan.enabled:
                continue
            if plan.muted_until and now < plan.muted_until:
                continue
            if not self._in_window(plan, moment):
                continue
            if self._displayed_count(plan, day) >= plan.daily_display_cap:
                continue
            last = plan.last_fired_at or 0.0
            if last and now - last < plan.interval_minutes * 60:
                continue
            # 明显超过「间隔 × 2」且至少一小时，说明电脑睡过/锁过屏：
            # 从现在重新计时，不补发积压的候选。阈值必须随间隔放大，
            # 否则 60 分钟间隔的计划会被 1 小时的硬阈值误判成刚睡醒。
            stale_after = max(plan.interval_minutes * 120, STALE_AFTER_S)
            if last and now - last > stale_after:
                plan.last_fired_at = now
                plan.revision += 1
                continue
            candidates.append((last, plan))

        if not candidates:
            self._scan_at = now
            self._scan_none = True
            return None
        if self._global_displayed(day) >= GLOBAL_DAILY_CAP:
            self._scan_at = now
            self._scan_none = True
            return None

        candidates.sort(key=lambda item: item[0])
        plan = candidates[0][1]
        occ_id = f"{plan.id}-{day}-{int(now // 60)}"
        if occ_id in self.occurrences:
            return None
        occ = Occurrence(
            id=occ_id,
            plan_id=plan.id,
            local_date=day,
            scheduled_at=now,
            status=STATUS_DUE,
        )
        self.occurrences[occ_id] = occ
        self._scan_none = False
        plan.last_fired_at = now
        plan.revision += 1
        self._save()
        return occ

    # ------------------------------------------------------------ 用户操作

    def complete(self, occurrence_id: str) -> tuple[bool, str]:
        occ = self.occurrences.get(occurrence_id)
        if occ is None:
            return False, "这张提醒已经过期了。"
        if occ.status == STATUS_COMPLETED:
            return True, "已经记下了。"
        occ.status = STATUS_COMPLETED
        occ.completed_at = self.clock()
        occ.displayed_at = occ.displayed_at or occ.completed_at
        self._save()
        plan = self.plans.get(occ.plan_id)
        return True, f"{plan.name if plan else '提醒'}完成～"

    def snooze(self, occurrence_id: str) -> tuple[bool, str]:
        occ = self.occurrences.get(occurrence_id)
        if occ is None:
            return False, "这张提醒已经过期了。"
        if occ.snooze_count >= MAX_SNOOZE:
            occ.status = STATUS_EXPIRED
            self._save()
            return True, "已经推迟过两次了，这次先放过。"
        occ.status = STATUS_SCHEDULED
        occ.snooze_count += 1
        self._save()
        return True, f"好，{SNOOZE_MINUTES} 分钟后再提醒你。"

    def dismiss(self, occurrence_id: str) -> tuple[bool, str]:
        occ = self.occurrences.get(occurrence_id)
        if occ is None:
            return False, "这张提醒已经过期了。"
        occ.status = STATUS_DISMISSED
        occ.displayed_at = self.clock()
        self._save()
        return True, "好，这次跳过。"

    # ------------------------------------------------------------ 维护

    def prune(self, days: int = HISTORY_DAYS) -> int:
        """丢掉过期的历史发生记录。"""
        cutoff = (_local(self.clock()) - timedelta(days=days)).date().isoformat()
        stale = [
            oid for oid, occ in self.occurrences.items()
            if occ.local_date < cutoff and occ.status != STATUS_DUE
        ]
        for oid in stale:
            del self.occurrences[oid]
        if stale:
            self._save()
        return len(stale)

    def summary(self) -> dict:
        today = _local(self.clock()).date().isoformat()
        items = []
        for plan in self.plans.values():
            items.append({
                "kind": plan.kind,
                "name": plan.name,
                "enabled": plan.enabled,
                "interval_minutes": plan.interval_minutes,
                "windows": [w.label for w in plan.windows],
                "weekdays": list(plan.weekdays),
                "daily_cap": plan.daily_display_cap,
                "used_today": self._displayed_count(plan, today),
                "muted": bool(plan.muted_until and self.clock() < plan.muted_until),
            })
        pending = [o for o in self.occurrences.values() if o.status == STATUS_DUE]
        return {
            "plans": items,
            "pending": None if not pending else {
                "id": pending[0].id,
                "kind": self.plans[pending[0].plan_id].kind if pending[0].plan_id in self.plans else "water",
            },
        }
