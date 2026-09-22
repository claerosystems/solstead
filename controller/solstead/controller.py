"""The controller ties storage, camera and event recording together behind one API surface."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from . import __version__
from .buffer import SegmentBuffer
from .camera import Camera, CameraError
from .clips import ClipError, assemble
from .config import Config
from .events import Event, EventRecorder, EventStore
from .health import HealthLog
from .live import LiveStream
from .motion import MotionWatcher
from .onvif import PullPointClient
from .sdnotify import notify
from .storage import Storage, StorageUnavailable
from .timelapse import TimeLapse, TimeLapseSettings, load_settings, save_settings

THERMAL_ZONE = Path("/sys/class/thermal/thermal_zone0/temp")
BUFFER_FOLDER = "buffer"
HEALTH_FILE = "health.jsonl"
TICK_SECONDS = 0.5
RETRY_SECONDS = 30
STORAGE_CHECK_SECONDS = 5
CLOCK_SANITY_UNIX = 1767225600  # 2026-01-01: earlier than this and the clock is not set
log = logging.getLogger("solstead")


def clock_ok(now: float | None = None) -> bool:
    return (time.time() if now is None else now) >= CLOCK_SANITY_UNIX


class Controller:
    def __init__(
        self,
        config: Config,
        storage: Storage | None = None,
        camera: Camera | None = None,
        buffer: SegmentBuffer | None = None,
        watcher: MotionWatcher | None = None,
        recorder: EventRecorder | None = None,
        live: LiveStream | None = None,
        timelapse: TimeLapse | None = None,
    ):
        self.config = config
        self.storage = storage or Storage(
            config.storage_path, config.storage_require_mount, config.min_free_bytes
        )
        self.camera = camera or Camera(config.camera_url, config.snapshot_width)
        self.store = EventStore(config.storage_path, config.events_allocation_bytes)
        self.buffer = buffer or SegmentBuffer(
            config.camera_url, config.storage_path / BUFFER_FOLDER,
            config.segment_seconds, config.buffer_seconds,
        )  # fmt: skip
        self.recorder = recorder or EventRecorder(
            self.buffer, self.store, config.event_pre_seconds, config.event_post_seconds,
            config.event_hold_seconds, config.event_max_seconds,
        )  # fmt: skip
        if watcher is None and config.events_enabled:
            client = PullPointClient(
                config.camera_onvif_url, config.camera_user, config.camera_password,
                topics=config.motion_topics,
            )  # fmt: skip
            watcher = MotionWatcher(client, self.recorder)
        self.watcher = watcher
        self.live = live or LiveStream(
            config.camera_live_url, config.camera_url,
            Path(config.live_path) if config.live_path else None,
            config.live_segment_seconds, config.live_idle_seconds,
        )  # fmt: skip
        settings = (
            load_settings(config.settings_path) if config.settings_path else TimeLapseSettings()
        )
        self.timelapse = timelapse or TimeLapse(
            config.storage_path, settings, self.frame_for_timelapse
        )
        self.health = HealthLog(config.storage_path / HEALTH_FILE, guard=self._storage_ok)
        self.started = time.monotonic()
        self.last_camera_ok: int | None = None
        self.events_running = False
        self.events_reason: str | None = (
            None if config.events_enabled else "Camera events not configured"
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_attempt = 0.0
        self._last_storage_check = 0.0
        self._seen: dict = {
            "buffer_restarts": 0,
            "watcher_errors": 0,
            "watcher_error": None,
            "timelapse_error": None,
            "recorder_error": None,
            "clock_warned": False,
        }

    def _storage_ok(self) -> bool:
        try:
            self.storage.check()
            return True
        except StorageUnavailable:
            return False

    # ---- lifecycle -----------------------------------------------------------------------

    def start(self) -> None:
        if self._thread:
            return
        self.health.record("info", "controller", f"Started, software {__version__}")
        if not clock_ok():
            self.health.record(
                "warning", "clock", "System clock is not set; timestamps are wrong until it is"
            )
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="controller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(5)
            self._thread = None
        self._stop_events()
        self.live.stop()

    def _loop(self) -> None:
        while not self._stop.is_set():
            notify("WATCHDOG=1")
            self._check_storage()
            if self.config.events_enabled:
                self._ensure_events()
            self._poll_incidents()
            self.live.reap()
            if self.events_running:
                try:
                    for event in self.recorder.tick():
                        self.health.record(
                            "info", "events", "Event recorded",
                            event=event.id, seconds=event.duration_seconds,
                        )  # fmt: skip
                except OSError as error:
                    self.recorder.last_error = f"Storage error: {type(error).__name__}"
            if self.camera.configured:
                try:
                    self.storage.check()
                    self.timelapse.tick()
                except StorageUnavailable as error:
                    self.timelapse.last_error = str(error)
            self._stop.wait(TICK_SECONDS)

    def _check_storage(self) -> None:
        """Stop recording when the SSD disappears so nothing lands on the OS card; log both ways."""
        if time.monotonic() - self._last_storage_check < STORAGE_CHECK_SECONDS:
            return
        self._last_storage_check = time.monotonic()
        try:
            self.storage.check()
            available = True
            reason = None
        except StorageUnavailable as error:
            available, reason = False, str(error)
        if not available and self.events_running:
            self.health.record("error", "storage", f"Recording storage lost: {reason}")
            self._stop_events()
            self.events_reason = reason
        elif (
            available
            and self.events_reason
            and "storage" in self.events_reason.lower()
            or (available and self.events_reason and "mounted" in self.events_reason.lower())
        ):
            self.health.record("info", "storage", "Recording storage is back")
            self.events_reason = None
            self._last_attempt = 0.0  # retry immediately

    def _ensure_events(self) -> None:
        """Start (or restart after the SSD returns) the buffer and motion watcher."""
        if self.events_running or time.monotonic() - self._last_attempt < RETRY_SECONDS:
            return
        self._last_attempt = time.monotonic()
        try:
            self.storage.check()
        except StorageUnavailable as error:
            if self.events_reason != str(error):
                self.health.record("error", "storage", f"Cannot record: {error}")
            self.events_reason = str(error)
            return
        self.buffer.start()
        if self.watcher:
            self.watcher.start()
        self.events_running = True
        self.events_reason = None
        self.health.record("info", "events", "Event recording started")

    def _poll_incidents(self) -> None:
        """Turn counters and error fields from the workers into health-log entries."""
        seen = self._seen
        if self.buffer.restarts != seen["buffer_restarts"]:
            seen["buffer_restarts"] = self.buffer.restarts
            self.health.record("warning", "camera", "Camera stream ended; restarting the buffer",
                               restarts=self.buffer.restarts)  # fmt: skip
        if self.watcher:
            if self.watcher.last_error != seen["watcher_error"]:
                seen["watcher_error"] = self.watcher.last_error
                if self.watcher.last_error:
                    self.health.record("warning", "camera", self.watcher.last_error)
                elif seen["watcher_errors"]:
                    self.health.record("info", "camera", "Camera events are flowing again")
            seen["watcher_errors"] = self.watcher.errors
        if self.recorder.last_error != seen["recorder_error"]:
            seen["recorder_error"] = self.recorder.last_error
            if self.recorder.last_error:
                self.health.record("error", "events", self.recorder.last_error)
        if self.timelapse.last_error != seen["timelapse_error"]:
            seen["timelapse_error"] = self.timelapse.last_error
            if self.timelapse.last_error:
                self.health.record("warning", "timelapse", self.timelapse.last_error)
        if not seen["clock_warned"] and not clock_ok():
            seen["clock_warned"] = True

    def _stop_events(self) -> None:
        if self.watcher:
            self.watcher.stop()
        self.buffer.stop()
        if self.events_running:
            self.health.record("info", "events", "Event recording stopped")
        self.events_running = False

    # ---- status --------------------------------------------------------------------------

    @staticmethod
    def cpu_temperature() -> float | None:
        try:
            return float(THERMAL_ZONE.read_text()) / 1000
        except (OSError, ValueError):
            return None

    def events_status(self) -> dict:
        status = {
            "enabled": self.config.events_enabled,
            "running": self.events_running,
            "reason": self.events_reason,
            "buffer_running": self.buffer.running,
            "buffer_age_seconds": None,
            "motion_active": self.recorder.active,
            "camera_subscribed": bool(self.watcher and self.watcher.subscribed),
            "camera_error": self.watcher.last_error if self.watcher else None,
            "camera_clock_skew_seconds": self.watcher.last_skew_seconds if self.watcher else None,
            "camera_clock_synced_unix": self.watcher.clock_synced_unix if self.watcher else None,
            "last_event_unix": self.recorder.last_event_unix,
            "last_error": self.recorder.last_error,
            "count": 0,
            "used_bytes": 0,
            "allocation_bytes": self.config.events_allocation_bytes,
        }
        try:
            age = self.buffer.newest_age()
            status["buffer_age_seconds"] = None if age is None else round(age, 1)
            status["count"] = len(self.store.events(limit=100000))
            status["used_bytes"] = self.store.used_bytes()
        except OSError:
            pass
        return status

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
            "events": self.events_status(),
            "live": self.live.status(),
            "timelapse": self.timelapse.status(),
            "health": {
                "clock_ok": clock_ok(),
                "last_day": self.health.counts(int(time.time()) - 86400),
                "latest": self.health.recent(3, "warning"),
            },
            "recording_mode": "events" if self.config.events_enabled else "manual",
            "remote_access": False,
        }

    # ---- actions -------------------------------------------------------------------------

    def _buffered(self, seconds: float) -> list:
        """Completed buffer segments for the last `seconds`, or [] when the buffer is idle."""
        if not (self.events_running and self.buffer.running):
            return []
        now = time.time()
        return self.buffer.completed_between(now - seconds, now)

    def snapshot(self) -> bytes:
        recent = self._buffered(3 * self.config.segment_seconds + 2)
        if recent:
            image = self.camera.snapshot_from(recent[-1].path)
        else:
            image = self.camera.snapshot()
        self.last_camera_ok = int(time.time())
        return image

    def record(self) -> dict:
        partial, final = self.storage.reserve_clip()
        try:
            recent = self._buffered(self.config.clip_seconds)
            if recent:
                try:
                    assemble(recent, partial, runner=self.camera.runner)
                except ClipError as error:
                    raise CameraError(str(error)) from None
            else:
                self.camera.record(partial, self.config.clip_seconds)
            self.last_camera_ok = int(time.time())
            return self.storage.commit_clip(partial, final)
        finally:
            partial.unlink(missing_ok=True)

    def recordings(self) -> list[dict]:
        return self.storage.clips()

    def frame_for_timelapse(self, width: int) -> bytes:
        recent = self._buffered(3 * self.config.segment_seconds + 2)
        if recent:
            return self.camera.snapshot_from(recent[-1].path, width)
        return self.camera.snapshot(width)

    def update_timelapse(self, changes: dict) -> dict:
        settings = TimeLapseSettings.from_dict(changes, base=self.timelapse.settings)
        self.timelapse.settings = settings
        if self.config.settings_path:
            save_settings(self.config.settings_path, settings)
        return self.timelapse.status()

    def start_live(self, quality: str = "low") -> dict:
        """Start live view (or keep it alive) and wait until it can be played."""
        if not self.live.available:
            raise CameraError("Camera is not configured")
        was_running = self.live.running
        self.live.start(quality)
        if not self.live.wait_ready():
            self.live.stop()
            self.health.record("warning", "live", f"Live view ({quality}) could not start")
            raise CameraError("Live view could not start; check the camera stream")
        if not was_running:
            self.health.record("info", "live", f"Live view started ({quality})")
        return {**self.live.status(), "playlist": "/v1/live/index.m3u8"}

    def stop_live(self) -> None:
        self.live.stop()

    def live_file(self, name: str) -> Path | None:
        self.live.touch()
        return self.live.file(name)

    def events(self) -> list[dict]:
        self.storage.check()
        return [event.to_dict() for event in self.store.events()]

    def event_clip(self, event_id: str) -> tuple[Event, Path] | None:
        self.storage.check()
        event = self.store.load(event_id)
        if event is None:
            return None
        path = self.store.clip_path(event.id)
        return (event, path) if path.is_file() else None


Unavailable = (StorageUnavailable, CameraError)
