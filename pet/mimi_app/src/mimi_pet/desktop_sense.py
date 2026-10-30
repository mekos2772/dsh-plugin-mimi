"""桌面感知：屏幕工作区、鼠标位置、前台窗口边界。

所有平台调用都是**可选**的：拿不到就返回 None，行为层会自动跳过依赖它的动作。
Windows 用 user32/dwmapi，macOS 用 Quartz（pyobjc 可用时）。不读取窗口内容、
不截图、不记录标题——只取矩形，用于"贴着窗口边缘探头"这类动作。

隐私边界：本模块只暴露几何信息（x/y/宽/高），不暴露任何文本或图像。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

Rect = tuple[float, float, float, float]  # x, y, w, h


@dataclass(frozen=True)
class DesktopInfo:
    work_area: Rect | None = None
    cursor: tuple[float, float] | None = None
    foreground: Rect | None = None

    @property
    def usable(self) -> bool:
        return self.work_area is not None


# --------------------------------------------------------------------- Windows

def _win_work_area() -> Rect | None:
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        ok = ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        if not ok:
            return None
        return (float(rect.left), float(rect.top),
                float(rect.right - rect.left), float(rect.bottom - rect.top))
    except Exception:  # noqa: BLE001
        return None


def _win_foreground() -> Rect | None:
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        dwmapi = ctypes.windll.dwmapi
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        # 优先用 DWM 的扩展边框：GetWindowRect 会把阴影算进去，导致偏移。
        rect = wintypes.RECT()
        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        result = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect), ctypes.sizeof(rect),
        )
        if result != 0:
            if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                return None
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return None
        return (float(rect.left), float(rect.top), float(width), float(height))
    except Exception:  # noqa: BLE001
        return None


def _win_cursor() -> tuple[float, float] | None:
    try:
        import ctypes
        from ctypes import wintypes

        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return None
        return (float(point.x), float(point.y))
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------- macOS

def _mac_foreground() -> Rect | None:
    try:
        import Quartz  # type: ignore

        windows = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        )
        for window in windows:
            if window.get("kCGWindowLayer") != 0:
                continue
            bounds = window.get("kCGWindowBounds")
            if not bounds:
                continue
            width = float(bounds.get("Width", 0))
            height = float(bounds.get("Height", 0))
            if width <= 0 or height <= 0:
                continue
            return (float(bounds.get("X", 0)), float(bounds.get("Y", 0)), width, height)
    except Exception:  # noqa: BLE001
        return None
    return None


def _mac_cursor() -> tuple[float, float] | None:
    try:
        import Quartz  # type: ignore

        event = Quartz.CGEventCreate(None)
        point = Quartz.CGEventGetLocation(event)
        return (float(point.x), float(point.y))
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------- 公开接口

def work_area() -> Rect | None:
    """屏幕可用区域（不含任务栏/Dock）。"""
    if sys.platform == "win32":
        return _win_work_area()
    return None  # macOS/Linux 交给 Qt 层提供


def cursor_position() -> tuple[float, float] | None:
    if sys.platform == "win32":
        return _win_cursor()
    if sys.platform == "darwin":
        return _mac_cursor()
    return None


def foreground_window() -> Rect | None:
    """当前前台窗口的屏幕矩形；拿不到返回 None。"""
    if sys.platform == "win32":
        return _win_foreground()
    if sys.platform == "darwin":
        return _mac_foreground()
    return None


def snapshot(work: Rect | None = None) -> DesktopInfo:
    return DesktopInfo(
        work_area=work if work is not None else work_area(),
        cursor=cursor_position(),
        foreground=foreground_window(),
    )


def is_cursor_near(info: DesktopInfo, window: Rect, margin: float = 48.0) -> bool:
    """鼠标是否停在某个窗口矩形附近（用于"看你在忙什么"这类反应）。"""
    if info.cursor is None:
        return False
    x, y = info.cursor
    wx, wy, ww, wh = window
    return (wx - margin <= x <= wx + ww + margin) and (wy - margin <= y <= wy + wh + margin)
