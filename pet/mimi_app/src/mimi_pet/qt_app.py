"""Qt application assembly: QApplication, 60 Hz ticker, scheduler timer and
window wiring. ``python -m mimi_pet`` launches this by default.
"""

from __future__ import annotations

import os
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
from .plugin_update import installed_plugin_version
from .image_cache import ImageCache
from .media import SmtcController
from .media_bar import MediaBar
from .qt_window import MimiWindow
from .release_notes import announcement_bubble, take_announcement
from .renderer import QtRenderer, build_snapshot
from .rig_model import load_rig_model
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
    app = QApplication(sys.argv)
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
    cache = ImageCache()
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
    companion_pipe = (
        CompanionPipe(companion.actions, app.quit)
        if os.environ.get("MIMI_COMPANION_IPC") == "stdio-v1" else None
    )
    if companion_pipe is not None:
        companion.panel_requested.connect(companion_pipe.open_panel)
    panel = DshPanel(window)
    integration.sink = panel
    panel.send_requested.connect(integration.prompt_active)
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
    window.raise_()
    window.repaint()
    app.processEvents()
    session_monitor = WindowsSessionMonitor(app, window, companion_service.system_event)

    ticker = QTimer()
    ticker.setTimerType(Qt.TimerType.PreciseTimer)
    ticker.setInterval(TICK_INTERVAL_MS)
    last_tick = [time.perf_counter()]
    last_cursor = [QCursor.pos()]

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
        # While the context menu is open the pet holds still and nothing else
        # is raised: both would dismiss the popup. engine.tick keeps running,
        # so no animation time is lost.
        popup_open = pet_popup_open()
        if not popup_open:
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
        snapshot = build_snapshot(frame, engine, (engine.display_w, engine.display_h))
        pixmap = renderer_holder["renderer"].render(snapshot)
        window.show_frame(snapshot, pixmap)
        if not popup_open:
            window.move(
                int(round(engine.root_x - engine.pivot_display_x)),
                int(round(engine.root_y - engine.pivot_display_y)),
            )

    check = QTimer()
    check.setInterval(int(config["scheduler"]["idle_check_interval_s"] * 1000))

    dsh_poll = QTimer()
    dsh_poll.setInterval(int(DshIntegration.POLL_INTERVAL_S * 1000))
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
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
