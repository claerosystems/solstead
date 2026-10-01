"""Sealed recordings: every write, background seal and header swap of recording files (ADR 0003).

A recording is sealed exactly when its file name ends in .age; nothing else records it. Event
clips, time-lapse frames and manual clips each keep their own folders and names and hand this
module a target path such as events/<id>.mp4. With recording keys enrolled the file lands as
<target>.age and its plaintext exists only in the RAM work folder; without keys it lands as
<target>.

Expensive work (sealing, copying a file under a new header) goes to a uniquely named partial
file outside the lock. Only the commit, which re-checks that the source is still there, and
remove() take the lock, so a recording the owner deleted or a kind pruned never comes back.
"""

from __future__ import annotations

import glob
import os
import threading
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path

from .sealing import SUFFIX, SealingError, read_header, seal_bytes, seal_file, write_rewrapped

SEAL_BUDGET_BYTES = 8 * 1024 * 1024  # older footage sealed per seal_older() call, at least one


def is_sealed(path: Path) -> bool:
    return path.name.endswith(SUFFIX)


def plain_form(path: Path) -> Path:
    return path.with_name(path.name.removesuffix(SUFFIX))


def sealed_form(path: Path) -> Path:
    return path if is_sealed(path) else path.with_name(path.name + SUFFIX)


def _partial(final: Path) -> Path:
    return final.with_name(f"{final.name}.{uuid.uuid4().hex[:8]}.partial")


def _unlink(path: Path) -> int:
    try:
        size = path.stat().st_size
        path.unlink()
        return size
    except FileNotFoundError:
        return 0


def _identity(stat: os.stat_result) -> tuple[int, int, int]:
    return stat.st_ino, stat.st_size, stat.st_mtime_ns


class SealedRecordings:
    def __init__(
        self,
        recipients: Callable[[], list[bytes]] = list,
        work_folder: Path | None = None,
    ):
        """recipients returns the enrolled public keys; work_folder is in RAM (/dev/shm).

        Without a work folder nothing can be sealed: plaintext is never staged on the SSD.
        """
        self.recipients = recipients
        self.work_folder = Path(work_folder) if work_folder else None
        self.pending = True  # older plain footage may be left to seal
        self.last_error: str | None = None
        self._sources: list[Callable[[], Iterable[Path]]] = []
        self._lock = threading.Lock()

    def _keys(self) -> list[bytes]:
        keys = self.recipients()
        if keys and self.work_folder is None:
            raise SealingError("No RAM work folder to seal recordings in")
        return keys

    # ---- writing new recordings ------------------------------------------------------------

    def write(self, target: Path, produce: Callable[[Path], None]) -> Path:
        """Have produce(path) write a recording's plaintext, and store it at target[.age].

        Returns the final path. On failure nothing is left behind, in RAM or on the SSD.
        """
        keys = self._keys()
        final = sealed_form(target) if keys else target
        final.parent.mkdir(parents=True, exist_ok=True)
        partial = _partial(final)
        plain = partial
        if keys:
            self.work_folder.mkdir(parents=True, exist_ok=True)
            plain = self.work_folder / f"{uuid.uuid4().hex[:8]}-{target.name}.partial"
        try:
            produce(plain)
            if keys:
                seal_file(plain, partial, keys)
            else:
                with partial.open("rb") as file:
                    os.fsync(file.fileno())
            partial.rename(final)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        finally:
            if plain != partial:
                plain.unlink(missing_ok=True)
        return final

    def write_bytes(self, target: Path, data: bytes) -> Path:
        """Store a small recording (a time-lapse frame) sealed in memory; returns the final path."""
        keys = self._keys()
        final = sealed_form(target) if keys else target
        final.parent.mkdir(parents=True, exist_ok=True)
        payload = seal_bytes(data, keys) if keys else data
        partial = _partial(final)
        try:
            with partial.open("wb") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            partial.rename(final)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        return final

    # ---- older footage -------------------------------------------------------------------

    def add_source(self, plain_files: Callable[[], Iterable[Path]]) -> None:
        """Register a kind of footage: plain_files lists its unsealed files, oldest first."""
        self._sources.append(plain_files)

    def keys_changed(self) -> None:
        """Keys were enrolled: look for older plain footage again, and retry after an error."""
        self.last_error = None
        self.pending = True

    def seal_older(self, budget_bytes: int = SEAL_BUDGET_BYTES) -> int:
        """Seal footage recorded before keys were enrolled, a budget's worth per call.

        Returns how many files were sealed. Stops at the first error until keys_changed(),
        leaving it in last_error. The plain files are deleted, but an SSD does not guarantee
        their old blocks are erased, so footage from before enrolment may remain recoverable.
        """
        if not self.pending or self.last_error:
            return 0
        try:
            keys = self._keys()
            if not keys:
                return 0
            count = done = 0
            for source in self._sources:
                for path in list(source()):
                    if done >= budget_bytes:
                        return count
                    size = self._seal_in_place(path, keys)
                    if size is not None:
                        count += 1
                        done += size
        except (OSError, SealingError) as error:
            self.last_error = str(error)
            return 0
        self.pending = False
        return count

    def _seal_in_place(self, path: Path, keys: list[bytes]) -> int | None:
        """Replace plain path with path.age, keeping its time; None if it went meanwhile."""
        sealed = sealed_form(path)
        if sealed.exists():  # a crash came between committing the sealed copy and unlinking
            _unlink(path)
            return 0
        partial = _partial(sealed)
        try:
            stat = path.stat()
            seal_file(path, partial, keys)
            os.utime(partial, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        except FileNotFoundError:
            partial.unlink(missing_ok=True)
            if path.exists():
                raise
            return None
        with self._lock:
            if not path.exists():  # removed while it was being sealed
                partial.unlink(missing_ok=True)
                return None
            partial.rename(sealed)
            path.unlink()
        return sealed.stat().st_size

    # ---- restoring access ------------------------------------------------------------------

    def header(self, path: Path) -> bytes | None:
        """A sealed recording's age header, for a key holder to re-wrap; None if not sealed."""
        if not is_sealed(path):
            return None
        try:
            with path.open("rb") as file:
                return read_header(file)[0]
        except FileNotFoundError:
            return None

    def replace_header(self, path: Path, new_header: bytes) -> int | None:
        """Swap a sealed recording's header for a re-wrapped one that keeps every existing key.

        Returns the new size, or None if the recording is not sealed or went meanwhile.
        """
        if not is_sealed(path):
            return None
        for _ in range(3):
            partial = _partial(path)
            try:
                before = _identity(path.stat())
                write_rewrapped(path, new_header, partial)
            except FileNotFoundError:
                partial.unlink(missing_ok=True)
                if path.exists():
                    raise
                return None
            with self._lock:
                try:
                    now = _identity(path.stat())
                except FileNotFoundError:
                    now = None
                if now == before:
                    partial.rename(path)
                    return path.stat().st_size
                partial.unlink(missing_ok=True)
                if now is None:
                    return None
            # Another header replacement committed first; re-wrap on top of it.
        raise SealingError("The recording kept changing; try again")

    # ---- removing ------------------------------------------------------------------------

    def remove(self, path: Path) -> int:
        """Delete a recording in any form (plain, sealed, partial); returns the bytes freed."""
        forms = [plain_form(path), sealed_form(path)]
        with self._lock:
            freed = sum(_unlink(form) for form in forms)
            for form in forms:
                for partial in form.parent.glob(glob.escape(form.name) + ".*partial"):
                    freed += _unlink(partial)
        return freed
