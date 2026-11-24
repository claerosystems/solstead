"""Telemetry: one-minute measurements of the system's condition, kept for an owner-chosen period.

Sources report readings every few seconds (today the Pi's own temperature and supply flags; later
temperature probes and a solar charger). Each minute becomes one sample: the mean of each reading
heard in it, the lowest value where a dip matters, the last value of a state, and whether a flag
was raised at all. Samples go to one JSON-lines file per UTC day on the recording SSD.

The Pi 4 has no real-time clock: after a power cut its clock restarts at the last saved time until
NTP answers. Readings are therefore stamped with the monotonic clock and only turned into samples
once the system clock is synchronised, so an outage never writes history at the wrong time.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

FOLDER = "telemetry"
RETENTION_CHOICES = (7, 30, 90, 365)
DEFAULT_RETENTION_DAYS = 30
RANGES = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
# History bucket sizes, smallest first; each divides a day, so no bucket spans two files.
BUCKETS = (60, 300, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400)
MAX_POINTS = 400
HOLD_READINGS = 4096  # readings waiting for the clock: about 11 hours at one every 10 s
HOLD_SAMPLES = 24 * 60  # minutes waiting for the SSD

# Sample field -> (reading it summarises, rule). Rules: mean, min, last, any.
FIELDS: dict[str, tuple[str, str]] = {
    "cpu_c": ("cpu_c", "mean"),
    "undervoltage": ("undervoltage", "any"),
    "throttled": ("throttled", "any"),
    # From the solar charger.
    "battery_v": ("battery_v", "mean"),
    "battery_v_min": ("battery_v", "min"),
    "battery_a": ("battery_a", "mean"),
    "solar_w": ("solar_w", "mean"),
    "load_w": ("load_w", "mean"),
    "charge_state": ("charge_state", "last"),
}
ENERGY = {"solar_w": "solar_wh", "load_w": "load_wh"}  # power field -> daily energy


class TelemetryError(Exception):
    pass


def summarise(minute: int, readings: list[dict]) -> dict:
    """One minute's readings as a sample; fields no reading carried are left out."""
    sample: dict = {"unix": minute * 60, "n": len(readings)}
    for field, (source, rule) in FIELDS.items():
        values = [r[source] for r in readings if r.get(source) is not None]
        if not values:
            continue
        if rule == "mean":
            sample[field] = round(sum(values) / len(values), 3)
        elif rule == "min":
            sample[field] = min(values)
        elif rule == "last":
            sample[field] = values[-1]
        else:
            sample[field] = any(values)
    return sample


def aggregate(samples: list[dict], bucket: int) -> list[dict]:
    """Samples as history points of `bucket` seconds. A mean field gives its average plus the
    lowest and highest minute (_low, _high); a flag gives the number of minutes it was raised."""
    groups: dict[int, list[dict]] = {}
    for sample in samples:
        groups.setdefault(sample["unix"] // bucket * bucket, []).append(sample)
    points = []
    for start in sorted(groups):
        group = groups[start]
        point: dict = {"unix": start, "minutes": len(group)}
        for field, (_, rule) in FIELDS.items():
            values = [s[field] for s in group if s.get(field) is not None]
            if not values:
                continue
            if rule == "mean":
                point[field] = round(sum(values) / len(values), 2)
                point[field + "_low"] = min(values)
                point[field + "_high"] = max(values)
            elif rule == "min":
                point[field] = min(values)
            elif rule == "last":
                point[field] = values[-1]
            else:
                point[field] = sum(1 for v in values if v)
            if field in ENERGY:  # each minute's mean power for one minute
                point[ENERGY[field]] = round(sum(values) / 60, 2)
        points.append(point)
    return points


def daily_energy(hours: list[dict]) -> list[dict]:
    """Watt-hours per local calendar day, summed from hourly points. Minutes without a charger
    reading count as nothing, so a day with gaps reads low rather than invented."""
    days: dict[str, dict] = {}
    for point in hours:
        totals = {t: point[t] for t in ENERGY.values() if point.get(t) is not None}
        if not totals:
            continue
        name = datetime.fromtimestamp(point["unix"]).date().isoformat()
        day = days.setdefault(name, {"date": name})
        for total, value in totals.items():
            day[total] = day.get(total, 0) + value
    for day in days.values():
        for total in ENERGY.values():
            if total in day:
                day[total] = round(day[total], 1)
    return [days[name] for name in sorted(days)]


def bucket_for(span_seconds: int) -> int:
    for bucket in BUCKETS:
        if span_seconds / bucket <= MAX_POINTS:
            return bucket
    return BUCKETS[-1]


def utc_day(unix: float) -> date:
    return datetime.fromtimestamp(unix, UTC).date()


class Telemetry:
    def __init__(
        self,
        root: Path,
        settings_path: Path | None = None,
        guard: Callable[[], bool] = lambda: True,
        synced: Callable[[], bool] = lambda: True,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        # `guard` says whether the SSD may be written now; `synced` whether the clock is right.
        self.folder = Path(root) / FOLDER
        self.settings_path = Path(settings_path) if settings_path else None
        self.guard = guard
        self.synced = synced
        self.clock = clock
        self.monotonic = monotonic
        self.retention_days = self._load_retention()
        self.last_sample_unix: int | None = None
        self._readings: deque[tuple[float, dict]] = deque(maxlen=HOLD_READINGS)
        self._pending: deque[dict] = deque(maxlen=HOLD_SAMPLES)
        self._cache: dict[tuple[date, int], list[dict]] = {}
        self._pruned: date | None = None
        self._lock = threading.Lock()

    # ---- settings --------------------------------------------------------------------------

    def _load_retention(self) -> int:
        try:
            days = json.loads(self.settings_path.read_text())["retention_days"]
        except (AttributeError, OSError, ValueError, KeyError, TypeError):
            return DEFAULT_RETENTION_DAYS
        return days if days in RETENTION_CHOICES else DEFAULT_RETENTION_DAYS

    def set_retention(self, days: object) -> None:
        """Keep `days` of history; days older than that are deleted now."""
        if isinstance(days, bool) or days not in RETENTION_CHOICES:
            choices = ", ".join(str(c) for c in RETENTION_CHOICES)
            raise TelemetryError(f"retention_days must be one of {choices}")
        self.retention_days = days
        if self.settings_path:
            self.settings_path.write_text(json.dumps({"retention_days": days}) + "\n")
        if self.guard():
            self.prune()

    # ---- recording -------------------------------------------------------------------------

    def add(self, **values) -> None:
        """One reading from a source, e.g. add(cpu_c=51.2, throttled=False)."""
        with self._lock:
            self._readings.append((self.monotonic(), values))

    def tick(self) -> None:
        """Turn finished minutes into samples and write them once the clock and SSD allow."""
        if not self.synced():
            return
        offset = self.clock() - self.monotonic()
        current = int(self.clock() // 60)
        with self._lock:
            minutes: dict[int, list[dict]] = {}
            while self._readings:
                at, values = self._readings[0]
                minute = int((at + offset) // 60)
                if minute >= current:
                    break
                minutes.setdefault(minute, []).append(values)
                self._readings.popleft()
            for minute in sorted(minutes):
                self._pending.append(summarise(minute, minutes[minute]))
            if not self._pending or not self.guard():
                return
            self._write_pending()
        if self._pruned != utc_day(self.clock()):
            self.prune()

    def _write_pending(self) -> None:
        by_day: dict[date, list[dict]] = {}
        for sample in self._pending:
            by_day.setdefault(utc_day(sample["unix"]), []).append(sample)
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            for day, samples in by_day.items():
                with self._path(day).open("a") as file:
                    file.write("".join(json.dumps(s) + "\n" for s in samples))
                for key in [k for k in self._cache if k[0] == day]:
                    del self._cache[key]
        except OSError:
            return  # the SSD may have gone; samples stay pending until it is back
        self.last_sample_unix = self._pending[-1]["unix"]
        self._pending.clear()

    def prune(self) -> int:
        """Delete day files older than the retention period; returns how many."""
        today = utc_day(self.clock())
        oldest_kept = today - timedelta(days=self.retention_days - 1)
        removed = 0
        with self._lock:
            for day in self._stored_days():
                if day < oldest_kept:
                    try:
                        self._path(day).unlink()
                        removed += 1
                    except OSError:
                        pass
            self._cache.clear()
        self._pruned = today
        return removed

    def _path(self, day: date) -> Path:
        return self.folder / f"{day.isoformat()}.jsonl"

    def _stored_days(self) -> list[date]:
        days = []
        try:
            for path in self.folder.glob("*.jsonl"):
                try:
                    days.append(date.fromisoformat(path.stem))
                except ValueError:
                    continue
        except OSError:
            pass
        return sorted(days)

    # ---- reading ---------------------------------------------------------------------------

    def _read_day(self, day: date) -> list[dict]:
        samples = []
        try:
            lines = self._path(day).read_text().splitlines()
        except OSError:
            return []
        for line in lines:
            try:
                sample = json.loads(line)
            except ValueError:
                continue  # a line cut short by a power cut
            if isinstance(sample, dict) and isinstance(sample.get("unix"), int):
                samples.append(sample)
        return samples

    def _day_points(self, day: date, bucket: int, today: date) -> list[dict]:
        with self._lock:
            pending = [s for s in self._pending if utc_day(s["unix"]) == day]
            cached = self._cache.get((day, bucket))
        if cached is not None and not pending:
            return cached
        points = aggregate(self._read_day(day) + pending, bucket)
        if day < today and not pending:
            with self._lock:
                self._cache[(day, bucket)] = points  # a finished day no longer changes
        return points

    def history(self, range_name: str = "24h") -> dict:
        """Summarised points for the range, at most about MAX_POINTS of them."""
        retention = self.retention_days * 86400
        if range_name == "all":
            span = retention
        elif range_name in RANGES:
            span = min(RANGES[range_name], retention)
        else:
            raise TelemetryError("range must be 24h, 7d, 30d or all")
        now = self.clock()
        bucket = bucket_for(span)
        start = int(now - span) // bucket * bucket
        today = utc_day(now)
        points, hours = [], []
        day = utc_day(start)
        while day <= today:
            points.extend(
                p for p in self._day_points(day, bucket, today) if start <= p["unix"] <= now
            )
            hours.extend(p for p in self._day_points(day, 3600, today) if start <= p["unix"] <= now)
            day += timedelta(days=1)
        return {
            "range": range_name,
            "since_unix": start,
            "until_unix": int(now),
            "bucket_seconds": bucket,
            "retention_days": self.retention_days,
            "points": points,
            "energy": daily_energy(hours),
        }

    def status(self) -> dict:
        with self._lock:
            held = len(self._readings)
            pending = len(self._pending)
        return {
            "retention_days": self.retention_days,
            "retention_choices": list(RETENTION_CHOICES),
            "clock_synchronized": self.synced(),
            "held_readings": held,
            "pending_minutes": pending,
            "last_sample_unix": self.last_sample_unix,
            "fields": list(FIELDS),
        }
