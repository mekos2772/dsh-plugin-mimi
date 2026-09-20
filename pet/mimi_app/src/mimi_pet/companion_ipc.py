"""Private inherited pipes between the DSH plugin and its pet process.

No listener, extra port or window. I/O lives on daemon threads; commands are
drained by Qt so the companion service and all widgets stay on the GUI thread.
"""

from __future__ import annotations

from collections import OrderedDict, deque
import json
import queue
import sys
import threading
import time
from typing import Callable, TextIO

from .companion_protocol import CompanionActions
from .debug_log import dbg

PREFIX = "MIMI/1 "
MAX_LINE = 1024 * 1024
# Frames waiting to go out. Each one embeds a full snapshot, and a Windows
# stdio pipe holds only ~4 KB, so a host that stops reading fills this quickly.
# Overflow drops frames; it must never be read as "the host is gone".
MAX_OUTBOX = 32


class CompanionPipe:
    def __init__(
        self, actions: CompanionActions, on_shutdown: Callable[[], None],
        *, reader: TextIO | None = None, writer: TextIO | None = None,
    ) -> None:
        self.actions = actions
        self.on_shutdown = on_shutdown
        self.reader = reader if reader is not None else sys.stdin
        self.writer = writer if writer is not None else sys.stdout
        self.commands: queue.Queue = queue.Queue(maxsize=32)
        self._closed = threading.Event()
        self._parent_gone = threading.Event()
        self._condition = threading.Condition()
        self._outbox: deque[dict] = deque()
        self._latest_state: dict | None = None
        self._completed: OrderedDict[str, dict] = OrderedDict()
        self._last_state_at = -1e9
        self._sequence = 0
        self._reader_thread: threading.Thread | None = None
        self._writer_thread: threading.Thread | None = None

    def start(self) -> None:
        if self.reader is None or self.writer is None:
            self._parent_gone.set()
            return
        for stream in (self.reader, self.writer):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure:
                # errors="replace", not "strict": a single byte the host got
                # wrong must degrade into one skipped line, not a decode
                # exception that shuts the pet down. Garbage can never pass the
                # PREFIX and JSON checks anyway.
                reconfigure(encoding="utf-8", errors="replace")
        self._enqueue(self._state_frame("ready", protocol=1))
        self._reader_thread = threading.Thread(target=self._read, name="mimi-host-in", daemon=True)
        self._writer_thread = threading.Thread(target=self._write, name="mimi-host-out", daemon=True)
        self._reader_thread.start()
        self._writer_thread.start()

    def _discard_line(self) -> bool:
        """Consume the tail of an oversized line. False when the stream ended."""
        while True:
            chunk = self.reader.readline(MAX_LINE)
            if not chunk:
                return False
            if chunk.endswith("\n"):
                return True

    def _read(self) -> None:
        try:
            while not self._closed.is_set():
                line = self.reader.readline(MAX_LINE + 1)
                if not line:
                    break  # EOF: the host really did close the pipe
                if len(line) > MAX_LINE:
                    # readline stops at the cap, so an unterminated result still
                    # has a tail in the buffer that must be consumed before the
                    # next frame. A complete-but-oversized line does not.
                    dbg(f"companion frame over {MAX_LINE} bytes: discarded")
                    if not line.endswith("\n") and not self._discard_line():
                        break
                    continue
                if not line.startswith(PREFIX):
                    continue
                try:
                    message = json.loads(line[len(PREFIX):])
                except (ValueError, TypeError):
                    continue
                if not isinstance(message, dict):
                    continue
                while not self._closed.is_set():
                    try:
                        self.commands.put(message, timeout=0.1)
                        break
                    except queue.Full:
                        continue
        except (OSError, UnicodeError, ValueError) as exc:
            # Only reached on a broken stream. Say so: this path ends the pet
            # process, and it used to be completely silent.
            dbg(f"companion read stopped: {type(exc).__name__}: {exc}")
        finally:
            self._parent_gone.set()

    def _write(self) -> None:
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._closed.is_set() or self._outbox or self._latest_state)
                    if self._outbox:
                        message = self._outbox.popleft()
                    elif self._latest_state is not None:
                        message, self._latest_state = self._latest_state, None
                    elif self._closed.is_set():
                        return
                    else:
                        continue
                try:
                    payload = PREFIX + json.dumps(message, ensure_ascii=True, allow_nan=False) + "\n"
                except (TypeError, ValueError) as exc:
                    # A frame we cannot serialise is a payload bug (a NaN, a
                    # non-JSON value), not a dead host. Drop it and keep the
                    # channel alive — escaping here used to kill the writer
                    # thread outright, after which the panel silently stopped
                    # updating and nothing ever noticed.
                    dbg(f"companion frame dropped, not serialisable: {exc}")
                    continue
                self.writer.write(payload)
                self.writer.flush()
        except (OSError, UnicodeError, ValueError) as exc:
            # Broken pipe / closed stream: the host is genuinely gone, and
            # drain() turns this into app exit.
            dbg(f"companion write stopped: {type(exc).__name__}: {exc}")
            self._parent_gone.set()

    def _enqueue(self, message: dict) -> None:
        with self._condition:
            # A full outbox means the host is reading slowly — back-pressure,
            # not a dead host. Dropping the oldest frame keeps the channel live
            # and lets the newest state, which is what the panel renders, get
            # through; every frame carries a sequence number, so the host can
            # see the gap. Treating this as "parent gone" made the pet quit
            # itself whenever the host briefly fell behind.
            if len(self._outbox) >= MAX_OUTBOX:
                self._outbox.popleft()
                dbg("companion outbox full: oldest frame dropped")
            self._outbox.append(message)
            self._condition.notify()

    def _state_frame(self, kind: str, **fields) -> dict:
        self._sequence += 1
        return {"type": kind, "sequence": self._sequence, "snapshot": self.actions.snapshot(), **fields}

    def drain(self) -> None:
        """GUI-thread only. Closing the host's pipe also closes the pet."""
        if self._closed.is_set():
            return
        if self._parent_gone.is_set():
            self.on_shutdown()
            return
        for _ in range(16):
            try:
                message = self.commands.get_nowait()
            except queue.Empty:
                break
            if message.get("type") == "shutdown":
                self.on_shutdown()
                return
            if message.get("type") != "action":
                continue
            identifier = message.get("id")
            if not isinstance(identifier, str) or not 8 <= len(identifier) <= 100:
                continue
            if identifier in self._completed:
                self._enqueue(self._completed[identifier])
                continue
            try:
                result = {"ok": True, **self.actions.execute(message.get("action"))}
            except ValueError as exc:
                result = {"ok": False, "message": str(exc)}
            response = self._state_frame("result", id=identifier, result=result)
            self._completed[identifier] = response
            while len(self._completed) > 128:
                self._completed.popitem(last=False)
            self._enqueue(response)

    def publish(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if self._closed.is_set() or (not force and now - self._last_state_at < 0.5):
            return
        self._last_state_at = now
        with self._condition:
            self._latest_state = self._state_frame("state")
            self._condition.notify()

    def open_panel(self, section: str = "reminders") -> None:
        self._enqueue({"type": "navigate", "section": section})

    def close(self) -> None:
        self._closed.set()
        with self._condition:
            self._condition.notify_all()
        if self._writer_thread is not None:
            self._writer_thread.join(timeout=0.3)
