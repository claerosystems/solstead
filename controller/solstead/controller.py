"""The controller ties storage, camera and event recording together behind one API surface."""

from __future__ import annotations

import logging
import shutil
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
from .recipients import RecipientStore
from .recipients import parse as parse_recipients
from .sdnotify import notify
from .sealed import SealedRecordings
from .storage import Storage, StorageUnavailable, ram_folder
from .thermal import Thermal, read_thermal
from .timelapse import TimeLapse, TimeLapseSettings, load_settings, save_settings

LEGACY_BUFFER_FOLDER = "buffer"  # the pre-event buffer lived on the SSD before it moved to RAM
HEALTH_FILE = "health.jsonl"
TICK_SECONDS = 0.5
RETRY_SECONDS = 30
STORAGE_CHECK_SECONDS = 5
THERMAL_CHECK_SECONDS = 10
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
        self.recipients = RecipientStore(config.recipients_path)
        ram = config.buffer_path or ram_folder("solstead-buffer")
        # Every recording file goes through here; plaintext for sealing is staged in ram/work.
        self.sealed = SealedRecordings(self.recipients.keys, ram / "work")
        self.store = EventStore(config.storage_path, config.events_allocation_bytes, self.sealed)
        self.buffer = buffer or SegmentBuffer(
            config.camera_url, ram / "segments", config.segment_seconds, config.buffer_seconds,
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
            config.storage_path, settings, self.frame_for_timelapse, sealed=self.sealed,
        )  # fmt: skip
        # Older plain footage is sealed events first; a lambda so a replaced timelapse counts.
        self.sealed.add_source(self.store.plain_clips)
        self.sealed.add_source(lambda: self.timelapse.plain_frames())
        self.sealed.add_source(self.storage.plain_clips)
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
        self._last_thermal_check = 0.0
        self.thermal = Thermal(temperature_c=None)
        self.thermal_reader = read_thermal
        self._seen: dict = {
            "buffer_restarts": 0,
            "watcher_errors": 0,
            "watcher_error": None,
            "timelapse_error": None,
            "recorder_error": None,
            "sealing_error": None,  # the sealed recordings module's last_error
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
            self._check_thermal()
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
            if self.sealed.pending and self._storage_ok():
                self.sealed.seal_older()
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

    def _check_thermal(self) -> None:
        """Read temperature and Pi power flags; log when they cross into or out of trouble."""
        if time.monotonic() - self._last_thermal_check < THERMAL_CHECK_SECONDS:
            return
        self._last_thermal_check = time.monotonic()
        before, now = self.thermal, self.thermal_reader()
        self.thermal = now
        temp = now.temperature_c
        if now.undervoltage_now and not before.undervoltage_now:
            self.health.record("error", "power", "Under-voltage: the Pi's supply is sagging")
        elif before.undervoltage_now and not now.undervoltage_now:
            self.health.record("info", "power", "Supply voltage back to normal")
        if now.throttled_now and not before.throttled_now:
            self.health.record("warning", "thermal", "CPU is being throttled", temperature_c=temp)
        elif before.throttled_now and not now.throttled_now:
            self.health.record("info", "thermal", "CPU throttling ended", temperature_c=temp)
        if temp is not None and before.temperature_c is not None:
            if temp >= now.throttle_c > before.temperature_c:
                self.health.record(
                    "error", "thermal", f"CPU at {temp} C, throttle limit", temperature_c=temp
                )
            elif temp >= now.warning_c > before.temperature_c:
                self.health.record(
                    "warning", "thermal", f"CPU at {temp} C, getting hot", temperature_c=temp
                )
            elif before.temperature_c >= now.warning_c > temp:
                self.health.record("info", "thermal", f"CPU back to {temp} C", temperature_c=temp)

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
        legacy = self.config.storage_path / LEGACY_BUFFER_FOLDER
        if legacy.is_dir() and legacy != self.buffer.folder:
            shutil.rmtree(legacy, ignore_errors=True)
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
        if self.sealed.last_error != seen["sealing_error"]:
            seen["sealing_error"] = self.sealed.last_error
            if self.sealed.last_error:
                self.health.record(
                    "error",
                    "encryption",
                    f"Could not encrypt older footage: {seen['sealing_error']}",
                )
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
            "temperature_c": self.thermal.temperature_c,
            "thermal": self.thermal.to_dict(),
            "storage": self.storage.status(),
            "camera": {
                "configured": self.camera.configured,
                "last_success_unix": self.last_camera_ok,
            },
            "events": self.events_status(),
            "live": self.live.status(),
            "timelapse": self.timelapse.status(),
            "encryption": self.encryption_status(),
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

    def snapshot(self, width: int | None = None) -> bytes:
        """A JPEG frame; width 0 means full camera resolution, None the configured preview size."""
        recent = self._buffered(3 * self.config.segment_seconds + 2)
        if recent:
            image = self.camera.snapshot_from(recent[-1].path, width)
        else:
            image = self.camera.snapshot(width)
        self.last_camera_ok = int(time.time())
        return image

    def record(self) -> dict:
        """A manual test clip. With keys enrolled it is assembled in RAM and only sealed on disk."""
        target = self.storage.reserve_clip()
        recent = self._buffered(self.config.clip_seconds)

        def produce(path: Path) -> None:
            if not recent:
                self.camera.record(path, self.config.clip_seconds)
                return
            try:
                assemble(recent, path, runner=self.camera.runner)
            except ClipError as error:
                raise CameraError(str(error)) from None

        clip = self.sealed.write(target, produce)
        self.last_camera_ok = int(time.time())
        return self.storage.describe(clip)

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

    def events(self, limit: int = 200) -> list[dict]:
        self.storage.check()
        return [event.to_dict() for event in self.store.events(limit)]

    def delete_event(self, event_id: str) -> bool:
        self.storage.check()
        event = self.store.load(event_id)
        if not self.store.remove(event_id):
            return False
        self.health.record("info", "events", "Event deleted by the owner", event=event_id,
                           encrypted=bool(event and event.encrypted))  # fmt: skip
        return True

    def event_clip(self, event_id: str) -> tuple[Event, Path] | None:
        self.storage.check()
        event = self.store.load(event_id)
        if event is None:
            return None
        path = self.store.clip_path(event)
        return (event, path) if path.is_file() else None

    # ---- encryption ----------------------------------------------------------------------

    def event_header(self, event_id: str) -> bytes | None:
        self.storage.check()
        return self.store.header(event_id)

    def replace_event_header(self, event_id: str, header: bytes) -> dict | None:
        """A key holder added a recipient to an older clip (restoring access on a new phone)."""
        self.storage.check()
        event = self.store.replace_header(event_id, header)
        return event.to_dict() if event else None

    def recording_header(self, clip_id: str) -> bytes | None:
        self.storage.check()
        path = self.storage.clip_path(clip_id)
        return self.sealed.header(path) if path else None

    def replace_recording_header(self, clip_id: str, header: bytes) -> dict | None:
        """A key holder added a recipient to an older manual clip (restoring access)."""
        self.storage.check()
        path = self.storage.clip_path(clip_id)
        if path is None or self.sealed.replace_header(path, header) is None:
            return None
        return self.storage.describe(path)

    def timelapse_headers(self, day: str) -> list[tuple[str, bytes]]:
        self.storage.check()
        return self.timelapse.headers(day)

    def replace_timelapse_headers(self, day: str, headers: dict[str, bytes]) -> int:
        """A key holder added a recipient to a day's older frames (restoring access)."""
        self.storage.check()
        return self.timelapse.replace_headers(day, headers)

    def encryption_status(self) -> dict:
        status = self.recipients.status()
        try:
            events = self.store.events(limit=100000)
            status["unencrypted_events"] = sum(not e.encrypted for e in events)
        except OSError:
            status["unencrypted_events"] = None
        try:
            days = self.timelapse.days()
            status["unencrypted_frames"] = sum(d["frames"] - d["encrypted_frames"] for d in days)
        except OSError:
            status["unencrypted_frames"] = None
        return status

    def enroll(self, payload: object) -> dict:
        """Replace the keys new recordings are sealed to. Existing sealed files are unchanged."""
        recipients = parse_recipients(payload)
        self.recipients.replace(recipients)
        self.sealed.keys_changed()
        self.timelapse.clear_exports()
        self.health.record(
            "info", "encryption", "Recording keys enrolled",
            devices=sum(r.role == "device" for r in recipients),
        )  # fmt: skip
        return self.encryption_status()

    def disable_encryption(self) -> dict:
        """The owner turned encryption off: new recordings are stored unencrypted."""
        if self.recipients.enrolled:
            self.recipients.clear()
            self.health.record(
                "warning", "encryption", "Encryption turned off; new recordings are not encrypted"
            )
        return self.encryption_status()


Unavailable = (StorageUnavailable, CameraError)
