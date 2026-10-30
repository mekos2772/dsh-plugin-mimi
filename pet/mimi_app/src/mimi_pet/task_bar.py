"""桌宠模式下的任务状态条。

一个很小的置顶玻璃条，显示当前任务、步骤进度和专注计时。
存在的意义：不用打开 DSH 面板也能看到"我现在做到哪了"。

交互：
* 单击 → 打开任务列表
* 拖动 → 移动位置，自动记住
* 双击 → 切换极简模式（只剩一条细进度）

约束：不抢焦点（``WindowDoesNotAcceptFocus`` + ``WA_ShowWithoutActivating``），
无任务且无专注时自动隐藏；纯 Qt 绘制，Windows / macOS 同一套代码。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

RADIUS = 14.0
MARGIN = 10
HEIGHT = 54
MINIMAL_HEIGHT = 16
MIN_WIDTH = 190
MAX_WIDTH = 340
MINIMAL_WIDTH = 110


@dataclass
class BarState:
    title: str = ""
    step: str = ""
    done: int = 0
    total: int = 0
    focus_label: str = ""
    paused: bool = False

    @property
    def visible(self) -> bool:
        return bool(self.title or self.focus_label)


class TaskStatusBar(QWidget):
    open_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedHeight(HEIGHT + MARGIN * 2)
        self.setMinimumWidth(MIN_WIDTH + MARGIN * 2)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("拖动移动 · 双击极简 · 单击打开任务")

        self._state = BarState()
        self._minimal = os.environ.get("MIMI_STATUSBAR_MINIMAL", "0") == "1"
        self._drag_origin: QPoint | None = None
        self._drag_offset = QPoint()
        self._dragged = False
        self._pos_path = os.environ.get("MIMI_STATUSBAR_POS", "")
        self._saved_pos = self._load_pos()

    # ---------------------------------------------------------------- 数据

    def set_state(self, state: BarState) -> None:
        if state == self._state:
            return
        self._state = state
        self._apply_size(state)
        self.setVisible(state.visible)
        self.update()

    def _apply_size(self, state: BarState) -> None:
        if self._minimal:
            self.setFixedHeight(MINIMAL_HEIGHT + MARGIN * 2)
            self.setFixedWidth(MINIMAL_WIDTH + MARGIN * 2)
            return
        self.setFixedHeight(HEIGHT + MARGIN * 2)
        self.setFixedWidth(self._measure(state))

    def state(self) -> BarState:
        return self._state

    def _measure(self, state: BarState) -> int:
        font = QFont(self.font())
        font.setPointSizeF(9.5)
        metrics = QFontMetrics(font)
        widest = max(
            metrics.horizontalAdvance(state.title or ""),
            metrics.horizontalAdvance(state.step or "") + 60,
            metrics.horizontalAdvance(state.focus_label or ""),
        )
        return int(min(MAX_WIDTH, max(MIN_WIDTH, widest + 58)) + MARGIN * 2)

    # ---------------------------------------------------------------- 极简

    def set_minimal(self, minimal: bool) -> None:
        if minimal == self._minimal:
            return
        self._minimal = minimal
        self._apply_size(self._state)
        self.update()

    def is_minimal(self) -> bool:
        return self._minimal

    # ---------------------------------------------------------------- 位置

    def _load_pos(self) -> QPoint | None:
        if not self._pos_path:
            return None
        try:
            raw = json.loads(Path(self._pos_path).read_text(encoding="utf-8"))
            return QPoint(int(raw["x"]), int(raw["y"]))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _save_pos(self) -> None:
        if not self._pos_path:
            return
        try:
            path = Path(self._pos_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps({"x": self.x(), "y": self.y()}), encoding="utf-8")
            os.replace(temp, path)
        except OSError:
            pass

    def place(self, screen_rect) -> None:
        """用户拖过就用记住的位置，否则放顶部居中。

        记住的位置必须落在当前屏幕内：换显示器或改分辨率后，旧坐标可能
        完全在屏幕外，表现就是"状态条定在一个奇怪的地方再也不动"。
        """
        if self._saved_pos is not None:
            x, y = self._saved_pos.x(), self._saved_pos.y()
            max_x = screen_rect.x() + screen_rect.width() - self.width()
            max_y = screen_rect.y() + screen_rect.height() - self.height()
            if screen_rect.x() <= x <= max_x and screen_rect.y() <= y <= max_y:
                self.move(x, y)
                return
            # 越界：丢掉旧坐标，回默认位置并立刻持久化
            self._saved_pos = None
            self._save_pos()
        x = int(screen_rect.x() + (screen_rect.width() - self.width()) / 2)
        y = int(screen_rect.y() + 14)
        self.move(x, y)

    # ---------------------------------------------------------------- 交互

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint()
            self._drag_offset = self._drag_origin - self.pos()
            self._dragged = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_origin is None:
            return
        current = event.globalPosition().toPoint()
        if not self._dragged and (current - self._drag_origin).manhattanLength() > 5:
            self._dragged = True
        if self._dragged:
            self.move(current - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_origin is not None:
            if self._dragged:
                self._saved_pos = self.pos()
                self._save_pos()
            else:
                self.open_requested.emit()
            self._drag_origin = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.set_minimal(not self._minimal)
        super().mouseDoubleClickEvent(event)

    # ---------------------------------------------------------------- 绘制

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(MARGIN, MARGIN, -MARGIN, -MARGIN)
        self._paint_shadow(painter, rect)
        self._paint_glass(painter, rect)
        if self._minimal:
            self._paint_minimal(painter, rect)
        else:
            self._paint_text(painter, rect)
        painter.end()

    def _paint_shadow(self, painter: QPainter, rect: QRectF) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        for step in range(8, 0, -1):
            alpha = int(3 + 16 * (1.0 - step / 8) ** 2)
            painter.setBrush(QColor(28, 42, 78, alpha))
            painter.drawRoundedRect(rect.adjusted(-step, -step + 2, step, step + 2),
                                    RADIUS + step, RADIUS + step)

    def _paint_glass(self, painter: QPainter, rect: QRectF) -> None:
        path = QPainterPath()
        path.addRoundedRect(rect, RADIUS, RADIUS)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, QColor(255, 255, 255, 218))
        gradient.setColorAt(1.0, QColor(238, 244, 253, 206))
        painter.fillPath(path, gradient)
        edge = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        edge.setColorAt(0.0, QColor(255, 255, 255, 235))
        edge.setColorAt(1.0, QColor(150, 175, 220, 110))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(edge, 1.0))
        painter.drawPath(path)

    def _paint_minimal(self, painter: QPainter, rect: QRectF) -> None:
        state = self._state
        if not state.total:
            return
        ratio = max(0.0, min(1.0, state.done / state.total))
        bar = QRectF(rect.left() + 4, rect.center().y() - 2,
                     (rect.width() - 8) * ratio, 4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(88, 132, 232, 220))
        painter.drawRoundedRect(bar, 2, 2)

    def _paint_text(self, painter: QPainter, rect: QRectF) -> None:
        state = self._state
        x = rect.left() + 13
        top = rect.top() + 9

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(232, 172, 72) if state.paused else QColor(88, 132, 232))
        painter.drawEllipse(QPointF(x + 3.5, top + 6.5), 3.5, 3.5)

        title_font = QFont(self.font())
        title_font.setPointSizeF(9.5)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor(22, 32, 58))
        title_rect = QRectF(x + 13, top - 2, rect.width() - 30, 18)
        metrics = QFontMetrics(title_font)
        fallback = "专注中" if state.focus_label else "空闲"
        title = metrics.elidedText(state.title or fallback, Qt.TextElideMode.ElideRight,
                                   int(title_rect.width()) - 46)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)

        badge = f"{state.done}/{state.total}" if state.total else state.focus_label
        if badge:
            small = QFont(self.font())
            small.setPointSizeF(8.5)
            painter.setFont(small)
            painter.setPen(QColor(88, 104, 136))
            painter.drawText(QRectF(rect.right() - 84, top - 2, 72, 18),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, badge)

        if state.step:
            step_font = QFont(self.font())
            step_font.setPointSizeF(8.5)
            painter.setFont(step_font)
            painter.setPen(QColor(104, 118, 146))
            step_metrics = QFontMetrics(step_font)
            step = step_metrics.elidedText(state.step, Qt.TextElideMode.ElideRight,
                                           int(rect.width()) - 26)
            painter.drawText(QRectF(x, top + 16, rect.width() - 24, 16),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, step)

        if state.total > 1:
            bar_y = rect.bottom() - 7
            bar_w = rect.width() - 26
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(120, 145, 200, 40))
            painter.drawRoundedRect(QRectF(x, bar_y, bar_w, 3), 1.5, 1.5)
            ratio = max(0.0, min(1.0, state.done / state.total))
            if ratio > 0:
                painter.setBrush(QColor(88, 132, 232, 210))
                painter.drawRoundedRect(QRectF(x, bar_y, bar_w * ratio, 3), 1.5, 1.5)


class MenuBarStatus(QObject):
    """macOS 顶部菜单栏里的状态项。

    用 QSystemTrayIcon 实现：在 macOS 上它就是菜单栏右侧的图标，不需要
    自己写 NSStatusItem（纯 ctypes 调 AppKit 会崩）。图标里画一圈进度环，
    悬停看详情，点开菜单能看到完整状态。
    """

    open_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._state = BarState()
        # 不给托盘挂 QMenu：macOS 上从状态栏弹原生菜单会走 NSMenuTrackingSession，
        # 实测触发 AppKit 断言（-[NSEvent clickCount]）直接 abort 闪退。
        # 改成"点一下就打开 Mimi"，状态信息放在 tooltip 里。
        self._tray = QSystemTrayIcon(self._icon(0.0), self)
        self._tray.setToolTip("Mimi")
        self._tray.activated.connect(self._on_activated)
        self._tray.show()

    # ------------------------------------------------------------------ 状态

    def set_state(self, state: BarState) -> None:
        if state == self._state:
            return
        self._state = state
        ratio = (state.done / state.total) if state.total else 0.0
        self._tray.setIcon(self._icon(max(0.0, min(1.0, ratio))))
        detail = " · ".join(x for x in (state.title, state.step) if x) or "空闲"
        if state.total:
            detail += f"  {state.done}/{state.total}"
        if state.focus_label:
            detail += f"  {state.focus_label}"
        self._tray.setToolTip(f"Mimi · {detail}")
        # 菜单栏项常驻：它就是"状态在哪"的入口，隐藏了用户就找不到了。
        self._tray.setVisible(True)

    def place(self, screen_rect) -> None:
        """菜单栏项不需要定位；保留接口以兼容浮动状态条。"""
        return

    def isVisible(self) -> bool:  # noqa: N802 - 与 QWidget 接口对齐
        return bool(self._tray.isVisible())

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open_requested.emit()

    def _icon(self, ratio: float) -> QIcon:
        """画一个 22×22 的进度环；空进度时是一个实心小圆点。"""
        size = 22
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(255, 255, 255, 90), 2.0)
        painter.setPen(pen)
        painter.drawEllipse(QRectF(3, 3, 16, 16))
        if ratio > 0:
            painter.setPen(QPen(QColor(255, 255, 255, 240), 2.0))
            painter.drawArc(QRectF(3, 3, 16, 16), 90 * 16, int(-360 * 16 * ratio))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 210))
            painter.drawEllipse(QPointF(11, 11), 2.6, 2.6)
        painter.end()
        pixmap.setDevicePixelRatio(2.0)
        return QIcon(pixmap)
