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
from .network import Failover, Network, probe_hosts, read_network, read_routes
from .onvif import PullPointClient
from .recipients import RecipientStore
from .recipients import parse as parse_recipients
from .recognition import Recognizer
from .sdnotify import notify
from .sealing import SealingError, seal_file
from .storage import Storage, StorageUnavailable, ram_folder
from .telemetry import Telemetry
from .thermal import Thermal, read_thermal
from .timelapse import TimeLapse, TimeLapseSettings, load_settings, save_settings
from .uplink import AUTOMATIC, NetworkManagerError, Uplink, first_link
from .uplink import NAMES as UPLINK_NAMES
from .verifier import MotionGate, MotionVerifier
from .victron import Charger
from .zones import MotionSettings, load_motion_settings, save_motion_settings

LEGACY_BUFFER_FOLDER = "buffer"  # the pre-event buffer lived on the SSD before it moved to RAM
HEALTH_FILE = "health.jsonl"
TICK_SECONDS = 0.5
RETRY_SECONDS = 30
STORAGE_CHECK_SECONDS = 5
THERMAL_CHECK_SECONDS = 10
CHARGER_CHECK_SECONDS = 10
NETWORK_CHECK_SECONDS = 30
CHARGER_FRESH_SECONDS = 30  # an older reading is not added to telemetry
CHARGER_LOST_SECONDS = 300
# 12 V battery: warn below LOW, all clear again above RECOVERED (both lead-acid and LiFePO4 are
# well discharged at 12.0 V; the charger's own load disconnect is the real protection).
BATTERY_LOW_V = 12.0
BATTERY_RECOVERED_V = 12.6
CLOCK_SANITY_UNIX = 1767225600  # 2026-01-01: earlier than this and the clock is not set
# systemd-timesyncd creates this once NTP has set the clock since boot.
TIMESYNC_MARKER = Path("/run/systemd/timesync/synchronized")
log = logging.getLogger("solstead")


def clock_ok(now: float | None = None) -> bool:
    return (time.time() if now is None else now) >= CLOCK_SANITY_UNIX


def clock_synchronized(marker: Path = TIMESYNC_MARKER) -> bool:
    """NTP has set the clock since boot. After a power cut the Pi's clock resumes from the last
    saved time, which passes clock_ok() while still being wrong. Without timesyncd (development
    machines) fall back to the sanity check."""
    if marker.parent.is_dir():
        return marker.exists()
    return clock_ok()


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
        charger: Charger | None = None,
        uplink: Uplink | None = None,
    ):
        self.config = config
        self.storage = storage or Storage(
            config.storage_path, config.storage_require_mount, config.min_free_bytes
        )
        self.camera = camera or Camera(config.camera_url, config.snapshot_width)
        self.store = EventStore(config.storage_path, config.events_allocation_bytes)
        self.recipients = RecipientStore(config.recipients_path)
        ram = config.buffer_path or ram_folder("solstead-buffer")
        self.work_folder = ram / "work"  # plaintext clips being assembled for sealing
        self.buffer = buffer or SegmentBuffer(
            config.camera_url, ram / "segments", config.segment_seconds, config.buffer_seconds,
        )  # fmt: skip
        self.recorder = recorder or EventRecorder(
            self.buffer, self.store, config.event_pre_seconds, config.event_post_seconds,
            config.event_hold_seconds, config.event_max_seconds,
            work_folder=self.work_folder, recipients=self.recipients.keys,
        )  # fmt: skip
        self.motion_settings = load_motion_settings(config.motion_settings_path)
        self.gate = MotionGate(self.recorder, hold_seconds=config.event_hold_seconds,
                               lookback=config.event_pre_seconds)  # fmt: skip
        self.verifier = MotionVerifier(
            config.camera_motion_url or config.camera_live_url or config.camera_url,
            self.gate, self.motion_settings,
            recognizer=Recognizer(config.recognition_model_path),
        )  # fmt: skip
        if watcher is None and config.events_enabled:
            client = PullPointClient(
                config.camera_onvif_url, config.camera_user, config.camera_password,
                topics=config.motion_topics,
            )  # fmt: skip
            watcher = MotionWatcher(client, self.gate)
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
            config.storage_path, settings, self.frame_for_timelapse,
            recipients=self.recipients.keys,
        )  # fmt: skip
        self.health = HealthLog(config.storage_path / HEALTH_FILE, guard=self._storage_ok)
        self.telemetry = Telemetry(
            config.storage_path, config.telemetry_settings_path,
            guard=self._storage_ok, synced=clock_synchronized,
        )  # fmt: skip
        self.charger = charger or Charger(config.charger_settings_path)
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
        self._last_charger_check = 0.0
        self._last_network_check = 0.0
        self._charger_heard = False
        self._battery_low = False
        self._charger_error = 0
        self.thermal = Thermal(temperature_c=None)
        self.thermal_reader = read_thermal
        self.network = Network(uplink=None, interface=None)
        self.network_reader = read_network
        self.uplink = uplink or Uplink(config.uplink_settings_path)
        self.failover = Failover(self.health.record, hosts=probe_hosts(config.hub_address))
        self.routes_reader = read_routes
        self._failover_thread: threading.Thread | None = None
        self._seen: dict = {
            "buffer_restarts": 0,
            "watcher_errors": 0,
            "watcher_error": None,
            "timelapse_error": None,
            "recorder_error": None,
            "checker_error": None,
            "sealing_error": None,
            "clock_warned": False,
        }
        # Frames and manual clips from before enrolment still to seal; cleared when none are left.
        self._backlog = True

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
        self._failover_thread = threading.Thread(
            target=self._failover_loop, name="failover", daemon=True
        )
        self._failover_thread.start()
        self.charger.start()
        self._apply_uplink()

    def _apply_uplink(self) -> None:
        """Make sure the NetworkManager profiles match the owner's preferred link; a profile
        added since (a new Wi-Fi network, say) starts with NetworkManager's default metric."""
        try:
            if self.uplink.apply():
                name = UPLINK_NAMES[first_link(self.uplink.preferred)]
                self.health.record("info", "network", f"Route metrics reset to prefer {name}")
        except NetworkManagerError as error:
            self.health.record("warning", "network", f"Could not apply preferred link: {error}")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(5)
            self._thread = None
        if self._failover_thread:
            self._failover_thread.join(5)
            self._failover_thread = None
        self.charger.stop()
        self._stop_events()
        self.live.stop()

    def _loop(self) -> None:
        while not self._stop.is_set():
            notify("WATCHDOG=1")
            self._check_storage()
            self._check_thermal()
            self._check_charger()
            self._check_network()
            self.telemetry.tick()
            if self.config.events_enabled:
                self._ensure_events()
            self._poll_incidents()
            self.live.reap()
            if self.events_running:
                self.gate.tick()
                try:
                    for event in self.recorder.tick():
                        self.health.record(
                            "info", "events", "Event recorded",
                            event=event.id, seconds=event.duration_seconds,
                        )  # fmt: skip
                except OSError as error:
                    self.recorder.last_error = f"Storage error: {type(error).__name__}"
                self._seal_backlog()
            if self.camera.configured:
                try:
                    self.storage.check()
                    self.timelapse.tick()
                except StorageUnavailable as error:
                    self.timelapse.last_error = str(error)
            if self._backlog and self._storage_ok():
                self._seal_older_frames_and_clips()
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

    def _check_network(self) -> None:
        """Read the uplink and radio signal; log when the uplink changes."""
        if time.monotonic() - self._last_network_check < NETWORK_CHECK_SECONDS:
            return
        first = self._last_network_check == 0.0
        self._last_network_check = time.monotonic()
        before, now = self.network, self.network_reader()
        self.network = now
        if first or now.uplink == before.uplink:
            return
        names = {"wifi": "Wi-Fi", "cellular": "cellular", "ethernet": "Ethernet"}
        if now.uplink is None:
            self.health.record("warning", "network", "No internet connection")
        else:
            name = names.get(now.uplink, now.interface or now.uplink)
            self.health.record("info", "network", f"Connected over {name}", interface=now.interface)

    def network_status(self) -> dict:
        return {
            **self.network.to_dict(),
            **self.uplink.status(),
            "fallback": self.failover.fallback,
            "fallback_reason": self.failover.fallback_reason,
        }

    def set_network(self, changes: dict) -> dict:
        """The owner picked the link to use while it works; the other stays up as fallback."""
        preferred = changes.get("preferred")
        if self.uplink.set_preferred(preferred):
            if preferred == AUTOMATIC:
                message = "Owner set the preferred link to Automatic (Wi-Fi first)"
            else:
                message = f"Owner set {UPLINK_NAMES[preferred]} as the preferred link"
            self.health.record("info", "network", message)
        self.network = self.network_reader()
        return self.network_status()

    def _failover_loop(self) -> None:
        """Ping through the links on a thread of its own so a slow ping never delays recording."""
        while not self._stop.wait(NETWORK_CHECK_SECONDS):
            try:
                cellular = self.network.cellular
                connected = bool(cellular and cellular.state == "connected")
                self.failover.check(self.routes_reader(), connected)
            except Exception:  # keep checking; the next round may succeed
                log.exception("Failover check failed")

    def _check_thermal(self) -> None:
        """Read temperature and Pi power flags; log when they cross into or out of trouble."""
        if time.monotonic() - self._last_thermal_check < THERMAL_CHECK_SECONDS:
            return
        self._last_thermal_check = time.monotonic()
        before, now = self.thermal, self.thermal_reader()
        self.thermal = now
        temp = now.temperature_c
        flags = now.flags_available
        self.telemetry.add(
            cpu_c=temp,
            undervoltage=now.undervoltage_now if flags else None,
            throttled=now.throttled_now if flags else None,
        )
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

    def _check_charger(self) -> None:
        """Add the solar charger's latest reading to telemetry; log when it goes quiet, reports
        an error or the battery runs low, and when each clears."""
        if time.monotonic() - self._last_charger_check < CHARGER_CHECK_SECONDS:
            return
        self._last_charger_check = time.monotonic()
        if not self.charger.settings:
            self._charger_heard = False
            return
        reading = self.charger.fresh(CHARGER_FRESH_SECONDS)
        if reading is None:
            if self._charger_heard and not self.charger.fresh(CHARGER_LOST_SECONDS):
                self._charger_heard = False
                self.health.record("warning", "charger", "Solar charger not heard for 5 minutes")
            return
        if not self._charger_heard:
            self._charger_heard = True
            name = self.charger.settings.name
            self.health.record("info", "charger", f"Reading solar charger {name}")
        self.telemetry.add(
            battery_v=reading.battery_v, battery_a=reading.battery_a, solar_w=reading.solar_w,
            load_w=reading.load_w, charge_state=reading.state,
        )  # fmt: skip
        volts = reading.battery_v
        if volts is not None and volts < BATTERY_LOW_V and not self._battery_low:
            self._battery_low = True
            self.health.record(
                "warning", "battery", f"Battery low at {volts:.2f} V", battery_v=volts
            )
        elif volts is not None and volts >= BATTERY_RECOVERED_V and self._battery_low:
            self._battery_low = False
            self.health.record(
                "info", "battery", f"Battery recovered to {volts:.2f} V", battery_v=volts
            )
        if reading.error != self._charger_error:
            if reading.error:
                self.health.record(
                    "error", "charger", f"Solar charger reports error {reading.error}",
                    code=reading.error,
                )  # fmt: skip
            else:
                self.health.record("info", "charger", "Solar charger error cleared")
            self._charger_error = reading.error

    def charger_status(self) -> dict:
        return {**self.charger.status(), "seen": self.charger.seen()}

    def choose_charger(self, changes: dict) -> dict:
        settings = self.charger.choose(changes.get("address"), changes.get("key"))
        self.health.record("info", "charger", f"Solar charger set to {settings.name}")
        self._charger_heard = False
        self._last_charger_check = 0.0
        return self.charger_status()

    def forget_charger(self) -> dict:
        if self.charger.settings:
            name = self.charger.settings.name
            self.health.record("info", "charger", f"Solar charger {name} removed")
        self.charger.forget()
        return self.charger_status()

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
            self.verifier.start()
        self.events_running = True
        self.events_reason = None
        self.health.record("info", "events", "Event recording started")

    def _seal_backlog(self) -> None:
        """Seal one clip recorded before keys were enrolled, a tick at a time."""
        if not self.recipients.enrolled or self._seen["sealing_error"]:
            return
        try:
            event = self.store.seal_next_plain(self.recipients.keys())
        except (OSError, SealingError) as error:
            self._seen["sealing_error"] = str(error)
            self.health.record("error", "encryption", f"Could not encrypt an older clip: {error}")
            return
        if event:
            log.info("Encrypted earlier clip %s", event.id)

    def _seal_older_frames_and_clips(self) -> None:
        """Seal time-lapse frames and manual clips recorded before keys were enrolled."""
        if not self.recipients.enrolled or self._seen["sealing_error"]:
            return
        keys = self.recipients.keys()
        try:
            frames = self.timelapse.seal_plain(keys)
            clip = None if frames else self.storage.seal_next_plain_clip(keys)
        except (OSError, SealingError) as error:
            self._seen["sealing_error"] = str(error)
            self.health.record(
                "error",
                "encryption",
                f"Could not encrypt older time-lapse or test footage: {error}",
            )
            return
        if frames or clip:
            log.info("Encrypted %s earlier time-lapse frames, clip %s", frames, clip)
        else:
            self._backlog = False

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
        if self.verifier.last_error != seen["checker_error"]:
            seen["checker_error"] = self.verifier.last_error
            if self.verifier.last_error:
                self.health.record("warning", "events", self.verifier.last_error)
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
        self.verifier.stop()
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
            "motion_check": {**self.verifier.status(), **self.gate.status()},
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
            "telemetry": self.telemetry.status(),
            "charger": self.charger.status(),
            "network": self.network_status(),
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
        keys = self.recipients.keys()
        partial, final = self.storage.reserve_clip(sealed=bool(keys))
        plain = partial
        if keys:
            self.work_folder.mkdir(parents=True, exist_ok=True)
            plain = self.work_folder / ("manual-" + final.name.split(".")[0] + ".mp4.partial")
        try:
            recent = self._buffered(self.config.clip_seconds)
            if recent:
                try:
                    assemble(recent, plain, runner=self.camera.runner)
                except ClipError as error:
                    raise CameraError(str(error)) from None
            else:
                self.camera.record(plain, self.config.clip_seconds)
            self.last_camera_ok = int(time.time())
            if keys:
                seal_file(plain, partial, keys)
            return self.storage.commit_clip(partial, final)
        finally:
            partial.unlink(missing_ok=True)
            plain.unlink(missing_ok=True)

    def recordings(self) -> list[dict]:
        return self.storage.clips()

    def recording_file(self, recording_id: str) -> Path | None:
        return self.storage.clip_path(recording_id)

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

    def motion_status(self) -> dict:
        return {
            **self.motion_settings.to_dict(),
            "check": {**self.verifier.status(), **self.gate.status()},
        }

    def update_motion(self, changes: dict) -> dict:
        settings = MotionSettings.from_dict(changes, base=self.motion_settings)
        if self.config.motion_settings_path:
            save_motion_settings(self.config.motion_settings_path, settings)
        self.motion_settings = settings
        if self.events_running and self.watcher:
            self.verifier.update(settings)
        else:
            self.verifier.settings = settings
        areas = len(settings.zones)
        self.health.record(
            "info", "events",
            f"Motion settings changed: {areas or 'no'} area{'s' if areas != 1 else ''}, "
            f"shadows {'ignored' if settings.ignore_shadows else 'counted'}, "
            f"{settings.sensitivity} sensitivity, "
            + (f"recording only {' and '.join(settings.objects)}" if settings.recognize
               else "recognition off"),
        )  # fmt: skip
        return self.motion_status()

    def update_telemetry(self, changes: dict) -> dict:
        """The owner changed how long telemetry is kept; shortening deletes older days now."""
        if "retention_days" in changes:
            before = self.telemetry.retention_days
            self.telemetry.set_retention(changes["retention_days"])
            if self.telemetry.retention_days != before:
                self.health.record(
                    "info", "telemetry",
                    f"Telemetry kept for {self.telemetry.retention_days} days",
                )  # fmt: skip
        return self.telemetry.status()

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
        self._seen["sealing_error"] = None
        self._backlog = True
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
