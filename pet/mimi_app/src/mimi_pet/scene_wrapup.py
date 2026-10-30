"""F03 下班收尾卡 + F07 可收起的小布景。

把两件事放在一个模块，因为它们的冲突面完全重叠：
收尾会改布景（关灯/隐藏），而用户随后可能又手动布景——必须用 revision
保证"迟到的旧命令"不会覆盖用户的新布局。

设计边界（对应 docs/expansion/FEATURE-SPEC.md）：
* 收尾只是仪式，不关机、不取消任务、不改真实桌面。
* 布景只是宠物自己的装饰；普通模式鼠标穿透，编辑模式才可拖动。
* 断屏/负坐标用归一化锚点 + 可见矩形校正，保证至少能抓回来。

Qt-free（绘制在 renderer / qt_window，这里只管状态）。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date

from .debug_log import dbg
from .expansion_store import JsonStore

SCHEMA_VERSION = 1
MAX_SCENE_ITEMS = 3
MAX_SNAPSHOTS = 3
NEXT_STEP_LIMIT = 160
NOTE_LIMIT = 3
TASK_LIMIT = 3
LAYOUTS = ("follow", "fixed")
SCENE_ITEMS = ("books", "lamp", "cushion", "plant")


class SceneError(ValueError):
    """可以展示给用户的内容错误。"""


def _now() -> float:
    return time.time()


# ---------------------------------------------------------------- F07 布景


@dataclass
class SceneItem:
    id: str
    asset_id: str
    offset_x: float = 0.0      # 相对角色根节点，逻辑像素
    offset_y: float = 0.0
    scale: float = 1.0
    z: int = 0                 # 负=角色后，正=角色前
    light_on: bool = False
    light_level: int = 1


@dataclass
class SceneState:
    visible: bool = False
    placement: str = "follow"
    monitor_hint: str = ""
    anchor_x: float = 0.5      # 归一化，仅 fixed 模式使用
    anchor_y: float = 0.8
    items: list[SceneItem] = field(default_factory=list)
    revision: int = 1


class SceneService:
    """小布景的可见性、布局与编辑态。"""

    def __init__(self, store: JsonStore | None = None, *, clock=_now) -> None:
        self.store = store or JsonStore.for_user_file(
            "scene.json", schema_version=SCHEMA_VERSION, override_env="MIMI_SCENE_PATH"
        )
        self.clock = clock
        self.scene = SceneState()
        self.editing = False
        self._draft: SceneState | None = None
        self.error = ""
        self._load()

    # ---------------------------------------------------------- 持久化

    def _load(self) -> None:
        payload = self.store.load()
        self.error = self.store.error
        raw = payload.get("scene")
        if not isinstance(raw, dict):
            return
        try:
            items = [
                SceneItem(
                    id=str(i["id"]),
                    asset_id=str(i["asset_id"]),
                    offset_x=float(i.get("offset_x", 0.0)),
                    offset_y=float(i.get("offset_y", 0.0)),
                    scale=float(i.get("scale", 1.0)),
                    z=int(i.get("z", 0)),
                    light_on=bool(i.get("light_on", False)),
                    light_level=int(i.get("light_level", 1)),
                )
                for i in raw.get("items", [])
            ]
        except (KeyError, TypeError, ValueError) as exc:
            dbg(f"scene items rejected: {exc}")
            return
        placement = str(raw.get("placement", "follow"))
        self.scene = SceneState(
            visible=bool(raw.get("visible", False)),
            placement=placement if placement in LAYOUTS else "follow",
            monitor_hint=str(raw.get("monitor_hint", "")),
            anchor_x=float(raw.get("anchor_x", 0.5)),
            anchor_y=float(raw.get("anchor_y", 0.8)),
            items=[i for i in items if i.asset_id in SCENE_ITEMS][:MAX_SCENE_ITEMS],
            revision=int(raw.get("revision", 1)),
        )

    def _save(self) -> bool:
        ok = self.store.save({"scene": asdict(self.scene)})
        self.error = self.store.error
        return ok

    # ---------------------------------------------------------- 展示

    def show(self) -> tuple[bool, str]:
        self.scene.visible = True
        self.scene.revision += 1
        self._save()
        return True, "小空间打开啦。"

    def hide(self) -> tuple[bool, str]:
        """关闭立即生效，不等动画。"""
        self.scene.visible = False
        self.scene.revision += 1
        self._save()
        return True, "小空间收好了。"

    @property
    def is_visible(self) -> bool:
        return self.scene.visible

    # ---------------------------------------------------------- 编辑

    def begin_edit(self) -> tuple[bool, str]:
        if self.editing:
            return False, "已经在编辑了。"
        self._draft = SceneState(**{**asdict(self.scene), "items": [
            SceneItem(**asdict(i)) for i in self.scene.items
        ]})
        self.editing = True
        return True, "拖动摆一下吧。"

    def cancel_edit(self) -> tuple[bool, str]:
        """取消不落盘，恢复进入编辑前的样子。"""
        if not self.editing:
            return False, "当前不在编辑。"
        if self._draft is not None:
            self.scene = self._draft
        self._draft = None
        self.editing = False
        return True, "已恢复原来的摆放。"

    def commit_edit(self) -> tuple[bool, str]:
        if not self.editing:
            return False, "当前不在编辑。"
        self._draft = None
        self.editing = False
        self.scene.revision += 1
        saved = self._save()
        return True, ("摆好了。" if saved else "摆好了，但保存失败。")

    def add_item(self, asset_id: str) -> tuple[bool, str]:
        if asset_id not in SCENE_ITEMS:
            return False, "没有这个物件。"
        if any(i.asset_id == asset_id for i in self.scene.items):
            return False, "这个物件已经摆出来了。"
        if len(self.scene.items) >= MAX_SCENE_ITEMS:
            return False, f"最多摆 {MAX_SCENE_ITEMS} 个物件。"
        # 默认把物件摆在角色两侧，避免叠在身上看不见。
        # 摆在角色两侧：角色宽约 150px 居中，所以 ±70 以外才不会被身体挡住。
        # cushion 故意留在正中（后景层），像垫在身下。
        slots = {"books": (-74.0, -6.0), "cushion": (0.0, -4.0),
                 "lamp": (80.0, -6.0), "plant": (92.0, -16.0)}
        dx, dy = slots.get(asset_id, (40.0 * len(self.scene.items), 0.0))
        self.scene.items.append(SceneItem(
            id=uuid.uuid4().hex[:8], asset_id=asset_id,
            offset_x=dx, offset_y=dy,
            z=-1 if asset_id in ("books", "cushion") else 1,
        ))
        if self.editing:
            return True, "已加入，记得保存。"
        self.scene.revision += 1
        self._save()
        return True, "摆好了。"

    def remove_item(self, item_id: str) -> tuple[bool, str]:
        before = len(self.scene.items)
        self.scene.items = [i for i in self.scene.items if i.id != item_id]
        if len(self.scene.items) == before:
            return False, "没有这个物件。"
        if not self.editing:
            self.scene.revision += 1
            self._save()
        return True, "已经收起来了。"

    def move_item(self, item_id: str, dx: float, dy: float) -> bool:
        item = next((i for i in self.scene.items if i.id == item_id), None)
        if item is None:
            return False
        item.offset_x += float(dx)
        item.offset_y += float(dy)
        return True

    def toggle_lamp(self) -> tuple[bool, str]:
        lamp = next((i for i in self.scene.items if i.asset_id == "lamp"), None)
        if lamp is None:
            return False, "还没有摆小灯。"
        lamp.light_on = not lamp.light_on
        if not self.editing:
            self.scene.revision += 1
            self._save()
        return True, ("小灯亮了。" if lamp.light_on else "小灯关了。")

    def set_placement(self, placement: str) -> tuple[bool, str]:
        if placement not in LAYOUTS:
            return False, "摆放方式只有跟随或固定。"
        self.scene.placement = placement
        self.scene.revision += 1
        self._save()
        return True, ("跟着 Mimi 走。" if placement == "follow" else "固定在这个角落。")

    # ---------------------------------------------------------- 收尾联动

    def apply_wrapup(self, choice: str, *, expected_revision: int | None = None) -> tuple[bool, str]:
        """F03 收尾调用：只改必要字段，不重写整个布局。

        ``expected_revision`` 不匹配说明用户在这之后手动改过布景，
        此时放弃这次收尾动作，避免把新布局覆盖回旧状态。
        """
        if expected_revision is not None and self.scene.revision != expected_revision:
            return False, "布景刚被改过，收尾没有覆盖你的摆放。"
        if choice == "keep":
            return True, ""
        if choice == "hide":
            self.scene.visible = False
        elif choice == "lamp_off":
            lamp = next((i for i in self.scene.items if i.asset_id == "lamp"), None)
            if lamp is not None:
                lamp.light_on = False
        self.scene.revision += 1
        self._save()
        return True, "小空间收好了。"


# ---------------------------------------------------------------- F03 收尾


@dataclass
class Wrapup:
    id: str
    local_date: str
    note_ids: list[str] = field(default_factory=list)
    task_ids: list[str] = field(default_factory=list)
    title_snapshots: list[str] = field(default_factory=list)
    next_step: str = ""
    scene_choice: str = "keep"
    created_at: float = 0.0
    revision: int = 1
    linked_next_task_id: str = ""


class WrapupService:
    """下班收尾：记录今天留下什么、明天从哪开始。"""

    CHOICES = ("keep", "hide", "lamp_off")

    def __init__(self, store: JsonStore | None = None, *, clock=_now) -> None:
        self.store = store or JsonStore.for_user_file(
            "wrapups.json", schema_version=SCHEMA_VERSION, override_env="MIMI_WRAPUP_PATH"
        )
        self.clock = clock
        self.wrapups: dict[str, Wrapup] = {}
        self.error = ""
        self._load()

    def _load(self) -> None:
        payload = self.store.load()
        self.error = self.store.error
        for raw in payload.get("wrapups", []):
            try:
                item = Wrapup(
                    id=str(raw["id"]),
                    local_date=str(raw["local_date"]),
                    note_ids=list(raw.get("note_ids", [])),
                    task_ids=list(raw.get("task_ids", [])),
                    title_snapshots=list(raw.get("title_snapshots", [])),
                    next_step=str(raw.get("next_step", "")),
                    scene_choice=str(raw.get("scene_choice", "keep")),
                    created_at=float(raw.get("created_at", 0.0)),
                    revision=int(raw.get("revision", 1)),
                    linked_next_task_id=str(raw.get("linked_next_task_id", "")),
                )
            except (KeyError, TypeError, ValueError) as exc:
                dbg(f"wrapup rejected: {exc}")
                continue
            self.wrapups[item.id] = item

    def _save(self) -> bool:
        ok = self.store.save({"wrapups": [asdict(w) for w in self.wrapups.values()]})
        self.error = self.store.error
        return ok

    def today(self) -> Wrapup | None:
        day = date.fromtimestamp(self.clock()).isoformat()
        same_day = [w for w in self.wrapups.values() if w.local_date == day]
        return max(same_day, key=lambda w: w.created_at) if same_day else None

    def latest(self) -> Wrapup | None:
        return max(self.wrapups.values(), key=lambda w: w.created_at, default=None)

    def save(
        self,
        *,
        note_ids: list[str] | None = None,
        task_ids: list[str] | None = None,
        title_snapshots: list[str] | None = None,
        next_step: str = "",
        scene_choice: str = "keep",
    ) -> tuple[Wrapup, str]:
        """保存收尾。允许全空——不想留什么也能结束一天。"""
        step = (next_step or "").strip()
        if len(step) > NEXT_STEP_LIMIT:
            raise SceneError(f"明天第一步最多 {NEXT_STEP_LIMIT} 个字。")
        if scene_choice not in self.CHOICES:
            raise SceneError("小空间选项不对。")
        now = self.clock()
        item = Wrapup(
            id=uuid.uuid4().hex[:12],
            local_date=date.fromtimestamp(now).isoformat(),
            note_ids=list(note_ids or [])[:NOTE_LIMIT],
            task_ids=list(task_ids or [])[:TASK_LIMIT],
            title_snapshots=list(title_snapshots or [])[:NOTE_LIMIT + TASK_LIMIT],
            next_step=step,
            scene_choice=scene_choice,
            created_at=now,
        )
        self.wrapups[item.id] = item
        saved = self._save()
        message = "今天收好了。"
        if item.next_step:
            message += f" 明天先做：{item.next_step}"
        if not saved:
            message += "\n" + (self.error or "保存失败。")
        return item, message

    def link_next_task(self, wrapup_id: str, task_id: str) -> None:
        item = self.wrapups.get(wrapup_id)
        if item is None:
            return
        item.linked_next_task_id = task_id
        item.revision += 1
        self._save()

    def pending_next_step(self) -> tuple[str, str]:
        """返回 (wrapup_id, 下一步)；没有就返回空。"""
        latest = self.latest()
        if latest is None or not latest.next_step:
            return "", ""
        return latest.id, latest.next_step
