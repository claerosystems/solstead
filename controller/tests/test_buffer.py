import os
import subprocess
import time
from datetime import datetime

from solstead.buffer import NAME_FORMAT, SegmentBuffer, list_segments, segment_start


def make_segment(folder, start, duration=2, size=100):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (datetime.fromtimestamp(start).strftime(NAME_FORMAT) + ".ts")
    path.write_bytes(b"x" * size)
    os.utime(path, (start + duration, start + duration))
    return path


def test_segment_names_round_trip(tmp_path):
    start = int(time.time()) - 100
    path = make_segment(tmp_path, start)
    assert segment_start(path) == start
    assert segment_start(tmp_path / "junk.ts") is None
    (tmp_path / "junk.ts").write_bytes(b"")
    segments = list_segments(tmp_path)
    assert len(segments) == 1 and segments[0].start == start and segments[0].end == start + 2


def test_completed_between_excludes_segment_being_written(tmp_path):
    base = int(time.time()) - 100
    for offset in range(0, 20, 2):
        make_segment(tmp_path, base + offset)
    buffer = SegmentBuffer("rtsp://cam/1", tmp_path)

    class Alive:
        def poll(self):
            return None

    buffer.process = Alive()
    chosen = buffer.completed_between(base + 5, base + 11)
    assert [s.start - base for s in chosen] == [4, 6, 8, 10]
    # The newest segment (start 18) is still open while ffmpeg runs.
    assert [s.start - base for s in buffer.completed_between(base, base + 100)] == list(
        range(0, 18, 2)
    )
    buffer.process = None
    assert buffer.completed_between(base + 17, base + 100)[-1].start == base + 18
    assert buffer.covers(base + 18) and not buffer.covers(base + 19)
    assert buffer.newest_start() == base + 18


def test_prune_keeps_recent(tmp_path):
    now = int(time.time())
    old = make_segment(tmp_path, now - 200)
    fresh = make_segment(tmp_path, now - 10)
    buffer = SegmentBuffer("rtsp://cam/1", tmp_path, keep_seconds=60)
    assert buffer.prune(now) == 1
    assert not old.exists() and fresh.exists()
    assert buffer.newest_age(now) == 10


def test_command_shape(tmp_path):
    buffer = SegmentBuffer("rtsp://u:p@cam/1", tmp_path / "buf", segment_seconds=3)
    args = buffer.command()
    assert args[args.index("-segment_time") + 1] == "3"
    assert args[-1].endswith(".ts") and "%Y" in args[-1]
    assert "copy" in args and "mpegts" in args


def test_supervisor_restarts_after_exit(tmp_path):
    spawned = []

    class Dead:
        def __init__(self):
            self.terminated = False

        def poll(self):
            return 1

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 1

    def spawner(args, stdout=None, stderr=None):
        spawned.append(args)
        return Dead()

    buffer = SegmentBuffer("rtsp://cam/1", tmp_path / "buf", spawner=spawner, restart_delay=0.05)
    buffer.start()
    deadline = time.time() + 3
    while len(spawned) < 3 and time.time() < deadline:
        time.sleep(0.02)
    buffer.stop()
    assert len(spawned) >= 3 and buffer.restarts >= 2
    assert (tmp_path / "buf").is_dir()


def test_start_without_camera_is_noop(tmp_path):
    buffer = SegmentBuffer(None, tmp_path / "buf")
    buffer.start()
    assert not buffer.running and not (tmp_path / "buf").exists()
    buffer.stop()


def test_real_popen_spawn_is_terminated(tmp_path):
    """Use a real subprocess so terminate() and the daemon thread are exercised."""
    buffer = SegmentBuffer("rtsp://cam/1", tmp_path / "buf", restart_delay=5)
    buffer.spawner = lambda args, stdout=None, stderr=None: subprocess.Popen(["sleep", "30"])
    buffer.start()
    deadline = time.time() + 3
    while not buffer.running and time.time() < deadline:
        time.sleep(0.02)
    assert buffer.running
    buffer.stop()
    assert not buffer.running


def test_prune_respects_hold_from(tmp_path):
    now = int(time.time())
    old = make_segment(tmp_path, now - 200)
    held = make_segment(tmp_path, now - 150)
    buffer = SegmentBuffer("rtsp://cam/1", tmp_path, keep_seconds=60)
    buffer.hold_from = now - 160
    assert buffer.prune(now) == 1
    assert not old.exists() and held.exists()
    buffer.hold_from = None
    assert buffer.prune(now) == 1 and not held.exists()
