"""Recording storage on the controller's dedicated SSD."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

MANUAL_FOLDER = "manual"


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

    def reserve_clip(self) -> tuple[Path, Path]:
        """Return (partial, final) paths for a new manual clip, checking free space first."""
        if self.usage()["free_bytes"] < self.min_free_bytes:
            gib = self.min_free_bytes / 1024**3
            raise StorageUnavailable(f"Less than {gib:g} GiB free; recording paused")
        folder = self.root / MANUAL_FOLDER
        folder.mkdir(exist_ok=True)
        final = folder / (str(uuid.uuid4()) + ".mp4")
        return final.with_name(final.name + ".partial"), final

    @staticmethod
    def commit_clip(partial: Path, final: Path) -> dict:
        with partial.open("rb") as file:
            os.fsync(file.fileno())
        partial.rename(final)
        return Storage.describe(final)

    @staticmethod
    def describe(path: Path) -> dict:
        stat = path.stat()
        return {"id": path.name, "size_bytes": stat.st_size, "created_unix": int(stat.st_mtime)}

    def clips(self, limit: int = 200) -> list[dict]:
        self.check()
        folder = self.root / MANUAL_FOLDER
        if not folder.is_dir():
            return []
        paths = [p for p in folder.glob("*.mp4") if p.is_file() and not p.is_symlink()]
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return [self.describe(p) for p in paths[:limit]]
