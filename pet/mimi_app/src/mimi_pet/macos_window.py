"""macOS 窗口层级与 Dock 图标控制。

Qt 的 ``WindowStaysOnTopHint`` 只映射到 NSFloatingWindowLevel，遇到全屏应用或
其他浮层仍会被压下去。这里直接用 Objective-C 运行时改 NSWindow：

* ``setLevel:`` 提到 NSStatusWindowLevel（25），高于普通窗口与浮动面板；
* ``setCollectionBehavior:`` 允许出现在所有 Space 与全屏应用之上；
* ``setActivationPolicy:`` 设为 Accessory，隐藏 Dock 图标，但窗口照常显示。

全部是可选调用：拿不到 Objective-C 运行时（非 macOS、无权限）时静默跳过。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys

#: NSWindowLevel
NS_STATUS_WINDOW_LEVEL = 25
NS_SCREEN_SAVER_WINDOW_LEVEL = 1000

#: NSWindowCollectionBehavior
CAN_JOIN_ALL_SPACES = 1 << 0
STATIONARY = 1 << 4
FULL_SCREEN_AUXILIARY = 1 << 8

#: NSApplicationActivationPolicy
ACTIVATION_POLICY_REGULAR = 0
ACTIVATION_POLICY_ACCESSORY = 1


def _load():
    if sys.platform != "darwin":
        return None
    try:
        path = ctypes.util.find_library("objc") or "/usr/lib/libobjc.A.dylib"
        objc = ctypes.cdll.LoadLibrary(path)
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        return objc, address
    except Exception:  # noqa: BLE001
        return None


_CACHE: dict = {}


def _msg(address: int, restype, *argtypes):
    key = (restype, argtypes)
    fn = _CACHE.get(key)
    if fn is None:
        fn = ctypes.CFUNCTYPE(restype, *argtypes)(address)
        _CACHE[key] = fn
    return fn


def _sel(objc, name: str) -> int:
    return objc.sel_registerName(name.encode())


def _cls(objc, name: str) -> int:
    return objc.objc_getClass(name.encode())


def hide_dock_icon() -> bool:
    """把进程设为 Accessory：Dock 里不再出现 Python 图标。"""
    if not _native_platform():
        return False
    loaded = _load()
    if loaded is None:
        return False
    objc, address = loaded
    try:
        app = _msg(address, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
            _cls(objc, "NSApplication"), _sel(objc, "sharedApplication"))
        if not app:
            return False
        set_policy = _msg(address, ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)
        return bool(set_policy(app, _sel(objc, "setActivationPolicy:"), ACTIVATION_POLICY_ACCESSORY))
    except Exception:  # noqa: BLE001
        return False


def _native_platform() -> bool:
    """只有真正的 cocoa 平台才去碰 NSWindow；offscreen/minimal 下会崩。"""
    try:
        from PySide6.QtGui import QGuiApplication

        name = (QGuiApplication.platformName() or "").lower()
        return name in ("cocoa", "macos")
    except Exception:  # noqa: BLE001
        return False


def make_always_on_top(widget, level: int | None = None) -> bool:
    if not _native_platform():
        return False
    if level is None:
        import os
        level = int(os.environ.get("MIMI_WINDOW_LEVEL", "3"))
    """把 Qt 顶层窗口提到指定层级，并允许跨 Space / 全屏显示。"""
    loaded = _load()
    if loaded is None or widget is None:
        return False
    objc, address = loaded
    try:
        view = ctypes.c_void_p(int(widget.winId()))
        window = _msg(address, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
            view, _sel(objc, "window"))
        if not window:
            return False
        set_level = _msg(address, None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)
        set_level(window, _sel(objc, "setLevel:"), int(level))
        behavior = CAN_JOIN_ALL_SPACES | STATIONARY | FULL_SCREEN_AUXILIARY
        set_behavior = _msg(address, None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)
        set_behavior(window, _sel(objc, "setCollectionBehavior:"), behavior)
        # 让窗口不参与"隐藏应用"（Cmd+H）也不被最小化
        set_hides = _msg(address, None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool)
        set_hides(window, _sel(objc, "setHidesOnDeactivate:"), False)
        can_hide = _msg(address, ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        can_hide(window, _sel(objc, "canHide"))
        return True
    except Exception:  # noqa: BLE001
        return False
