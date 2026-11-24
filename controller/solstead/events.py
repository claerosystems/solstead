"""Event clips: the size-capped events allocation and the recorder state machine."""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .buffer import SegmentBuffer
from .clips import ClipError, assemble
from .sealed import SealedRecordings, is_sealed
from .sealing import SUFFIX, SealingError

EVENTS_FOLDER = "events"


@dataclass(frozen=True)
class Event:
    id: str
    started_unix: int  # first detected activity
    ended_unix: int  # last detected activity
    clip_from_unix: int  # start of footage including pre-roll
    clip_to_unix: int  # end of footage including post-roll
    size_bytes: int  # of the clip file as stored; read from it, not kept in <id>.json
    trigger: str
    created_unix: int
    encrypted: bool = False  # the clip is <id>.mp4.age; read from the file name

    RECORDED = ("size_bytes", "encrypted")  # derived from the clip, never written to the JSON

    @property
    def duration_seconds(self) -> int:
        return self.clip_to_unix - self.clip_from_unix

    def to_dict(self) -> dict:
        return {**asdict(self), "duration_seconds": self.duration_seconds}


class EventStore:
    """events/<id>.mp4[.age] plus <id>.json, oldest deleted when the allocation is exceeded.

    <id>.json holds what was known when the event was recorded. Whether the clip is sealed, and
    its size, come from the clip file itself, so sealing or re-wrapping never touches the JSON.
    """

    def __init__(self, root: Path, allocation_bytes: int, sealed: SealedRecordings | None = None):
        self.folder = Path(root) / EVENTS_FOLDER
        self.allocation_bytes = allocation_bytes
        self.sealed = sealed or SealedRecordings()
        # The API thread (owner deletes) against the loop (pruning).
        self._lock = threading.Lock()

    def plain_path(self, event_id: str) -> Path:
        return self.folder / (event_id + ".mp4")

    def sealed_path(self, event_id: str) -> Path:
        return self.folder / (event_id + ".mp4" + SUFFIX)

    def clip_path(self, event: Event) -> Path:
        return self.sealed_path(event.id) if event.encrypted else self.plain_path(event.id)

    def _json_path(self, event_id: str) -> Path:
        return self.folder / (event_id + ".json")

    def new_id(self, started: float) -> str:
        stamp = datetime.fromtimestamp(started).strftime("%Y%m%dT%H%M%S")
        return f"{stamp}-{uuid.uuid4().hex[:8]}"

    def write_clip(self, event_id: str, produce: Callable[[Path], None]) -> Path:
        """Store a new clip through the sealed recordings module; returns where it landed."""
        return self.sealed.write(self.plain_path(event_id), produce)

    def save(self, event: Event) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        recorded = {k: v for k, v in asdict(event).items() if k not in Event.RECORDED}
        self._json_path(event.id).write_text(json.dumps(recorded))
        self.prune()

    def load(self, event_id: str) -> Event | None:
        path = self._json_path(event_id)
        if "/" in event_id or ".." in event_id or not path.is_file():
            return None
        try:
            recorded = json.loads(path.read_text())
            for key in Event.RECORDED:  # written by earlier versions; the file decides now
                recorded.pop(key, None)
            sealed = self.sealed_path(event_id)
            # A crash while sealing can leave both forms; the sealed one is complete.
            clip = sealed if sealed.is_file() else self.plain_path(event_id)
            size = clip.stat().st_size if clip.is_file() else 0
            return Event(**recorded, size_bytes=size, encrypted=clip == sealed)
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

    def plain_clips(self) -> list[Path]:
        """Clips recorded before keys were enrolled, oldest first (ids start with the time)."""
        if not self.folder.is_dir():
            return []
        return sorted(p for p in self.folder.glob("*.mp4") if p.is_file() and not p.is_symlink())

    def used_bytes(self) -> int:
        if not self.folder.is_dir():
            return 0
        return sum(p.stat().st_size for p in self.folder.iterdir() if p.is_file())

    def delete(self, event_id: str) -> int:
        """Remove an event's clip in any form and its record; returns the bytes freed."""
        return self.sealed.remove(self.plain_path(event_id)) + _unlink(self._json_path(event_id))

    def remove(self, event_id: str) -> bool:
        """The owner deleted an event: its clip and record go. False if there was no such event."""
        with self._lock:
            if self.load(event_id) is None:
                return False
            self.delete(event_id)
            return True

    def header(self, event_id: str) -> bytes | None:
        """The age header of a sealed clip, for a key holder to re-wrap."""
        event = self.load(event_id)
        return self.sealed.header(self.clip_path(event)) if event else None

    def replace_header(self, event_id: str, new_header: bytes) -> Event | None:
        """Swap a sealed clip's header for a re-wrapped one that keeps every existing key."""
        event = self.load(event_id)
        if event is None or self.sealed.replace_header(self.clip_path(event), new_header) is None:
            return None
        return self.load(event_id)

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
            used -= self.delete(oldest.id)
            deleted.append(oldest.id)
        return deleted


def _unlink(path: Path) -> int:
    try:
        size = path.stat().st_size
        path.unlink()
        return size
    except FileNotFoundError:
        return 0


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
    ):
        self.buffer = buffer
        self.store = store
        self.pre_seconds = pre_seconds
        self.post_seconds = post_seconds
        self.hold_seconds = hold_seconds
        self.max_seconds = max_seconds
        self.assembler = assembler
        self.clock = clock
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
        try:
            # With keys enrolled the clip is assembled in RAM and only the sealed file is stored.
            clip = self.store.write_clip(event_id, lambda path: self.assembler(segments, path))
        except (ClipError, OSError, SealingError) as error:
            self.last_error = str(error) if isinstance(error, ClipError) else "Saving clip failed"
            return None
        event = Event(
            id=event_id, started_unix=int(started), ended_unix=int(last),
            clip_from_unix=int(segments[0].start), clip_to_unix=int(segments[-1].end),
            size_bytes=clip.stat().st_size, trigger=trigger, created_unix=int(now),
            encrypted=is_sealed(clip),
        )  # fmt: skip
        self.store.save(event)
        self.last_event_unix = event.created_unix
        self.last_error = None
        return event
