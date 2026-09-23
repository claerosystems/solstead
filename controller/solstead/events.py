"""Event clips: the size-capped events allocation and the recorder state machine."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

from .buffer import SegmentBuffer
from .clips import ClipError, assemble
from .sealing import MAX_HEADER, SealingError, read_header, seal_file
from .sealing import SUFFIX as SEALED_SUFFIX

EVENTS_FOLDER = "events"


@dataclass(frozen=True)
class Event:
    id: str
    started_unix: int  # first detected activity
    ended_unix: int  # last detected activity
    clip_from_unix: int  # start of footage including pre-roll
    clip_to_unix: int  # end of footage including post-roll
    size_bytes: int
    trigger: str
    created_unix: int
    encrypted: bool = False  # the clip is <id>.mp4.age, sealed to the enrolled keys

    @property
    def duration_seconds(self) -> int:
        return self.clip_to_unix - self.clip_from_unix

    def to_dict(self) -> dict:
        return {**asdict(self), "duration_seconds": self.duration_seconds}


class EventStore:
    """events/<id>.mp4[.age] plus <id>.json, oldest deleted when the allocation is exceeded."""

    def __init__(self, root: Path, allocation_bytes: int):
        self.folder = Path(root) / EVENTS_FOLDER
        self.allocation_bytes = allocation_bytes
        self._lock = threading.Lock()  # header replacement (API thread) against pruning

    def plain_path(self, event_id: str) -> Path:
        return self.folder / (event_id + ".mp4")

    def sealed_path(self, event_id: str) -> Path:
        return self.folder / (event_id + ".mp4" + SEALED_SUFFIX)

    def clip_path(self, event: Event) -> Path:
        return self.sealed_path(event.id) if event.encrypted else self.plain_path(event.id)

    def new_id(self, started: float) -> str:
        stamp = datetime.fromtimestamp(started).strftime("%Y%m%dT%H%M%S")
        return f"{stamp}-{uuid.uuid4().hex[:8]}"

    def save(self, event: Event) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / (event.id + ".json")).write_text(json.dumps(asdict(event)))
        self.prune()

    def load(self, event_id: str) -> Event | None:
        path = self.folder / (event_id + ".json")
        if "/" in event_id or ".." in event_id or not path.is_file():
            return None
        try:
            return Event(**json.loads(path.read_text()))
        except (OSError, ValueError, TypeError):
            return None

    def events(self, limit: int = 200) -> list[Event]:
        if not self.folder.is_dir():
            return []
        found = []
        for path in self.folder.glob("*.json"):
            event = self.load(path.stem)
            if event and self.clip_path(event).is_file():
                found.append(event)
        found.sort(key=lambda e: e.started_unix, reverse=True)
        return found[:limit]

    def used_bytes(self) -> int:
        if not self.folder.is_dir():
            return 0
        return sum(p.stat().st_size for p in self.folder.iterdir() if p.is_file())

    def delete(self, event_id: str) -> None:
        for suffix in (".mp4", ".mp4" + SEALED_SUFFIX, ".json"):
            (self.folder / (event_id + suffix)).unlink(missing_ok=True)

    def header(self, event_id: str) -> bytes | None:
        """The age header of a sealed clip, for a key holder to re-wrap."""
        event = self.load(event_id)
        if event is None or not event.encrypted or not self.clip_path(event).is_file():
            return None
        with self.clip_path(event).open("rb") as file:
            return read_header(file)[0]

    def replace_header(self, event_id: str, new_header: bytes) -> Event | None:
        """Swap a sealed clip's header for a re-wrapped one that keeps every existing stanza.

        The controller cannot check the header MAC (it has no key), but it can refuse anything
        that would drop a recipient or touch the payload.
        """
        from io import BytesIO

        with self._lock:
            event = self.load(event_id)
            if event is None or not event.encrypted or not self.clip_path(event).is_file():
                return None
            if len(new_header) > MAX_HEADER:
                raise SealingError("Header is too long")
            parsed, stanzas = read_header(BytesIO(new_header))
            if parsed != new_header:
                raise SealingError("Body must be exactly one age header")
            path = self.clip_path(event)
            partial = path.with_name(path.name + ".partial")
            with path.open("rb") as old:
                _, old_stanzas = read_header(old)
                missing = [s for s in old_stanzas if s not in stanzas]
                if missing:
                    raise SealingError("The new header must keep every existing key")
                try:
                    with partial.open("wb") as new:
                        new.write(new_header)
                        while chunk := old.read(1024 * 1024):
                            new.write(chunk)
                        new.flush()
                        os.fsync(new.fileno())
                except BaseException:
                    partial.unlink(missing_ok=True)
                    raise
            partial.rename(path)
            updated = replace(event, size_bytes=path.stat().st_size)
            (self.folder / (event.id + ".json")).write_text(json.dumps(asdict(updated)))
            return updated

    def seal_next_plain(self, recipients: list[bytes]) -> Event | None:
        """Seal one clip recorded before keys were enrolled; None when there are none left.

        The plaintext file is deleted afterwards, but an SSD does not guarantee the old blocks
        are erased, so footage recorded before enrolment may remain recoverable from the drive.
        """
        for event in self.events(limit=100000):
            if event.encrypted:
                continue
            plain, sealed = self.plain_path(event.id), self.sealed_path(event.id)
            partial = sealed.with_name(sealed.name + ".partial")
            seal_file(plain, partial, recipients)
            partial.rename(sealed)
            done = replace(event, encrypted=True, size_bytes=sealed.stat().st_size)
            (self.folder / (event.id + ".json")).write_text(json.dumps(asdict(done)))
            plain.unlink()
            return done
        return None

    def prune(self) -> list[str]:
        """Delete oldest events until the allocation fits. Returns deleted ids."""
        with self._lock:
            return self._prune()

    def _prune(self) -> list[str]:
        deleted = []
        events = self.events(limit=100000)
        used = self.used_bytes()
        while used > self.allocation_bytes and events:
            oldest = events.pop()
            for path in (self.clip_path(oldest), self.folder / (oldest.id + ".json")):
                used -= path.stat().st_size if path.exists() else 0
            self.delete(oldest.id)
            deleted.append(oldest.id)
        return deleted


class EventRecorder:
    """Turns motion on/off into one clip per burst of activity.

    Activity opens an event; each further activity report extends it. The event ends
    `post_seconds` after the last activity, and the clip then covers
    [first activity - pre_seconds, last activity + post_seconds]. Events longer than
    `max_seconds` are cut and a new one starts, so a stuck detector cannot fill the SSD
    with one clip. Drive it with motion() and tick(now).
    """

    def __init__(
        self,
        buffer: SegmentBuffer,
        store: EventStore,
        pre_seconds: int = 10,
        post_seconds: int = 30,
        hold_seconds: int = 10,
        max_seconds: int = 300,
        assembler: Callable = assemble,
        clock: Callable[[], float] = time.time,
        work_folder: Path | None = None,
        recipients: Callable[[], list[bytes]] = list,
    ):
        self.buffer = buffer
        self.store = store
        self.pre_seconds = pre_seconds
        self.post_seconds = post_seconds
        self.hold_seconds = hold_seconds
        self.max_seconds = max_seconds
        self.assembler = assembler
        self.clock = clock
        # With recipients enrolled, clips are assembled in work_folder (RAM) and only the sealed
        # file reaches the SSD.
        self.work_folder = work_folder
        self.recipients = recipients
        self.started: float | None = None
        self.last_activity: float | None = None
        self.motion_ended: float | None = None
        self.trigger = ""
        self.last_event_unix: int | None = None
        self.last_error: str | None = None
        self.pending: list[tuple[float, float, str]] = []  # (started, last_activity, trigger)

    @property
    def active(self) -> bool:
        return self.started is not None

    def motion(self, active: bool, at: float | None = None, trigger: str = "motion") -> None:
        at = self.clock() if at is None else at
        if active:
            if self.started is None:
                self.started, self.trigger = at, trigger
            self.last_activity = max(at, self.last_activity or at)
            self.motion_ended = None
        elif self.started is not None:
            self.motion_ended = max(at, self.last_activity or at)
        self._update_hold()

    def _update_hold(self) -> None:
        """Tell the buffer how far back footage must be kept for open and pending events."""
        starts = [started for started, _, _ in self.pending]
        if self.started is not None:
            starts.append(self.started)
        self.buffer.hold_from = min(starts) - self.pre_seconds if starts else None

    def activity_end(self) -> float | None:
        """When the current burst of activity is considered over."""
        if self.started is None or self.last_activity is None:
            return None
        if self.motion_ended is not None:
            return self.motion_ended
        # Cameras that only ever report "on" get a sensible end via hold_seconds.
        return self.last_activity + self.hold_seconds

    def _close(self, last_activity: float) -> None:
        if self.started is None:
            return
        self.pending.append((self.started, last_activity, self.trigger))
        self.started = self.last_activity = self.motion_ended = None
        self.trigger = ""

    def tick(self, now: float | None = None) -> list[Event]:
        """Close finished events and assemble clips whose footage is complete."""
        now = self.clock() if now is None else now
        end = self.activity_end()
        if end is not None:
            # Activity within the post-roll extends the same event, so only close once the
            # post-roll has fully elapsed with nothing new.
            if now >= end + self.post_seconds:
                self._close(end)
            elif now - self.started >= self.max_seconds:
                self._close(now)
        finished = []
        still_pending = []
        for started, last, trigger in self.pending:
            clip_to = last + self.post_seconds
            # Wait until the segment containing clip_to has been closed; if the buffer stalled,
            # give up a little later and take whatever footage exists.
            grace = 2 * self.buffer.typical_segment_seconds() + 5
            if self.buffer.covers(clip_to) or now >= clip_to + grace:
                event = self._assemble(started, last, trigger, now)
                if event:
                    finished.append(event)
            else:
                still_pending.append((started, last, trigger))
        self.pending = still_pending
        self._update_hold()
        return finished

    def _assemble(self, started: float, last: float, trigger: str, now: float) -> Event | None:
        clip_from = started - self.pre_seconds
        clip_to = last + self.post_seconds
        segments = self.buffer.completed_between(clip_from, clip_to)
        event_id = self.store.new_id(started)
        keys = self.recipients()
        output = self.store.sealed_path(event_id) if keys else self.store.plain_path(event_id)
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_name(output.name + ".partial")
        plain = partial
        if keys:
            work = self.work_folder or output.parent
            work.mkdir(parents=True, exist_ok=True)
            plain = work / (event_id + ".mp4.partial")
        try:
            self.assembler(segments, plain)
            if keys:
                seal_file(plain, partial, keys)
            partial.rename(output)
        except (ClipError, OSError) as error:
            self.last_error = str(error) if isinstance(error, ClipError) else "Saving clip failed"
            partial.unlink(missing_ok=True)
            return None
        finally:
            if plain != partial:
                plain.unlink(missing_ok=True)
        event = Event(
            id=event_id, started_unix=int(started), ended_unix=int(last),
            clip_from_unix=int(segments[0].start), clip_to_unix=int(segments[-1].end),
            size_bytes=output.stat().st_size, trigger=trigger, created_unix=int(now),
            encrypted=bool(keys),
        )  # fmt: skip
        self.store.save(event)
        self.last_event_unix = event.created_unix
        self.last_error = None
        return event
