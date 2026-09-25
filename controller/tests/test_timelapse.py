import os
import subprocess
from datetime import datetime

import pytest

from solstead.timelapse import (
    TimeLapse,
    TimeLapseError,
    TimeLapseSettings,
    load_settings,
    save_settings,
)


def local(hour, minute=0, day="2026-09-22"):
    return datetime.strptime(f"{day} {hour:02d}:{minute:02d}:00", "%Y-%m-%d %H:%M:%S").timestamp()


def test_settings_validation_and_window():
    s = TimeLapseSettings()
    assert s.window_minutes() == (360, 1080) and s.frames_per_day() == 144
    assert s.in_window(local(6)) and s.in_window(local(17, 59)) and not s.in_window(local(18))
    for bad in [{"interval_seconds": 5}, {"window": "18:00-06:00"}, {"window": "6-18"}, {"width": 10},
                {"allocation_bytes": 1}, {"export_fps": 0}, {"enabled": "yes"}]:  # fmt: skip
        with pytest.raises(TimeLapseError):
            TimeLapseSettings.from_dict(bad)
    changed = TimeLapseSettings.from_dict({"interval_seconds": 60, "window": "07:30-16:00"}, base=s)
    assert (
        changed.interval_seconds == 60
        and changed.width == s.width
        and changed.frames_per_day() == 510
    )


def test_settings_round_trip(tmp_path):
    path = tmp_path / "timelapse.json"
    assert load_settings(path) == TimeLapseSettings()
    save_settings(path, TimeLapseSettings(interval_seconds=120))
    assert load_settings(path).interval_seconds == 120
    path.write_text("{broken")
    assert load_settings(path) == TimeLapseSettings()


class Capturer:
    def __init__(self, size=1000):
        self.calls = []
        self.size = size
        self.fail = False

    def __call__(self, width):
        self.calls.append(width)
        if self.fail:
            raise RuntimeError("camera down")
        return b"\xff\xd8" + b"x" * (self.size - 2)


def make(tmp_path, **settings):
    capture = Capturer()
    now = {"t": local(9)}
    lapse = TimeLapse(tmp_path, TimeLapseSettings(**settings), capture, clock=lambda: now["t"])
    return lapse, capture, now


def test_captures_on_interval_boundaries_inside_window(tmp_path):
    lapse, capture, now = make(tmp_path)
    frame = lapse.tick(local(9, 0))
    assert (
        frame
        and frame.day == "2026-09-22"
        and frame.name == "090000.jpg"
        and frame.size_bytes == 1000
    )
    assert capture.calls == [1920]
    assert lapse.tick(local(9, 4)) is None  # same 5-minute slot
    assert lapse.tick(local(9, 5)).name == "090500.jpg"
    assert lapse.tick(local(19, 0)) is None  # outside the window
    assert lapse.tick(local(9, 10, day="2026-09-23")).day == "2026-09-23"
    days = lapse.days()
    assert [d["date"] for d in days] == ["2026-09-23", "2026-09-22"]
    assert days[1]["frames"] == 2 and days[1]["bytes"] == 2000
    frames = lapse.frames("2026-09-22")
    assert [f.name for f in frames] == ["090000.jpg", "090500.jpg"]
    assert frames[0].unix == int(local(9, 0))
    assert lapse.frame_path("2026-09-22", "090000.jpg").is_file()
    assert (
        lapse.frame_path("../x", "090000.jpg") is None
        and lapse.frame_path("2026-09-22", "x.jpg") is None
    )
    status = lapse.status()
    assert status["frames"] == 3 and status["day_count"] == 2 and status["used_bytes"] == 3000
    assert status["estimated_retention_days"] == int(4 * 1024**3 / (1000 * 144))
    assert status["newest_unix"] == int(local(9, 10, day="2026-09-23"))


def test_disabled_and_failures(tmp_path):
    lapse, capture, now = make(tmp_path, enabled=False)
    assert lapse.tick(local(9)) is None and capture.calls == []
    lapse.settings = TimeLapseSettings()
    capture.fail = True
    assert lapse.tick(local(9)) is None
    assert "camera down" in lapse.last_error
    assert (
        lapse.tick(local(9, 1)) is None and len(capture.calls) == 1
    )  # slot consumed, no retry storm
    capture.fail = False
    assert lapse.tick(local(9, 5)) is not None and lapse.last_error is None
    assert not list((tmp_path / "timelapse" / "2026-09-22").glob("*.partial"))


def test_resumes_schedule_from_existing_frames_after_restart(tmp_path):
    lapse, capture, now = make(tmp_path)
    lapse.tick(local(9, 0))
    fresh = TimeLapse(tmp_path, TimeLapseSettings(), capture, clock=lambda: local(9, 2))
    assert fresh.tick(local(9, 2)) is None  # 09:00 frame exists; wait for 09:05
    assert fresh.tick(local(9, 5)) is not None


def test_prune_keeps_allocation(tmp_path):
    lapse, capture, now = make(tmp_path, allocation_bytes=64 * 1024**2)
    capture.size = 30 * 1024**2
    lapse.tick(local(9, 0))
    lapse.tick(local(9, 5))
    lapse.tick(local(9, 10))
    names = [f.name for f in lapse.frames("2026-09-22")]
    assert names == ["090500.jpg", "091000.jpg"]
    assert lapse.used_bytes() <= 64 * 1024**2


def test_export_video_caches_and_validates(tmp_path):
    lapse, capture, now = make(tmp_path)
    with pytest.raises(TimeLapseError, match="No frames"):
        lapse.export_video("2026-09-22")
    lapse.tick(local(9, 0))
    lapse.tick(local(9, 5))
    calls = []

    def runner(args, capture_output, timeout):
        calls.append(args)
        listing = __import__("pathlib").Path(args[args.index("-i") + 1]).read_text()
        assert "090000.jpg" in listing and "090500.jpg" in listing
        __import__("pathlib").Path(args[-1]).write_bytes(b"mp4")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    lapse.runner = runner
    out = lapse.export_video("2026-09-22")
    assert out.name == "2026-09-22-090500-12fps.mp4" and out.read_bytes() == b"mp4"
    assert lapse.export_video("2026-09-22") == out and len(calls) == 1  # cached
    lapse.tick(local(9, 10))
    assert lapse.export_video("2026-09-22").name == "2026-09-22-091000-12fps.mp4"
    assert not list(lapse.exports.glob("*.txt"))
    for path in lapse.exports.iterdir():  # made "at" 09:10 on the 22nd, whatever today is
        os.utime(path, (local(9, 10), local(9, 10)))
    now["t"] = local(9) + 3 * 86400
    lapse.prune()
    assert list(lapse.exports.iterdir()) == []


def test_export_failure(tmp_path):
    lapse, capture, now = make(tmp_path)
    lapse.tick(local(9, 0))
    lapse.runner = lambda args, capture_output, timeout: subprocess.CompletedProcess(
        args, 1, b"", b""
    )
    with pytest.raises(TimeLapseError, match="failed"):
        lapse.export_video("2026-09-22")
    assert not list(lapse.exports.glob("*.partial"))
