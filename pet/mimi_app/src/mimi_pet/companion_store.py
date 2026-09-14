"""Versioned, atomic storage for local focus sessions and reminders."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path


class CompanionStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else self.default_path()
        self.error = ""
        self._write_blocked = False

    @staticmethod
    def default_path() -> Path:
        override = os.environ.get("MIMI_COMPANION_STATE_PATH")
        if override:
            return Path(override).expanduser()
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) / "MimiDesktopPet" if appdata else Path.home() / ".mimi-desktop-pet"
        return base / "companion.json"

    def quarantine(self) -> None:
        """Keep an unreadable state file for recovery, without overwriting it."""
        if not self.path.exists():
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = self.path.with_name(f"{self.path.stem}.corrupt-{stamp}-{time.time_ns()}.json")
        try:
            os.replace(self.path, backup)
            self.error = "原记录格式异常，已保留备份并启用空白记录。"
        except OSError:
            self.error = "原记录无法读取或备份，暂时无法保存。"
            self._write_blocked = True

    def load(self) -> dict:
        self.error = ""
        self._write_blocked = False
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("schema_version") != 1:
                raise ValueError("unsupported companion state")
            return payload
        except FileNotFoundError:
            return {}
        except (ValueError, TypeError):
            self.quarantine()
        except OSError:
            self.error = "无法读取陪伴记录，请检查文件访问权限。"
            self._write_blocked = True
        return {}

    def save(self, payload: dict) -> bool:
        if self._write_blocked:
            return False
        temporary: str | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                temporary = handle.name
                json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
            self.error = ""
            return True
        except (OSError, TypeError, ValueError):
            self.error = "本次操作已生效，但保存失败；重启后可能丢失，请检查存储位置。"
            return False
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
