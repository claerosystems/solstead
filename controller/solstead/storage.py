"""Recording storage on the controller's dedicated SSD."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

from .sealed import is_sealed
from .sealing import SUFFIX as SEALED_SUFFIX

MANUAL_FOLDER = "manual"
CLIP_NAME = re.compile(r"^[0-9a-f-]{36}\.mp4(\.age)?$")


def ram_folder(name: str) -> Path:
    """A folder in RAM (/dev/shm) for video that must never reach the SSD unencrypted."""
    shm = Path("/dev/shm")
    return (shm if shm.is_dir() else Path(tempfile.gettempdir())) / name


class StorageUnavailable(Exception):
    """The recording volume is missing, read-only, or too full to record."""


class Storage:
    def __init__(self, root: Path, require_mount: bool = True, min_free_bytes: int = 0):
        self.root = Path(root)
        self.require_mount = require_mount
        self.min_free_bytes = min_free_bytes

    def check(self) -> None:
        # Never silently write recordings onto the OS card if the SSD disappears.
        if self.require_mount and not self.root.is_mount():
            raise StorageUnavailable("Recording SSD is not mounted")
        if not self.root.is_dir() or not os.access(self.root, os.W_OK):
            raise StorageUnavailable("Recording storage is not writable")

    def usage(self) -> dict[str, int]:
        self.check()
        usage = shutil.disk_usage(self.root)
        return {"total_bytes": usage.total, "free_bytes": usage.free, "used_bytes": usage.used}

    def status(self) -> dict:
        try:
            return {"ready": True, **self.usage()}
        except StorageUnavailable as error:
            return {"ready": False, "error": str(error)}

    def reserve_clip(self) -> Path:
        """The target path for a new manual clip, checking free space first.

        The sealed recordings module stores it there, or at <target>.age with keys enrolled.
        """
        if self.usage()["free_bytes"] < self.min_free_bytes:
            gib = self.min_free_bytes / 1024**3
            raise StorageUnavailable(f"Less than {gib:g} GiB free; recording paused")
        folder = self.root / MANUAL_FOLDER
        folder.mkdir(exist_ok=True)
        return folder / (str(uuid.uuid4()) + ".mp4")

    def clip_path(self, clip_id: str) -> Path | None:
        """The manual clip called clip_id (<uuid>.mp4 or <uuid>.mp4.age), if it exists."""
        if not CLIP_NAME.match(clip_id):
            return None
        path = self.root / MANUAL_FOLDER / clip_id
        return path if path.is_file() and not path.is_symlink() else None

    @staticmethod
    def describe(path: Path) -> dict:
        stat = path.stat()
        return {
            "id": path.name,
            "size_bytes": stat.st_size,
            "created_unix": int(stat.st_mtime),
            "encrypted": is_sealed(path),
        }

    def clips(self, limit: int = 200) -> list[dict]:
        self.check()
        folder = self.root / MANUAL_FOLDER
        if not folder.is_dir():
            return []
        paths = [
            p for pattern in ("*.mp4", "*.mp4" + SEALED_SUFFIX) for p in folder.glob(pattern)
            if p.is_file() and not p.is_symlink()
        ]  # fmt: skip
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return [self.describe(p) for p in paths[:limit]]

    def plain_clips(self) -> list[Path]:
        """Manual clips recorded before keys were enrolled, oldest first."""
        folder = self.root / MANUAL_FOLDER
        if not folder.is_dir():
            return []
        return sorted(p for p in folder.glob("*.mp4") if p.is_file() and not p.is_symlink())
