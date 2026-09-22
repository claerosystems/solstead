"""Health log: what happened on the controller, kept in memory and in a small file on the SSD."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("solstead")

LEVELS = ("info", "warning", "error")


class HealthLog:
    """Append-only record of incidents (camera lost, storage returned, event recorded, ...).

    Memory holds the last `capacity` entries; the file is rewritten to the same cap when it
    grows past twice that, so it can never fill the disk.
    """

    def __init__(
        self, path: Path | None, capacity: int = 500, guard: Callable[[], bool] = lambda: True
    ):
        # `guard` says whether the file may be written now; the controller passes its storage
        # check so a missing SSD never causes the log to land on the OS card.
        self.path = Path(path) if path else None
        self.capacity = capacity
        self.guard = guard
        self.entries: deque[dict] = deque(maxlen=capacity)
        self._lines = 0
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.is_file():
            return
        try:
            lines = self.path.read_text().splitlines()
        except OSError:
            return
        for line in lines[-self.capacity :]:
            try:
                self.entries.append(json.loads(line))
            except ValueError:
                continue
        self._lines = len(lines)

    def record(self, level: str, kind: str, message: str, **fields) -> dict:
        if level not in LEVELS:
            raise ValueError("level must be info, warning or error")
        entry = {
            "unix": int(time.time()),
            "level": level,
            "kind": kind,
            "message": message,
            **fields,
        }
        getattr(log, level)("%s: %s", kind, message)
        with self._lock:
            self.entries.append(entry)
            self._write(entry)
        return entry

    def _write(self, entry: dict) -> None:
        if not self.path or not self.guard():
            return
        try:
            if self._lines >= 2 * self.capacity:
                self.path.write_text("".join(json.dumps(e) + "\n" for e in self.entries))
                self._lines = len(self.entries)
            else:
                with self.path.open("a") as file:
                    file.write(json.dumps(entry) + "\n")
                self._lines += 1
        except OSError:
            pass  # the SSD may be gone; memory still has it and journald has the log line

    def recent(self, limit: int = 50, minimum_level: str = "info") -> list[dict]:
        threshold = LEVELS.index(minimum_level)
        with self._lock:
            chosen = [e for e in self.entries if LEVELS.index(e["level"]) >= threshold]
        return list(reversed(chosen))[:limit]

    def counts(self, since_unix: int) -> dict:
        with self._lock:
            recent = [e for e in self.entries if e["unix"] >= since_unix]
        return {level: sum(1 for e in recent if e["level"] == level) for level in LEVELS}
