"""本地扩展数据的通用存储：原子写、版本校验、坏档隔离。

F01–F08 各自的数据文件都通过这里读写，避免每个功能重写一遍
"临时文件 + fsync + replace + 备份"的逻辑，也避免其中一处漏掉
坏档保护就把用户数据覆盖掉。

Qt-free：领域层和测试都能直接用。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .debug_log import dbg

#: 读文件时允许的重试次数。杀毒、同步盘、资源管理器会短暂占用句柄。
READ_ATTEMPTS = 4
READ_RETRY_DELAY_S = 0.1


def user_data_root() -> Path:
    """%APPDATA%/MimiDesktopPet，其他平台落到用户主目录。"""
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "MimiDesktopPet" if appdata else Path.home() / ".mimi-desktop-pet"


class JsonStore:
    """一个小型版本化 JSON 文件的读写器。

    设计取舍：
    * 读失败（被占用）连续重试，仍失败就**禁止写入**，绝不覆盖没读到的东西。
    * 内容损坏或版本不符时把原文件改名保留，再启用空白记录。
    * 写入失败不抛异常，返回 False 并留下可展示的错误文案——调用方通常
      已经改完了内存状态，此时抛异常只会让界面处于半完成状态。
    """

    def __init__(self, path: Path | str, *, schema_version: int, override_env: str = "") -> None:
        self.path = Path(path)
        self.schema_version = schema_version
        self.override_env = override_env
        self.error = ""
        self._write_blocked = False
        self._quarantine_pending = False

    # ------------------------------------------------------------ 路径

    @classmethod
    def for_user_file(
        cls, name: str, *, schema_version: int, override_env: str = ""
    ) -> "JsonStore":
        override = os.environ.get(override_env) if override_env else ""
        path = Path(override).expanduser() if override else user_data_root() / name
        return cls(path, schema_version=schema_version, override_env=override_env)

    # ------------------------------------------------------------ 读

    def load(self) -> dict:
        self.error = ""
        self._write_blocked = False
        self._quarantine_pending = False

        raw: str | None = None
        for attempt in range(READ_ATTEMPTS):
            try:
                raw = self.path.read_text(encoding="utf-8")
                break
            except FileNotFoundError:
                return {}
            except OSError:
                if attempt + 1 < READ_ATTEMPTS:
                    time.sleep(READ_RETRY_DELAY_S)
        if raw is None:
            self.error = "无法读取本地记录，请检查文件访问权限。"
            self._write_blocked = True
            return {}

        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            self._quarantine()
            return {}
        if not isinstance(payload, dict) or payload.get("schema_version") != self.schema_version:
            self._quarantine()
            return {}
        return payload

    def _quarantine(self) -> None:
        """保留读不懂的原文件；失败则禁止覆盖。"""
        if not self.path.exists():
            self._write_blocked = False
            self._quarantine_pending = False
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = self.path.with_name(f"{self.path.stem}.corrupt-{stamp}-{time.time_ns()}.json")
        try:
            os.replace(self.path, backup)
            self.error = "原记录格式异常，已保留备份并启用空白记录。"
            self._write_blocked = False
            self._quarantine_pending = False
        except OSError:
            self.error = "原记录无法读取或备份，暂时无法保存。"
            self._write_blocked = True
            self._quarantine_pending = True

    # ------------------------------------------------------------ 写

    def save(self, payload: dict[str, Any]) -> bool:
        if self._write_blocked:
            if not self._quarantine_pending:
                # 有一份没读到过的数据，覆盖它就是丢数据。
                return False
            self._quarantine()
            if self._write_blocked:
                return False

        body = dict(payload)
        body["schema_version"] = self.schema_version
        temporary: str | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                temporary = handle.name
                json.dump(body, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
            self.error = ""
            return True
        except (OSError, TypeError, ValueError) as exc:
            dbg(f"store save failed ({self.path.name}): {exc}")
            self.error = "本次操作已生效，但保存失败；重启后可能丢失。"
            return False
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
