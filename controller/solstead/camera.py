"""RTSP camera access through ffmpeg."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

Runner = Callable[..., subprocess.CompletedProcess]


class CameraError(Exception):
    """The camera is not configured, busy, unreachable, or produced no output."""


def input_args(url: str) -> list[str]:
    """ffmpeg input options for a camera stream URL (RTSP over TCP, or plain HTTP/MPEG-TS)."""
    args = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"]
    if url.startswith(("rtsp://", "rtsps://")):
        args += ["-rtsp_transport", "tcp"]
    return args + ["-i", url]


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
        if not self.configured:
            raise CameraError("Camera is not configured")
        return input_args(self.url)

    def _run(
        self, args: list[str], timeout: int, need_camera: bool = True
    ) -> subprocess.CompletedProcess:
        if need_camera and not self.configured:
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

    def _frame_args(self, width: int | None) -> list[str]:
        """Output options for one JPEG frame; width 0 means the camera's full resolution."""
        width = self.snapshot_width if width is None else width
        scale = ["-vf", f"scale={width}:-2"] if width > 0 else []
        return [
            "-frames:v",
            "1",
            "-an",
            *scale,
            "-q:v",
            "2",
            "-f",
            "image2pipe",
            "-c:v",
            "mjpeg",
            "pipe:1",
        ]

    def snapshot_from(self, path: Path, width: int | None = None) -> bytes:
        """First frame of a buffered segment, so the camera is not opened a second time."""
        args = [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path),
            *self._frame_args(width),
        ]  # fmt: skip
        result = self._run(args, self.timeout, need_camera=False)
        if not result.stdout:
            raise CameraError("Buffered segment produced no image")
        return result.stdout

    def snapshot(self, width: int | None = None) -> bytes:
        args = self._base_args() + self._frame_args(width)
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
