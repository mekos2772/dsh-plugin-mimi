"""Qt-free local focus, quiet mode, reminders and notification history.

Focus uses elapsed monotonic time; reminders use wall-clock deadlines.
Running sessions are restored paused so offline time is never counted.
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Callable

from .companion_store import CompanionStore

MAX_HISTORY = 100
MAX_REMINDERS = 100
CHECKPOINT_S = 15.0
INTERRUPTION_GAP_S = 10.0


def _finite(value, low: float = 0.0, high: float = 253402214400.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid number")
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError("number out of bounds")
    return value


def _text(value, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("invalid text")
    return value.strip()


@dataclass
class FocusSession:
    id: str
    kind: str
    total_s: float
    remaining_s: float
    status: str = "running"
    quiet: bool = True
    pause_reason: str = ""

    @property
    def label(self) -> str:
        return "休息" if self.kind == "break" else "专注"

    @classmethod
    def restore(cls, raw: dict) -> "FocusSession":
        if not isinstance(raw, dict):
            raise ValueError("invalid focus")
        session = cls(
            id=_text(raw["id"], 80),
            kind=raw["kind"],
            total_s=_finite(raw["total_s"], 60, 240 * 60),
            remaining_s=_finite(raw["remaining_s"], 0, 240 * 60),
            status=raw["status"],
            quiet=raw.get("quiet", True),
            pause_reason=str(raw.get("pause_reason", ""))[:100],
        )
        if (
            session.kind not in ("focus", "break")
            or session.status not in ("running", "paused", "finished", "cancelled")
            or not isinstance(session.quiet, bool)
            or session.remaining_s > session.total_s
            or (session.status in ("running", "paused") and session.remaining_s <= 0)
        ):
            raise ValueError("invalid focus state")
        if session.status == "running":
            session.status = "paused"
            session.pause_reason = "程序已重启，点击继续"
        return session


@dataclass
class Reminder:
    id: str
    text: str
    due_at: float
    status: str = "scheduled"
    occurrence: int = 0

    @classmethod
    def restore(cls, raw: dict) -> "Reminder":
        if not isinstance(raw, dict):
            raise ValueError("invalid reminder")
        item = cls(
            id=_text(raw["id"], 80), text=_text(raw["text"], 200),
            due_at=_finite(raw["due_at"]), status=raw["status"],
            occurrence=int(_finite(raw.get("occurrence", 0), 0, 1_000_000)),
        )
        if item.status not in ("scheduled", "due", "done", "cancelled"):
            raise ValueError("invalid reminder status")
        return item


@dataclass
class Notice:
    id: str
    text: str
    created_at: float
    kind: str
    source_id: str = ""
    delivered: bool = False

    @classmethod
    def restore(cls, raw: dict) -> "Notice":
        if not isinstance(raw, dict):
            raise ValueError("invalid notice")
        item = cls(
            id=_text(raw["id"], 160), text=_text(raw["text"], 400),
            created_at=_finite(raw["created_at"]), kind=raw["kind"],
            source_id=str(raw.get("source_id", ""))[:80],
            delivered=raw.get("delivered", False),
        )
        if item.kind not in ("focus", "break", "reminder", "pause", "update") or not isinstance(item.delivered, bool):
            raise ValueError("invalid notice state")
        return item


class CompanionService:
    def __init__(
        self,
        store: CompanionStore | None = None,
        *,
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.wall_clock = wall_clock
        self.monotonic = monotonic
        self.focus: FocusSession | None = None
        self.manual_quiet = False
        self.reminders: list[Reminder] = []
        self.notices: list[Notice] = []
        self.revision = 0
        self._blocked_reasons: set[str] = set()
        self._last_tick = self.monotonic()
        self._last_checkpoint = self._last_tick
        self._restore()

    def _restore(self) -> None:
        if self.store is None:
            return
        raw = self.store.load()
        if not raw:
            return
        try:
            focus = FocusSession.restore(raw["focus"]) if raw.get("focus") is not None else None
            quiet = raw.get("manual_quiet", False)
            reminders = raw.get("reminders", [])
            notices = raw.get("notices", [])
            if (
                not isinstance(quiet, bool) or not isinstance(reminders, list)
                or not isinstance(notices, list)
                or len(reminders) > MAX_REMINDERS * 2 or len(notices) > MAX_HISTORY
            ):
                raise ValueError("invalid companion state")
            parsed_reminders = [Reminder.restore(item) for item in reminders]
            parsed_notices = [Notice.restore(item) for item in notices]
            if len({item.id for item in parsed_reminders}) != len(parsed_reminders):
                raise ValueError("duplicate reminder id")
            if len({item.id for item in parsed_notices}) != len(parsed_notices):
                raise ValueError("duplicate notice id")
            self.focus, self.manual_quiet = focus, quiet
            self.reminders, self.notices = parsed_reminders, parsed_notices
        except (KeyError, TypeError, ValueError, OverflowError):
            self.store.quarantine()

    @property
    def running(self) -> bool:
        return self.focus is not None and self.focus.status == "running"

    @property
    def quiet(self) -> bool:
        # Changes during a running timer apply to that timer. Pausing/ending
        # restores the independent manual setting.
        return self.focus.quiet if self.running else self.manual_quiet

    @property
    def system_blocked(self) -> bool:
        return bool(self._blocked_reasons)

    @property
    def pending_notices(self) -> list[Notice]:
        return [item for item in self.notices if not item.delivered]

    @property
    def active_reminders(self) -> list[Reminder]:
        return sorted(
            (item for item in self.reminders if item.status in ("scheduled", "due")),
            key=lambda item: item.due_at,
        )

    def to_payload(self) -> dict:
        return {
            "schema_version": 1,
            "manual_quiet": self.manual_quiet,
            "focus": asdict(self.focus) if self.focus else None,
            "reminders": [asdict(item) for item in self.reminders],
            "notices": [asdict(item) for item in self.notices],
        }

    def save(self) -> bool:
        self._last_checkpoint = self.monotonic()
        return self.store.save(self.to_payload()) if self.store is not None else True

    def _changed(self) -> None:
        self.revision += 1
        self.save()

    def _notice(self, item: Notice) -> None:
        if any(existing.id == item.id for existing in self.notices):
            return
        self.notices.append(item)
        # Prefer retaining pending notices. Due reminders themselves remain
        # accessible even when older history has reached the display limit.
        while len(self.notices) > MAX_HISTORY:
            delivered = next((n for n in self.notices if n.delivered), self.notices[0])
            self.notices.remove(delivered)

    def add_announcement(self, text: str, identifier: str = "") -> bool:
        """Keep a release announcement in the panel's history.

        Born delivered: it is shown once by the desktop pet and belongs in the
        history afterwards, not in the pending queue that drives reminders.
        """
        # Trim rather than reject: a long changelog still deserves its headline.
        body = (text or "").strip()[:400]
        if not body:
            return False
        stamp = self.wall_clock()
        self._notice(Notice(
            id=identifier or f"update-{int(stamp)}", text=body, created_at=stamp,
            kind="update", delivered=True,
        ))
        self._changed()
        return True

    def tick(self) -> None:
        mono = self.monotonic()
        elapsed = max(0.0, mono - self._last_tick)
        self._last_tick = mono
        now = self.wall_clock()
        changed = False
        if self.running and not self.system_blocked:
            if elapsed > INTERRUPTION_GAP_S:
                # Fallback for missed suspend notifications or a stopped GUI.
                # The unobserved gap cannot be counted as productive time.
                self.focus.status = "paused"
                self.focus.pause_reason = "检测到系统中断，点击继续"
                self._pause_notice(now)
                changed = True
            else:
                self.focus.remaining_s = max(0.0, self.focus.remaining_s - elapsed)
                if self.focus.remaining_s <= 0:
                    self.focus.status = "finished"
                    minutes = round(self.focus.total_s / 60)
                    ending = "可以休息 5 分钟啦。" if self.focus.kind == "focus" else "休息结束，准备好再继续吧。"
                    self._notice(Notice(
                        f"timer:{self.focus.id}", f"{minutes} 分钟{self.focus.label}结束。{ending}",
                        now, self.focus.kind, self.focus.id,
                    ))
                    changed = True
        for reminder in self.reminders:
            if reminder.status == "scheduled" and reminder.due_at <= now:
                reminder.status = "due"
                due = datetime.fromtimestamp(reminder.due_at).strftime("%m月%d日 %H:%M")
                self._notice(Notice(
                    f"reminder:{reminder.id}:{reminder.occurrence}",
                    f"提醒：{reminder.text}（约定 {due}）", now, "reminder", reminder.id,
                ))
                changed = True
        if changed:
            self._changed()
        elif self.running and mono - self._last_checkpoint >= CHECKPOINT_S:
            self.save()

    def start_focus(self, minutes: int = 25, *, kind: str = "focus") -> str:
        self.tick()
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 240:
            raise ValueError("时长请设为 1–240 分钟的整数。")
        if kind not in ("focus", "break"):
            raise ValueError("不支持的计时类型。")
        if self.focus and self.focus.status in ("running", "paused"):
            raise ValueError(f"还有一段{self.focus.label}，请先继续或取消。")
        if self.system_blocked:
            raise ValueError("解锁或恢复电脑后，再开始计时吧。")
        self.focus = FocusSession(uuid.uuid4().hex, kind, minutes * 60.0, minutes * 60.0)
        self._changed()
        return f"{minutes} 分钟{self.focus.label}开始啦，我会安静陪着你。"

    def pause_focus(self, reason: str = "手动暂停") -> str:
        self.tick()
        if not self.running:
            raise ValueError("当前没有正在进行的计时。")
        self.focus.status = "paused"
        self.focus.pause_reason = reason
        self._changed()
        return f"{self.focus.label}已暂停，准备好后可以继续。"

    def resume_focus(self) -> str:
        self.tick()
        if self.focus is None or self.focus.status != "paused":
            raise ValueError("当前没有已暂停的计时。")
        if self.system_blocked:
            raise ValueError("解锁或恢复电脑后，再继续计时吧。")
        self.focus.status = "running"
        self.focus.pause_reason = ""
        self._changed()
        return f"继续{self.focus.label}，剩余 {self.remaining_text()}。"

    def cancel_focus(self) -> str:
        self.tick()
        if self.focus is None or self.focus.status not in ("running", "paused"):
            raise ValueError("当前没有需要取消的计时。")
        self.focus.status = "cancelled"
        self._changed()
        return f"这段{self.focus.label}已取消，随时可以重新开始。"

    def set_quiet(self, enabled: bool) -> str:
        self.tick()
        if self.running:
            self.focus.quiet = bool(enabled)
        else:
            self.manual_quiet = bool(enabled)
        self._changed()
        return "勿扰已开启，提醒会先保存在列表里。" if enabled else "勿扰已关闭。"

    def dismiss_focus(self) -> str:
        if self.focus is None or self.focus.status != "finished":
            raise ValueError("当前没有已结束的计时。")
        self.focus = None
        self._changed()
        return "这段计时已结束，准备好再开始吧。"

    def _pause_notice(self, now: float) -> None:
        if self.focus is not None:
            self._notice(Notice(
                f"pause:{self.focus.id}:{uuid.uuid4().hex[:8]}",
                f"{self.focus.label}已暂停：{self.focus.pause_reason}。", now, "pause", self.focus.id,
            ))

    def system_event(self, reason: str, unavailable: bool) -> None:
        """Lock/suspend never auto-resumes focus; notifications wait for return."""
        self.tick()
        if unavailable:
            self._blocked_reasons.add(reason)
            if self.running:
                self.focus.status = "paused"
                self.focus.pause_reason = {
                    "lock": "锁屏，点击继续",
                    "suspend": "系统休眠，点击继续",
                    "session": "桌面会话已断开，点击继续",
                    "desktop": "桌面暂不可用，回来后点击继续",
                }.get(reason, "系统中断，点击继续")
                self._pause_notice(self.wall_clock())
                self._changed()
        else:
            self._blocked_reasons.discard(reason)
        self._last_tick = self.monotonic()

    def add_reminder(self, text: str, due_at: float) -> str:
        self.tick()
        text = text.strip()
        if not text or len(text) > 200:
            raise ValueError("提醒事项请填写 1–200 个字。")
        try:
            due_at = _finite(due_at)
        except ValueError:
            raise ValueError("提醒时间无效。") from None
        now = self.wall_clock()
        if not now < due_at <= now + 366 * 86400:
            raise ValueError("请选择未来 366 天以内的提醒时间。")
        if len(self.active_reminders) >= MAX_REMINDERS:
            raise ValueError("已有 100 条待处理提醒，请先完成或取消一些。")
        self.reminders.append(Reminder(uuid.uuid4().hex, text, due_at))
        self._trim_reminders()
        self._changed()
        due = datetime.fromtimestamp(due_at).strftime("%Y-%m-%d %H:%M")
        return f"记好啦：{due} 提醒你{text}。"

    def _trim_reminders(self) -> None:
        finished = [r for r in self.reminders if r.status in ("done", "cancelled")]
        remove = {r.id for r in finished[:-MAX_REMINDERS]}
        self.reminders = [r for r in self.reminders if r.id not in remove]

    def find_reminder(self, identifier: str) -> Reminder:
        matches = [r for r in self.active_reminders if r.id == identifier or r.text == identifier]
        if len(matches) != 1:
            raise ValueError("请在“提醒与通知”里选择具体事项。")
        return matches[0]

    def update_reminder(self, identifier: str, *, status: str = "", due_at: float | None = None, text: str | None = None) -> str:
        self.tick()
        reminder = self.find_reminder(identifier)
        if due_at is not None:
            try:
                due_at = _finite(due_at)
            except ValueError:
                raise ValueError("提醒时间无效。") from None
            if not self.wall_clock() < due_at <= self.wall_clock() + 366 * 86400:
                raise ValueError("请选择未来 366 天以内的提醒时间。")
        if text is not None and (not text.strip() or len(text.strip()) > 200):
            raise ValueError("提醒事项请填写 1–200 个字。")
        if status and status not in ("done", "cancelled"):
            raise ValueError("不支持的提醒状态。")
        if due_at is not None:
            reminder.due_at = due_at
            reminder.occurrence += 1
            reminder.status = "scheduled"
        if text is not None:
            reminder.text = text.strip()
        if status:
            reminder.status = status
        # Retire stale pending bubbles when their item has been handled,
        # edited or snoozed. A snooze will create a new occurrence at due time.
        for notice in self.notices:
            if notice.source_id == reminder.id:
                notice.delivered = True
        self._trim_reminders()
        self._changed()
        if status:
            return "提醒已完成。" if status == "done" else "提醒已取消。"
        due = datetime.fromtimestamp(reminder.due_at).strftime("%Y-%m-%d %H:%M")
        return f"提醒已更新：{due} · {reminder.text}"

    def clear_history(self) -> str:
        self.notices.clear()
        self._changed()
        return "最近通知已清空，待处理提醒仍在列表中。"

    def notification_batch(self, *, presentation_blocked: bool = False) -> tuple[str, tuple[str, ...]] | None:
        pending = self.pending_notices
        if self.quiet or self.system_blocked or presentation_blocked or not pending:
            return None
        if len(pending) == 1:
            text = pending[0].text
        else:
            text = f"有 {len(pending)} 条提醒与通知待查看，点击查看。"
        return text, tuple(item.id for item in pending)

    def mark_delivered(self, identifiers: tuple[str, ...]) -> None:
        ids = set(identifiers)
        changed = False
        for notice in self.notices:
            if notice.id in ids and not notice.delivered:
                notice.delivered = True
                changed = True
        if changed:
            self._changed()

    def allows_bubble(self, kind: str) -> bool:
        return not (self.quiet and kind in ("tool", "progress", "status"))

    def remaining_text(self) -> str:
        seconds = math.ceil(self.focus.remaining_s) if self.focus else 0
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def status_text(self) -> str:
        focus = self.focus
        if focus is None or focus.status == "cancelled":
            return "准备好时，陪你专注一会儿。"
        if focus.status == "finished":
            return f"{focus.label}已结束"
        if focus.status == "paused":
            return f"{focus.label}已暂停 · 剩余 {self.remaining_text()}"
        return f"{focus.label}中 · 剩余 {self.remaining_text()}"
