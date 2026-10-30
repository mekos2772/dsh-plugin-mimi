"""F06/F07 的绘制：玩具与宠物自己的小布景。

为什么用 QPainter 矢量而不是图片素材：
* 小布景只有三四个固定物件，矢量绘制在任意 DPI 下都清晰，
  也不需要为每套造型再准备一套素材。
* 玩具是运行时对象，位置每帧变化，本来就不是静态图片。
* 两者都属于"宠物自己的小东西"，用同一套简笔风格不会抢角色的视觉重心。

层级约定（与 docs/expansion/FEATURE-SPEC.md 一致）：
    背景道具 → 角色 → 前景道具 → 玩具 → 气泡
调用方负责在角色 pixmap 之前/之后分别调用对应的函数。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF

# 布景配色：低饱和，避免和角色抢眼
WOOD = QColor(0xC8, 0xA6, 0x7E)
WOOD_DARK = QColor(0xA8, 0x86, 0x60)
PAPER = QColor(0xF5, 0xF1, 0xE8)
LAMP_GLOW = QColor(0xFF, 0xE0, 0x9B, 210)
LAMP_OFF = QColor(0xC9, 0xCE, 0xDA)
CUSHION = QColor(0x9F, 0xB6, 0xE0)
LEAF = QColor(0x7F, 0xB0, 0x94)
POT = QColor(0xC4, 0x8F, 0x74)
BALL = QColor(0x7E, 0x9C, 0xE8)
STAR = QColor(0xFF, 0xD2, 0x6E)


def draw_scene_item(
    painter: QPainter,
    asset_id: str,
    center: QPointF,
    scale: float,
    *,
    light_on: bool = False,
) -> None:
    """画一个布景物件，原点在物件底部中心。"""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.translate(center)
    # 落地阴影：让物件看起来是放在桌上，不是浮在角色旁边。
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(40, 44, 60, 34)))
    painter.drawEllipse(QPointF(0.0, 0.5), 18.0 * scale, 3.4 * scale)
    painter.scale(scale, scale)
    try:
        if asset_id == "books":
            _draw_books(painter)
        elif asset_id == "lamp":
            _draw_lamp(painter, light_on)
        elif asset_id == "cushion":
            _draw_cushion(painter)
        elif asset_id == "plant":
            _draw_plant(painter)
    finally:
        painter.restore()


def _draw_books(painter: QPainter) -> None:
    """三本叠放的书，稍微错开。"""
    widths = (34.0, 30.0, 26.0)
    heights = (7.0, 6.0, 6.0)
    colors = (QColor(0x8E, 0x9F, 0xC8), QColor(0xC8, 0x9A, 0x9A), QColor(0x9C, 0xC0, 0xA8))
    y = 0.0
    for index, (w, h) in enumerate(zip(widths, heights)):
        offset = (-3.0, 2.0, -1.0)[index]
        rect = QRectF(-w / 2 + offset, y - h, w, h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(colors[index]))
        painter.drawRoundedRect(rect, 1.5, 1.5)
        # 书页侧面
        painter.setBrush(QBrush(PAPER))
        painter.drawRect(QRectF(rect.left() + 1.5, rect.top() + 1.2, rect.width() - 3, 1.6))
        y -= h


def _draw_lamp(painter: QPainter, light_on: bool) -> None:
    """小台灯：底座、灯杆、灯罩。开灯时加一圈柔光。"""
    if light_on:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(LAMP_GLOW))
        painter.drawEllipse(QPointF(0.0, -24.0), 22.0, 22.0)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(WOOD_DARK))
    painter.drawRoundedRect(QRectF(-9.0, -4.0, 18.0, 4.0), 1.5, 1.5)
    painter.setBrush(QBrush(WOOD))
    painter.drawRect(QRectF(-1.5, -22.0, 3.0, 18.0))
    # 灯罩
    shade = QPolygonF([
        QPointF(-11.0, -22.0), QPointF(11.0, -22.0),
        QPointF(7.0, -32.0), QPointF(-7.0, -32.0),
    ])
    painter.setBrush(QBrush(QColor(0xFF, 0xE9, 0xB8) if light_on else LAMP_OFF))
    painter.drawPolygon(shade)


def _draw_cushion(painter: QPainter) -> None:
    """圆角坐垫。"""
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(CUSHION))
    painter.drawRoundedRect(QRectF(-20.0, -8.0, 40.0, 8.0), 4.0, 4.0)
    painter.setBrush(QBrush(CUSHION.lighter(112)))
    painter.drawRoundedRect(QRectF(-18.0, -11.0, 36.0, 5.0), 2.5, 2.5)


def _draw_plant(painter: QPainter) -> None:
    """小盆栽：两片叶子加花盆。"""
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(POT))
    pot = QPolygonF([
        QPointF(-7.0, -1.0), QPointF(7.0, -1.0),
        QPointF(5.0, -11.0), QPointF(-5.0, -11.0),
    ])
    painter.drawPolygon(pot)
    painter.setBrush(QBrush(LEAF))
    painter.drawEllipse(QPointF(-4.0, -16.0), 5.0, 7.0)
    painter.drawEllipse(QPointF(4.0, -19.0), 5.0, 8.0)


def draw_toy(painter: QPainter, obj: dict) -> None:
    """画一个运行时玩具对象（球或纸星）。"""
    kind = obj.get("kind", "ball")
    center = QPointF(float(obj.get("x", 0.0)), float(obj.get("y", 0.0)))
    spin = float(obj.get("spin", 0.0))
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.translate(center)
    try:
        if kind == "star":
            painter.rotate(spin % 360.0)
            _draw_star(painter)
        else:
            painter.rotate(spin % 360.0)
            _draw_ball(painter)
    finally:
        painter.restore()


def _draw_ball(painter: QPainter) -> None:
    radius = 9.0
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(BALL))
    painter.drawEllipse(QPointF(0.0, 0.0), radius, radius)
    painter.setBrush(QBrush(QColor(255, 255, 255, 120)))
    painter.drawEllipse(QPointF(-3.0, -3.5), 3.0, 2.4)


def _draw_star(painter: QPainter) -> None:
    outer, inner = 9.0, 4.2
    points = []
    for index in range(10):
        angle = math.radians(-90 + index * 36)
        radius = outer if index % 2 == 0 else inner
        points.append(QPointF(math.cos(angle) * radius, math.sin(angle) * radius))
    painter.setPen(QPen(STAR.darker(120), 1.0))
    painter.setBrush(QBrush(STAR))
    painter.drawPolygon(QPolygonF(points))


def draw_toy_bar(painter: QPainter, remaining_s: float, width: int) -> None:
    """玩具会话的剩余时间条；贴在窗口底部。"""
    if remaining_s <= 0:
        return
    total = 20.0
    ratio = max(0.0, min(1.0, remaining_s / total))
    bar_w = max(0.0, (width - 24) * ratio)
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(0, 0, 0, 40)))
    painter.drawRoundedRect(QRectF(12.0, width * 0 + 12.0, width - 24.0, 3.0), 1.5, 1.5)
    painter.setBrush(QBrush(QColor(0x5D, 0x81, 0xDF, 190)))
    painter.drawRoundedRect(QRectF(12.0, 12.0, bar_w, 3.0), 1.5, 1.5)
    painter.restore()
