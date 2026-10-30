"""Transparent, frameless, always-on-top pet window.

The window never mutates the state machine: mouse events send domain events
to the engine, and paintEvent only consumes the latest RenderSnapshot.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import (
    QActionGroup,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QPainter,
    QPolygonF,
)
from PySide6.QtWidgets import QMenu, QSlider, QWidget, QWidgetAction

from .collision import WorkArea, ground_for_point
from .debug_overlay import draw_debug_overlay
from .engine import PetEngine, touch_region
from .glass_menu import LIQUID_GLASS_QSS, GlassMenu, submenu
from .scene_draw import draw_scene_item, draw_toy, draw_toy_bar
from . import menu_labels

# Dropped picture files count as treats (bread) for the pet.
FEED_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
from .renderer import QtRenderer, RenderSnapshot
from .state_machine import PetState


class MimiWindow(QWidget):
    closed = Signal()
    head_hovered = Signal(bool)
    # 需要弹出卡片的功能入口（便签/任务/收尾），具体 UI 由 qt_app 接。
    feature_requested = Signal(str)
    bubble_requested = Signal(str, str)

    def __init__(
        self,
        engine: PetEngine,
        renderer: QtRenderer,
        ground_provider: callable,
        bounds_provider: callable,
        size_changed: callable | None = None,
    ) -> None:
        super().__init__()
        self.engine = engine
        self.renderer = renderer
        self.ground_provider = ground_provider
        self.bounds_provider = bounds_provider
        self.size_changed = size_changed
        self.dsh = None
        self.companion = None
        self.media = None
        # 预设造型服务，由 qt_app 装配时注入；None 表示不提供造型功能。
        self.outfits = None
        # 玩具盒状态（F06），由 qt_app 每帧写入。
        self._toy_state = None
        self._drag_scene_item = None
        self._drag_scene_origin = None
        # F01–F08 的总服务，由 qt_app 注入。
        self.hub = None
        self._snapshot: RenderSnapshot | None = None
        self._pixmap = None
        self.debug_enabled = False
        self._dragging = False
        # Long-press-to-drag: a press arms a timer; dragging only starts when
        # the mouse is held still for long_press_s or moves beyond
        # drag_start_move_px (instant grab for fast drags). A plain click
        # never drags.
        self._press_armed = False
        self._press_pos = None
        self._press_local = None
        self._long_press_s = float(engine.drag.config.long_press_s)
        self._start_move_px = float(engine.drag.config.drag_start_move_px)
        self._long_press_timer = QTimer(self)
        self._long_press_timer.setSingleShot(True)
        self._long_press_timer.setInterval(int(self._long_press_s * 1000))
        self._long_press_timer.timeout.connect(self._on_long_press)

        # Double-click detection (two clicks < 320 ms apart on the pet).
        self._last_release_at = 0.0
        self._double_click_ms = 320
        # Welcome back: a pointer returning after a long absence waves hello.
        # Hover itself stays silent — cursor attention is continuous (Live
        # gaze tracking), only the leave→enter gap carries meaning.
        self._pointer_left_at: float | None = None
        self._head_hovered = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.resize(engine.display_w, engine.display_h)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        # Keep the non-blocking popup alive until Qt closes it.
        self._active_context_menu: QMenu | None = None
        # True while the context menu owns the pointer; the per-tick layout
        # pass must not move this window or raise another top-level one then.
        self.menu_open = False

    # ------------------------------------------------------------------ rendering

    def show_frame(self, snapshot: RenderSnapshot, pixmap) -> None:
        self._snapshot = snapshot
        self._pixmap = pixmap
        # On macOS, deferred updates for this transparent always-on-top window
        # can remain visually stale until another window exposes it. The frame
        # is already composed at the tick rate, so paint it before returning.
        self.repaint()

    def current_pixmap(self):
        """最后成功合成的一帧；渲染抛异常时用它顶住画面。"""
        return self._pixmap

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        # 层级：后景布景 → 角色 → 前景布景 → 玩具 → 气泡
        self._draw_scene(painter, behind=True)
        if self._pixmap is not None:
            painter.drawPixmap(0, 0, self._pixmap)
        self._draw_scene(painter, behind=False)
        self._draw_toys(painter)
        if self.debug_enabled and self._snapshot is not None:
            draw_debug_overlay(painter, self.rect(), self._snapshot)
        if self._snapshot is not None and self._snapshot.bubble_text:
            self._draw_bubble(painter, self._snapshot.bubble_text)
        painter.end()

    def _scene_origin(self) -> tuple[float, float]:
        """布景参考原点：窗口内角色脚底位置。

        必须用窗口坐标，不能用引擎的世界坐标：引擎 root 是屏幕坐标，
        直接拿来画会把物件推出窗口外（表现为"物件消失"）。
        """
        engine = self.engine
        return (self.width() / 2.0, float(engine.pivot_display_y))

    def _draw_scene(self, painter: QPainter, *, behind: bool) -> None:
        """画 F07 的小布景。物件按 z 分到角色前后两层。"""
        hub = self.hub
        if hub is None:
            return
        scene = hub.scene
        if not scene.is_visible or not scene.scene.items:
            return
        ox, oy = self._scene_origin()
        # 窗口只包住角色，物件很容易落在外框之外被裁掉；
        # 绘制前把位置夹进窗口内，保证用户永远看得见自己摆的东西。
        margin = 26.0
        for item in scene.scene.items:
            is_behind = item.z < 0
            if is_behind != behind:
                continue
            x = min(max(ox + item.offset_x, margin), self.width() - margin)
            y = min(max(oy + item.offset_y, margin), self.height() - 6.0)
            draw_scene_item(
                painter,
                item.asset_id,
                QPointF(x, y),
                max(0.4, min(3.0, item.scale)),
                light_on=item.light_on,
            )

    def _draw_toys(self, painter: QPainter) -> None:
        """画 F06 的玩具对象与剩余时间条。"""
        state = self._toy_state
        if not state or not state.get("active"):
            return
        for obj in state.get("objects", []):
            draw_toy(painter, obj)
        draw_toy_bar(painter, float(state.get("remaining_s", 0.0)), self.width())

    def _draw_bubble(self, painter: QPainter, text: str) -> None:
        """Speech bubble above the character's head."""
        font = QFont("Microsoft YaHei")
        font.setPixelSize(13)
        metrics = QFontMetrics(font)
        text_width = metrics.horizontalAdvance(text)
        padding_x, padding_y = 10, 7
        bubble_w = text_width + padding_x * 2
        bubble_h = metrics.height() + padding_y * 2
        bubble_x = (self.width() - bubble_w) / 2.0
        bubble_y = 6.0
        painter.save()
        painter.setFont(font)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 235))
        painter.drawRoundedRect(QRectF(bubble_x, bubble_y, bubble_w, bubble_h), 10.0, 10.0)
        # Tail pointing down toward the character.
        tail = QPolygonF(
            [
                QPointF(bubble_x + bubble_w / 2.0 - 8, bubble_y + bubble_h - 1),
                QPointF(bubble_x + bubble_w / 2.0 + 8, bubble_y + bubble_h - 1),
                QPointF(bubble_x + bubble_w / 2.0, bubble_y + bubble_h + 8),
            ]
        )
        painter.drawPolygon(tail)
        painter.setPen(QColor(30, 30, 30))
        painter.drawText(
            QRectF(bubble_x, bubble_y, bubble_w, bubble_h),
            Qt.AlignmentFlag.AlignCenter,
            text,
        )
        painter.restore()

    def _set_head_hovered(self, hovered: bool) -> None:
        hovered = bool(hovered)
        if hovered == self._head_hovered:
            return
        self._head_hovered = hovered
        self.head_hovered.emit(hovered)

    def _is_head_at(self, local_pos: QPoint) -> bool:
        if self.width() <= 0 or self.height() <= 0:
            return False
        x_ratio = local_pos.x() / float(self.width())
        y_ratio = local_pos.y() / float(self.height())
        # Match the existing touch-region definition: only the upper head/hair
        # area is a hover affordance, not the face/body click zones.
        return self._visible_at(local_pos) and y_ratio < 0.62 and (
            y_ratio < 0.52 or 0.30 <= x_ratio < 0.70
        )

    # ------------------------------------------------------------------ mouse

    def _visible_at(self, local_pos: QPoint) -> bool:
        """Alpha hit test: only the visible character pixels grab the mouse.

        必须把**逻辑坐标**换算成位图的**物理像素**再取样。Retina 屏上
        ``scaled_pixmap`` 出的是 2 倍物理尺寸（256×384 窗口 → 512×768 位图），
        而 ``toImage()`` 不会替我们缩小；直接拿逻辑坐标去 ``pixelColor`` 等于
        只查到左上角 1/4 区域——实测 dpr=2 时点身体/脚（逻辑 128,200）取到
        alpha=0，于是 ``mousePressEvent`` 根本没进入拖拽/点击分支，表现就是
        "点上去完全没反应"。
        """
        if self._pixmap is None:
            return False
        if not (0 <= local_pos.x() < self.width() and 0 <= local_pos.y() < self.height()):
            return False
        image = self._pixmap.toImage()
        dpr = float(self._pixmap.devicePixelRatio() or 1.0)
        px = int(local_pos.x() * dpr)
        py = int(local_pos.y() * dpr)
        if not (0 <= px < image.width() and 0 <= py < image.height()):
            return False
        return image.pixelColor(px, py).alpha() > 0

    def _hit_toy(self, pos) -> bool:
        """点击是否落在玩具对象上（半径内）。

        玩具优先于角色命中：否则小球滚到脚下时永远点不到，
        用户会以为按钮坏了。
        """
        state = self._toy_state
        if not state or not state.get("active"):
            return False
        for obj in state.get("objects", []):
            dx = float(pos.x()) - float(obj.get("x", 0.0))
            dy = float(pos.y()) - float(obj.get("y", 0.0))
            if dx * dx + dy * dy <= 16.0 * 16.0:
                return True
        return False

    def _hit_scene_item(self, pos):
        """编辑模式下的物件命中：返回最近的一个物件。"""
        hub = self.hub
        if hub is None or not hub.scene.editing or not hub.scene.is_visible:
            return None
        ox, oy = self._scene_origin()
        best = None
        best_d2 = 30.0 * 30.0
        for item in hub.scene.scene.items:
            dx = float(pos.x()) - (ox + item.offset_x)
            dy = float(pos.y()) - (oy + item.offset_y)
            d2 = dx * dx + dy * dy
            if d2 <= best_d2:
                best, best_d2 = item, d2
        return best

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton and self.hub is not None:
            item = self._hit_scene_item(event.position())
            if item is not None:
                self._drag_scene_item = item
                self._drag_scene_origin = event.position()
                event.accept()
                return
        if event.button() == Qt.MouseButton.LeftButton and self._hit_toy(event.position()):
            toy = self.hub.toys if self.hub is not None else None
            if toy is not None:
                if toy.session.toy == "ball":
                    toy.push_ball()
                else:
                    toy.catch_star()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._visible_at(event.position().toPoint()):
            self._dragging = True
            self._press_armed = True
            self._press_pos = event.globalPosition()
            self._press_local = event.position()
            self._long_press_timer.start()
            event.accept()

    def _begin_drag(self, x: float, y: float) -> None:
        self._press_armed = False
        self._long_press_timer.stop()
        self.engine.begin_drag(x, y, time.perf_counter())
        self.grabMouse()

    def _on_long_press(self) -> None:
        if not self._press_armed:
            return
        cursor = QCursor.pos()
        self._begin_drag(float(cursor.x()), float(cursor.y()))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        item = getattr(self, "_drag_scene_item", None)
        if item is not None and self.hub is not None:
            origin = self._drag_scene_origin
            self.hub.scene.move_item(
                item.id,
                event.position().x() - origin.x(),
                event.position().y() - origin.y(),
            )
            self._drag_scene_origin = event.position()
            self.update()
            event.accept()
            return
    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._set_head_hovered(self._is_head_at(event.position().toPoint()))
        if self.engine.states.state is PetState.DRAGGING:
            self.engine.update_drag(
                event.globalPosition().x(),
                event.globalPosition().y(),
                time.perf_counter(),
                bounds=self.bounds_provider(),
            )
            self._move_to_root()
            event.accept()
        elif self._press_armed and self._press_pos is not None:
            # Moving beyond the threshold while pressing starts the drag
            # immediately (fast drags must not wait for the long press).
            delta = event.globalPosition() - self._press_pos
            if abs(delta.x()) > self._start_move_px or abs(delta.y()) > self._start_move_px:
                self._begin_drag(event.globalPosition().x(), event.globalPosition().y())
                self.engine.update_drag(
                    event.globalPosition().x(),
                    event.globalPosition().y(),
                    time.perf_counter(),
                    bounds=self.bounds_provider(),
                )
                self._move_to_root()
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if getattr(self, "_drag_scene_item", None) is not None:
            # 拖动物件结束：清掉引用，不触发角色拖拽逻辑。
            self._drag_scene_item = None
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            # Open on release: a popup shown while the button is still held is
            # dismissed again by the button-up that follows.
            self._show_context_menu(event.globalPosition().toPoint())
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._press_armed:
            # Plain click: never drags, but gives a soft reaction.
            self._press_armed = False
            self._long_press_timer.stop()
            self._press_pos = None
            self._dragging = False
            now_s = time.perf_counter()
            double_click = (now_s - self._last_release_at) * 1000.0 < self._double_click_ms
            self._last_release_at = now_s
            if double_click:
                self.engine.trigger_double_click()
            else:
                local = self._press_local or event.position()
                x_ratio = local.x() / max(1.0, float(self.width()))
                y_ratio = local.y() / max(1.0, float(self.height()))
                self.engine.trigger_touch(
                    touch_region(x_ratio, y_ratio), x_ratio=x_ratio
                )
            self._press_local = None
            event.accept()
            return
        if self.engine.states.state is PetState.DRAGGING:
            ground = self.ground_provider(self.engine.root_x, self.engine.root_y)
            self.engine.release(ground, time.perf_counter())
            self._dragging = False
            self.releaseMouse()
            event.accept()

    def enterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().enterEvent(event)
        self._set_head_hovered(self._is_head_at(self.mapFromGlobal(QCursor.pos())))
        if self._dragging or self._press_armed or self._pointer_left_at is None:
            return
        away_s = time.perf_counter() - self._pointer_left_at
        self._pointer_left_at = None
        self.engine.trigger_welcome_back(away_s)

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().leaveEvent(event)
        self._set_head_hovered(False)
        self._pointer_left_at = time.perf_counter()

    def _move_to_root(self) -> None:
        self.move(
            int(round(self.engine.root_x - self.engine.pivot_display_x)),
            int(round(self.engine.root_y - self.engine.pivot_display_y)),
        )

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        mime = event.mimeData()
        if mime.hasUrls() or mime.hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        mime = event.mimeData()
        if mime.hasUrls():
            # Pictures are treats: dropping an image feeds her bread.
            paths = [url.toLocalFile() for url in mime.urls()]
            if any(path.lower().endswith(FEED_IMAGE_EXTS) for path in paths):
                if self.engine.feed_bread():
                    event.acceptProposedAction()
                    return
        if self.engine.receive_drop():
            event.acceptProposedAction()
        else:
            event.ignore()

    # ------------------------------------------------------------------ menu

    MENU_QSS = LIQUID_GLASS_QSS

    def _show_context_menu(self, global_pos: QPoint) -> None:
        if self.engine.states.state in (PetState.DRAGGING, PetState.FALLING):
            return
        # 菜单已经在开的时候再开一次，会让 AppKit 的菜单追踪拿到错乱的
        # NSEvent，直接 NSAssertionHandler abort（实测崩过）。
        if self.menu_open:
            return
        menu = self._build_context_menu()
        # QMenu.exec() enters a nested native event loop on macOS. Use popup()
        # so pet animation and input remain on the regular Qt event loop.
        self._active_context_menu = menu
        self.menu_open = True

        def _on_menu_hidden() -> None:
            self.menu_open = False
            if self._active_context_menu is menu:
                self._active_context_menu = None
            menu.deleteLater()

        menu.aboutToHide.connect(_on_menu_hidden)
        menu.popup(global_pos)

    # ------------------------------------------------------ 菜单分组

    def _menu_header(self, menu: QMenu, title: str, status: str = "", counts: str = "") -> None:
        """菜单顶部的只读状态区。

        用 QWidgetAction 而不是禁用的 QAction：后者会继承 disabled 的灰色，
        在一堆可点条目里看起来像"坏了"。分成三行——名字、当前状态、计数——
        这样长状态不会把计数挤掉。
        """
        from PySide6.QtWidgets import QLabel, QVBoxLayout

        from PySide6.QtGui import QPalette

        action = QWidgetAction(menu)
        box = QWidget()
        # QWidgetAction 的容器默认会用系统窗口色填充，在玻璃面板上就是一块
        # 方角白底。三个开关都要关：样式、系统背景、调色板。
        box.setStyleSheet("background: transparent; border: none;")
        box.setAutoFillBackground(False)
        box.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        box.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        palette = box.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0, 0))
        palette.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0, 0))
        box.setPalette(palette)
        column = QVBoxLayout(box)
        column.setContentsMargins(12, 4, 12, 6)
        column.setSpacing(1)
        head = QLabel(title)
        # 浅色玻璃底上必须用深色文字；沿用深色主题的浅灰会几乎看不见。
        head.setStyleSheet("color:#16203a; font-size:13px; font-weight:600;")
        column.addWidget(head)
        if status:
            line = QLabel(menu_labels.short_label(status, limit=30))
            line.setStyleSheet("color:#5f6b85; font-size:11px;")
            line.setToolTip(status)
            column.addWidget(line)
        if counts:
            line = QLabel(counts)
            line.setStyleSheet("color:#7c88a3; font-size:11px;")
            column.addWidget(line)
        action.setDefaultWidget(box)
        menu.addAction(action)

    def _menu_status(self) -> tuple[str, str]:
        """返回 (当前状态, 计数行)。两行分开，避免互相挤掉。"""
        status = ""
        counts = ""
        if self.companion is not None:
            text = self.companion.service.status_text() if hasattr(self.companion, "service") else ""
            status = (text or "").rstrip("。")
        hub = self.hub
        if hub is not None:
            parts: list[str] = []
            notes = hub.notes.count()
            if notes:
                parts.append(f"便签 {notes}")
            task = hub.tasks.active()
            if task is not None:
                parts.append(f"任务 {task.progress_text}")
            counts = " · ".join(parts)
        return status, counts

    def _build_companion_menu(self, parent: QMenu) -> QMenu:
        """陪她一会儿：投喂、玩具、跟听、小空间——都是"一起做点什么"。"""
        sub = submenu(parent, "陪她一会儿")
        sub.setStyleSheet(self.MENU_QSS)
        menu_labels.cap_width(sub)

        feed = sub.addAction("投喂圆面包")
        feed.triggered.connect(self.engine.feed_bread)

        hub = self.hub
        if hub is None:
            return sub

        sub.addSeparator()
        if hub.toys.active:
            stop = sub.addAction("玩具收好了")
            stop.triggered.connect(lambda: self._hub_say(hub.stop_toy("user")))
        else:
            ball = sub.addAction("滚小球（20 秒）")
            ball.triggered.connect(lambda: self._start_toy("ball"))
            star = sub.addAction("接纸星（20 秒）")
            star.triggered.connect(lambda: self._start_toy("star"))

        listen = sub.addAction("不用跟着听" if hub.music.enabled else "陪我听音乐")
        listen.setToolTip("跟听只影响 Mimi 的表现，不会控制你的播放器。")
        listen.triggered.connect(self._toggle_music)
        if self.media is not None:
            # 播放器的实际控制单独一层：跟听是"Mimi 陪你"，这里是"操作播放器"。
            music_ctl = submenu(sub, "音乐控制")
            music_ctl.setStyleSheet(self.MENU_QSS)
            menu_labels.cap_width(music_ctl)
            self.media.build_menu(music_ctl)

        sub.addSeparator()
        toggle = sub.addAction("收起小空间" if hub.scene.is_visible else "打开小空间")
        toggle.triggered.connect(self._toggle_scene)
        if hub.scene.scene.items:
            lamp = sub.addAction("开关小灯")
            lamp.triggered.connect(lambda: self._hub_say(hub.scene.toggle_lamp()))
            layout = sub.addAction(
                "改成固定角落" if hub.scene.scene.placement == "follow" else "改成跟着走"
            )
            layout.triggered.connect(self._toggle_placement)
        return sub

    def _build_records_menu(self, parent: QMenu) -> QMenu:
        """记录与任务：便签、三步任务、下班收尾。"""
        hub = self.hub
        sub = submenu(parent, "记录与任务")
        sub.setStyleSheet(self.MENU_QSS)
        menu_labels.cap_width(sub)
        if hub is None:
            return sub

        stats = getattr(self, "stats", None)
        if stats is not None:
            today = menu_labels.add_item(sub, stats.sentence(), enabled=False)
            today.setToolTip("今天是陪你的时长、专注与完成情况")
            sub.addSeparator()

        notes = sub.addAction(f"全部便签（{hub.notes.count()}）")
        notes.triggered.connect(lambda: self.feature_requested.emit("notes_list"))

        task_menu = submenu(sub, f"任务（{len(hub.tasks.open_tasks())}）")
        task_menu.setStyleSheet(self.MENU_QSS)
        menu_labels.cap_width(task_menu)
        new_task = task_menu.addAction("拆成三步…")
        new_task.triggered.connect(lambda: self.feature_requested.emit("task_new"))
        all_tasks = task_menu.addAction("全部任务…")
        all_tasks.triggered.connect(lambda: self.feature_requested.emit("task_list"))
        active = hub.tasks.active()
        if active is not None:
            task_menu.addSeparator()
            step = active.current_step
            label = f"完成：{step.text}" if step else "完成当前任务"
            done_item = task_menu.addAction(menu_labels.short_label(label))
            done_item.setToolTip(label)
            done_item.triggered.connect(lambda: self._finish_task_step(active.id))
            undo_item = task_menu.addAction("撤销上一步")
            undo_item.triggered.connect(lambda: self._undo_task_step(active.id))
            pause_item = task_menu.addAction("先停一下")
            pause_item.triggered.connect(lambda: self._hub_say(hub.tasks.pause(active.id)))

        sub.addSeparator()
        wrap = sub.addAction("今天到这里…")
        wrap.triggered.connect(lambda: self.feature_requested.emit("wrapup"))
        _wid, next_step = hub.wrapups.pending_next_step()
        if next_step:
            resume = sub.addAction(f"接着昨天：{menu_labels.short_label(next_step)}")
            resume.setToolTip(next_step)
            resume.triggered.connect(self._continue_tomorrow)
        return sub

    def _build_settings_menu(self, parent: QMenu) -> QMenu:
        """设置：尺寸位置、健康小卡、调试。低频但必须可达。"""
        sub = submenu(parent, "设置")
        sub.setStyleSheet(self.MENU_QSS)
        menu_labels.cap_width(sub)

        self._build_size_menu(sub)

        if self.hub is not None:
            health = submenu(sub, "健康小卡")
            health.setStyleSheet(self.MENU_QSS)
            menu_labels.cap_width(health)
            for plan in self.hub.health.summary()["plans"]:
                mark = "●" if plan["enabled"] else "○"
                item = health.addAction(f"{mark} {plan['name']} 每 {plan['interval_minutes']} 分钟")
                item.setToolTip(
                    f"{'、'.join(plan['windows'])} · 每天最多 {plan['daily_cap']} 次"
                )
                item.setCheckable(True)
                item.setChecked(plan["enabled"])
                item.triggered.connect(
                    lambda checked=False, kind=plan["kind"]: self._toggle_health(kind, checked)
                )
            health.addSeparator()
            tune = health.addAction("调整间隔与上限…")
            tune.triggered.connect(lambda: self.feature_requested.emit("health_settings"))

        sub.addSeparator()
        debug_action = sub.addAction("显示调试信息")
        debug_action.setCheckable(True)
        debug_action.setChecked(self.debug_enabled)
        debug_action.toggled.connect(self._set_debug_enabled)
        return sub

    # ---------------------------------------------------------- 功能动作

    def _hub_say(self, result) -> None:
        ok, message = result if isinstance(result, tuple) else (True, str(result))
        if message:
            self.bubble_requested.emit(message, "companion")

    def _finish_task_step(self, task_id: str) -> None:
        ok, message = self.hub.complete_task_step(task_id)
        self._hub_say((ok, message))

    def _undo_task_step(self, task_id: str) -> None:
        self._hub_say(self.hub.undo_task_step(task_id))

    def _toggle_scene(self) -> None:
        hub = self.hub
        self._hub_say(hub.scene.hide() if hub.scene.is_visible else hub.scene.show())

    def _toggle_placement(self) -> None:
        hub = self.hub
        target = "fixed" if hub.scene.scene.placement == "follow" else "follow"
        self._hub_say(hub.scene.set_placement(target))

    def _start_toy(self, toy: str) -> None:
        ok, message = self.hub.start_toy(toy)
        if ok:
            # 玩具只在宠物窗口内活动，避免全屏遮罩挡住真实桌面操作。
            self.hub.toys.bounds = (
                0.0, 0.0, float(self.width()), float(self.height())
            )
            # 开局播一次对应姿势：有素材就是造型动作，没有就静默跳过。
            self.engine.force_perform("push_ball" if toy == "ball" else "catch_star")
        self._hub_say((ok, message))

    def _toggle_music(self) -> None:
        ok, message = self.hub.music.toggle()
        if ok and self.hub.music.enabled:
            track = self.media.track if self.media is not None else None
            if track is not None:
                self.hub.music.update(
                    title=track.display(), playing=track.status == "playing"
                )
        self._hub_say((ok, message))

    def _continue_tomorrow(self) -> None:
        ok, message, _task_id = self.hub.continue_tomorrow()
        if message:
            self.bubble_requested.emit(message, "companion")
        if ok:
            self.engine.force_perform("think_pencil")

    def _toggle_health(self, kind: str, enabled: bool) -> None:
        self._hub_say(self.hub.health.configure(kind, enabled=enabled))

    def _build_outfit_menu(self, menu: QMenu) -> None:
        """造型入口：整套预设，不做单件混搭。"""
        service = self.outfits
        catalog = service.catalog
        if not len(catalog):
            return
        outfit_menu = submenu(menu, "造型")
        outfit_menu.setStyleSheet(self.MENU_QSS)
        menu_labels.cap_width(outfit_menu)

        current = service.selection.id
        group = QActionGroup(outfit_menu)
        group.setExclusive(True)

        default_item = outfit_menu.addAction("默认造型")
        default_item.setCheckable(True)
        default_item.setChecked(not current)
        group.addAction(default_item)
        default_item.triggered.connect(lambda: self._apply_outfit(""))

        for spec in catalog.presets():
            suffix = "" if spec.is_daily else "（展示）"
            item = outfit_menu.addAction(f"{spec.name_zh}{suffix}")
            # 选中前就能看到这套衣服到底能做什么，不必试了才发现缺动作。
            acts = len(spec.supported_actions)
            missing = spec.missing_actions()
            tip = spec.description or spec.name_zh
            tip += f"\n可做 {acts} 个动作"
            if missing:
                tip += f"；还缺 {len(missing)} 个日常动作，暂不走路"
            item.setToolTip(tip)
            item.setCheckable(True)
            item.setChecked(current == spec.id)
            group.addAction(item)
            item.triggered.connect(
                lambda _checked=False, oid=spec.id: self._choose_outfit(oid)
            )

        if service.preview_id:
            outfit_menu.addSeparator()
            cancel = outfit_menu.addAction("取消试穿")
            cancel.triggered.connect(self._cancel_preview)

        if service.selection.store_error:
            outfit_menu.addSeparator()
            note = outfit_menu.addAction(menu_labels.short_label(service.selection.store_error))
            note.setEnabled(False)
            note.setToolTip(service.selection.store_error)

    def set_toy_state(self, state) -> None:
        """玩具会话的画面状态；只画在宠物窗口内，不建全屏遮罩。"""
        self._toy_state = state
        self.update()

    def _choose_outfit(self, outfit_id: str) -> None:
        """第一次点试穿，再点同一套才保存——避免误触改掉搭配。"""
        service = self.outfits
        if service is None:
            return
        if service.preview_id == outfit_id:
            self._apply_outfit(outfit_id)
        else:
            self._preview_outfit(outfit_id)

    def _apply_engine_outfit(self, outfit) -> None:
        """把造型交给引擎，并保证它真的换上去。

        ``engine.set_outfit`` 在角色忙时只排队（坐姿循环会一直占着身体），
        旧实现丢掉这个返回状态，于是"菜单里已经打勾、画面还是旧衣服"。
        这里主动收口：排队的切换立刻结算。
        """
        if not self.engine.set_outfit(outfit):
            return
        if self.engine.pending_outfit is not None:
            self.engine.apply_pending_outfit_now()

    def _preview_outfit(self, outfit_id: str) -> None:
        """先试穿：不改记忆，用户确认后才落盘。"""
        service = self.outfits
        if service is None:
            return
        ok, message = service.preview(outfit_id)
        if ok:
            self._apply_engine_outfit(service.effective())
        if message:
            self.bubble_requested.emit(message + "\n再点一次就保存。", "companion")

    def _cancel_preview(self) -> None:
        service = self.outfits
        if service is None:
            return
        ok, message = service.cancel_preview()
        if ok:
            self._apply_engine_outfit(service.effective())
        if message:
            self.bubble_requested.emit(message, "companion")

    def _apply_outfit(self, outfit_id: str) -> None:
        """确认保存：把试穿结果写进记忆。"""
        service = self.outfits
        if service is None:
            return
        ok, message = service.select(outfit_id)
        if ok:
            self._apply_engine_outfit(service.current())
        if message:
            self.bubble_requested.emit(message, "companion")

    def _build_size_menu(self, parent: QMenu) -> QMenu:
        """尺寸、缩放与摆放。"""
        from PySide6.QtWidgets import QHBoxLayout, QLabel

        sub = submenu(parent, "尺寸与位置")
        sub.setStyleSheet(self.MENU_QSS)
        menu_labels.cap_width(sub)
        size_group = QActionGroup(sub)
        size_group.setExclusive(True)
        for label, (width, height) in (
            ("小号（192×288）", (192, 288)),
            ("中号（256×384）", (256, 384)),
            ("大号（320×480）", (320, 480)),
        ):
            item = sub.addAction(label)
            item.setCheckable(True)
            item.setChecked(self.engine.display_w == width)
            size_group.addAction(item)
            item.triggered.connect(
                lambda checked=False, w=width, h=height: self._apply_size(w, h)
            )
        sub.addSeparator()

        slide_action = QWidgetAction(sub)
        row = QWidget()
        row.setStyleSheet(
            "QLabel { color: #cbd7f5; font-size: 11px; }"
            "QSlider::groove:horizontal { height: 4px; background: #303954; "
            "border-radius: 2px; }"
            "QSlider::sub-page:horizontal { background: #7198ff; border-radius: 2px; }"
            "QSlider::handle:horizontal { background: #f2f5ff; width: 12px; "
            "margin: -4px 0; border-radius: 6px; }"
        )
        layout = QHBoxLayout(row)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(4)
        layout.addWidget(QLabel("缩放"))
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(self.ZOOM_MIN_H, self.ZOOM_MAX_H)
        slider.setValue(self.engine.display_h)
        slider.setFixedWidth(150)
        layout.addWidget(slider)
        zoom_value = QLabel(f"高 {self.engine.display_h}px")
        slider.valueChanged.connect(lambda h: self._apply_size(int(round(h * 2 / 3)), h))
        slider.valueChanged.connect(lambda h: zoom_value.setText(f"高 {h}px"))
        layout.addWidget(zoom_value)
        slide_action.setDefaultWidget(row)
        sub.addAction(slide_action)

        sub.addSeparator()
        placement = QActionGroup(sub)
        placement.setExclusive(True)
        dock = sub.addAction("停靠屏幕底部")
        dock.setCheckable(True)
        dock.setChecked(not self.engine.free_placement)
        placement.addAction(dock)
        dock.toggled.connect(lambda checked: self.engine.set_free_placement(not checked))
        free = sub.addAction("自由放置")
        free.setCheckable(True)
        free.setChecked(self.engine.free_placement)
        placement.addAction(free)
        free.toggled.connect(self.engine.set_free_placement)
        reset = sub.addAction("回到屏幕底部")
        reset.triggered.connect(self._reset_position)
        return sub

    def _build_harness_menu(self, parent: QMenu) -> QMenu:
        """DSH 联动：模式、面板、项目、模型、更新。"""
        sub = submenu(parent, "Harness")
        sub.setStyleSheet(self.MENU_QSS)
        menu_labels.cap_width(sub)
        if self.dsh is None:
            menu_labels.add_item(sub, "未连接 DSH", enabled=False)
            return sub

        mode_group = QActionGroup(sub)
        mode_group.setExclusive(True)
        for label, value in (
            ("工作模式（跟随 DSH）", "link"),
            ("桌宠模式（和 Mimi 聊天）", "agent"),
        ):
            item = sub.addAction(label)
            item.setCheckable(True)
            item.setChecked(self.dsh.mode == value)
            mode_group.addAction(item)
            item.triggered.connect(
                lambda checked=False, m=value, i=item: self._dsh_set_mode(i, m)
            )
        sub.addSeparator()
        # 消息栏已移除：不再提供"打开消息面板"（DSH 信息只走头顶气泡）。
        self._build_project_menu(sub)
        self._build_pet_model_menu(sub)

        update_info = self.dsh.plugin_update_available()
        if update_info:
            installed, latest = update_info
            menu_labels.add_item(
                sub, f"插件更新：v{latest} 已发布（当前 v{installed}）", enabled=False
            )
        check = sub.addAction("检查更新")
        check.triggered.connect(lambda: self.dsh.check_plugin_update(manual=True))

        cot_menu = submenu(sub, "摘要模型")
        cot_menu.setStyleSheet(self.MENU_QSS)
        menu_labels.cap_width(cot_menu)
        self._build_cot_menu(cot_menu)

        if self.dsh.pending_questions:
            q_menu = submenu(sub, f"回答 DSH 问题（{len(self.dsh.pending_questions)}）")
            q_menu.setStyleSheet(self.MENU_QSS)
            menu_labels.cap_width(q_menu)
            for question in list(self.dsh.pending_questions):
                q_item = menu_labels.add_item(
                    q_menu, question.question, tooltip=question.detail or question.question
                )
                q_item.triggered.connect(
                    lambda checked=False, q=question: self._dsh_answer(q)
                )
        if self.dsh.connection_error:
            menu_labels.add_item(sub, f"未连接：{self.dsh.connection_error}", enabled=False)
        return sub

    def _build_context_menu(self) -> QMenu:
        """右键菜单：8 个顶层入口，按"做事 / 陪她 / 管理"分组。

        拆成独立方法是为了能在 offscreen 测试里直接构造并检查，不必真的弹出。
        """
        menu = GlassMenu(self)
        menu.setStyleSheet(self.MENU_QSS)
        # 桌宠、输入框、气泡都是置顶窗口；菜单不带同样的标志会在鼠标移过去
        # 的瞬间被它们盖住并自动关闭。
        menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        menu_labels.cap_width(menu)

        self._menu_header(menu, "Mimi", *self._menu_status())
        menu.addSeparator()

        note = menu.addAction("记一下…")
        note.triggered.connect(lambda: self.feature_requested.emit("note"))

        focus = menu.addAction("专注与提醒…")
        focus.triggered.connect(lambda: self.companion.request_panel() if self.companion else None)

        menu.addSeparator()
        self._build_companion_menu(menu)
        if self.outfits is not None:
            self._build_outfit_menu(menu)

        menu.addSeparator()
        if self.hub is not None:
            self._build_records_menu(menu)
        self._build_settings_menu(menu)
        self._build_harness_menu(menu)

        menu.addSeparator()
        quit_action = menu.addAction("退出 Mimi")
        quit_action.triggered.connect(self.close)
        return menu

    def set_dsh_integration(self, dsh) -> None:
        self.dsh = dsh

    def set_project_provider(self, choices_fn, select_fn, current_fn) -> None:
        """Wire the Harness 项目 picker to the integration's session list."""
        self._project_choices = choices_fn
        self._project_select = select_fn
        self._project_current = current_fn

    def _build_project_menu(self, dsh_menu) -> None:
        """DSH 联动模式：选择跟随哪个项目（会话）。"""
        if getattr(self, "_project_choices", None) is None or self.dsh.mode != "link":
            return
        project_menu = submenu(dsh_menu, "项目")
        project_menu.setStyleSheet(self.MENU_QSS)
        menu_labels.cap_width(project_menu)
        current = self._project_current() if self._project_current else None
        auto = project_menu.addAction("跟随当前活动")
        auto.setCheckable(True)
        auto.setChecked(current is None)
        auto.triggered.connect(lambda: self._project_select(""))
        project_menu.addSeparator()
        choices = self._project_choices()
        if not choices:
            empty = project_menu.addAction("没有其他 DSH 会话")
            empty.setEnabled(False)
            return
        for choice in choices:
            sid = choice["session_id"]
            dot = "●" if choice.get("running") else "○"
            # Session labels are arbitrary user text; cap them so one long
            # project name cannot widen the popup.
            action = menu_labels.add_item(
                project_menu, f"{dot} {choice['label']}",
                tooltip=choice["label"],
            )
            action.setCheckable(True)
            action.setChecked(current == sid)
            action.triggered.connect(
                lambda checked=False, _sid=sid: self._project_select(_sid)
            )

    def _build_pet_model_menu(self, dsh_menu) -> None:
        """桌宠模型：只切换 Mimi shadow session，独立于 DSH 工作模型。"""
        if self.dsh is None or self.dsh.mode != "agent":
            return
        current_sid = self.dsh._agent_session_id
        model_menu = submenu(dsh_menu, "桌宠模型")
        model_menu.setStyleSheet(self.MENU_QSS)
        menu_labels.cap_width(model_menu)
        catalog = self.dsh.session_model_choices(force_refresh=True)
        current_provider, current_model, current_effort = self.dsh.current_session_model(current_sid)
        if not catalog:
            empty = model_menu.addAction("暂无可用模型")
            empty.setEnabled(False)
            if self.dsh._model_catalog_error:
                menu_labels.add_item(
                    model_menu, f"获取失败：{self.dsh._model_catalog_error}", enabled=False
                )
            return
        if not current_sid:
            empty = model_menu.addAction("未准备桌宠会话")
            empty.setEnabled(False)
            return
        for entry in catalog:
            label = entry.label or entry.model_id
            if entry.provider_label:
                label = f"{entry.provider_label} · {label}"
            if entry.reasoning_efforts:
                # A submenu title is capped too: model ids plus provider
                # labels are the widest strings the menu ever sees.
                model_submenu = submenu(model_menu, menu_labels.short_label(label))
                model_submenu.setStyleSheet(self.MENU_QSS)
                menu_labels.cap_width(model_submenu)
                model_submenu.setToolTip(label)
                for effort in entry.reasoning_efforts:
                    effort_action = model_submenu.addAction(effort.label or effort.effort_id)
                    effort_action.setCheckable(True)
                    effort_action.setChecked(
                        entry.provider == current_provider
                        and entry.model_id == current_model
                        and effort.effort_id == current_effort
                    )
                    effort_action.setToolTip(effort.description or effort.effort_id)
                    effort_action.triggered.connect(
                        lambda checked=False, _sid=current_sid, _mid=entry.model_id,
                        _provider=entry.provider, _effort=effort.effort_id: (
                            self.dsh.select_session_model(_sid, _mid, _provider, _effort)
                        )
                    )
                continue
            action = menu_labels.add_item(model_menu, label)
            action.setCheckable(True)
            action.setChecked(
                entry.provider == current_provider and entry.model_id == current_model
            )
            action.setToolTip(entry.description or entry.model_id)
            action.triggered.connect(
                lambda checked=False, _sid=current_sid, _mid=entry.model_id,
                _provider=entry.provider: (
                    self.dsh.select_session_model(_sid, _mid, _provider)
                )
            )


    def _dsh_prompt(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        text, ok = QInputDialog.getText(self, "给 DSH 发消息", "消息内容：")
        if ok and text.strip() and self.dsh is not None:
            self.dsh.prompt_active(text.strip())

    def _dsh_show_panel(self) -> None:
        if self.dsh is not None:
            self.dsh.request_show_panel()

    def _dsh_set_mode(self, action, mode: str) -> None:
        """Switch DSH link / pet-agent mode; reset the tick on failure."""
        if self.dsh is None:
            return
        if not self.dsh.set_mode(mode):
            action.setChecked(False)
            self.bubble_requested.emit("切换失败：DSH 没连上，或桌宠会话创建不了。", "companion")
            return
        # 切换成功要明确告诉用户"现在能干什么"，否则桌宠模式看起来只是换了个标签。
        if mode == "agent":
            self.bubble_requested.emit("桌宠模式：把鼠标放到头顶就能跟我说话。", "companion")
            self.engine.force_perform("wave")
        else:
            self.bubble_requested.emit("工作模式：我会跟着 DSH 的任务走。", "companion")

    def _build_cot_menu(self, cot_menu) -> None:
        """摘要模型：自动跟随 DSH / 指定模型 / 关闭（对回复做总结）。"""
        if self.dsh is None or self.dsh.cot is None:
            return
        current = (self.dsh.cot.provider, self.dsh.cot.model)
        for label, provider, model in self.dsh.cot_model_choices():
            action = menu_labels.add_item(cot_menu, label, tooltip=model or label)
            action.setCheckable(True)
            action.setChecked(
                bool(self.dsh.cot.enabled) and (provider, model) == current
            )
            action.triggered.connect(
                lambda checked=False, p=provider, m=model: self.dsh.configure_cot(p, m)
            )
        cot_menu.addSeparator()
        off_action = cot_menu.addAction("关闭总结")
        off_action.setCheckable(True)
        off_action.setChecked(not self.dsh.cot.enabled)
        off_action.triggered.connect(
            lambda checked=False: self.dsh.configure_cot("auto", "", enabled=False)
        )

    def _dsh_answer(self, question) -> None:
        if self.dsh is None:
            return
        options = [opt.get("label") for opt in question.options] or [question.question]
        if question.multi_select:
            from PySide6.QtWidgets import QInputDialog

            chosen, ok = QInputDialog.getItem(
                self, "回答 DSH 问题", question.question, options, 0, False
            )
            if ok:
                self.dsh.answer_question(question, [chosen])
        else:
            from PySide6.QtWidgets import QInputDialog

            chosen, ok = QInputDialog.getItem(
                self, "回答 DSH 问题", question.question, options, 0, False
            )
            if ok:
                self.dsh.answer_question(question, [chosen])

    def _apply_size(self, width: int, height: int) -> None:
        """Window-style uniform scaling of the pet."""
        self.engine.set_display_size(width, height)
        if self.size_changed is not None:
            self.size_changed()
        self.resize(width, height)
        self._move_to_root()
        self.update()

    # ------------------------------------------------------------------ zoom (无级缩放)

    ZOOM_MIN_H = 160
    ZOOM_MAX_H = 640
    ZOOM_STEP_H = 16

    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Mouse wheel scales the pet continuously (keeps the 2:3 window ratio)."""
        if self.engine.states.state in (PetState.DRAGGING, PetState.FALLING):
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        new_h = self.engine.display_h + (1 if delta > 0 else -1) * self.ZOOM_STEP_H
        new_h = max(self.ZOOM_MIN_H, min(self.ZOOM_MAX_H, new_h))
        if new_h != self.engine.display_h:
            self._apply_size(int(round(new_h * 2 / 3)), new_h)
        event.accept()

    def _reset_position(self) -> None:
        """Move the pet back to the bottom centre of the primary screen."""
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.engine.place_at(available.center().x(), float(available.bottom()))
        self._move_to_root()

    def _set_debug_enabled(self, enabled: bool) -> None:
        self.debug_enabled = enabled
        self.update()

    # ------------------------------------------------------------------ close

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        from .state_machine import Event

        # 第一次关闭先道个别：推迟 0.8 秒真正退出，让气泡有时间出现。
        if not getattr(self, "_farewell_done", False):
            self._farewell_done = True
            try:
                self.bubble_requested.emit("那我先休息啦，明天见。", "companion")
            except Exception:  # noqa: BLE001 - 告别失败不该阻塞退出
                pass
            self.engine.force_perform("wave_slow")
            event.ignore()
            from PySide6.QtCore import QTimer

            QTimer.singleShot(800, self.close)
            return

        self.engine.states.dispatch(Event.CLOSE)
        self.closed.emit()
        super().closeEvent(event)
