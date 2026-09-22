"""The controller ties storage and camera together behind one API surface."""

from __future__ import annotations

import time
from pathlib import Path

from . import __version__
from .camera import Camera, CameraError
from .config import Config
from .storage import Storage, StorageUnavailable

THERMAL_ZONE = Path("/sys/class/thermal/thermal_zone0/temp")


class Controller:
    def __init__(
        self, config: Config, storage: Storage | None = None, camera: Camera | None = None
    ):
        self.config = config
        self.storage = storage or Storage(
            config.storage_path, config.storage_require_mount, config.min_free_bytes
        )
        self.camera = camera or Camera(config.camera_url, config.snapshot_width)
        self.started = time.monotonic()
        self.last_camera_ok: int | None = None

    @staticmethod
    def cpu_temperature() -> float | None:
        try:
            return float(THERMAL_ZONE.read_text()) / 1000
        except (OSError, ValueError):
            return None

    def status(self) -> dict:
        return {
            "name": self.config.name,
            "version": __version__,
            "uptime_seconds": int(time.monotonic() - self.started),
            "temperature_c": self.cpu_temperature(),
            "storage": self.storage.status(),
            "camera": {
                "configured": self.camera.configured,
                "last_success_unix": self.last_camera_ok,
            },
            "recording_mode": "manual",
            "remote_access": False,
        }

    def snapshot(self) -> bytes:
        image = self.camera.snapshot()
        self.last_camera_ok = int(time.time())
        return image

    def record(self) -> dict:
        partial, final = self.storage.reserve_clip()
        try:
            self.camera.record(partial, self.config.clip_seconds)
            self.last_camera_ok = int(time.time())
            return self.storage.commit_clip(partial, final)
        finally:
            partial.unlink(missing_ok=True)

    def recordings(self) -> list[dict]:
        return self.storage.clips()


Unavailable = (StorageUnavailable, CameraError)
