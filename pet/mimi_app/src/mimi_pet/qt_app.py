"""Qt application assembly: QApplication, 60 Hz ticker, scheduler timer and
window wiring. ``python -m mimi_pet`` launches this by default.
"""

from __future__ import annotations

import os
import random
import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication

from .action_library import ActionLibrary
from .affection import AffectionStore
from .asset_check import find_broken_assets
from .asset_prefetch import AssetPrefetcher
from .bubble_layer import DEFAULT_LIFETIME_S, BubbleLayer
from .focus_clock import FocusClock
from .collision import WorkArea, ground_for_point, union_bounds
from .companion import CompanionService
from .companion_ipc import CompanionPipe
from .companion_store import CompanionStore
from .companion_ui import CompanionController
from .config import load_config
from .dsh_bridge import dbg
from .dsh_integration import DshIntegration
from .dsh_panel import DshPanel
from .engine import PetEngine
from .expansion_hub import ExpansionHub
from . import desktop_sense
from .daily_stats import StatsTracker
from .system_sense import SystemSense, TransitionDetector
from .task_bar import BarState, MenuBarStatus, TaskStatusBar
from .time_sense import TimeSense
from .proactive import Context as ProactiveContext, ProactiveDirector, is_special
from .expansion_cards import (
    HealthCard, HealthSettingsCard, NoteCard, NoteEditorCard, NotesCard,
    TaskCard, TaskListCard, WrapupCard,
)
from .plugin_update import installed_plugin_version
from .image_cache import ImageCache
from .media import SmtcController
from .media_bar import MediaBar
from .qt_window import MimiWindow
from .release_notes import announcement_bubble, take_announcement
from .renderer import QtRenderer, build_snapshot
from .rig_model import load_rig_model
from .outfits import OutfitCatalog, OutfitService
from .session_monitor import WindowsSessionMonitor
from .state_machine import PetState

TICK_INTERVAL_MS = 16  # ~60 Hz logic/render loop
MAX_DT_S = 0.1
SINGLETON_KEY = os.environ.get("MIMI_SINGLETON_KEY", "mimi-pet-singleton")


def qt_work_areas() -> tuple[WorkArea, ...]:
    """All monitors' available desktop rectangles in virtual screen coords."""
    areas = []
    for screen in QGuiApplication.screens():
        geometry = screen.availableGeometry()
        areas.append(WorkArea(geometry.x(), geometry.y(), geometry.width(), geometry.height()))
    return tuple(areas)


def ground_y_at(root_x: float, root_y: float) -> float | None:
    area = ground_for_point(qt_work_areas(), root_x, root_y)
    return area.bottom if area is not None else None


def desktop_bounds() -> WorkArea | None:
    """Bounding rectangle of every work area (the drag cage)."""
    return union_bounds(qt_work_areas())


def run() -> int:
    # 测试里可能已经建过 QApplication（Qt 不允许两个实例），复用它。
    app = QApplication.instance() or QApplication(sys.argv)
    from .macos_window import hide_dock_icon
    hide_dock_icon()
    app.setApplicationName("Mimi 桌宠")
    app.setQuitOnLastWindowClosed(True)

    # Single desktop pet: when DSH's plugin auto-start collides with a
    # manually launched pet, the latecomer exits instead of stacking a
    # second overlapping character. removeServer clears a stale owner.
    singleton = QLocalServer()
    if not singleton.listen(SINGLETON_KEY):
        QLocalServer.removeServer(SINGLETON_KEY)
        if not singleton.listen(SINGLETON_KEY):
            return 0

    config = load_config()
    library = ActionLibrary(Path(config["asset_manifest"]))
    affection_store = AffectionStore()
    affection = affection_store.load()
    engine = PetEngine(library, config, affection=affection, affection_store=affection_store)
    rig_model_path = Path(config["asset_manifest"]).parent / library.live_rig["model"]
    rig = load_rig_model(rig_model_path)
    # 素材在外置卷上冷读 41–252 ms/件；主线程一旦直接读盘就整只桌宠冻住，
    # 画面停在上一帧（看上去像"动作做完卡在不该停的姿势"）。后台线程先把
    # 字节读进内存，渲染线程只解内存里的字节。
    prefetch = AssetPrefetcher()
    prefetch.start()
    cache = ImageCache(prefetcher=prefetch)
    renderer_holder: dict[str, QtRenderer] = {}

    def build_renderer() -> QtRenderer:
        return QtRenderer(
            cache,
            rig,
            (engine.display_w, engine.display_h),
            config.get("rig"),
            gaze_translation_scale=float(config["live"].get("gaze_translation_scale", 0.35)),
            gaze_hair_counter_scale=float(config["live"].get("gaze_hair_counter_scale", 0.3)),
            flat_gaze_shift=float(config["live"].get("flat_gaze_shift", 1.0)),
            flat_gaze_angle=float(config["live"].get("flat_gaze_angle", 0.35)),
        )

    renderer_holder["renderer"] = build_renderer()

    def rebuild_renderer() -> None:
        renderer_holder["renderer"] = build_renderer()

    window = MimiWindow(
        engine,
        renderer_holder["renderer"],
        ground_y_at,
        desktop_bounds,
        size_changed=rebuild_renderer,
    )

    # DSH integration: status polling + live event stream + message bar.
    integration = DshIntegration(engine)
    companion_service = CompanionService(CompanionStore())
    try:
        focus_minutes = int(os.environ.get("MIMI_FOCUS_MINUTES", "25"))
    except ValueError:
        focus_minutes = 25
    companion = CompanionController(
        window, companion_service,
        enabled=os.environ.get("MIMI_COMPANION_ENABLED", "1") != "0",
        default_minutes=focus_minutes,
    )
    companion.is_dsh_busy = lambda: integration.is_busy
    # System media (SMTC) companion: menu + status only. Chat requests go to
    # the pet agent, which drives media_cmd.py through its PowerShell tool.
    media = SmtcController(window)
    window.media = media
    if companion.actions.enabled:
        integration.local_command_handler = companion.handle_command
        integration.bubble_allowed = companion_service.allows_bubble
    window.companion = companion

    # 预设造型：目录随素材包分发，选择记录在用户目录。
    outfit_catalog = OutfitCatalog(
        Path(config["asset_manifest"]).parent.parent / "expansion_v1" / "presets.json"
    )
    outfit_service = OutfitService(outfit_catalog)
    window.outfits = outfit_service
    engine.set_outfit(outfit_service.current())
    # 面板快照需要造型能力表，必须在目录建好之后再挂上去。
    companion.outfit_catalog = outfit_catalog
    companion.actions.outfit_catalog = outfit_catalog
    if outfit_catalog.load_error:
        dbg(f"outfit catalog: {outfit_catalog.load_error}")
    if outfit_service.selection.store_error:
        dbg(f"outfit store: {outfit_service.selection.store_error}")
    # F01–F08 的本地功能；全部本地计算，不触发模型调用。
    hub = ExpansionHub()
    window.hub = hub
    companion.hub = hub
    companion.actions.hub = hub

    def _action_exists(action_id: str) -> bool:
        """这个动作现在**真的**能播吗（含当前造型的近似链）。

        以前只查动作库，于是穿着造型时：
        * 主动行为/健康卡会挑到造型根本播不出来的动作 → 白等一轮、动作静默消失；
        * 造型独占的动作（clap / shy / tilt_head / land_recover）反而被判成不存在。
        引擎的 ``can_play`` 才是同一个问题的正确口径。
        """
        return engine.can_play(action_id)

    # 让健康卡在派生动作缺失时能回退到原始动作。
    hub._action_checker = _action_exists

    note_card = NoteCard(window)
    task_card = TaskCard(window)
    wrapup_card = WrapupCard(window)
    health_card = HealthCard(window)
    health_settings = HealthSettingsCard(window)
    notes_card = NotesCard(window)
    note_editor = NoteEditorCard(window)
    task_list = TaskListCard(window)

    def on_note(body: str, tags: list) -> None:
        ok, message, _note_id = hub.add_note(body, tags=tags)
        if message:
            bubbles.add_bubble(message, "companion")
        if ok:
            refresh_notes()

    def on_task(title: str, steps: list) -> None:
        ok, message, _task_id = hub.start_task(title, steps)
        if message:
            bubbles.add_bubble(message, "companion")
        if ok:
            engine.force_perform("think_pencil")
            refresh_tasks()

    def on_wrapup(next_step: str, scene_choice: str) -> None:
        # 自动带上今天还在做的任务和最近改过的便签，用户不必再点一遍。
        task = hub.tasks.active()
        task_ids = [task.id] if task is not None else []
        titles = [task.title] if task is not None else []
        recent_notes = hub.notes.list()[:2]
        ok, message, _wid = hub.finish_day(
            task_ids=task_ids,
            title_snapshots=titles + [n.title for n in recent_notes],
            note_ids=[n.id for n in recent_notes],
            next_step=next_step,
            scene_choice=scene_choice,
        )
        if message:
            bubbles.add_bubble(message, "companion")

    def on_health_done() -> None:
        due = hub.health.summary().get("pending")
        if due:
            ok, message = hub.complete_health(due["id"])
            if message:
                bubbles.add_bubble(message, "companion")

    def on_health_snooze() -> None:
        due = hub.health.summary().get("pending")
        if due:
            _ok, message = hub.health.snooze(due["id"])
            if message:
                bubbles.add_bubble(message, "companion")

    def on_health_mute() -> None:
        due = hub.health.summary().get("pending")
        if due:
            plan = hub.health.plans.get(
                next((o.plan_id for o in hub.health.occurrences.values()
                      if o.status == "due"), "")
            )
            if plan is not None:
                _ok, message = hub.health.mute_today(plan.kind)
                if message:
                    bubbles.add_bubble(message, "companion")

    def on_health_saved(kind: str, interval: int, cap: int) -> None:
        ok, message = hub.health.configure(
            kind, enabled=True, interval_minutes=interval, daily_display_cap=cap
        )
        if message:
            bubbles.add_bubble(message, "companion")

    def refresh_notes(query: str = "") -> None:
        items = [
            {"id": n.id, "title": n.title, "excerpt": n.excerpt,
             "archived": n.status == "archived"}
            for n in (hub.notes.search(query) if query else hub.notes.list())
        ]
        notes_card.set_items(items)

    def refresh_tasks() -> None:
        items = []
        for task in hub.tasks.open_tasks():
            items.append({
                "id": task.id, "title": task.title, "progress": task.progress_text,
                "current": task.current_step.text if task.current_step else "",
                "status": task.status, "done_steps": task.done_count,
            })
        task_list.set_items(items)

    def on_open_note(note_id: str) -> None:
        note = hub.notes.get(note_id)
        if note is not None:
            note_editor.open_note(note)

    def on_save_note(note_id: str, body: str, revision: int) -> None:
        ok, message = hub.notes.update(note_id, body=body, expected_revision=revision)
        bubbles.add_bubble(message, "companion")
        if ok:
            refresh_notes(notes_card.query.text().strip())

    def on_archive_note(note_id: str, archived: bool) -> None:
        _ok, message = hub.notes.archive(note_id, archived)
        bubbles.add_bubble(message, "companion")
        refresh_notes(notes_card.query.text().strip())

    def on_trash_note(note_id: str) -> None:
        _ok, message = hub.notes.delete(note_id)
        bubbles.add_bubble(message, "companion")
        refresh_notes(notes_card.query.text().strip())

    def on_new_note() -> None:
        note_card.open_card()

    def on_activate_task(task_id: str) -> None:
        _ok, message = hub.tasks.activate(task_id)
        bubbles.add_bubble(message, "companion")
        refresh_tasks()

    def on_pause_task(task_id: str) -> None:
        _ok, message = hub.tasks.pause(task_id)
        bubbles.add_bubble(message, "companion")
        refresh_tasks()

    def on_cancel_task(task_id: str) -> None:
        _ok, message = hub.tasks.cancel(task_id)
        bubbles.add_bubble(message, "companion")
        refresh_tasks()

    def on_undo_task(task_id: str) -> None:
        _ok, message = hub.undo_task_step(task_id)
        bubbles.add_bubble(message, "companion")
        refresh_tasks()

    notes_card.open_note.connect(on_open_note)
    notes_card.archive_note.connect(on_archive_note)
    notes_card.trash_note.connect(on_trash_note)
    notes_card.new_note.connect(on_new_note)
    notes_card.search_changed.connect(refresh_notes)

    def refresh_trash() -> None:
        notes_card.set_trash([
            {"id": n.id, "title": n.title} for n in hub.notes.trashed()
        ])
        notes_card.open_card()

    def on_restore_note(note_id: str) -> None:
        _ok, message = hub.notes.restore(note_id)
        bubbles.add_bubble(message, "companion")
        refresh_trash()

    def on_purge_note(note_id: str) -> None:
        _ok, message = hub.notes.purge(note_id)
        bubbles.add_bubble(message, "companion")
        refresh_trash()

    def on_export_notes() -> None:
        """导出到用户数据目录；不弹文件对话框，避免打断当前输入。"""
        from .expansion_store import user_data_root
        target = user_data_root() / "notes-export.md"
        ok, message = hub.notes.write_export(target)
        bubbles.add_bubble(message, "companion")

    notes_card.export_requested.connect(on_export_notes)
    notes_card.show_trash.connect(refresh_trash)
    notes_card.restore_note.connect(on_restore_note)
    notes_card.purge_note.connect(on_purge_note)
    note_editor.saved.connect(on_save_note)
    task_list.activate_task.connect(on_activate_task)
    task_list.pause_task.connect(on_pause_task)
    task_list.cancel_task.connect(on_cancel_task)
    task_list.undo_step.connect(on_undo_task)
    task_list.new_task.connect(lambda: task_card.open_card())

    note_card.submitted.connect(on_note)
    task_card.submitted.connect(on_task)
    wrapup_card.submitted.connect(on_wrapup)
    health_card.done_clicked.connect(on_health_done)
    health_card.snooze_clicked.connect(on_health_snooze)
    health_card.mute_clicked.connect(on_health_mute)
    health_settings.saved.connect(on_health_saved)

    def on_feature(kind: str) -> None:
        if kind == "note":
            note_card.open_card()
        elif kind == "notes_list":
            refresh_notes()
            notes_card.open_card()
        elif kind == "task_new":
            task_card.open_card()
        elif kind == "task_list":
            refresh_tasks()
            task_list.open_card()
        elif kind == "wrapup":
            _wid, step = hub.wrapups.pending_next_step()
            wrapup_card.open_card(next_step=step)
        elif kind == "health_settings":
            health_settings.open_card()

    window.feature_requested.connect(on_feature)

    companion_pipe = (
        CompanionPipe(companion.actions, app.quit)
        if os.environ.get("MIMI_COMPANION_IPC") == "stdio-v1" else None
    )
    if companion_pipe is not None:
        companion.panel_requested.connect(companion_pipe.open_panel)
    panel = DshPanel(window)
    integration.sink = panel
    def _on_send(text: str) -> None:
        if not integration.prompt_active(text):
            panel.set_busy(False)
            return
        panel.set_busy(True, "思考中…")

    panel.send_requested.connect(_on_send)
    # 回复到达（或失败）后退出"思考中"；超时也会自动恢复。
    integration.on_reply = lambda text: panel.set_busy(False)
    panel.answer_requested.connect(integration.answer_question)
    # Multi-project picking lives in the pet's right-click Harness menu.
    window.set_project_provider(
        integration.session_choices,
        integration.select_session,
        integration.pinned_session,
    )

    def pet_head_y() -> float:
        """Character's actual head position (height ~220 display px, scaled)."""
        return engine.root_y - 220.0 * engine.display_h / 384.0

    def pet_popup_open() -> bool:
        """True while a popup owns the pointer (the pet's context menu).

        Moving the pet window or raising the always-on-top capsule underneath
        an open popup dismisses it on Windows, so the layout pass holds still.
        """
        return window.menu_open or app.activePopupWidget() is not None

    head_hovered = [False]
    hide_timer = QTimer()
    hide_timer.setSingleShot(True)
    hide_timer.setInterval(180)

    def on_hide_timeout() -> None:
        if panel.quick_input.hasFocus() or pet_popup_open():
            return
        position_panel_at_pet()

    hide_timer.timeout.connect(on_hide_timeout)

    def on_head_hovered(hovered: bool) -> None:
        head_hovered[0] = bool(hovered)
        if pet_popup_open():
            return
        if hovered:
            hide_timer.stop()
            position_panel_at_pet()
        elif integration.mode == "agent" and not panel.quick_input.hasFocus():
            hide_timer.start()

    def position_panel_at_pet() -> None:
        """Pin and reveal the input according to connection, mode and activity."""
        if not integration.connected:
            panel.hide()
            return
        if panel.quick_input.hasFocus():
            return  # never move the window mid-IME-composition
        # 一律贴在桌宠头顶（v1.0.0 曾把 macOS 改成贴屏幕顶部，
        # 用户期望的是"在 Mimi 头上"，所以这里不再区分平台）。
        panel.position_near(engine.root_x, pet_head_y())
        interacting = panel.rect().contains(panel.mapFromGlobal(QCursor.pos()))
        if integration.should_show_panel(
            head_hovered=head_hovered[0],
            panel_interacting=interacting,
        ):
            panel.show_box()
        else:
            panel.hide()

    integration.on_activity = position_panel_at_pet
    window.head_hovered.connect(on_head_hovered)
    window.set_dsh_integration(integration)
    integration.start()

    # DSH messages surface as transient speech bubbles above the head
    # (one bubble per message); clicking one focuses the input.
    bubbles = BubbleLayer()
    # 之前这个信号从来没被连接过：菜单里的"已记下""收好了"之类反馈
    # 全都没有出口。接到气泡层，让所有 _hub_say 真正可见。
    window.bubble_requested.connect(bubbles.add_bubble)

    # 缺少实时事件依赖时明确告诉用户，否则"工作模式看不到当前任务"
    # 会变成静默降级，很难排查。
    from .dsh_bridge import has_event_stream

    if not has_event_stream():
        QTimer.singleShot(3000, lambda: bubbles.add_bubble(
            "缺少 websocket-client，实时任务状态不可用；已退回 1 秒轮询。", "companion"))

    # 悬停 1.4 秒显示今日概览；10 分钟内最多一次，避免刷屏。
    _hover_timer = QTimer()
    _hover_timer.setSingleShot(True)
    _hover_timer.setInterval(1400)
    _hover_shown = [0.0]

    def _show_today() -> None:
        now_ts = time.monotonic()
        if now_ts - _hover_shown[0] < 600:
            return
        _hover_shown[0] = now_ts
        bubbles.add_bubble(stats.sentence(), "companion")

    _hover_timer.timeout.connect(_show_today)
    window.head_hovered.connect(lambda hovered: _hover_timer.start() if hovered else _hover_timer.stop())

    # The focus dial trails the pet for the whole session, so the countdown
    # stays visible without opening the DSH panel.
    clock = FocusClock()
    companion.clock = clock

    def on_bubble_requested(text: str, kind: str, lifetime_s: float = DEFAULT_LIFETIME_S) -> None:
        dbg(f"bubble layer add kind={kind}")
        top = panel.y() if panel.isVisible() else pet_head_y()
        bubbles.position_above(engine.root_x, top)
        bubbles.add_bubble(text, kind, lifetime_s)
        if not panel.quick_input.hasFocus():
            position_panel_at_pet()
        dbg(f"bubble layer chips={len(bubbles.bubbles())} visible={bubbles.isVisible()}")

    panel.bubble_requested.connect(on_bubble_requested)
    companion.bubble_requested.connect(panel.show_bubble)
    media.bubble_requested.connect(panel.show_bubble)

    # 播放栏：镜像系统媒体卡片，跟随宠物左侧；所有操作转发给 SMTC 会话。
    # 默认关闭，由右键菜单的「显示音乐栏」打开。
    media_bar = MediaBar()
    media.track_changed.connect(media_bar.set_track)
    media_bar.toggle_requested.connect(lambda: media.control("toggle"))
    media_bar.next_requested.connect(lambda: media.control("next"))
    media_bar.prev_requested.connect(lambda: media.control("previous"))
    media.bar = media_bar

    def announce_update() -> None:
        """Say what changed, once, the first time this version is opened."""
        version = installed_plugin_version()
        text = take_announcement(version) if version else None
        if not text:
            return
        # The full list lives in the panel's notice history; the bubble is the
        # pointer to it, so it only needs the headline.
        companion_service.add_announcement(text, f"update-{version}")
        on_bubble_requested(announcement_bubble(version), "info", lifetime_s=12.0)
        dbg(f"release announcement shown for v{version}")

    # Asset check. The loaders only confirm that files exist, so a 0-byte image
    # reaches the first render instead: a dropped frame on the full-frame path,
    # a frozen silhouette on the rig path. Two passes cover the two real risks:
    #
    #   * every frame, stat only - ~200ms measured for 1125 files. This is
    #     what catches a 0-byte file, the shape that actually shipped.
    #   * the rig's ~11 every-frame assets, fully decoded - ~220ms. A broken
    #     one of these is the only case that costs the silhouette rather than
    #     a single frame, so it earns the decode.
    #
    # Decoding all 1103 frames was measured at ~4.6s; spreading that over idle
    # ticks stretches it to tens of seconds of background churn, and the only
    # extra failure it would catch is a truncated-but-non-empty action frame,
    # worth one dropped frame. Not worth it.
    asset_bubbled = [False]

    def report_broken_assets(problems) -> None:
        for path, reason in problems:
            dbg(f"broken asset: {path} - {reason}")
        if problems and not asset_bubbled[0]:
            asset_bubbled[0] = True
            on_bubble_requested(
                f"素材损坏：{problems[0][0].name}（共 {len(problems)} 个），"
                "显示可能异常。",
                "info",
                lifetime_s=12.0,
            )

    def check_action_frames() -> None:
        """Stat every playable frame: catches a 0-byte file, costs ~200ms."""
        rig_paths = set(rig.asset_paths())
        report_broken_assets(
            find_broken_assets(
                (p for p in library.asset_paths() if p not in rig_paths),
                decode=False,
            )
        )

    def check_rig_assets() -> None:
        """Decode the every-frame assets: a broken one costs the silhouette."""
        report_broken_assets(find_broken_assets(rig.asset_paths(), decode=True))

    def on_bubble_clicked() -> None:
        if integration.connected:
            panel.show_panel()
        else:
            bubbles.hide()

    bubbles.message_clicked.connect(on_bubble_clicked)
    bubbles.companion_clicked.connect(lambda: companion.request_panel("notices"))

    def can_present_companion() -> bool:
        focused = app.focusWidget()
        typing = focused is not None and (
            focused is panel or panel.isAncestorOf(focused)
        )
        return (
            not typing and not integration.is_busy
            and app.activePopupWidget() is None and app.activeModalWidget() is None
            and (engine.states.state in (PetState.IDLE, PetState.SLEEPING) or engine.is_sitting)
        )

    companion.can_present = can_present_companion

    primary = QGuiApplication.primaryScreen()
    available = primary.availableGeometry()
    engine.place_at(available.center().x(), float(available.bottom()))
    engine.note_input(time.perf_counter())
    window.move(
        int(round(engine.root_x - engine.pivot_display_x)),
        int(round(engine.root_y - engine.pivot_display_y)),
    )
    # Paint one complete frame before exposing the translucent Tool window.
    # Otherwise Windows can keep the initially empty alpha surface visually
    # absent until a later repaint, making a healthy process look like a
    # failed launch.
    initial_now = time.perf_counter()
    initial_cursor = QCursor.pos()
    initial_frame = engine.tick(
        initial_now,
        0.0,
        (float(initial_cursor.x()), float(initial_cursor.y())),
        None,
        None,
    )
    initial_snapshot = build_snapshot(
        initial_frame,
        engine,
        (engine.display_w, engine.display_h),
    )
    initial_pixmap = renderer_holder["renderer"].render(initial_snapshot)
    window.show_frame(initial_snapshot, initial_pixmap)
    window.setWindowTitle("Mimi 桌宠")
    window.show()

    from .macos_window import make_always_on_top

    def _raise_all() -> None:
        for widget in QApplication.topLevelWidgets():
            if widget.isVisible():
                make_always_on_top(widget)

    # 只在启动和应用重新激活时提升一次层级。原来每 2 秒重申一次，
    # 会让窗口服务器反复重排，整机明显变卡。
    _raise_all()
    app.applicationStateChanged.connect(
        lambda state: _raise_all() if state == Qt.ApplicationState.ApplicationActive else None
    )
    window.raise_()
    window.repaint()
    app.processEvents()
    session_monitor = WindowsSessionMonitor(app, window, companion_service.system_event)

    ticker = QTimer()
    ticker.setTimerType(Qt.TimerType.PreciseTimer)
    ticker.setInterval(TICK_INTERVAL_MS)
    last_tick = [time.perf_counter()]
    last_cursor = [QCursor.pos()]
    # 上一次渲染失败的原因：只在变化时写日志，避免每帧刷屏。
    render_errors = {"last": ""}
    # 辅助窗口排布上一次的失败原因（只在变化时写日志）。
    layout_errors = {"last": ""}

    def handle_hub_events(events: dict) -> None:
        """把功能事件翻译成桌宠反馈；播不了动画也不影响业务状态。"""
        if not events:
            return
        anim = events.get("animation")
        if anim is not None and not engine.is_busy_physical():
            engine.force_perform(anim.action_id)
        due = events.get("health_due")
        if due is not None:
            # 卡片只在允许展示时弹出；勿扰时留在待处理，面板里能看到。
            if companion.can_present():
                health_card.open_card(due["name"])
            else:
                bubbles.add_bubble(f"{due['name']}记一笔，等你方便再说。", "companion")
        toy = events.get("toy")
        if toy is not None:
            window.set_toy_state(toy)

    # 启动时把"最后输入"对齐到当前时刻：engine 里默认是 0.0，
    # 直接拿 perf_counter 去减会得到"已经静止了几千秒"，一开机就触发主动行为。
    engine.note_input(time.perf_counter())
    # 默认开启；MIMI_PROACTIVE=0 或插件配置 proactiveEnabled=false 时完全静默。
    proactive_enabled = os.environ.get("MIMI_PROACTIVE", "1") != "0"
    status_bar_enabled = os.environ.get("MIMI_STATUSBAR", "1") != "0"
    director = ProactiveDirector()
    from .expansion_store import user_data_root
    time_sense = TimeSense(state_path=str(user_data_root() / "time_sense.json"))
    stats = StatsTracker(path=str(user_data_root() / "daily_stats.json"))
    app.aboutToQuit.connect(stats.flush)
    system_sense = SystemSense()
    system_events = TransitionDetector()
    # 前台窗口只有在尺寸稳定一段时间后才拿来贴边，否则会"追着窗口跑"。
    window_watch = {"rect": None, "since": 0.0}
    announced = {"boot": False, "low_battery": 0.0, "offline": False}
    stats_memo = {"notes": 0, "task_id": "", "done": 0}
    focus_memo = {"state": ("", "")}
    os.environ.setdefault("MIMI_STATUSBAR_POS", str(user_data_root() / "statusbar.json"))
    # macOS 用菜单栏状态项；Windows/Linux 用屏幕上的浮动玻璃条。
    status_bar = MenuBarStatus() if sys.platform == "darwin" else TaskStatusBar()
    window.stats = stats
    # 点状态条 = 看"现在在跑什么"。工作模式下这是 DSH 的实时活动，
    # 不是本地任务板的"新建任务"，所以直接开 DSH 面板。
    # 点状态条只做一件事：把 Mimi 的输入/消息条叫出来。
    # 不再跳本地任务板——用户要看的是"现在在跑什么"，不是"新建任务"。
    status_bar.open_requested.connect(panel.show_panel)
    director_last_input = [engine._last_input_s]
    # 挂到窗口上，便于测试与调试查看；也让面板能显示"现在在做什么"。
    window.proactive = director

    def _work_rect():
        """当前屏幕的可用区域（Qt 提供，跨平台）。"""
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else None

    def _stable_foreground():
        """前台窗口矩形；只有连续 3 秒没变才返回，避免追着窗口跑。"""
        rect = desktop_sense.foreground_window()
        now = time.monotonic()
        if rect != window_watch["rect"]:
            window_watch["rect"] = rect
            window_watch["since"] = now
            return None
        if rect is None or now - window_watch["since"] < 3.0:
            return None
        return rect

    def _walk_target(kind: str, arg: str, rect) -> float | None:
        """把 @walk:... 的参数翻译成世界坐标 x。"""
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return None
        geo = screen.geometry()
        pivot = engine.display_w / 2.0
        if kind == "home":
            return geo.x() + geo.width() / 2.0 - pivot
        if kind == "cursor":
            cursor = desktop_sense.cursor_position()
            return None if cursor is None else cursor[0] - pivot
        if kind == "random":
            return geo.x() + geo.width() * random.uniform(0.18, 0.82) - pivot
        if kind in ("window_left", "window_right"):
            rect = _stable_foreground()
            if rect is None:
                return None
            wx, _, ww, _ = rect
            edge = wx if kind == "window_left" else wx + ww
            return edge - pivot
        # left/right + 比例
        try:
            frac = float(arg)
        except (TypeError, ValueError):
            frac = 0.1
        return geo.x() + geo.width() * frac - pivot

    def _execute_step(action: str, rect) -> bool:
        """执行一步：普通动作走引擎，@ 开头的是桌面行为。"""
        if action == "@sit":
            return engine.start_sit()
        if action == "@stand":
            return engine.stand_up()
        if action == "@sleep":
            return engine.start_sleep()
        if action.startswith("@walk:"):
            parts = action.split(":")
            kind = parts[1] if len(parts) > 1 else "home"
            arg = parts[2] if len(parts) > 2 else ""
            target = _walk_target(kind, arg, rect)
            if target is None:
                return False
            direction = "right" if target >= engine.root_x else "left"
            return engine.start_walk(direction, target_x=target)
        # Greeting actions use the authored default-skin wave. wave_hello is an
        # expansion clip with a visibly different character design.
        if action == "wave_hello":
            action = "wave"
        return engine.force_perform(action)

    def _run_proactive(now_s: float) -> None:
        """把导演的决定翻译成动作；播不了就跳过，不影响其他逻辑。"""
        from mimi_pet.state_machine import PetState as _State

        # 用户刚动过 → 立刻放弃当前行为
        if engine._last_input_s != director_last_input[0]:
            director_last_input[0] = engine._last_input_s
            director.note_user_input(now_s)
            time_sense.note_activity(time.time())

        idle_s = max(0.0, now_s - engine._last_input_s)
        music = hub.music.state
        signals = time_sense.tick(time.time())
        rect = _work_rect()
        work = None
        if rect is not None:
            work = (float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()))
        info = desktop_sense.snapshot(work)

        ctx = ProactiveContext(
            now=now_s,
            idle_s=idle_s,
            hour=signals.hour,
            quiet=bool(companion.service.quiet) if companion is not None else False,
            focus_running=bool(companion.service.running) if companion is not None else False,
            music_playing=bool(music.playing),
            dsh_busy=integration.is_busy,
            dragging=engine.states.state is _State.DRAGGING,
            phase=signals.phase,
            is_weekend=signals.is_weekend,
            first_seen_today=signals.first_seen_today,
            welcome_back=signals.welcome_back,
            hour_changed=signals.hour_changed,
            desktop=info,
            screen_width=float(rect.width()) if rect is not None else 0.0,
            available=_action_exists,
        )

        # 一步演完回到待机 → 交给导演排下一步。
        # 这里必须每个待机 tick 都问一次导演：旧实现只在 busy_until 是无穷大
        # 时通知一次，而 notify_idle 之后 busy_until 变成有限值、current 仍然
        # 非空，于是下面那行 return 永远挡住下一步——每套主动行为都只演第一步
        # 就停住（47 套里 33 套因此完全看不到）。
        if director.state.current is not None and engine.states.state is _State.IDLE:
            if director.state.busy_until == float("inf"):
                director.notify_idle(now_s)

        if engine.states.state is not _State.IDLE:
            return
        if director.state.current is not None and now_s < director.state.busy_until:
            return
        action = director.next_action(now_s, ctx)
        if not action:
            return
        if _execute_step(action, rect):
            director.notify_action_started(now_s)
        else:
            # 这一步在这套衣服上播不出来：让导演立刻跳过它，别把整套路停住。
            director.state.busy_until = now_s
            director.notify_idle(now_s)

    def _boot_greeting() -> None:
        if announced["boot"]:
            return
        announced["boot"] = True
        bubbles.add_bubble("Mimi 来了，今天也一起加油。", "companion")
        engine.force_perform("wave")

    QTimer.singleShot(2500, _boot_greeting)

    window.run_proactive = _run_proactive

    def _layout_aux_windows() -> None:
        """每帧重排跟随桌宠的辅助窗口（输入条、气泡、计时、媒体条）。"""
        # Keep the capsule pinned above the pet's head while visible, but
        # re-evaluate the mode-specific visibility whenever the cursor moves.
        if not panel.quick_input.hasFocus():
            position_panel_at_pet()
        if bubbles.isVisible() and not panel.quick_input.hasFocus():
            top = panel.y() if panel.isVisible() else pet_head_y()
            bubbles.position_above(engine.root_x, top)
        clock.position_near(engine.root_x, pet_head_y(), engine.display_w)
        # Keep the anchor fresh whenever the bar is open, not just while
        # visible, so opening it never flashes at a stale position.
        if media_bar.is_open:
            # Real window rect (the rig pivot is not the window centre).
            media_bar.position_near(
                engine.root_x - engine.pivot_display_x,
                engine.root_y - engine.pivot_display_y,
                engine.display_w,
                engine.display_h,
            )

    def on_tick() -> None:
        now = time.perf_counter()
        dt = min(MAX_DT_S, max(0.0, now - last_tick[0]))
        last_tick[0] = now
        cursor = QCursor.pos()
        # Track mouse activity for scenario triggers (idle durations).
        if cursor != last_cursor[0]:
            last_cursor[0] = cursor
            engine.note_input(now)
        ground = ground_y_at(engine.root_x, engine.root_y) if engine.states.state is PetState.FALLING else None
        x_bounds = None
        bounds = desktop_bounds()
        if bounds is not None:
            x_bounds = (
                bounds.x + engine.pivot_display_x,
                bounds.right - (engine.display_w - engine.pivot_display_x),
            )
        frame = engine.tick(now, dt, (float(cursor.x()), float(cursor.y())), ground, x_bounds)
        integration.maintain_anim()
        # 跟听状态机需要真实的播放器状态：每帧把 SMTC 快照喂给它。
        # 只在"开启跟听"时喂一次会让状态机永远停在 waiting。
        if media is not None and hub.music.enabled:
            media_track = media.track
            before = hub.music.state.state
            hub.music.update(
                title=(media_track.display() if media_track is not None else ""),
                playing=bool(media_track is not None and media_track.status == "playing"),
                source=(media_track.source if media_track is not None else ""),
            )
            after = hub.music.state.state
            # 只在进入/离开"跟听中"时提示一次，避免每帧刷气泡。
            if after != before:
                if after == "listening":
                    bubbles.add_bubble("一起听～", "companion")
                elif before == "listening" and after == "waiting":
                    bubbles.add_bubble("先歇一会儿，等你继续放。", "companion")
        hub_events = hub.tick(dt)
        handle_hub_events(hub_events)
        # While the context menu is open the pet holds still and nothing else
        # is raised: both would dismiss the popup. engine.tick keeps running,
        # so no animation time is lost.
        popup_open = pet_popup_open()
        # 主动行为：只在待机时发起，任何用户输入都会让它立刻让位。
        # 必须放在 popup_open 之后：菜单打开时不发起，否则弹窗会被顶掉。
        if proactive_enabled and not popup_open:
            _run_proactive(now)
        # ---- 今日统计：用增量对比，避免改动其他模块 ----
        _focusing = bool(companion is not None and companion.service.running)
        _active = (time.perf_counter() - engine._last_input_s) < 5.0
        stats.tick(dt, active=_active, focusing=_focusing)
        _notes_now = hub.notes.count()
        if _notes_now > stats_memo["notes"]:
            stats.today.notes_added += _notes_now - stats_memo["notes"]
        stats_memo["notes"] = _notes_now
        _task = hub.tasks.active()
        if _task is not None:
            if _task.id == stats_memo["task_id"]:
                _done = _task.done_count
                if _done > stats_memo["done"]:
                    stats.today.steps_done += _done - stats_memo["done"]
                stats_memo["done"] = _done
            else:
                stats_memo.update(task_id=_task.id, done=_task.done_count)
        elif stats_memo["task_id"]:
            # 上一个任务从"活动"里消失：说明完成了（取消的不会走到这里）
            stats.today.tasks_done += 1
            stats_memo.update(task_id="", done=0)

        # ---- 番茄钟自动衔接 ----
        _focus = companion.service.focus if companion is not None else None
        _fstatus = (_focus.status, _focus.kind) if _focus is not None else ("", "")
        if _fstatus != focus_memo["state"]:
            _prev = focus_memo["state"]
            focus_memo["state"] = _fstatus
            if _fstatus[0] == "finished" and _fstatus[1] == "focus":
                bubbles.add_bubble("这一轮专注结束了，休息 5 分钟吧。", "companion")
                if companion is not None:
                    companion.service.start_focus(5, kind="break")
                engine.force_perform("stretch")
            elif _fstatus[0] == "finished" and _fstatus[1] == "break":
                bubbles.add_bubble("休息结束，准备好了就开始下一轮。", "companion")
                engine.force_perform("wave")

        # ---- 系统小反应：只在状态翻转时触发 ----
        for _event, _payload in system_events.update(system_sense.poll(time.monotonic())).items():
            if _event == "low_battery":
                bubbles.add_bubble(f"电量只剩 {_payload}% 了，先充会儿电吧。", "companion")
                engine.force_perform("sleepy_nod")
            elif _event == "offline":
                bubbles.add_bubble("网断了…我先安静待着。", "companion")
                engine.force_perform("look_down_sad")
            elif _event == "online":
                bubbles.add_bubble("网络回来了。", "companion")
                engine.force_perform("cheer_jump")

        if status_bar_enabled:
            _task = hub.tasks.active()
            _focus = ""
            if companion is not None and companion.service.running:
                _focus = f"{companion.service.focus.label} {companion.service.remaining_text()}"
            _title = _task.title if _task is not None else ""
            _step = _task.current_step.text if _task is not None and _task.current_step else ""
            _done = _task.done_count if _task is not None else 0
            _total = len(_task.steps) if _task is not None else 0
            if _task is None and integration.mode == "link" and integration.connected:
                # 工作模式：没有本地任务时，显示 DSH 正在做什么。
                _act = integration.activity
                _title = getattr(_act, "status_zh", "") or ""
                _step = getattr(_act, "summary", "") or ""
            _bar = BarState(
                title=_title,
                step=_step,
                done=_done,
                total=_total,
                focus_label=_focus,
                paused=bool(_task is not None and _task.status == "paused"),
            )
            status_bar.set_state(_bar)
            if _bar.visible:
                _rect = _work_rect()
                if _rect is not None:
                    status_bar.place(_rect)
        if not popup_open:
            # 辅助界面（输入条、气泡、计时、媒体条、任务条）的排布失败，
            # 绝不允许拖垮主画面这一帧：它们在 show_frame 之前，一旦抛异常
            # 整个 tick 就在这里结束，桌宠会永远停在上一帧——正是"只有右键
            # 菜单打开时才像刷新了一下"的那种死画面。
            try:
                _layout_aux_windows()
            except Exception as exc:  # noqa: BLE001 - 排布故障只影响本帧位置
                reason = f"{type(exc).__name__}: {exc}"
                if layout_errors["last"] != reason:
                    layout_errors["last"] = reason
                    dbg(f"aux layout failed (rendering continues): {reason}")
            else:
                if layout_errors["last"]:
                    layout_errors["last"] = ""
                    dbg("aux layout recovered")
        # Move first, then paint the frame into the window's final position.
        # On macOS a transparent window can otherwise keep the freshly painted
        # backing surface stale until another window exposes it (right-clicking
        # the pet appeared to "refresh" it). Avoid calling move() every tick:
        # repeated same-position moves also invalidate the native surface.
        if not popup_open:
            target_x = int(round(engine.root_x - engine.pivot_display_x))
            target_y = int(round(engine.root_y - engine.pivot_display_y))
            if window.x() != target_x or window.y() != target_y:
                window.move(target_x, target_y)
        snapshot = build_snapshot(frame, engine, (engine.display_w, engine.display_h))
        # A single undecodable asset must never freeze the whole pet: without
        # this guard one failing frame silently kills every later tick and the
        # window keeps showing whatever was painted last — the "pet stands
        # still until I right-click it" bug. Keep the last good pixmap instead.
        try:
            pixmap = renderer_holder["renderer"].render(snapshot)
        except Exception as exc:  # noqa: BLE001 - 画面故障不该终止整个循环
            reason = f"{type(exc).__name__}: {exc}"
            if render_errors["last"] != reason:
                render_errors["last"] = reason
                dbg(f"render failed, keeping last frame: {reason}")
            pixmap = window.current_pixmap()
            if pixmap is None:
                return
        else:
            if render_errors["last"]:
                render_errors["last"] = ""
                dbg("render recovered")
        window.show_frame(snapshot, pixmap)

    check = QTimer()
    check.setInterval(int(config["scheduler"]["idle_check_interval_s"] * 1000))

    dsh_poll = QTimer()
    dsh_poll.setInterval(int(integration.poll_interval_s() * 1000))
    dsh_drain = QTimer()
    # Keep streamed text and approval cards feeling live. drain_events itself
    # has a per-pass budget, so this cadence cannot starve rendering.
    dsh_drain.setInterval(50)
    companion_timer = QTimer()
    companion_timer.setInterval(250)

    def on_companion_tick() -> None:
        session_monitor.poll()
        if companion_pipe is not None:
            companion_pipe.drain()
        companion.tick()
        if companion_pipe is not None:
            companion_pipe.publish()

    companion_timer.timeout.connect(on_companion_tick)

    def on_check() -> None:
        now = time.perf_counter()
        if engine.scenario_check(now):
            return
        engine.try_random_performance(now)

    def on_dsh_poll() -> None:
        integration.poll_status_async()
        # 事件流断了就切到更快的轮询，恢复后再放慢。
        want = int(integration.poll_interval_s() * 1000)
        if dsh_poll.interval() != want:
            dsh_poll.setInterval(want)

    def on_dsh_drain() -> None:
        integration.drain_events()
        integration.drain_poll()

    check.timeout.connect(on_check)
    dsh_poll.timeout.connect(on_dsh_poll)
    dsh_drain.timeout.connect(on_dsh_drain)

    shutdown_done = False

    def shutdown() -> None:
        nonlocal shutdown_done
        if shutdown_done:
            return
        shutdown_done = True
        ticker.stop()
        check.stop()
        dsh_poll.stop()
        dsh_drain.stop()
        companion_timer.stop()
        prefetch.stop()
        session_monitor.close()
        companion_service.tick()
        companion_service.save()
        if companion_pipe is not None:
            companion_pipe.close()
        bubbles.hide()
        clock.hide()
        media_bar.hide()
        media.shutdown()
        integration.stop()
        affection_store.save(engine.affection)

    window.closed.connect(shutdown)
    window.closed.connect(app.quit)
    app.aboutToQuit.connect(shutdown)

    ticker.timeout.connect(on_tick)
    ticker.start()
    check.start()
    dsh_poll.start()
    dsh_drain.start()
    companion_timer.start()
    if companion_pipe is not None:
        companion_pipe.start()
    # Announce after the pet is on screen and positioned, not mid-startup.
    QTimer.singleShot(1500, announce_update)
    # Likewise: never make startup wait on decoding assets. The two passes are
    # ~200ms each, so they run apart rather than as one half-second hitch.
    QTimer.singleShot(2500, check_action_frames)
    QTimer.singleShot(3000, check_rig_assets)

    # 全库字节预热放在最后、最低优先级：整库 webp 约 95 MB，后台慢慢读，
    # 逐件让路给正在播的动作。播放时的冷读卡顿由它消掉。
    def warm_asset_bytes() -> None:
        added = prefetch.sweep(library.asset_paths())
        added += prefetch.sweep(rig.asset_paths())
        if added:
            dbg(f"asset prefetch queued {added} files")

    QTimer.singleShot(4000, warm_asset_bytes)

    # 播一个动作时先把它整段排到最前面：一次请求顶掉逐帧的冷读等待。
    prefetched_action: list[str] = [""]

    def prefetch_current_action() -> None:
        action = engine.player.action
        action_id = action.id if action is not None else ""
        if action_id == prefetched_action[0]:
            return
        prefetched_action[0] = action_id
        if action is not None:
            prefetch.request(action.frames, priority=0)

    prefetch_timer = QTimer()
    prefetch_timer.setInterval(120)
    prefetch_timer.timeout.connect(prefetch_current_action)
    prefetch_timer.start()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
