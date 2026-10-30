"""F02 三步任务陪跑：用户自己拆步骤，Mimi 只陪当前一步。

设计边界（对应 docs/expansion/FEATURE-SPEC.md）：
* 最多 3 步，不自动拆解、不自动判定完成、不推断用户在做什么。
* 同时只允许一个 active 任务；其余进 queued/paused。
* 完成事件用 operation_id 去重：连点、重试、重启都不会重复播动画。
* 取消不扣好感、不播负面反馈。

Qt-free。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field

from .debug_log import dbg
from .expansion_store import JsonStore

SCHEMA_VERSION = 1
MAX_STEPS = 3
TITLE_LIMIT = 80
STEP_LIMIT = 160
OPEN_TASK_LIMIT = 20

STATUS_DRAFT = "draft"
STATUS_QUEUED = "queued"
STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"

STEP_PENDING = "pending"
STEP_DONE = "done"

EVENT_STEP_DONE = "task.step.completed"
EVENT_TASK_DONE = "task.run.completed"


class TaskError(ValueError):
    """可以直接展示给用户的内容错误。"""


@dataclass
class Step:
    id: str
    text: str
    status: str = STEP_PENDING
    completed_at: float = 0.0


@dataclass
class TaskRun:
    id: str
    title: str
    steps: list[Step] = field(default_factory=list)
    status: str = STATUS_DRAFT
    source_note_id: str = ""
    focus_session_id: str = ""
    revision: int = 1
    created_at: float = 0.0
    updated_at: float = 0.0
    #: 已消费的完成事件，防止重启后重播动画
    emitted_events: list[str] = field(default_factory=list)

    @property
    def current_step(self) -> Step | None:
        for step in self.steps:
            if step.status == STEP_PENDING:
                return step
        return None

    @property
    def done_count(self) -> int:
        return sum(1 for s in self.steps if s.status == STEP_DONE)

    @property
    def progress_text(self) -> str:
        return f"{self.done_count}/{len(self.steps)}"

    @property
    def is_open(self) -> bool:
        return self.status in (STATUS_DRAFT, STATUS_QUEUED, STATUS_ACTIVE, STATUS_PAUSED)


def _now() -> float:
    return time.time()


def _clean(text: str, *, limit: int, what: str) -> str:
    value = (text or "").strip()
    if not value:
        raise TaskError(f"{what}不能为空。")
    if len(value) > limit:
        raise TaskError(f"{what}最多 {limit} 个字。")
    return value


class TaskBoard:
    """任务集合与当前聚焦任务。"""

    def __init__(self, store: JsonStore | None = None, *, clock=_now) -> None:
        self.store = store or JsonStore.for_user_file(
            "task_runs.json", schema_version=SCHEMA_VERSION, override_env="MIMI_TASKS_PATH"
        )
        self.clock = clock
        self.tasks: dict[str, TaskRun] = {}
        self.active_id = ""
        self.error = ""
        self._load()

    # ------------------------------------------------------------ 持久化

    def _load(self) -> None:
        payload = self.store.load()
        self.error = self.store.error
        for raw in payload.get("tasks", []):
            try:
                steps = [
                    Step(
                        id=str(s["id"]),
                        text=str(s["text"]),
                        status=str(s.get("status", STEP_PENDING)),
                        completed_at=float(s.get("completed_at", 0.0)),
                    )
                    for s in raw.get("steps", [])
                ]
                task = TaskRun(
                    id=str(raw["id"]),
                    title=str(raw["title"]),
                    steps=steps,
                    status=str(raw.get("status", STATUS_DRAFT)),
                    source_note_id=str(raw.get("source_note_id", "")),
                    focus_session_id=str(raw.get("focus_session_id", "")),
                    revision=int(raw.get("revision", 1)),
                    created_at=float(raw.get("created_at", 0.0)),
                    updated_at=float(raw.get("updated_at", 0.0)),
                    emitted_events=list(raw.get("emitted_events", [])),
                )
            except (KeyError, TypeError, ValueError) as exc:
                dbg(f"task entry rejected: {exc}")
                continue
            if not task.steps or len(task.steps) > MAX_STEPS:
                dbg(f"task {task.id}: step count out of range; skipped")
                continue
            self.tasks[task.id] = task
        active = str(payload.get("active_id", ""))
        if active in self.tasks and self.tasks[active].is_open:
            self.active_id = active
        else:
            # 记录里 active 丢了就按当前状态重算，避免两个任务同时"进行中"。
            for task in self.tasks.values():
                if task.status == STATUS_ACTIVE:
                    task.status = STATUS_PAUSED
            self.active_id = ""

    def _save(self) -> bool:
        payload = {
            "active_id": self.active_id,
            "tasks": [asdict(t) for t in self.tasks.values()],
        }
        ok = self.store.save(payload)
        self.error = self.store.error
        return ok

    # ------------------------------------------------------------ 查询

    def get(self, task_id: str) -> TaskRun | None:
        return self.tasks.get(task_id)

    def active(self) -> TaskRun | None:
        return self.tasks.get(self.active_id) if self.active_id else None

    def open_tasks(self) -> list[TaskRun]:
        return sorted(
            (t for t in self.tasks.values() if t.is_open),
            key=lambda t: (t.status != STATUS_ACTIVE, -t.updated_at),
        )

    def completed(self, *, limit: int = 20) -> list[TaskRun]:
        items = [t for t in self.tasks.values() if t.status == STATUS_COMPLETED]
        items.sort(key=lambda t: -t.updated_at)
        return items[:limit]

    # ------------------------------------------------------------ 创建

    def create(
        self,
        title: str,
        steps: list[str],
        *,
        source_note_id: str = "",
        start: bool = True,
    ) -> tuple[TaskRun | None, str]:
        clean_title = _clean(title, limit=TITLE_LIMIT, what="任务标题")
        if not steps:
            raise TaskError("至少写一步再开始。")
        if len(steps) > MAX_STEPS:
            raise TaskError(f"最多拆成 {MAX_STEPS} 步。")
        clean_steps = [_clean(s, limit=STEP_LIMIT, what="步骤") for s in steps]
        if len(self.open_tasks()) >= OPEN_TASK_LIMIT:
            return None, f"还没结束的任务有 {OPEN_TASK_LIMIT} 个，先完成或取消一些吧。"

        now = self.clock()
        task = TaskRun(
            id=uuid.uuid4().hex[:12],
            title=clean_title,
            steps=[Step(id=uuid.uuid4().hex[:8], text=s) for s in clean_steps],
            status=STATUS_QUEUED,
            source_note_id=source_note_id,
            created_at=now,
            updated_at=now,
        )
        self.tasks[task.id] = task
        message = "记下了。"
        if start:
            ok, activated = self.activate(task.id)
            message = activated
        else:
            self._save()
        return task, message

    # ------------------------------------------------------------ 状态流转

    def activate(self, task_id: str) -> tuple[bool, str]:
        task = self.tasks.get(task_id)
        if task is None:
            return False, "没有找到这个任务。"
        if not task.is_open:
            return False, "这个任务已经结束了。"
        current = self.active()
        if current is not None and current.id != task_id:
            # 一次只陪一步：旧任务自动进 paused，不丢进度。
            current.status = STATUS_PAUSED
            current.updated_at = self.clock()
            current.revision += 1
        task.status = STATUS_ACTIVE
        task.updated_at = self.clock()
        task.revision += 1
        self.active_id = task.id
        saved = self._save()
        hint = "" if saved else "\n（保存失败，重启后可能丢失）"
        step = task.current_step
        if step is None:
            return True, f"「{task.title}」的步骤都完成了。" + hint
        return True, f"现在只做这一步：{step.text}" + hint

    def pause(self, task_id: str) -> tuple[bool, str]:
        task = self.tasks.get(task_id)
        if task is None:
            return False, "没有找到这个任务。"
        if task.status != STATUS_ACTIVE:
            return False, "这个任务当前不在进行中。"
        task.status = STATUS_PAUSED
        task.updated_at = self.clock()
        task.revision += 1
        if self.active_id == task.id:
            self.active_id = ""
        self._save()
        return True, "先停在这儿，回来接着做。"

    def complete_step(self, task_id: str, step_id: str = "") -> tuple[bool, dict]:
        """勾掉一步。返回 (ok, payload)，payload 带是否需要播动画。

        连点同一个步骤只会生效一次：第二次会看到状态已是 done 并直接返回。
        """
        task = self.tasks.get(task_id)
        if task is None:
            return False, {"message": "没有找到这个任务。"}
        step = task.current_step
        if step_id:
            match = next((s for s in task.steps if s.id == step_id), None)
            if match is None:
                return False, {"message": "没有找到这一步。"}
            step = match
        if step is None:
            return False, {"message": "这个任务没有待完成的步骤了。"}
        if step.status == STEP_DONE:
            return True, {"message": "这一步已经完成过了。", "already": True}

        step.status = STEP_DONE
        step.completed_at = self.clock()
        task.updated_at = self.clock()
        task.revision += 1

        event = ""
        finished = False
        if task.current_step is None:
            task.status = STATUS_COMPLETED
            if self.active_id == task.id:
                self.active_id = ""
            event = EVENT_TASK_DONE
            finished = True
            message = f"「{task.title}」全部完成。"
        else:
            event = EVENT_STEP_DONE
            message = f"完成一步，接下来：{task.current_step.text}"
        self._save()
        # 事件去重：同一个 revision 只播一次
        key = f"{event}:{task.revision}"
        play = key not in task.emitted_events
        if play:
            task.emitted_events.append(key)
            self._save()
        return True, {
            "message": message,
            "event": event if play else "",
            "event_key": key if play else "",
            "finished": finished,
            "task_id": task.id,
            "step_id": step.id,
        }

    def undo_step(self, task_id: str) -> tuple[bool, str]:
        """撤销最近完成的一步；不扣分、不播负面动作。"""
        task = self.tasks.get(task_id)
        if task is None:
            return False, "没有找到这个任务。"
        done = [s for s in task.steps if s.status == STEP_DONE]
        if not done:
            return False, "还没有已完成步骤可以撤销。"
        last = max(done, key=lambda s: s.completed_at)
        last.status = STEP_PENDING
        last.completed_at = 0.0
        task.status = STATUS_ACTIVE
        task.updated_at = self.clock()
        task.revision += 1
        if not self.active_id:
            self.active_id = task.id
        self._save()
        return True, f"已撤销「{last.text}」，可以重做。"

    def cancel(self, task_id: str) -> tuple[bool, str]:
        task = self.tasks.get(task_id)
        if task is None:
            return False, "没有找到这个任务。"
        if task.status == STATUS_CANCELLED:
            return False, "这个任务已经取消了。"
        task.status = STATUS_CANCELLED
        task.updated_at = self.clock()
        task.revision += 1
        if self.active_id == task.id:
            self.active_id = ""
        self._save()
        return True, "已取消，想继续随时能找回。"

    def reopen(self, task_id: str) -> tuple[bool, str]:
        task = self.tasks.get(task_id)
        if task is None:
            return False, "没有找到这个任务。"
        if task.status in (STATUS_DRAFT, STATUS_QUEUED, STATUS_ACTIVE, STATUS_PAUSED):
            return False, "这个任务还在。"
        task.status = STATUS_PAUSED
        task.updated_at = self.clock()
        task.revision += 1
        self._save()
        return True, "已放回进行列表。"

    def attach_focus(self, task_id: str, focus_session_id: str) -> None:
        task = self.tasks.get(task_id)
        if task is None:
            return
        task.focus_session_id = focus_session_id
        task.revision += 1
        self._save()

    # ------------------------------------------------------------ 摘要

    def summary(self) -> dict:
        active = self.active()
        return {
            "active": None if active is None else {
                "id": active.id,
                "title": active.title,
                "progress": active.progress_text,
                "current": active.current_step.text if active.current_step else "",
                "steps": [
                    {"id": s.id, "text": s.text, "done": s.status == STEP_DONE}
                    for s in active.steps
                ],
            },
            "open_count": len(self.open_tasks()),
            "completed_count": len(self.completed(limit=1000)),
        }
