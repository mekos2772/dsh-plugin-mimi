"""Windows lock/suspend notifications; all callbacks run on the Qt thread."""

from __future__ import annotations

import sys
import time
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter


def _input_desktop_available() -> bool:
    """Read the input desktop; never switch desktops or send any input."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.OpenInputDesktop.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.GetUserObjectInformationW.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    )
    user32.GetUserObjectInformationW.restype = wintypes.BOOL
    user32.CloseDesktop.argtypes = (wintypes.HANDLE,)
    user32.CloseDesktop.restype = wintypes.BOOL
    desktop = user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
    if not desktop:
        return False
    try:
        name = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        if not user32.GetUserObjectInformationW(
            desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed),  # UOI_NAME
        ):
            return False
        return name.value.casefold() == "default"
    finally:
        user32.CloseDesktop(desktop)


class WindowsSessionMonitor(QAbstractNativeEventFilter):
    def __init__(self, app, window, callback: Callable[[str, bool], None]) -> None:
        super().__init__()
        self.app = app
        self.callback = callback
        self.hwnd = int(window.winId())
        self.registered = False
        self.installed = False
        self.registration_error = 0
        self.native_enabled = sys.platform == "win32" and app.platformName() == "windows"
        self._last_poll = -1e9
        if not self.native_enabled:
            return
        import ctypes
        from ctypes import wintypes

        self._wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
        self._wts.WTSRegisterSessionNotification.argtypes = (wintypes.HWND, wintypes.DWORD)
        self._wts.WTSRegisterSessionNotification.restype = wintypes.BOOL
        self._wts.WTSUnRegisterSessionNotification.argtypes = (wintypes.HWND,)
        self._wts.WTSUnRegisterSessionNotification.restype = wintypes.BOOL
        self.registered = bool(self._wts.WTSRegisterSessionNotification(self.hwnd, 0))
        self.registration_error = 0 if self.registered else ctypes.get_last_error()
        app.installNativeEventFilter(self)
        self.installed = True

    def poll(self, *, force: bool = False) -> None:
        """Also cover startup on the lock screen and a missed WTS message."""
        now = time.monotonic()
        if not self.native_enabled or (not force and now - self._last_poll < 1.0):
            return
        self._last_poll = now
        available = _input_desktop_available()
        self.callback("desktop", not available)
        if available:
            # The event loop is running on the interactive input desktop.
            # Clearing stale lock/power flags does not resume the timer.
            self.callback("lock", False)
            self.callback("suspend", False)

    def handle_message(self, message: int, event: int) -> None:
        if message == 0x02B1:  # WM_WTSSESSION_CHANGE
            if event in (7, 8):  # WTS_SESSION_LOCK / UNLOCK
                self.callback("lock", event == 7)
            elif event in (1, 2, 3, 4):  # console / remote connect / disconnect
                self.callback("session", event in (2, 4))
        elif message == 0x0218:  # WM_POWERBROADCAST
            if event == 4:  # PBT_APMSUSPEND
                self.callback("suspend", True)
            elif event in (6, 7, 18):  # resume critical / suspend / automatic
                self.callback("suspend", False)

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        if sys.platform == "win32" and bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            import ctypes
            from ctypes import wintypes

            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
            if msg.hWnd == self.hwnd:
                self.handle_message(int(msg.message), int(msg.wParam))
        return False, 0

    def close(self) -> None:
        if self.installed:
            self.app.removeNativeEventFilter(self)
            self.installed = False
        if self.registered:
            self._wts.WTSUnRegisterSessionNotification(self.hwnd)
            self.registered = False
