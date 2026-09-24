"""Floating playback bar mirroring the system media card (SMTC).

The desktop counterpart to whatever plays: a small always-on-top card with
the track title, artist, progress and prev / play-pause / next buttons. It
trails the pet on its left, mirroring the focus dial on the right, until the
user drags it somewhere; a double-click hands it back to the pet.

Closed by default. The pet's right-click menu opens it, and it stays closed
until asked for — detecting a track never pops the card on screen by itself.

Read-only against the world: every action is forwarded to the SMTC session
via signals — this widget never talks to any media API itself.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
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

# Wide enough that the title column survives the fourth (close) button:
# at 276 the title was squeezed to 112px, at 300 it keeps 136px.
BAR_W = 300.0
BAR_H = 54.0
PAD = 10.0
BTN_W = 26.0
BTN_H = 22.0
BTN_GAP = 4.0
# prev / toggle / next / close, laid out left to right.
BTN_COUNT = 4
BTN_PREV, BTN_TOGGLE, BTN_NEXT, BTN_CLOSE = 0, 1, 2, 3
PROGRESS_H = 3.0
DRAG_SLOP_PX = 4

CARD_BG = QColor(25, 30, 48, 248)
CARD_BORDER = QColor(126, 151, 218, 85)
TEXT_COLOR = QColor(0xED, 0xF2, 0xFF)
MUTED_COLOR = QColor(159, 176, 208)
ACCENT = QColor(0x71, 0x98, 0xFF)
TRACK_COLOR = QColor(126, 151, 218, 70)
CLOSE_COLOR = QColor(159, 176, 208)


class MediaBar(QWidget):
    """Playback card that follows the pet while the user keeps it open."""

    toggle_requested = Signal()
    next_requested = Signal()
    prev_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setFixedSize(int(BAR_W), int(BAR_H))

        self._title = ""
        self._artist = ""
        self._status = ""
        self._position = 0
        self._duration = 0
        self._open = False
        self._pinned = False
        self._drag_offset = None
        self._press_global: QPoint | None = None
        self._anchor: tuple[float, float, float] | None = None

        self._title_font = QFont("Microsoft YaHei UI")
        self._title_font.setPixelSize(12)
        self._artist_font = QFont("Microsoft YaHei UI")
        self._artist_font.setPixelSize(10)
        self._note_font = QFont("Microsoft YaHei UI")
        self._note_font.setPixelSize(15)
        self.hide()

    # ------------------------------------------------------------------ state

    @property
    def is_open(self) -> bool:
        """Whether the user asked for the bar. Closed until they do."""
        return self._open

    def open_bar(self) -> None:
        self._open = True
        if not self._pinned:
            # Re-trail the pet, but never move a card the user placed.
            self._apply_anchor()
        self._reveal()

    def close_bar(self) -> None:
        self._open = False
        self.hide()

    def toggle(self) -> None:
        if self._open:
            self.close_bar()
        else:
            self.open_bar()

    def _reveal(self) -> None:
        """Show the card, but only once it can be placed beside the pet.

        ``position_near`` owns the pet rectangle; until it has run once there
        is nowhere correct to put the card, so opening waits for it rather than
        flashing in the screen corner. A pinned card is never re-anchored, so
        reopening returns it exactly where the user dragged it.
        """
        if not self._open or not self._title or self.isVisible():
            return
        if self._anchor is None:
            return
        self.show()
        self.raise_()
        self.update()

    def set_track(self, track) -> None:
        """Adopt a MediaTrack snapshot (or None when nothing is playing)."""
        if track is None:
            self._title = self._artist = self._status = ""
            self._position = self._duration = 0
            self.hide()
            return
        self._title = track.title
        self._artist = track.artist
        self._status = track.status
        self._position = track.position_ms
        self._duration = track.duration_ms
        self.setToolTip(track.display())
        self._reveal()
        if self.isVisible():
            self.update()

    # ---------------------------------------------------------------- geometry

    def position_near(self, pet_left: float, pet_top: float,
                      pet_width: float, pet_height: float) -> None:
        """Trail the pet's left edge, unless the user already placed the bar.

        Takes the pet window's actual rectangle: the rig pivot is not the
        window centre, so deriving the rect from ``root_x`` alone overlaps the
        two always-on-top windows and the pet swallows the bar's clicks.
        """
        self._anchor = (float(pet_left), float(pet_top),
                        float(pet_width), float(pet_height))
        if not self._pinned:
            self._apply_anchor()
        self._reveal()

    def _apply_anchor(self) -> None:
        if self._anchor is None:
            return
        pet_left, pet_top, pet_width, pet_height = self._anchor
        width, height = self.width(), self.height()
        # Left of the sprite with a clear gap (the focus dial owns the right).
        x = pet_left - 8.0 - width
        y = pet_top + (pet_height - height) / 2.0
        # Pick the monitor from the pet's centre: its top-left corner sits on
        # a seam (or briefly off every screen) far more often than the middle,
        # and screenAt() would then hand back the wrong monitor or None.
        screen = QGuiApplication.screenAt(
            QPoint(int(pet_left + pet_width / 2.0), int(pet_top + pet_height / 2.0))
        )
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            if x < geo.left():
                x = pet_left + pet_width + 8.0  # fall back to the right side
            x = max(geo.left(), min(x, geo.right() - width))
            y = max(geo.top(), min(y, geo.bottom() - height))
        if (round(x), round(y)) != (self.x(), self.y()):
            self.move(int(round(x)), int(round(y)))

    # ---------------------------------------------------------------- buttons

    def _button_rects(self) -> list[QRectF]:
        """prev / toggle / next / close, right-aligned and vertically centred."""
        right = BAR_W - PAD
        top = (BAR_H - BTN_H) / 2.0
        rects = []
        for index in range(BTN_COUNT):
            left = right - (BTN_COUNT - index) * BTN_W - (BTN_COUNT - 1 - index) * BTN_GAP
            rects.append(QRectF(left, top, BTN_W, BTN_H))
        return rects

    def _hit_button(self, pos: QPoint) -> int:
        point = QPointF(pos)
        for index, rect in enumerate(self._button_rects()):
            if rect.contains(point):
                return index
        return -1

    # ------------------------------------------------------------------ mouse

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        button = self._hit_button(event.position().toPoint())
        if button == BTN_PREV:
            self.prev_requested.emit()
        elif button == BTN_TOGGLE:
            self.toggle_requested.emit()
        elif button == BTN_NEXT:
            self.next_requested.emit()
        elif button == BTN_CLOSE:
            # Close, don't drag: the user asked for this card to go away.
            self.close_bar()
        else:
            self._press_global = event.globalPosition().toPoint()
            self._drag_offset = None
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._press_global is None:
            return
        current = event.globalPosition().toPoint()
        # A page of pixels of slop keeps an imprecise button click from
        # silently "dragging" (and then pinning) the bar.
        if self._drag_offset is None:
            delta = current - self._press_global
            if abs(delta.x()) < DRAG_SLOP_PX and abs(delta.y()) < DRAG_SLOP_PX:
                return
            self._drag_offset = self._press_global - self.pos()
        self.move(current - self._drag_offset)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        # Accept every left-button path, including a release that never became
        # a drag, so the two branches cannot diverge.
        event.accept()
        if self._press_global is None:
            return
        dragged = self._drag_offset is not None
        self._press_global = None
        self._drag_offset = None
        if dragged:
            # The user put it here on purpose; stop trailing so it stays put.
            self._pinned = True
            self._clamp_into_screen()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._press_global = None
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

    # ----------------------------------------------------------------- paint

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if not self._title:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        card = QRectF(0.5, 0.5, BAR_W - 1.0, BAR_H - 1.0)
        painter.setPen(QPen(CARD_BORDER, 1.0))
        painter.setBrush(CARD_BG)
        painter.drawRoundedRect(card, 12.0, 12.0)

        note_w = 20.0
        buttons_left = self._button_rects()[0].left()
        text_left = PAD + note_w
        text_width = int(buttons_left - text_left - 8.0)

        painter.setFont(self._note_font)
        painter.setPen(ACCENT)
        painter.drawText(
            QRectF(PAD - 2.0, 0.0, note_w, BAR_H), Qt.AlignmentFlag.AlignCenter, "♪"
        )

        title = QFontMetrics(self._title_font).elidedText(
            self._title, Qt.TextElideMode.ElideRight, max(20, text_width)
        )
        painter.setFont(self._title_font)
        painter.setPen(TEXT_COLOR)
        painter.drawText(
            QRectF(text_left, 10.0, text_width, 16.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            title,
        )
        artist = QFontMetrics(self._artist_font).elidedText(
            self._artist or "", Qt.TextElideMode.ElideRight, max(20, text_width)
        )
        if artist:
            painter.setFont(self._artist_font)
            painter.setPen(MUTED_COLOR)
            painter.drawText(
                QRectF(text_left, 27.0, text_width, 14.0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                artist,
            )

        self._draw_progress(painter, card)
        self._draw_buttons(painter)
        painter.end()

    def _draw_progress(self, painter: QPainter, card: QRectF) -> None:
        inset = 10.0
        track = QRectF(
            card.left() + inset, card.bottom() - PROGRESS_H - 1.5,
            card.width() - inset * 2, PROGRESS_H,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(TRACK_COLOR)
        painter.drawRoundedRect(track, 1.5, 1.5)
        if self._duration <= 0:
            return
        ratio = max(0.0, min(1.0, self._position / float(self._duration)))
        if ratio <= 0.0:
            return
        fill = QRectF(track.left(), track.top(), track.width() * ratio, track.height())
        painter.setBrush(ACCENT)
        painter.drawRoundedRect(fill, 1.5, 1.5)

    def _draw_buttons(self, painter: QPainter) -> None:
        rects = self._button_rects()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(TEXT_COLOR)
        cx0, cy0 = rects[0].center().x(), rects[0].center().y()
        cx1, cy1 = rects[1].center().x(), rects[1].center().y()
        cx2, cy2 = rects[2].center().x(), rects[2].center().y()
        s = 5.0

        # Prev: bar + two left triangles.
        painter.drawRect(QRectF(cx0 - 7.0, cy0 - s, 1.6, s * 2.0))
        painter.drawPolygon(QPolygonF([
            QPointF(cx0 - 5.0, cy0 - s), QPointF(cx0 - 5.0, cy0 + s),
            QPointF(cx0 + 0.6, cy0),
        ]))
        painter.drawPolygon(QPolygonF([
            QPointF(cx0 - 0.4, cy0 - s), QPointF(cx0 - 0.4, cy0 + s),
            QPointF(cx0 + 5.2, cy0),
        ]))

        # Toggle: play triangle or pause bars.
        if self._status == "playing":
            painter.drawRect(QRectF(cx1 - 4.0, cy1 - s, 3.0, s * 2.0))
            painter.drawRect(QRectF(cx1 + 1.0, cy1 - s, 3.0, s * 2.0))
        else:
            painter.drawPolygon(QPolygonF([
                QPointF(cx1 - 3.6, cy1 - s), QPointF(cx1 - 3.6, cy1 + s),
                QPointF(cx1 + 5.0, cy1),
            ]))

        # Next: two right triangles + bar.
        painter.drawPolygon(QPolygonF([
            QPointF(cx2 - 5.2, cy2 - s), QPointF(cx2 - 5.2, cy2 + s),
            QPointF(cx2 + 0.4, cy2),
        ]))
        painter.drawPolygon(QPolygonF([
            QPointF(cx2 - 0.6, cy2 - s), QPointF(cx2 - 0.6, cy2 + s),
            QPointF(cx2 + 5.0, cy2),
        ]))
        painter.drawRect(QRectF(cx2 + 5.4, cy2 - s, 1.6, s * 2.0))

        # Close: a thin × in the muted colour, so it reads as secondary to
        # the transport controls.
        close = rects[BTN_CLOSE]
        cx3, cy3 = close.center().x(), close.center().y()
        painter.setPen(QPen(CLOSE_COLOR, 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(cx3 - 3.8, cy3 - 3.8), QPointF(cx3 + 3.8, cy3 + 3.8))
        painter.drawLine(QPointF(cx3 - 3.8, cy3 + 3.8), QPointF(cx3 + 3.8, cy3 - 3.8))
