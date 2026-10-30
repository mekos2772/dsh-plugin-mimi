"""把 F01–F08 串起来的总服务。

单个入口给界面用：菜单、快捷卡片、面板快照都从这里取，避免每个 widget
各自持有半套状态。也正因为如此，业务规则只在这里实现一次。

设计要点：
* 每个功能一个独立子服务，互不 import。
* 动画只是结果订阅者：``consume_animation()`` 返回"该播什么"，播没播成功
  都不影响业务状态。
* 所有对外方法返回 ``(ok, message)``，界面直接拿去展示，不需要理解异常。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .debug_log import dbg
from .health_plans import HealthPlanner, KIND_NAMES
from .music_companion import MusicCompanion
from .notes import NoteBook, NoteError
from .scene_wrapup import SceneError, SceneService, WrapupService
from .task_runs import TaskBoard, TaskError
from .toy_box import ToyBox

#: 功能完成时希望角色播的动作；缺素材时由引擎自行忽略。
ANIM_STEP_DONE = "catch_star"
ANIM_TASK_DONE = "celebrate"
#: 每类健康卡配一个动作：有对应素材就播，没有就静默跳过。
ANIM_HEALTH_DONE = "drink_water"
#: 派生动作还没生成时的兜底，按顺序取第一个存在的。
ANIM_HEALTH_FALLBACK = {
    "water": ("drink_water_hold", "drink_water"),
    "eyes": ("listen_pingpong", "listen_tilt", "listen"),
    "stretch": ("stretch_hold", "stretch"),
}
ANIM_HEALTH_BY_KIND = {
    # 优先用派生动作：预备一拍再开始，观感比直接动要自然。
    "water": "drink_water_hold",
    "eyes": "listen_pingpong",   # 来回倾听，接近"远望放松"
    "stretch": "stretch_hold",
}
ANIM_WRAPUP = "lantern_off"


@dataclass
class PendingAnimation:
    action_id: str
    source: str
    key: str


class ExpansionHub:
    """F01–F08 的统一门面。"""

    def __init__(
        self,
        *,
        clock=None,
        calendar_clock=None,
        data_root: Path | None = None,
    ) -> None:
        self.notes = NoteBook(clock=clock) if clock else NoteBook()
        self.tasks = TaskBoard(clock=clock) if clock else TaskBoard()
        self.scene = SceneService(clock=clock) if clock else SceneService()
        self.wrapups = WrapupService(clock=clock) if clock else WrapupService()
        self.health = HealthPlanner(clock=calendar_clock or clock) if (calendar_clock or clock) else HealthPlanner()
        self.health.ensure_defaults()
        self.toys = ToyBox(clock=clock) if clock else ToyBox()
        self.music = MusicCompanion(clock=clock) if clock else MusicCompanion()
        self._pending: list[PendingAnimation] = []
        self._toy_was_active = False
        self._data_root = data_root

    # ------------------------------------------------------------ 动画队列

    def request_animation(self, action_id: str, *, source: str, key: str = "") -> None:
        if not action_id:
            return
        if key and any(p.key == key for p in self._pending):
            return
        self._pending.append(PendingAnimation(action_id=action_id, source=source, key=key))

    def consume_animation(self) -> PendingAnimation | None:
        """取一个待播动作；调用方播不了也应当丢弃，不要堆积。"""
        return self._pending.pop(0) if self._pending else None

    # ------------------------------------------------------------ F01

    def add_note(self, body: str, tags=None) -> tuple[bool, str, str]:
        try:
            note, message = self.notes.create(body, tags=tags)
        except NoteError as exc:
            return False, str(exc), ""
        if note is not None:
            self.request_animation("peek_note", source="note", key=f"note:{note.id}")
            return True, message, note.id
        return False, message, ""

    def search_notes(self, query: str) -> list[dict]:
        return [
            {"id": n.id, "title": n.title, "excerpt": n.excerpt, "tags": list(n.tags)}
            for n in self.notes.search(query)
        ]

    # ------------------------------------------------------------ F02

    def start_task(
        self, title: str, steps: list[str], *, note_id: str = "", start: bool = True
    ) -> tuple[bool, str, str]:
        """建任务；``start=False`` 时只排队，不抢占当前专注。"""
        try:
            task, message = self.tasks.create(
                title, steps, source_note_id=note_id, start=start
            )
        except TaskError as exc:
            return False, str(exc), ""
        if task is None:
            return False, message, ""
        if note_id:
            self.notes.link_task(note_id, task.id)
        if not start:
            message = f"「{task.title}」先放着了，想做的时候在任务列表里点「接着做」。"
        return True, message, task.id

    def complete_task_step(self, task_id: str, step_id: str = "") -> tuple[bool, str]:
        ok, payload = self.tasks.complete_step(task_id, step_id)
        if not ok:
            return False, payload.get("message", "")
        if payload.get("event"):
            action = ANIM_TASK_DONE if payload.get("finished") else ANIM_STEP_DONE
            self.request_animation(action, source="task", key=payload.get("event_key", ""))
        message = payload.get("message", "")
        if payload.get("finished"):
            # 全部做完时补一句"要不要收尾"，但不自动打开收尾卡（用户可能还想再做一件）。
            message += "\n想结束今天的话，右键「今天到这里」。"
        return True, message

    def undo_task_step(self, task_id: str) -> tuple[bool, str]:
        return self.tasks.undo_step(task_id)

    # ------------------------------------------------------- 任务 × 专注联动

    def link_focus(self, task_id: str, session_id: str) -> None:
        """把当前任务与一段专注计时关联起来。

        只记录关联，不改任何一方的状态：任务完成不自动取消计时，
        计时结束也不自动完成任务——两者都由用户决定。
        """
        self.tasks.attach_focus(task_id, session_id)

    def focus_started(self, session_id: str, *, minutes: int) -> tuple[bool, str]:
        """用户开始专注时调用：若正在做任务，自动建立关联。"""
        task = self.tasks.active()
        if task is None:
            return False, ""
        self.tasks.attach_focus(task.id, session_id)
        return True, f"专注 {minutes} 分钟，先把「{task.title}」的这一步做完。"

    def focus_finished(self, session_id: str) -> dict:
        """专注结束时给出建议，不擅自改任务状态。"""
        task = self.tasks.active()
        if task is None or task.focus_session_id != session_id:
            return {}
        step = task.current_step
        return {
            "task_id": task.id,
            "title": task.title,
            "current": step.text if step else "",
            "message": (
                f"这段专注结束了。「{task.title}」还差一步：{step.text}"
                if step else f"这段专注结束了，「{task.title}」也已经做完了。"
            ),
        }

    # ------------------------------------------------------------ F03 + F07

    def finish_day(
        self,
        *,
        note_ids=None,
        task_ids=None,
        title_snapshots=None,
        next_step: str = "",
        scene_choice: str = "keep",
    ) -> tuple[bool, str, str]:
        try:
            item, message = self.wrapups.save(
                note_ids=note_ids,
                task_ids=task_ids,
                title_snapshots=title_snapshots,
                next_step=next_step,
                scene_choice=scene_choice,
            )
        except SceneError as exc:
            return False, str(exc), ""
        ok_scene, scene_note = self.scene.apply_wrapup(
            scene_choice, expected_revision=self.scene.scene.revision
        )
        if not ok_scene:
            message += "\n" + scene_note
        self.request_animation(ANIM_WRAPUP, source="wrapup", key=f"wrapup:{item.id}")
        return True, message, item.id

    def continue_tomorrow(self) -> tuple[bool, str, str]:
        """把「明天第一步」变成任务：已关联则激活，否则新建。

        新建时用收尾里那句话作为任务标题，只放一步——用户点开就能立刻开始，
        不需要再想怎么拆。
        """
        _wrapup_id, step = self.wrapups.pending_next_step()
        if not step:
            return False, "还没有记下明天第一步。", ""
        latest = self.wrapups.latest()
        if latest is not None and latest.linked_next_task_id:
            task = self.tasks.get(latest.linked_next_task_id)
            if task is not None:
                ok, message = self.tasks.activate(task.id)
                return ok, message, task.id
        # 还没有对应任务：建一个单步任务并关联回收尾记录。
        ok, message, task_id = self.start_task(step, [step])
        if not ok:
            return False, message, ""
        if latest is not None:
            self.wrapups.link_next_task(latest.id, task_id)
        return True, f"接着昨天的：{step}", task_id

    # ------------------------------------------------------------ F08

    def health_due(self):
        return self.health.due()

    def complete_health(self, occurrence_id: str) -> tuple[bool, str]:
        ok, message = self.health.complete(occurrence_id)
        if ok:
            # 按计划类型选动作：喝水/远望/伸展各自更贴合，不必统一成喝水。
            plan_kind = ""
            occ = self.health.occurrences.get(occurrence_id)
            if occ is not None:
                plan = self.health.plans.get(occ.plan_id)
                plan_kind = plan.kind if plan else ""
            # 选第一个实际存在的候选：派生动作没生成时回退到原始动作。
            candidates = ANIM_HEALTH_FALLBACK.get(
                plan_kind, (ANIM_HEALTH_BY_KIND.get(plan_kind, ANIM_HEALTH_DONE),)
            )
            action = candidates[0]
            available = getattr(self, "action_exists", None)
            if available is not None:
                action = next((c for c in candidates if available(c)), candidates[0])
            self.request_animation(
                action, source="health", key=f"health:{occurrence_id}"
            )
        return ok, message

    # ------------------------------------------------------------ F06

    def start_toy(self, toy: str) -> tuple[bool, str]:
        return self.toys.start(toy)

    def stop_toy(self, reason: str = "user") -> tuple[bool, str]:
        return self.toys.finish(reason)

    # ------------------------------------------------------------ 快照

    def action_exists(self, action_id: str) -> bool:
        """由 qt_app 注入：检查动作库里是否真的有这个动作。"""
        checker = getattr(self, "_action_checker", None)
        return True if checker is None else bool(checker(action_id))

    def snapshot(self) -> dict:
        return {
            "notes": {
                "count": self.notes.count(),
                "active": len(self.notes.list()),
                "trashed": len(self.notes.trashed()),
                "error": self.notes.error,
            },
            "tasks": self.tasks.summary(),
            "scene": {
                "visible": self.scene.is_visible,
                "items": [i.asset_id for i in self.scene.scene.items],
                "placement": self.scene.scene.placement,
                "editing": self.scene.editing,
            },
            "wrapup": {
                "today": bool(self.wrapups.today()),
                "next_step": self.wrapups.pending_next_step()[1],
            },
            "health": self.health.summary(),
            "toys": self.toys.summary(),
            "music": self.music.summary(),
            "pending_animations": len(self._pending),
        }

    # ------------------------------------------------------------ 每帧

    def tick(self, dt_s: float) -> dict:
        """统一推进与时间相关的子服务；返回需要界面立刻响应的信息。"""
        events: dict = {}
        toy_payload = self.toys.tick(dt_s)
        # 结束那一刻也要下发：否则窗口会保留最后一帧的玩具状态，
        # 画面上留下永远不消失的"幽灵球"。
        if toy_payload.get("active"):
            self._toy_was_active = True
            events["toy"] = toy_payload
        elif getattr(self, "_toy_was_active", False):
            self._toy_was_active = False
            events["toy"] = toy_payload
        due = self.health.due()
        if due is not None:
            plan = self.health.plans.get(due.plan_id)
            kind = plan.kind if plan else "water"
            events["health_due"] = {
                "id": due.id,
                "kind": kind,
                "name": KIND_NAMES.get(kind, "休息一下"),
                # 卡片据此决定是否显示"我也一起"，没有素材时也能正常弹卡。
                "action": ANIM_HEALTH_BY_KIND.get(kind, ANIM_HEALTH_DONE),
            }
        anim = self.consume_animation()
        if anim is not None:
            events["animation"] = anim
        return events
