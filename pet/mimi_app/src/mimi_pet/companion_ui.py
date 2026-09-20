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
        # Set by qt_app once the floating dial exists; None keeps this testable
        # without a Qt widget.
        self.clock = None
        self.can_present: Callable[[], bool] = lambda: True
        self.is_dsh_busy: Callable[[], bool] = lambda: False
        self._last_feedback = -1e9
        self._startup_warning = service.store.error if service.store else ""

    def _feedback(self, text: str) -> None:
        error = self.service.store.error if self.service.store else ""
        if error and error not in text:
            text += "\n" + error
        self._last_feedback = self.service.monotonic()
        self.bubble_requested.emit(text, "companion")

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
        if result.get("section"):
            self.request_panel(result["section"])
        elif result.get("message"):
            self._feedback(result["message"])
        return True

    def refresh(self) -> None:
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
