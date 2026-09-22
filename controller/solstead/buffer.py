"""Continuous pre-event buffer: ffmpeg writes short MPEG-TS segments; we keep the last minute."""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .camera import input_args

SEGMENT_SUFFIX = ".ts"
NAME_FORMAT = "%Y%m%dT%H%M%S"

Spawner = Callable[..., subprocess.Popen]


@dataclass(frozen=True)
class Segment:
    path: Path
    start: float
    end: float

    def overlaps(self, start: float, end: float) -> bool:
        return self.start < end and self.end > start


def segment_start(path: Path) -> float | None:
    try:
        return datetime.strptime(path.stem, NAME_FORMAT).astimezone().timestamp()
    except ValueError:
        return None


def list_segments(folder: Path) -> list[Segment]:
    """All segments in start order, using the file name as start and mtime as end."""
    segments = []
    for path in folder.glob("*" + SEGMENT_SUFFIX):
        start = segment_start(path)
        if start is None:
            continue
        try:
            end = path.stat().st_mtime
        except OSError:
            continue
        segments.append(Segment(path, start, max(end, start)))
    segments.sort(key=lambda s: s.start)
    return segments


class SegmentBuffer:
    """Supervises one ffmpeg segmenter and answers "which segments cover this time span?"."""

    def __init__(self, camera_url: str | None, folder: Path, segment_seconds: int = 2,
                 keep_seconds: int = 60, spawner: Spawner = subprocess.Popen,
                 restart_delay: float = 5.0):  # fmt: skip
        self.camera_url = camera_url
        self.folder = Path(folder)
        self.segment_seconds = segment_seconds
        self.keep_seconds = keep_seconds
        self.spawner = spawner
        self.restart_delay = restart_delay
        self.process: subprocess.Popen | None = None
        self.started_at: float | None = None
        self.restarts = 0
        # Set by the recorder while an event is open or awaiting assembly: segments ending at or
        # after this time are kept regardless of keep_seconds.
        self.hold_from: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- ffmpeg -------------------------------------------------------------------------

    def command(self) -> list[str]:
        return input_args(self.camera_url) + [
            "-map", "0:v:0", "-an", "-c:v", "copy",
            "-f", "segment", "-segment_time", str(self.segment_seconds),
            "-segment_format", "mpegts", "-reset_timestamps", "1", "-strftime", "1",
            str(self.folder / (NAME_FORMAT + SEGMENT_SUFFIX)),
        ]  # fmt: skip

    def start(self) -> None:
        if not self.camera_url or self._thread:
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._thread = threading.Thread(target=self._supervise, name="segment-buffer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._terminate()
        if self._thread:
            self._thread.join(10)
            self._thread = None

    def _terminate(self) -> None:
        process = self.process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill()

    def _supervise(self) -> None:
        while not self._stop.is_set():
            try:
                # stderr may contain the camera URL with credentials; discard it.
                self.process = self.spawner(
                    self.command(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                self.started_at = time.time()
            except OSError:
                self.process = None
            while self.process and self.process.poll() is None and not self._stop.is_set():
                self.prune()
                self._stop.wait(1)
            if self._stop.is_set():
                break
            self.restarts += 1
            self._stop.wait(self.restart_delay)

    # ---- segments -----------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def segments(self) -> list[Segment]:
        return list_segments(self.folder) if self.folder.is_dir() else []

    def newest_start(self) -> float | None:
        segments = self.segments()
        return segments[-1].start if segments else None

    def newest_age(self, now: float | None = None) -> float | None:
        newest = self.newest_start()
        return None if newest is None else (now or time.time()) - newest

    def completed_between(self, start: float, end: float) -> list[Segment]:
        """Segments overlapping [start, end], excluding the one still being written."""
        segments = self.segments()
        if self.running and segments:
            segments = segments[:-1]
        return [s for s in segments if s.overlaps(start, end)]

    def typical_segment_seconds(self) -> float:
        """Observed segment length: cameras with long GOPs produce longer segments than asked."""
        segments = self.segments()
        if self.running and segments:
            segments = segments[:-1]
        durations = sorted(s.end - s.start for s in segments[-5:] if s.end > s.start)
        measured = durations[len(durations) // 2] if durations else 0.0
        return max(float(self.segment_seconds), measured)

    def covers(self, until: float) -> bool:
        """True once a segment has started at or after `until`, so earlier ones are complete."""
        newest = self.newest_start()
        return newest is not None and newest >= until

    def prune(self, now: float | None = None) -> int:
        now = now or time.time()
        removed = 0
        hold = self.hold_from
        for segment in self.segments():
            if segment.end < now - self.keep_seconds and (hold is None or segment.end < hold):
                try:
                    segment.path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed
