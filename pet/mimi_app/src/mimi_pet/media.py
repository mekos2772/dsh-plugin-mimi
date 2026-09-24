"""System media companion: read and control whatever is playing.

Built on Windows System Media Transport Controls (SMTC) — Microsoft's
official API, the same data source as the Windows media overlay. Works with
every player that shows up there: Apple Music, browsers, QQ Music, Spotify…

winsdk is an optional dependency: without it the controller degrades to a
quiet no-op and nothing else in the pet is affected. Read + transport
control only — the controller never fetches or plays content itself, so
nothing here leaves the official API surface.
"""

from __future__ import annotations

import asyncio
import threading

from PySide6.QtCore import QObject, Signal

from . import menu_labels
from .smtc_core import STATUS_TEXT, pick_session, pretty_title

try:  # pragma: no cover - exercised only on Windows with winsdk installed
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _SmtcManager,
    )

    HAVE_WINSDK = True
except ImportError:  # pragma: no cover
    HAVE_WINSDK = False

POLL_S = 1.0


class MediaTrack:
    """Snapshot of one SMTC session; compares by value for change detection."""

    __slots__ = ("title", "artist", "status", "source", "position_ms", "duration_ms")

    def __init__(
        self,
        title: str,
        artist: str,
        status: str,
        source: str,
        position_ms: int = 0,
        duration_ms: int = 0,
    ) -> None:
        self.title = title
        self.artist = artist
        self.status = status
        self.source = source
        self.position_ms = position_ms
        self.duration_ms = duration_ms

    def __eq__(self, other) -> bool:
        return (
            isinstance(other, MediaTrack)
            and (self.title, self.artist, self.status, self.source,
                 self.position_ms, self.duration_ms)
            == (other.title, other.artist, other.status, other.source,
                other.position_ms, other.duration_ms)
        )

    def display(self) -> str:
        text = pretty_title(self.title)
        if self.artist:
            text += f" - {self.artist}"
        return text


class SmtcController(QObject):
    """Reads and controls the system media session from a background loop."""

    bubble_requested = Signal(str, str)
    track_changed = Signal(object)  # MediaTrack | None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.available = HAVE_WINSDK
        # Why control is off. Never left blank while unavailable: the menu and
        # the bubbles both surface it, so a broken SMTC cannot look idle.
        self.unavailable_reason = "" if HAVE_WINSDK else "音乐控制不可用（缺少 winsdk）"
        self.track: MediaTrack | None = None
        # Optional MediaBar, set by the app assembly so the menu can offer to
        # open and close it.
        self.bar = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if self.available:
            self._thread = threading.Thread(
                target=self._run_loop, name="mimi-media-smtc", daemon=True
            )
            self._thread.start()

    # ------------------------------------------------------------- background

    def _run_loop(self) -> None:  # pragma: no cover - real SMTC only
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._poll_forever())
        except Exception as exc:  # noqa: BLE001 - many COM/asyncio failure modes
            # Swallowing this would leave ``available`` True while nothing is
            # ever polled again — the menu would keep claiming music works.
            if not self._stop.is_set():
                self._mark_unavailable(
                    f"音乐控制不可用（{type(exc).__name__}）"
                )
        finally:
            self._loop.close()
            asyncio.set_event_loop(None)

    def _mark_unavailable(self, reason: str) -> None:
        """Report a dead SMTC once; safe to call from the poll thread."""
        if not self.available:
            return
        self.available = False
        self.unavailable_reason = reason
        self._bubble(reason)

    async def _poll_forever(self) -> None:  # pragma: no cover
        manager = await _SmtcManager.request_async()
        while not self._stop.is_set():
            track = await self._read_track(manager)
            if track != self.track:
                self.track = track
                self.track_changed.emit(track)
            await asyncio.sleep(POLL_S)

    async def _read_track(self, manager) -> MediaTrack | None:  # pragma: no cover
        # Session choice lives in smtc_core so the track this displays and the
        # track _send drives can never drift apart.
        session, props = await pick_session(manager)
        if session is None or props is None or not props.title:
            return None
        status = STATUS_TEXT.get(
            int(session.get_playback_info().playback_status), "unknown"
        )
        timeline = session.get_timeline_properties()
        position_ms = (
            int(timeline.position.total_seconds() * 1000) if timeline else 0
        )
        duration_ms = (
            int(timeline.end_time.total_seconds() * 1000) if timeline else 0
        )
        return MediaTrack(
            props.title, props.artist or "", status,
            session.source_app_user_model_id or "",
            position_ms, duration_ms,
        )

    def _submit(self, factory):  # pragma: no cover - real SMTC only
        loop = self._loop
        if loop is None or loop.is_closed() or self._stop.is_set():
            return None
        try:
            return asyncio.run_coroutine_threadsafe(factory(), loop)
        except RuntimeError:
            # The loop can close between the check above and this call.
            return None

    async def _send(self, action: str) -> bool:  # pragma: no cover
        manager = await _SmtcManager.request_async()
        # Same session choice as _read_track, so a click never drives a
        # different player than the one the playback bar is showing.
        session, _ = await pick_session(manager)
        if session is None:
            return False
        ops = {
            "resume": session.try_play_async,
            "pause": session.try_pause_async,
            "toggle": session.try_toggle_play_pause_async,
            "next": session.try_skip_next_async,
            "previous": session.try_skip_previous_async,
        }
        result = await ops[action]()
        return bool(result)

    # ---------------------------------------------------------------- public

    def control(self, action: str) -> None:
        """Ask the SMTC session to do something, without blocking the GUI thread.

        This is called from menu items and playback-bar clicks, so waiting on
        the session here would freeze the pet for as long as the player takes
        to answer. The reply comes back through ``_report_control`` instead.
        """
        if not self.available:
            self._bubble(self.unavailable_reason or "这台电脑上没有可控制的播放器会话。")
            return
        future = self._submit(lambda: self._send(action))
        if future is None:
            # The poll loop has not come up yet (or already stopped); saying
            # nothing would make the click look broken.
            self._bubble("音乐控制还没就绪，稍后再试一次。")
            return
        # Runs on the asyncio thread; _bubble emits a signal, which Qt queues
        # back onto the GUI thread.
        future.add_done_callback(self._report_control)

    def _report_control(self, future) -> None:
        try:
            ok = bool(future.result(timeout=0))
        except Exception:  # noqa: BLE001 - a failed transport call is not fatal
            ok = False
        if not ok:
            self._bubble(
                "没有正在播放的音乐。先在任意播放器"
                "（Apple Music、浏览器都行）里放点什么，我就能控制了。"
            )

    def status_text(self) -> str:
        if not self.available:
            return self.unavailable_reason or "音乐控制不可用"
        if self.track is None:
            return "音乐空闲"
        state = {"playing": "播放中", "paused": "已暂停"}.get(self.track.status, "")
        return f"♪ {self.track.display()}{f'（{state}）' if state else ''}"

    # ------------------------------------------------------------------ menu

    def _bubble(self, text: str) -> None:
        self.bubble_requested.emit(text, "media")

    def build_menu(self, menu) -> None:
        # A track title is the longest string this menu can carry, and an
        # unshaped row used to stretch the popup across the whole screen. The
        # label is capped; the full text stays in the status row's tooltip.
        status = menu_labels.add_item(
            menu, self.status_text(), enabled=False
        )
        status.setToolTip(self.status_text())
        if self.available:
            if self.bar is not None:
                # The bar is closed by default; this is how it gets opened.
                toggle = menu.addAction(
                    "隐藏音乐栏" if self.bar.is_open else "显示音乐栏"
                )
                toggle.triggered.connect(self.bar.toggle)
            if self.track is not None:
                if self.track.status == "playing":
                    pause_item = menu.addAction("暂停播放")
                    pause_item.triggered.connect(lambda: self.control("pause"))
                else:
                    resume_item = menu.addAction("继续播放")
                    resume_item.triggered.connect(lambda: self.control("resume"))
                next_item = menu.addAction("下一首")
                next_item.triggered.connect(lambda: self.control("next"))
                prev_item = menu.addAction("上一首")
                prev_item.triggered.connect(lambda: self.control("previous"))
            else:
                launch = menu.addAction("打开 Apple Music")
                launch.triggered.connect(self._launch_apple_music)
        # Unconditional: an unavailable controller must not shift where the
        # separator lands for the menus that follow.
        menu.addSeparator()

    # Steam-free launch helper: the Store app is addressed by its AUMID.
    APPLE_MUSIC_MATCH = "*AppleMusic*"

    def _launch_apple_music(self) -> None:
        """Menu entry point.

        Probing Get-StartApps is a PowerShell round-trip that can take seconds,
        so it runs on a worker thread instead of freezing the pet.
        """
        threading.Thread(
            target=self._launch_apple_music_worker,
            name="mimi-media-launch",
            daemon=True,
        ).start()

    def _launch_apple_music_worker(self) -> None:  # pragma: no cover - real shell
        import subprocess

        flags = 0x08000000  # CREATE_NO_WINDOW
        try:
            probe = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-StartApps | Where-Object { $_.AppID -like '*AppleMusic*' } "
                 "| Select-Object -First 1 -ExpandProperty AppID"],
                capture_output=True, text=True, timeout=10,
                creationflags=flags,
            )
        except (OSError, subprocess.SubprocessError):
            probe = None
        app_id = (probe.stdout or "").strip() if probe is not None else ""
        if not app_id:
            self._bubble("没找到 Apple Music，先去 Microsoft Store 装一个吧。")
            return
        try:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-Command",
                 f"Start-Process 'shell:AppsFolder\\{app_id}'"],
                creationflags=flags,
            )
        except OSError:
            self._bubble("Apple Music 没能打开，稍后再试试。")
            return
        self._bubble("苹果音乐打开啦，放首歌我来帮你控制～")

    def shutdown(self) -> None:
        self._stop.set()
