"""Window-free actions and snapshots for the DSH companion panel."""

from __future__ import annotations

from dataclasses import asdict
import math

from .companion import CompanionService
from .companion_commands import CompanionCommand, parse_command


class CompanionActions:
    def __init__(self, service: CompanionService, *, enabled: bool = True, default_minutes: int = 25) -> None:
        self.service = service
        self.enabled = enabled
        self.default_minutes = default_minutes if 1 <= default_minutes <= 240 else 25

    def snapshot(self) -> dict:
        service = self.service
        focus = asdict(service.focus) if service.focus else None
        if focus:
            focus["remaining_s"] = math.ceil(focus["remaining_s"])
        return {
            "enabled": self.enabled,
            "default_minutes": self.default_minutes,
            "focus": focus,
            "quiet": service.quiet,
            "system_blocked": service.system_blocked,
            "status_text": service.status_text(),
            "reminders": [asdict(item) for item in service.active_reminders],
            "notices": [asdict(item) for item in reversed(service.notices)],
            "storage_error": service.store.error if service.store else "",
            "revision": service.revision,
        }

    @staticmethod
    def _text(value, *, limit: int = 200) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"请填写 1–{limit} 个字。")
        return value.strip()

    @staticmethod
    def _minutes(value) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 240:
            raise ValueError("时长请填写 1–240 的整数分钟。")
        return value

    def _command(self, command: CompanionCommand) -> dict:
        service = self.service
        kind = command.kind
        if kind in ("focus_start", "break_start"):
            return {"message": service.start_focus(command.minutes, kind="break" if kind == "break_start" else "focus")}
        if kind == "focus_pause":
            return {"message": service.pause_focus()}
        if kind == "focus_resume":
            return {"message": service.resume_focus()}
        if kind == "focus_cancel":
            return {"message": self._cancel()}
        if kind == "quiet":
            return {"message": service.set_quiet(command.enabled)}
        if kind == "reminder_add":
            return {"message": service.add_reminder(command.text, command.due_at)}
        if kind == "reminder_cancel":
            return {"message": service.update_reminder(command.text, status="cancelled")}
        if kind == "show":
            return {"message": "提醒与通知在 DSH 的 Mimi 面板中。", "section": "reminders"}
        if kind == "status":
            service.tick()
            return {"message": service.status_text()}
        raise ValueError(command.text or "这条陪伴指令暂不支持。")

    def _cancel(self) -> str:
        focus = self.service.focus
        return self.service.dismiss_focus() if focus and focus.status == "finished" else self.service.cancel_focus()

    def execute(self, action: dict) -> dict:
        """Execute only explicit companion operations; never run arbitrary code."""
        if not self.enabled:
            raise ValueError("请先在 DSH 的 mimi-pet 设置中启用专注与提醒，再重启插件。")
        if not isinstance(action, dict):
            raise ValueError("操作格式不正确。")
        op = action.get("op")
        allowed = {
            "focus.start": {"minutes"}, "focus.break": {"minutes"},
            "focus.pause": set(), "focus.resume": set(), "focus.cancel": set(),
            "focus.dismiss": set(), "quiet.set": {"enabled"},
            "reminder.add": {"text", "due_at"},
            "reminder.update": {"id", "text", "due_at", "status"},
            "reminder.snooze": {"id", "minutes"},
            "notices.clear": set(), "command": {"text"},
        }
        if not isinstance(op, str) or op not in allowed or set(action) - {"op"} - allowed[op]:
            raise ValueError("不支持的陪伴操作。")
        service = self.service
        if op in ("focus.start", "focus.break"):
            minutes = self._minutes(action.get("minutes", 5 if op == "focus.break" else self.default_minutes))
            message = service.start_focus(minutes, kind="break" if op == "focus.break" else "focus")
        elif op == "focus.pause":
            message = service.pause_focus()
        elif op == "focus.resume":
            message = service.resume_focus()
        elif op == "focus.cancel":
            message = self._cancel()
        elif op == "focus.dismiss":
            message = service.dismiss_focus()
        elif op == "quiet.set":
            if not isinstance(action.get("enabled"), bool):
                raise ValueError("勿扰开关必须为开启或关闭。")
            message = service.set_quiet(action["enabled"])
        elif op == "reminder.add":
            message = service.add_reminder(self._text(action.get("text")), action.get("due_at"))
        elif op == "reminder.update":
            identifier = self._text(action.get("id"), limit=80)
            changes = {key: action[key] for key in ("text", "due_at", "status") if key in action}
            if not changes:
                raise ValueError("请填写要修改的提醒内容。")
            if "text" in changes:
                changes["text"] = self._text(changes["text"])
            if "due_at" in changes and (isinstance(changes["due_at"], bool) or not isinstance(changes["due_at"], (int, float))):
                raise ValueError("提醒时间无效。")
            if "status" in changes and changes["status"] not in ("done", "cancelled"):
                raise ValueError("不支持的提醒状态。")
            message = service.update_reminder(identifier, **changes)
        elif op == "reminder.snooze":
            identifier = self._text(action.get("id"), limit=80)
            minutes = self._minutes(action.get("minutes", 10))
            message = service.update_reminder(identifier, due_at=service.wall_clock() + minutes * 60)
        elif op == "notices.clear":
            message = service.clear_history()
        else:
            text = self._text(action.get("text"), limit=300)
            command = parse_command(text, now=service.wall_clock())
            if command is None:
                raise ValueError("这里接受专注和提醒指令，例如：20 分钟后提醒我喝水。")
            if text in ("专注", "开始专注"):
                command = CompanionCommand("focus_start", minutes=self.default_minutes)
            return self._command(command)
        return {"message": message}
