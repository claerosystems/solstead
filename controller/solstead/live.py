"""On-demand live view: ffmpeg copies a camera stream into short HLS segments held in RAM."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from .camera import input_args
from .storage import ram_folder

Spawner = Callable[..., subprocess.Popen]

PLAYLIST = "index.m3u8"
SEGMENT_NAME = re.compile(r"^(index\.m3u8|init\.mp4|seg_\d{5}\.m4s)$")
QUALITIES = ("low", "high")


def default_live_dir() -> Path:
    return ram_folder("solstead-live")


class LiveStream:
    """One ffmpeg HLS writer at a time. touch() marks a viewer; reap() stops it when idle."""

    def __init__(
        self,
        low_url: str | None,
        high_url: str | None,
        folder: Path | None = None,
        segment_seconds: int = 1,
        idle_seconds: int = 20,
        spawner: Spawner = subprocess.Popen,
    ):
        self.urls = {"low": low_url or high_url, "high": high_url or low_url}
        self.folder = Path(folder) if folder else default_live_dir()
        self.segment_seconds = segment_seconds
        self.idle_seconds = idle_seconds
        self.spawner = spawner
        self.process: subprocess.Popen | None = None
        self.quality: str | None = None
        self.started_at: float | None = None
        self.last_touch: float = 0.0
        self.viewers = 0  # requests in the last idle window, informational

    @property
    def available(self) -> bool:
        return bool(self.urls["low"])

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def command(self, quality: str) -> list[str]:
        return input_args(self.urls[quality]) + [
            "-map", "0:v:0", "-an", "-c:v", "copy",
            "-f", "hls", "-hls_time", str(self.segment_seconds), "-hls_list_size", "6",
            "-hls_flags", "delete_segments+independent_segments+omit_endlist+temp_file",
            "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init.mp4",
            "-hls_segment_filename", str(self.folder / "seg_%05d.m4s"),
            str(self.folder / PLAYLIST),
        ]  # fmt: skip

    def start(self, quality: str = "low", now: float | None = None) -> None:
        """Start (or switch quality of) the stream. Idempotent when already running."""
        if quality not in QUALITIES:
            raise ValueError("quality must be low or high")
        if not self.available:
            raise RuntimeError("Camera is not configured")
        now = time.time() if now is None else now
        self.last_touch = now
        if self.running and self.quality == quality:
            return
        self.stop()
        if self.folder.exists():
            shutil.rmtree(self.folder, ignore_errors=True)
        self.folder.mkdir(parents=True, exist_ok=True)
        # stderr may contain the camera URL with credentials; discard it.
        self.process = self.spawner(
            self.command(quality), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self.quality = quality
        self.started_at = now

    def touch(self, now: float | None = None) -> None:
        self.last_touch = time.time() if now is None else now

    def ready(self) -> bool:
        """True once the playlist lists at least two segments and the init file exists."""
        playlist = self.folder / PLAYLIST
        if not self.running or not playlist.is_file() or not (self.folder / "init.mp4").is_file():
            return False
        try:
            return playlist.read_text().count("seg_") >= 2
        except OSError:
            return False

    def wait_ready(self, timeout: float = 12.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.ready():
                return True
            if not self.running:
                return False
            time.sleep(0.2)
        return self.ready()

    def file(self, name: str) -> Path | None:
        """Path of a playlist or segment for serving, or None if unknown or not allowed."""
        if not SEGMENT_NAME.match(name):
            return None
        path = self.folder / name
        return path if path.is_file() else None

    def reap(self, now: float | None = None) -> bool:
        """Stop the stream when idle or when ffmpeg has died. Returns True if it was stopped."""
        now = time.time() if now is None else now
        if self.process is None:
            return False
        if self.running and now - self.last_touch < self.idle_seconds:
            return False
        self.stop()
        return True

    def stop(self) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill()
        self.process = None
        self.quality = None
        self.started_at = None
        shutil.rmtree(self.folder, ignore_errors=True)

    def status(self) -> dict:
        return {
            "available": self.available,
            "active": self.running,
            "quality": self.quality,
            "started_unix": int(self.started_at) if self.started_at else None,
            "ready": self.ready(),
        }
