"""液体玻璃风格的弹出菜单。

Qt 原生 QMenu 是系统主题的不透明面板。这个模块用自绘 + 半透明窗口把它
换成"玻璃"质感：

* 半透明白底 + 淡蓝渐变，模拟毛玻璃的通透感；
* 顶部一条高光、底部一条冷色描边，形成"液体"边缘；
* 大圆角、柔和投影，让面板像浮在桌面上；
* 出现时淡入 + 轻微上浮，收起不做动画（收起要快，不能拖沓）。

真正的背景模糊需要平台 API（Windows DWM / macOS NSVisualEffectView），
这里不做——半透明叠加深浅对比已经能得到接近的观感，而且跨平台一致。
"""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractAnimation, QEasingCurve, QParallelAnimationGroup, QPointF,
    QPropertyAnimation, QRectF, Qt,
)
from PySide6.QtGui import (
    QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import QMenu

#: 玻璃面板的圆角半径。
RADIUS = 18.0
#: 面板四周留给阴影的透明边距。Qt 不会把子控件裁到圆角，
#: 所以玻璃本体向内缩这么多，阴影画在外圈。
SHADOW_MARGIN = 13
#: 弹出动画：淡入 + 上浮的距离与时长。
FADE_MS = 130
RISE_MS = 170
RISE_PX = 10

#: 液体玻璃配色（浅色，不抢角色颜色）。
GLASS_TOP = QColor(255, 255, 255, 214)
GLASS_MID = QColor(244, 248, 255, 200)
GLASS_BOTTOM = QColor(226, 236, 252, 212)
EDGE_LIGHT = QColor(255, 255, 255, 235)
EDGE_COOL = QColor(150, 175, 220, 120)
TEXT_MAIN = QColor(26, 34, 51)
TEXT_MUTED = QColor(112, 124, 148)
HOVER_FILL = QColor(255, 255, 255, 240)
HOVER_EDGE = QColor(120, 150, 220, 90)
SEPARATOR = QColor(120, 140, 180, 46)


LIQUID_GLASS_QSS = f"""
QMenu {{
    background: transparent;
    border: none;
    padding: {SHADOW_MARGIN + 6}px;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC";
    font-size: 12px;
}}
QMenu::item {{
    min-width: 176px;
    background: transparent;
    color: #{TEXT_MAIN.red():02X}{TEXT_MAIN.green():02X}{TEXT_MAIN.blue():02X};
    padding: 8px 30px 8px 14px;
    border-radius: 9px;
    margin: 1px 3px;
}}
QMenu::item:selected {{
    background: rgba(255, 255, 255, 240);
    color: #101a2e;
}}
QMenu::item:disabled {{
    color: #{TEXT_MUTED.red():02X}{TEXT_MUTED.green():02X}{TEXT_MUTED.blue():02X};
}}
QMenu::separator {{
    height: 1px;
    background: rgba(120, 140, 180, 46);
    margin: 6px 14px;
}}
QMenu::indicator {{
    width: 14px;
    height: 14px;
    margin-left: 4px;
}}
QMenu::indicator:checked {{
    background: #4f74d8;
    border-radius: 4px;
}}
"""


class GlassMenu(QMenu):
    """自绘玻璃面板 + 弹出动画的 QMenu。

    只重写绘制与出现动画，菜单的键盘导航、子菜单、勾选等行为完全沿用
    Qt 实现——自己造一套菜单行为得不偿失，也容易出错。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setStyleSheet(LIQUID_GLASS_QSS)
        self._anim: QParallelAnimationGroup | None = None

    # ------------------------------------------------------------- 绘制

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        margin = float(SHADOW_MARGIN)
        rect = QRectF(self.rect()).adjusted(margin, margin, -margin, -margin)

        # 柔和投影：一圈圈递减的圆角矩形，比 QGraphicsDropShadowEffect
        # 更可控——后者在透明顶层窗口上会把源区域画成一块白色方块。
        painter.setPen(Qt.PenStyle.NoPen)
        for step in range(SHADOW_MARGIN, 0, -1):
            alpha = int(4 + 26 * (1.0 - step / SHADOW_MARGIN) ** 2)
            painter.setBrush(QColor(28, 42, 78, alpha))
            painter.drawRoundedRect(
                rect.adjusted(-step, -step + 3, step, step + 3),
                RADIUS + step, RADIUS + step,
            )

        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        path.addRoundedRect(rect, RADIUS, RADIUS)

        # 主体：自上而下由亮到冷的半透明渐变，模拟玻璃的进光方向。
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, GLASS_TOP)
        gradient.setColorAt(0.55, GLASS_MID)
        gradient.setColorAt(1.0, GLASS_BOTTOM)
        painter.fillPath(path, QBrush(gradient))

        # 顶部高光：一条更亮的细边，是"液体"感的来源。
        highlight = QPainterPath()
        highlight.addRoundedRect(rect.adjusted(1.0, 1.0, -1.0, -rect.height() * 0.55),
                                 RADIUS, RADIUS)
        painter.fillPath(highlight, QBrush(QColor(255, 255, 255, 120)))

        # 边缘：上亮下冷，制造厚度。
        edge = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        edge.setColorAt(0.0, EDGE_LIGHT)
        edge.setColorAt(1.0, EDGE_COOL)
        painter.setPen(QPen(QBrush(edge), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.end()

        # 先画好底，再让 Qt 画条目文字与指示器。
        super().paintEvent(event)

    # ------------------------------------------------------------- 动画

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        # 子菜单由父菜单定位，改它的 geometry 会让它跳位；只对顶层面板做动画。
        if isinstance(self.parent(), QMenu):
            return
        # QMenu.exec() 会先定位再 show，这里拿到的就是最终位置。
        final_rect = self.geometry()
        start_rect = final_rect.translated(0, RISE_PX)
        self.setWindowOpacity(0.0)
        self.setGeometry(start_rect)

        group = QParallelAnimationGroup(self)
        fade = QPropertyAnimation(self, b"windowOpacity", group)
        fade.setDuration(FADE_MS)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        rise = QPropertyAnimation(self, b"geometry", group)
        rise.setDuration(RISE_MS)
        rise.setStartValue(start_rect)
        rise.setEndValue(final_rect)
        rise.setEasingCurve(QEasingCurve.Type.OutCubic)

        group.addAnimation(fade)
        group.addAnimation(rise)
        group.finished.connect(lambda: self.setWindowOpacity(1.0))
        self._anim = group
        group.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # 动画对象可能在删除队列里，先松开引用避免关闭时访问已销毁对象。
        self._anim = None
        super().hideEvent(event)


def submenu(parent: QMenu, title: str) -> GlassMenu:
    """在 parent 下挂一个同风格的子菜单。

    必须显式用 GlassMenu 构造再 addMenu(menu)：``parent.addMenu("标题")``
    只会生成原生 QMenu，子菜单就会退回系统主题，和主面板不是一套。
    """
    menu = GlassMenu(parent)
    menu.setTitle(title)
    parent.addMenu(menu)
    return menu
