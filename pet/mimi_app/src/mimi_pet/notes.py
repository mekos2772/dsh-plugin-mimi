"""F01 随手记便签：本地纯文本笔记。

设计边界（对应 docs/expansion/FEATURE-SPEC.md）：
* 只做本地文本，不读剪贴板、不联网、不自动采集用户正在看的内容。
* 正文上限 2000 字符；标签单个 16 字符、最多 5 个。
* 删除先进回收站，保留 7 天后才真正清除；永久删除需要显式调用。
* 多端冲突靠 revision：提交时要带 expected_revision，过期不覆盖。

Qt-free。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .debug_log import dbg
from .expansion_store import JsonStore

SCHEMA_VERSION = 1
BODY_LIMIT = 2000
TAG_LIMIT = 5
TAG_LEN = 16
NOTE_LIMIT = 1000
TRASH_RETENTION_S = 7 * 24 * 3600

STATE_ACTIVE = "active"
STATE_ARCHIVED = "archived"
STATE_TRASHED = "trashed"


class NoteError(ValueError):
    """可以直接展示给用户的内容错误。"""


@dataclass
class Note:
    id: str
    body: str
    tags: list[str] = field(default_factory=list)
    pinned: bool = False
    status: str = STATE_ACTIVE
    created_at: float = 0.0
    updated_at: float = 0.0
    deleted_at: float = 0.0
    revision: int = 1
    linked_task_ids: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        """列表用标题：第一行非空文本。"""
        for line in self.body.splitlines():
            text = line.strip().lstrip("#").strip()
            if text:
                return text[:40]
        return "（空便签）"

    @property
    def excerpt(self) -> str:
        lines = [ln.strip() for ln in self.body.splitlines() if ln.strip()]
        rest = " ".join(lines[1:]) if len(lines) > 1 else ""
        return rest[:60]


def _now() -> float:
    return time.time()


def normalise_tags(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [part for part in raw.replace("，", ",").split(",")]
    if not isinstance(raw, (list, tuple)):
        raise NoteError("标签格式不对。")
    seen: list[str] = []
    for item in raw:
        text = str(item).strip().lstrip("#")
        if not text:
            continue
        if len(text) > TAG_LEN:
            raise NoteError(f"单个标签最多 {TAG_LEN} 个字。")
        if text not in seen:
            seen.append(text)
    if len(seen) > TAG_LIMIT:
        raise NoteError(f"最多 {TAG_LIMIT} 个标签。")
    return seen


class NoteBook:
    """便签集合；所有写操作都返回 (ok, message)。

    注意：``get`` / ``list`` / ``search`` 返回的是**活的** Note 对象，
    ``revision`` 会随写入变化。界面要拿来做冲突检测时，必须在提交前
    把 revision 整数取出来保存（快照里本来就是 int），不能更新后再读。
    """

    def __init__(self, store: JsonStore | None = None, *, clock=_now) -> None:
        self.store = store or JsonStore.for_user_file(
            "notes.json", schema_version=SCHEMA_VERSION, override_env="MIMI_NOTES_PATH"
        )
        self.clock = clock
        self.notes: dict[str, Note] = {}
        self.error = ""
        self._load()

    # ------------------------------------------------------------ 持久化

    def _load(self) -> None:
        payload = self.store.load()
        self.error = self.store.error
        for raw in payload.get("notes", []):
            try:
                note = Note(
                    id=str(raw["id"]),
                    body=str(raw["body"]),
                    tags=list(raw.get("tags", [])),
                    pinned=bool(raw.get("pinned", False)),
                    status=str(raw.get("status", STATE_ACTIVE)),
                    created_at=float(raw.get("created_at", 0.0)),
                    updated_at=float(raw.get("updated_at", 0.0)),
                    deleted_at=float(raw.get("deleted_at", 0.0)),
                    revision=int(raw.get("revision", 1)),
                    linked_task_ids=list(raw.get("linked_task_ids", [])),
                )
            except (KeyError, TypeError, ValueError) as exc:
                dbg(f"note entry rejected: {exc}")
                continue
            if note.status not in (STATE_ACTIVE, STATE_ARCHIVED, STATE_TRASHED):
                note.status = STATE_ACTIVE
            self.notes[note.id] = note
        self.purge_expired()

    def _save(self) -> bool:
        payload = {"notes": [asdict(n) for n in self.notes.values()]}
        ok = self.store.save(payload)
        self.error = self.store.error
        return ok

    # ------------------------------------------------------------ 查询

    def get(self, note_id: str) -> Note | None:
        return self.notes.get(note_id)

    def _alive(self) -> list[Note]:
        return [n for n in self.notes.values() if n.status != STATE_TRASHED]

    def list(
        self, *, status: str = STATE_ACTIVE, pinned_first: bool = True
    ) -> list[Note]:
        items = [n for n in self.notes.values() if n.status == status]
        items.sort(key=lambda n: (not n.pinned if pinned_first else 0, -n.updated_at))
        return items

    def trashed(self) -> list[Note]:
        return self.list(status=STATE_TRASHED, pinned_first=False)

    def search(self, query: str, *, limit: int = 50) -> list[Note]:
        needle = (query or "").strip().casefold()
        items = self._alive()
        if not needle:
            return self.list()[:limit]
        hits = []
        for note in items:
            haystack = f"{note.body}\n{' '.join(note.tags)}".casefold()
            if needle in haystack:
                hits.append(note)
        hits.sort(key=lambda n: (not n.pinned, -n.updated_at))
        return hits[:limit]

    def count(self) -> int:
        return len(self._alive())

    # ------------------------------------------------------------ 写操作

    def create(
        self, body: str, *, tags=None, pinned: bool = False
    ) -> tuple[Note | None, str]:
        text = (body or "").strip()
        if not text:
            raise NoteError("便签内容不能为空。")
        if len(text) > BODY_LIMIT:
            raise NoteError(f"便签最多 {BODY_LIMIT} 个字。")
        if self.count() >= NOTE_LIMIT:
            return None, f"便签已达到 {NOTE_LIMIT} 条上限，请先清理或导出。"

        now = self.clock()
        note = Note(
            id=uuid.uuid4().hex[:12],
            body=text,
            tags=normalise_tags(tags),
            pinned=bool(pinned),
            created_at=now,
            updated_at=now,
        )
        self.notes[note.id] = note
        saved = self._save()
        if not saved:
            # 先说要命的那句：内容只在内存里，重启就没了。
            message = "没能写入磁盘，重启后会丢失。\n" + (self.error or "")
            return note, message.strip()
        return note, "已记下。"

    def update(
        self,
        note_id: str,
        *,
        body: str | None = None,
        tags=None,
        pinned: bool | None = None,
        expected_revision: int | None = None,
    ) -> tuple[bool, str]:
        note = self.notes.get(note_id)
        if note is None or note.status == STATE_TRASHED:
            return False, "这条便签已经删除了。"
        # 冲突检查必须在改动之前；revision 是本地对象上的真实版本，
        # 每次成功更新都要 +1，否则过一次期的旧 revision 会再次通过。
        if expected_revision is not None and note.revision != expected_revision:
            return False, "这条便签在别处被改过了，已保留你的草稿，请确认后再保存。"

        if body is not None:
            text = body.strip()
            if not text:
                return False, "便签内容不能为空。"
            if len(text) > BODY_LIMIT:
                return False, f"便签最多 {BODY_LIMIT} 个字。"
            note.body = text
        if tags is not None:
            note.tags = normalise_tags(tags)
        if pinned is not None:
            note.pinned = bool(pinned)
        note.updated_at = self.clock()
        note.revision += 1
        saved = self._save()
        return True, ("已更新。" if saved else "已更新，但保存失败。")

    def archive(self, note_id: str, archived: bool = True) -> tuple[bool, str]:
        note = self.notes.get(note_id)
        if note is None or note.status == STATE_TRASHED:
            return False, "这条便签已经删除了。"
        note.status = STATE_ARCHIVED if archived else STATE_ACTIVE
        note.updated_at = self.clock()
        note.revision += 1
        self._save()
        return True, ("已归档。" if archived else "已恢复到便签列表。")

    def delete(self, note_id: str) -> tuple[bool, str]:
        note = self.notes.get(note_id)
        if note is None:
            return False, "没有找到这条便签。"
        if note.status == STATE_TRASHED:
            return False, "这条便签已经在回收站里了。"
        note.status = STATE_TRASHED
        note.deleted_at = self.clock()
        note.revision += 1
        self._save()
        return True, "已移到回收站，7 天内可以恢复。"

    def restore(self, note_id: str) -> tuple[bool, str]:
        note = self.notes.get(note_id)
        if note is None or note.status != STATE_TRASHED:
            return False, "回收站里没有这条便签。"
        note.status = STATE_ACTIVE
        note.deleted_at = 0.0
        note.updated_at = self.clock()
        note.revision += 1
        self._save()
        return True, "已恢复。"

    def purge(self, note_id: str) -> tuple[bool, str]:
        note = self.notes.get(note_id)
        if note is None:
            return False, "没有找到这条便签。"
        if note.status != STATE_TRASHED:
            return False, "只有回收站里的便签才能永久删除。"
        del self.notes[note_id]
        self._save()
        return True, "已永久删除。"

    def purge_expired(self) -> int:
        """清掉超过保留期的回收站便签；返回清理条数。"""
        deadline = self.clock() - TRASH_RETENTION_S
        expired = [
            nid for nid, n in self.notes.items()
            if n.status == STATE_TRASHED and 0 < n.deleted_at < deadline
        ]
        for nid in expired:
            del self.notes[nid]
        if expired:
            self._save()
        return len(expired)

    def link_task(self, note_id: str, task_id: str) -> None:
        note = self.notes.get(note_id)
        if note is None or task_id in note.linked_task_ids:
            return
        note.linked_task_ids.append(task_id)
        note.revision += 1
        self._save()

    # ------------------------------------------------------------ 导出

    def export_markdown(self) -> str:
        parts: list[str] = ["# Mimi 便签导出\n"]
        for note in sorted(self._alive(), key=lambda n: -n.updated_at):
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(note.updated_at or note.created_at))
            parts.append(f"## {note.title}\n")
            meta = [stamp]
            if note.tags:
                meta.append(" ".join(f"#{t}" for t in note.tags))
            if note.pinned:
                meta.append("置顶")
            if note.status == STATE_ARCHIVED:
                meta.append("已归档")
            parts.append("_" + " · ".join(meta) + "_\n")
            parts.append(note.body + "\n")
        return "\n".join(parts)

    def write_export(self, path: Path) -> tuple[bool, str]:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.export_markdown(), encoding="utf-8")
            return True, f"已导出到 {path.name}。"
        except OSError as exc:
            dbg(f"note export failed: {exc}")
            return False, "导出失败，请换一个位置再试。"
