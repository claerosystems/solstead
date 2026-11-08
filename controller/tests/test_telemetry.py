import json
from datetime import UTC, datetime, timedelta

import pytest

from solstead.controller import clock_synchronized
from solstead.telemetry import (
    Telemetry,
    TelemetryError,
    aggregate,
    bucket_for,
    summarise,
)

NOON = datetime(2026, 9, 25, 12, 0, tzinfo=UTC).timestamp()


class Clocks:
    """A wall clock that can be wrong and a monotonic clock that is always right."""

    def __init__(self, wall: float, synced: bool = True):
        self.mono = 1000.0
        self.offset = wall - self.mono
        self.is_synced = synced

    def wall(self) -> float:
        return self.mono + self.offset

    def monotonic(self) -> float:
        return self.mono

    def synced(self) -> bool:
        return self.is_synced

    def advance(self, seconds: float) -> None:
        self.mono += seconds


def make(tmp_path, clocks, guard=lambda: True, settings=None):
    return Telemetry(
        tmp_path / "ssd", settings, guard=guard, synced=clocks.synced,
        clock=clocks.wall, monotonic=clocks.monotonic,
    )  # fmt: skip


def stored(tmp_path) -> list[dict]:
    lines = []
    for path in sorted((tmp_path / "ssd" / "telemetry").glob("*.jsonl")):
        lines += [json.loads(line) for line in path.read_text().splitlines()]
    return lines


def feed(telemetry, clocks, minutes, every=10, cpu=50.0):
    for _ in range(minutes * 60 // every):
        telemetry.add(cpu_c=cpu, undervoltage=False, throttled=False)
        clocks.advance(every)
        telemetry.tick()


def test_summarise_means_mins_and_flags():
    sample = summarise(10, [
        {"cpu_c": 50.0, "undervoltage": False},
        {"cpu_c": 52.0, "undervoltage": True},
        {"cpu_c": None, "undervoltage": False},
    ])  # fmt: skip
    assert sample == {"unix": 600, "n": 3, "cpu_c": 51.0, "undervoltage": True}


def test_minutes_are_written_once_finished(tmp_path):
    clocks = Clocks(NOON)
    telemetry = make(tmp_path, clocks)
    feed(telemetry, clocks, minutes=3)
    samples = stored(tmp_path)
    assert [s["unix"] for s in samples] == [NOON, NOON + 60, NOON + 120]
    assert all(s["n"] == 6 and s["cpu_c"] == 50.0 for s in samples)
    assert telemetry.last_sample_unix == NOON + 120
    # A minute still in progress is not written.
    telemetry.add(cpu_c=70.0)
    clocks.advance(30)
    telemetry.tick()
    assert len(stored(tmp_path)) == 3


def test_readings_wait_for_the_clock_and_get_corrected_times(tmp_path):
    # The Pi booted after a two-day outage with its clock at the last saved time.
    clocks = Clocks(NOON - 2 * 86400, synced=False)
    telemetry = make(tmp_path, clocks)
    feed(telemetry, clocks, minutes=5)
    assert stored(tmp_path) == [] and telemetry.status()["held_readings"] == 30
    # NTP answers: the wall clock jumps forward and the held minutes land at the right time.
    clocks.offset += 2 * 86400
    clocks.is_synced = True
    telemetry.tick()
    samples = stored(tmp_path)
    assert [s["unix"] for s in samples] == [NOON + 60 * i for i in range(5)]
    assert list((tmp_path / "ssd" / "telemetry").iterdir())[0].name == "2026-09-25.jsonl"


def test_samples_wait_for_the_ssd(tmp_path):
    clocks = Clocks(NOON)
    present = {"ssd": False}
    telemetry = make(tmp_path, clocks, guard=lambda: present["ssd"])
    feed(telemetry, clocks, minutes=4)
    assert stored(tmp_path) == [] and telemetry.status()["pending_minutes"] == 4
    # History still shows minutes that have not reached the SSD.
    assert len(telemetry.history("24h")["points"]) >= 1
    present["ssd"] = True
    feed(telemetry, clocks, minutes=1)
    assert [s["unix"] for s in stored(tmp_path)] == [NOON + 60 * i for i in range(5)]


def test_retention_is_validated_saved_and_prunes(tmp_path):
    clocks = Clocks(NOON)
    settings = tmp_path / "telemetry.json"
    folder = tmp_path / "ssd" / "telemetry"
    folder.mkdir(parents=True)
    today = datetime.fromtimestamp(NOON, UTC).date()
    for age in (0, 6, 7, 40):
        (folder / f"{today - timedelta(days=age)}.jsonl").write_text("")
    telemetry = make(tmp_path, clocks, settings=settings)
    assert telemetry.retention_days == 30
    for bad in (0, 14, "30", True, None):
        with pytest.raises(TelemetryError):
            telemetry.set_retention(bad)
    telemetry.set_retention(7)
    assert json.loads(settings.read_text()) == {"retention_days": 7}
    kept = sorted(p.stem for p in folder.iterdir())
    assert kept == [str(today - timedelta(days=6)), str(today)]
    assert make(tmp_path, clocks, settings=settings).retention_days == 7


def test_bucket_sizes_keep_replies_small():
    assert bucket_for(86400) == 300
    assert bucket_for(7 * 86400) == 1800
    assert bucket_for(30 * 86400) == 7200
    assert bucket_for(365 * 86400) == 86400


def test_aggregate_gives_average_range_and_flag_minutes():
    samples = [
        {"unix": 0, "n": 6, "cpu_c": 50.0, "throttled": False},
        {"unix": 60, "n": 6, "cpu_c": 60.0, "throttled": True},
        {"unix": 300, "n": 6, "cpu_c": 40.0},
    ]
    assert aggregate(samples, 300) == [
        {"unix": 0, "minutes": 2, "cpu_c": 55.0, "cpu_c_low": 50.0, "cpu_c_high": 60.0,
         "throttled": 1},
        {"unix": 300, "minutes": 1, "cpu_c": 40.0, "cpu_c_low": 40.0, "cpu_c_high": 40.0},
    ]  # fmt: skip


def test_history_spans_days_and_respects_retention(tmp_path):
    clocks = Clocks(NOON)
    telemetry = make(tmp_path, clocks)
    folder = tmp_path / "ssd" / "telemetry"
    folder.mkdir(parents=True)
    today = datetime.fromtimestamp(NOON, UTC).date()
    for age in range(3):
        day = today - timedelta(days=age)
        start = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
        (folder / f"{day}.jsonl").write_text(
            "".join(json.dumps({"unix": start + m * 60, "n": 6, "cpu_c": 45.0 + age}) + "\n"
                    for m in range(0, 1440, 10))
            + "{cut short\n"
        )  # fmt: skip
    week = telemetry.history("7d")
    assert week["bucket_seconds"] == 1800 and week["retention_days"] == 30
    assert {p["cpu_c"] for p in week["points"]} == {45.0, 46.0, 47.0}
    assert all(week["since_unix"] <= p["unix"] <= week["until_unix"] for p in week["points"])
    day = telemetry.history("24h")
    # Yesterday noon to now, one sample every ten minutes; later lines in today's file are ignored.
    assert day["bucket_seconds"] == 300 and len(day["points"]) == 145
    assert telemetry.history("all")["bucket_seconds"] == 7200
    with pytest.raises(TelemetryError):
        telemetry.history("1y")


def test_clock_synchronized_uses_timesyncd_marker(tmp_path):
    marker = tmp_path / "timesync" / "synchronized"
    assert clock_synchronized(marker)  # no timesyncd here: falls back to the sanity check
    marker.parent.mkdir()
    assert not clock_synchronized(marker)
    marker.touch()
    assert clock_synchronized(marker)


def test_charger_fields_and_daily_energy(tmp_path):
    clocks = Clocks(NOON)
    telemetry = make(tmp_path, clocks)
    for _ in range(120):  # two hours: 60 W of solar, 10 W of load
        for volts in (12.6, 12.8):
            telemetry.add(
                battery_v=volts, battery_a=4.0, solar_w=60, load_w=10.0, charge_state="bulk"
            )
            clocks.advance(30)
        telemetry.tick()
    history = telemetry.history("24h")
    point = history["points"][-1]
    assert point["battery_v"] == 12.7 and point["battery_v_min"] == 12.6
    assert point["charge_state"] == "bulk"
    [day] = history["energy"]
    assert day["solar_wh"] == pytest.approx(119, abs=1)  # the last minute is still open
    assert day["load_wh"] == pytest.approx(19.8, abs=0.2)
    assert make(tmp_path / "empty", Clocks(NOON)).history("24h")["energy"] == []
