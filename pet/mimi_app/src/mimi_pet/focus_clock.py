"""Floating focus dial that trails the pet while a session is running.

The desktop counterpart to the DSH panel's timer: a small always-on-top dial
with the countdown, the session kind and a line of Mimi's chatter. It trails
the pet until the user drags it somewhere, after which it stays put until a
double-click hands it back. It carries the chatter itself rather than a speech
bubble, because starting a focus session turns quiet mode on and quiet mode
suppresses the bubble layer.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QWidget

DIAL_D = 104.0
CARD_W = 156.0
PAD = 8.0
GAP = 7.0
TAIL_W = 9.0
# The chatter line sits on its own plate: without it the text disappears
# against whatever desktop content happens to be behind the dial.
CAPTION_PLATE_PAD = 5.0
TIME_FONT_PX = 21
LABEL_FONT_PX = 10
CAPTION_FONT_PX = 11
CAPTION_MAX_LINES = 2
# How long one line of chatter stays up before the next one takes its turn.
CAPTION_INTERVAL_S = 180.0

FOCUS_LINES = (
    "我在这儿陪着你。",
    "再撑一会儿，就快了。",
    "别急，一件一件来。",
    "累了就抬头看看我。",
    "这会儿只做这一件事。",
)
BREAK_LINES = (
    "起来动动肩膀吧。",
    "喝口水再回来。",
    "眼睛也歇一会儿。",
)
PAUSED_LINE = "先歇会儿，我等你。"
FINISHED_LINE = "到点了，做得不错～"

ACCENT_FOCUS = QColor(0x5D, 0x81, 0xDF)
ACCENT_BREAK = QColor(0x5F, 0xD3, 0xB0)
ACCENT_PAUSED = QColor(0xFF, 0x9F, 0x43)
RING_TRACK = QColor(210, 216, 228, 220)
CARD_BG = QColor(255, 255, 255, 238)
TEXT_COLOR = QColor(38, 42, 56)
MUTED_COLOR = QColor(120, 132, 153)


def _wrap_caption(text: str, metrics: QFontMetrics, max_width: int) -> list[str]:
    """Greedy per-character wrap, capped at CAPTION_MAX_LINES."""
    lines: list[str] = []
    current = ""
    for ch in text:
        if current and metrics.horizontalAdvance(current + ch) > max_width:
            lines.append(current)
            current = ch
            if len(lines) == CAPTION_MAX_LINES:
                break
        else:
            current += ch
    if len(lines) < CAPTION_MAX_LINES and current:
        lines.append(current)
    if sum(len(line) for line in lines) < len(text) and lines:
        lines[-1] = lines[-1] + "…"
    return lines or [""]


def format_remaining(focus) -> str:
    """``MM:SS`` for a focus session, matching the DSH panel's format."""
    seconds = max(0, math.ceil(float(focus.remaining_s)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def status_label(focus) -> str:
    if focus.status == "finished":
        return "已完成"
    if focus.status == "paused":
        return "已暂停"
    return "休息" if focus.kind == "break" else "专注"


def accent_for(focus) -> QColor:
    if focus.status == "finished":
        return ACCENT_BREAK
    if focus.status == "paused":
        return ACCENT_PAUSED
    return ACCENT_BREAK if focus.kind == "break" else ACCENT_FOCUS


class FocusClock(QWidget):
    """Countdown dial that follows the pet while a session is on."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # Draggable: a hand cursor advertises it, and the user's placement wins
        # over the automatic trailing until a double-click hands it back.
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self._pinned = False
        self._drag_offset = None

        self._focus = None
        self._anchor: tuple[float, float, float] | None = None
        self._tail_on_right = False
        self._caption = ""
        self._caption_lines: list[str] = [""]

        self._time_font = QFont("Microsoft YaHei UI")
        self._time_font.setPixelSize(TIME_FONT_PX)
        self._label_font = QFont("Microsoft YaHei UI")
        self._label_font.setPixelSize(LABEL_FONT_PX)
        self._caption_font = QFont("Microsoft YaHei UI")
        self._caption_font.setPixelSize(CAPTION_FONT_PX)

        self._sync_size()
        self.hide()

    # ------------------------------------------------------------------ state

    @property
    def focus(self):
        return self._focus

    def caption(self) -> str:
        return self._caption

    def set_session(self, focus, status_text: str = "") -> None:
        """Adopt ``focus`` (a FocusSession or None) and refresh the dial.

        Cancelled and absent sessions clear the dial; the widget is hidden by
        the next ``position_near`` call.
        """
        active = focus is not None and getattr(focus, "status", "") != "cancelled"
        self._focus = focus if active else None
        self.setToolTip(status_text or "")
        if self._focus is None:
            if self._caption:
                self._caption = ""
                self._caption_lines = [""]
                self._sync_size()
            return
        elapsed = max(0.0, float(self._focus.total_s) - float(self._focus.remaining_s))
        caption = self._caption_for(self._focus, elapsed)
        if caption != self._caption:
            self._caption = caption
            self._caption_lines = _wrap_caption(
                caption, QFontMetrics(self._caption_font), int(CARD_W - PAD * 2)
            )
            self._sync_size()
        self.update()

    def _caption_for(self, focus, elapsed_s: float) -> str:
        if focus.status == "finished":
            return FINISHED_LINE
        if focus.status == "paused":
            return PAUSED_LINE
        lines = BREAK_LINES if focus.kind == "break" else FOCUS_LINES
        index = int(max(0.0, elapsed_s) // CAPTION_INTERVAL_S) % len(lines)
        return lines[index]

    def _sync_size(self) -> None:
        metrics = QFontMetrics(self._caption_font)
        height = PAD + DIAL_D + GAP + metrics.height() * len(self._caption_lines) + CAPTION_PLATE_PAD * 2 + PAD
        self.setFixedSize(int(CARD_W), int(height))

    # ---------------------------------------------------------------- geometry

    def position_near(self, root_x: float, head_y: float, pet_width: float) -> None:
        """Trail the pet, unless the user has already placed the dial."""
        self._anchor = (float(root_x), float(head_y), float(pet_width))
        if not self._pinned:
            self._apply_anchor()
        if self._focus is None:
            if self.isVisible():
                self.hide()
            return
        if not self.isVisible():
            self.show()
        self.raise_()

    # ------------------------------------------------------------------ dragging

    @property
    def pinned(self) -> bool:
        """True once the user dragged the dial away from the pet."""
        return self._pinned

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._drag_offset is None:
            return
        self.move(event.globalPosition().toPoint() - self._drag_offset)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() != Qt.MouseButton.LeftButton or self._drag_offset is None:
            return
        self._drag_offset = None
        # The user put it here on purpose; stop trailing so it stays put.
        self._pinned = True
        self._clamp_into_screen()
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag_offset = None
        self._pinned = False
        self._apply_anchor()
        event.accept()

    def _clamp_into_screen(self) -> None:
        screen = QGuiApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = max(geo.left(), min(self.x(), geo.right() - self.width()))
        y = max(geo.top(), min(self.y(), geo.bottom() - self.height()))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def _apply_anchor(self) -> None:
        if self._anchor is None:
            return
        root_x, head_y, pet_width = self._anchor
        width, height = self.width(), self.height()
        # Clear of the sprite, and below the bubble stack that grows upward.
        x = root_x + pet_width / 2.0 + 8.0
        self._tail_on_right = False
        screen = QGuiApplication.screenAt(QPoint(int(root_x), int(head_y)))
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        y = head_y - height / 2.0
        if screen is not None:
            geo = screen.availableGeometry()
            if x + width > geo.right():
                x = root_x - pet_width / 2.0 - 8.0 - width
                self._tail_on_right = True
            x = max(geo.left(), min(x, geo.right() - width))
            y = max(geo.top(), min(y, geo.bottom() - height))
        if (round(x), round(y)) != (self.x(), self.y()):
            self.move(int(round(x)), int(round(y)))

    # ----------------------------------------------------------------- painting

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        focus = self._focus
        if focus is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = accent_for(focus)

        dial = QRectF((self.width() - DIAL_D) / 2.0, PAD, DIAL_D, DIAL_D)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(CARD_BG)
        painter.drawEllipse(dial)

        ring = dial.adjusted(4.0, 4.0, -4.0, -4.0)
        pen = QPen(RING_TRACK, 5.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(ring, 0, 360 * 16)

        total = max(1e-6, float(focus.total_s))
        done = min(1.0, max(0.0, (total - float(focus.remaining_s)) / total))
        pen.setColor(accent)
        painter.setPen(pen)
        # Qt angles are 1/16 degree, 0 at 3 o'clock, positive counter-clockwise,
        # so start at 12 o'clock and sweep clockwise.
        painter.drawArc(ring, 90 * 16, int(-done * 360 * 16))

        painter.setFont(self._time_font)
        painter.setPen(TEXT_COLOR)
        painter.drawText(
            QRectF(dial.left(), dial.top() + 18.0, dial.width(), dial.height() - 36.0),
            Qt.AlignmentFlag.AlignCenter,
            format_remaining(focus),
        )
        painter.setFont(self._label_font)
        painter.setPen(MUTED_COLOR)
        painter.drawText(
            QRectF(dial.left(), dial.bottom() - 30.0, dial.width(), 18.0),
            Qt.AlignmentFlag.AlignCenter,
            status_label(focus),
        )

        painter.setFont(self._caption_font)
        painter.setPen(TEXT_COLOR)
        caption_top = dial.bottom() + GAP
        line_h = float(QFontMetrics(self._caption_font).height())
        plate = QRectF(
            PAD,
            caption_top - CAPTION_PLATE_PAD,
            self.width() - PAD * 2,
            line_h * len(self._caption_lines) + CAPTION_PLATE_PAD * 2,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(CARD_BG)
        painter.drawRoundedRect(plate, 8.0, 8.0)
        painter.setPen(TEXT_COLOR)
        for index, line in enumerate(self._caption_lines):
            painter.drawText(
                QRectF(PAD, caption_top + index * line_h, self.width() - PAD * 2, line_h),
                Qt.AlignmentFlag.AlignCenter,
                line,
            )

        # Tail pointing back at the pet, mirroring the bubble layer's.
        edge_x = dial.right() - 2.0 if self._tail_on_right else dial.left() + 2.0
        direction = -1.0 if self._tail_on_right else 1.0
        center_y = dial.center().y()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(CARD_BG)
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(edge_x, center_y - TAIL_W),
                    QPointF(edge_x, center_y + TAIL_W),
                    QPointF(edge_x + direction * TAIL_W, center_y),
                ]
            )
        )
        painter.end()
