"""Pet feedback for the DSH companion panel; never creates a management window."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, Signal

from .companion import CompanionService
from .companion_commands import parse_command
from .companion_protocol import CompanionActions
from . import menu_labels


class CompanionController(QObject):
    bubble_requested = Signal(str, str)
    panel_requested = Signal(str)

    def __init__(self, window, service: CompanionService, *, enabled: bool = True, default_minutes: int = 25) -> None:
        super().__init__(window)
        self.window = window
        self.engine = window.engine
        self.service = service
        self.actions = CompanionActions(service, enabled=enabled, default_minutes=default_minutes)
        # 造型摘要挂在 actions 上：snapshot() 由它生成，跨对象取属性会取不到。
        self.actions.outfit_catalog = None
        self.actions.current_outfit_id = ""
        self.actions.hub = None
        # Set by qt_app once the floating dial exists; None keeps this testable
        # without a Qt widget.
        self.clock = None
        # 造型能力摘要由 qt_app 注入；None 表示这次构建没有造型目录。
        self.outfit_catalog = None
        # 扩展功能的总服务，同样挂在 actions 上供快照读取。
        self.hub = None
        self.current_outfit_id = ""
        self.can_present: Callable[[], bool] = lambda: True
        self.is_dsh_busy: Callable[[], bool] = lambda: False
        self._last_feedback = -1e9
        self._last_focus_id = ""
        self._startup_warning = service.store.error if service.store else ""

    def _feedback(self, text: str) -> None:
        error = self.service.store.error if self.service.store else ""
        if error and error not in text:
            text += "\n" + error
        self._last_feedback = self.service.monotonic()
        self.bubble_requested.emit(text, "companion")

    def _notify_focus_started(self, message: str) -> None:
        """专注开始后把任务关联起来；没有 hub 时静默跳过。"""
        hub = getattr(self, "hub", None)
        if hub is None or not message:
            return
        focus = self.service.focus
        if focus is None:
            return
        ok, note = hub.focus_started(focus.id, minutes=max(1, int(focus.total_s // 60)))
        if ok and note:
            self._feedback(note)

    def invoke(self, action: Callable, *args, **kwargs) -> bool:
        try:
            message = action(*args, **kwargs)
        except ValueError as exc:
            self._feedback(str(exc))
            return False
        self.refresh()
        if message:
            self._feedback(message)
        return True

    def handle_command(self, text: str) -> bool | None:
        if not self.actions.enabled or parse_command(text, now=self.service.wall_clock()) is None:
            return None
        try:
            result = self.actions.execute({"op": "command", "text": text})
        except ValueError as exc:
            self._feedback(str(exc))
            return True
        self.refresh()
        focus = self.service.focus
        if focus is not None and focus.id != self._last_focus_id:
            # 新的一段专注开始了：与当前任务建立关联。
            self._last_focus_id = focus.id
            self._notify_focus_started(result.get("message", ""))
        if result.get("section"):
            self.request_panel(result["section"])
        elif result.get("message"):
            self._feedback(result["message"])
        return True

    def refresh(self) -> None:
        outfit = getattr(self.engine, "outfit", None)
        self.actions.current_outfit_id = outfit.id if outfit is not None else ""
        enabled = self.actions.enabled
        self.engine.set_companion_mode(
            enabled and self.service.running, enabled and self.service.quiet, busy=self.is_dsh_busy(),
        )
        if self.clock is not None:
            self.clock.set_session(
                self.service.focus if enabled else None, self.service.status_text(),
            )
        if enabled:
            self.window.setToolTip(
                self.service.status_text() + "\n在 DSH 的 Mimi 面板中管理专注与提醒。"
                + ("\n勿扰中 · 提问和批准仍可处理" if self.service.quiet else "")
            )

    def tick(self) -> None:
        if not self.actions.enabled:
            return
        self.service.tick()
        self.refresh()
        self.engine.companion_check()
        if self._startup_warning and self.can_present():
            self._feedback(self._startup_warning)
            self._startup_warning = ""
        batch = self.service.notification_batch(
            presentation_blocked=not self.can_present()
            or self.service.monotonic() - self._last_feedback < 3.0,
        )
        if batch is not None:
            text, identifiers = batch
            self._feedback(text + "\n点击后可回到 DSH 查看。")
            self.service.mark_delivered(identifiers)
            self.refresh()

    def request_panel(self, section: str = "reminders") -> None:
        self.panel_requested.emit(section)
        self._feedback("请回到 DSH，在会话顶部打开 Mimi 查看专注与提醒。")

    def build_menu(self, menu) -> None:
        if not self.actions.enabled:
            return
        # The focus label is runtime text; a long one would widen the whole
        # popup, so the row is capped and the full text moves to the tooltip.
        text = self.service.status_text()
        status = menu_labels.add_item(menu, text, enabled=False)
        status.setToolTip(text)
        manage = menu.addAction("在 DSH 中管理专注与提醒")
        manage.triggered.connect(lambda: self.request_panel())
        menu.addSeparator()
