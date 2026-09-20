"""Versioned, atomic storage for local focus sessions and reminders."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

# Reading the state file can fail because another process is holding it for a
# moment — antivirus, a sync client, Explorer. Those locks clear in well under
# a second, so give them a chance before declaring the record unreadable.
READ_ATTEMPTS = 4
READ_RETRY_DELAY_S = 0.1


class CompanionStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else self.default_path()
        self.error = ""
        self._write_blocked = False
        # True when the block is only "we could not move a known-bad file
        # aside". Retrying that move is always safe, unlike retrying a read
        # we never completed.
        self._quarantine_pending = False

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
            # Nothing left to preserve, so there is nothing left to block on.
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
            # Still locked after retrying. Blocking writes is the safe call —
            # the service has already been built from an empty read, so any
            # save would clobber data we never managed to see.
            self.error = "无法读取陪伴记录，请检查文件访问权限。"
            self._write_blocked = True
            return {}

        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            self.quarantine()
            return {}
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            self.quarantine()
            return {}
        return payload

    def save(self, payload: dict) -> bool:
        if self._write_blocked:
            if not self._quarantine_pending:
                # A file we could not read. Never overwrite data we never saw,
                # and the state built from the empty read is already in play,
                # so there is no going back within this process.
                return False
            # Moving a known-bad file aside is safe to retry — the first
            # attempt may simply have lost to a momentary lock, and latching
            # on that cost the whole session's records.
            self.quarantine()
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
