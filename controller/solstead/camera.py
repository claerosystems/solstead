"""RTSP camera access through ffmpeg."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

Runner = Callable[..., subprocess.CompletedProcess]


class CameraError(Exception):
    """The camera is not configured, busy, unreachable, or produced no output."""


class Camera:
    def __init__(
        self,
        url: str | None,
        snapshot_width: int = 960,
        timeout: int = 35,
        runner: Runner = subprocess.run,
    ):
        self.url = url
        self.snapshot_width = snapshot_width
        self.timeout = timeout
        self.runner = runner
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def _base_args(self) -> list[str]:
        return [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-rtsp_transport", "tcp", "-i", self.url,
        ]  # fmt: skip

    def _run(self, args: list[str], timeout: int) -> subprocess.CompletedProcess:
        if not self.configured:
            raise CameraError("Camera is not configured")
        if not self._lock.acquire(blocking=False):
            raise CameraError("Camera capture is busy; try again shortly")
        try:
            try:
                result = self.runner(args, capture_output=True, timeout=timeout)
            except (OSError, subprocess.TimeoutExpired):
                raise CameraError("Camera unavailable or capture timed out") from None
            if result.returncode:
                # ffmpeg stderr may contain camera credentials; never return or log it.
                raise CameraError("Camera capture failed; check camera connection and credentials")
            return result
        finally:
            self._lock.release()

    def snapshot(self) -> bytes:
        args = self._base_args() + [
            "-frames:v", "1", "-an", "-vf", f"scale={self.snapshot_width}:-2",
            "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1",
        ]  # fmt: skip
        result = self._run(args, self.timeout)
        if not result.stdout:
            raise CameraError("Camera returned an empty image")
        return result.stdout

    def record(self, output: Path, seconds: int) -> None:
        args = self._base_args() + [
            "-t", str(seconds), "-map", "0:v:0", "-an", "-c:v", "copy",
            "-movflags", "+faststart", "-f", "mp4", str(output),
        ]  # fmt: skip
        self._run(args, self.timeout + seconds)
