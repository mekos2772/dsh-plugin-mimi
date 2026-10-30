"""系统状态感知：电量与网络。

两类信号都只用于"小反应"——低电量提醒、断网失落、恢复开心。
拿不到的平台上返回 None，调用方自动跳过，不报错。

网络判断优先用 Qt 的 QNetworkInformation（不产生流量）；
不支持时退回到一个极短的 socket 探测，并且只在每 60 秒做一次。
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class SystemState:
    on_battery: bool | None = None
    battery_percent: int | None = None
    network_online: bool | None = None

    @property
    def low_battery(self) -> bool:
        return (
            self.battery_percent is not None
            and self.battery_percent <= 20
            and self.on_battery is True
        )


# ------------------------------------------------------------------ 电量

def _battery_windows() -> tuple[bool | None, int | None]:
    try:
        import ctypes
        from ctypes import wintypes

        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", wintypes.BYTE),
                ("BatteryFlag", wintypes.BYTE),
                ("BatteryLifePercent", wintypes.BYTE),
                ("Reserved1", wintypes.BYTE),
                ("BatteryLifeTime", wintypes.DWORD),
                ("BatteryFullLifeTime", wintypes.DWORD),
            ]

        status = SYSTEM_POWER_STATUS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return None, None
        on_battery = None if status.ACLineStatus == 255 else status.ACLineStatus == 0
        percent = None if status.BatteryLifePercent == 255 else int(status.BatteryLifePercent)
        return on_battery, percent
    except Exception:  # noqa: BLE001
        return None, None


def _battery_macos() -> tuple[bool | None, int | None]:
    try:
        output = subprocess.check_output(["pmset", "-g", "batt"], text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None, None
    on_battery = "Battery Power" in output
    percent = None
    for token in output.replace(";", " ").split():
        if token.endswith("%"):
            try:
                percent = int(token.rstrip("%"))
            except ValueError:
                percent = None
            break
    return on_battery, percent


def battery() -> tuple[bool | None, int | None]:
    if sys.platform == "win32":
        return _battery_windows()
    if sys.platform == "darwin":
        return _battery_macos()
    return None, None


# ------------------------------------------------------------------ 网络

def _network_qt() -> bool | None:
    try:
        from PySide6.QtNetwork import QNetworkInformation

        if not QNetworkInformation.load(QNetworkInformation.Feature.Reachability):
            return None
        info = QNetworkInformation.instance()
        if info is None:
            return None
        state = info.reachability()
        online = state in (
            QNetworkInformation.Reachability.Online,
            QNetworkInformation.Reachability.Local,
        )
        return bool(online)
    except Exception:  # noqa: BLE001
        return None


def _network_socket() -> bool | None:
    try:
        with socket.create_connection(("223.5.5.5", 53), timeout=0.4):
            return True
    except OSError:
        return False


def network() -> bool | None:
    value = _network_qt()
    if value is not None:
        return value
    return _network_socket()


# ------------------------------------------------------------------ 采样

@dataclass
class SystemSense:
    """节流采样，避免每帧都去查系统。"""

    battery_interval_s: float = 45.0
    network_interval_s: float = 60.0
    _last_battery: SystemState = SystemState()
    _last_network_at: float = -1e9
    _last_battery_at: float = -1e9
    _net_value: bool | None = None

    def poll(self, now: float | None = None) -> SystemState:
        now = time.monotonic() if now is None else now
        on_battery = self._last_battery.on_battery
        percent = self._last_battery.battery_percent
        if now - self._last_battery_at >= self.battery_interval_s:
            self._last_battery_at = now
            on_battery, percent = battery()
        if now - self._last_network_at >= self.network_interval_s:
            self._last_network_at = now
            self._net_value = network()
        state = SystemState(on_battery=on_battery, battery_percent=percent,
                            network_online=self._net_value)
        self._last_battery = state
        return state


class TransitionDetector:
    """只在状态**翻转**时给出事件，避免每帧重复触发。"""

    def __init__(self) -> None:
        self._previous: SystemState | None = None

    def update(self, state: SystemState) -> dict:
        events: dict = {}
        prev = self._previous
        self._previous = state
        if prev is None:
            return events
        if state.low_battery and not prev.low_battery:
            events["low_battery"] = state.battery_percent
        if prev.network_online is True and state.network_online is False:
            events["offline"] = True
        if prev.network_online is False and state.network_online is True:
            events["online"] = True
        return events
