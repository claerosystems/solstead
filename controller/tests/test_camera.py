import subprocess
import threading

import pytest

from solstead.camera import Camera, CameraError


def test_unconfigured_camera(ffmpeg):
    camera = Camera(None, runner=ffmpeg)
    assert not camera.configured
    with pytest.raises(CameraError, match="not configured"):
        camera.snapshot()
    assert ffmpeg.calls == []


def test_snapshot_command_and_output(ffmpeg):
    image = Camera("rtsp://cam/1", snapshot_width=640, runner=ffmpeg).snapshot()
    assert image.startswith(b"\xff\xd8")
    args = ffmpeg.calls[0]
    assert args[:2] == ["ffmpeg", "-nostdin"]
    assert "rtsp://cam/1" in args
    assert "scale=640:-2" in args and args[-1] == "pipe:1"


def test_record_command(ffmpeg, tmp_path):
    output = tmp_path / "clip.mp4.partial"
    Camera("rtsp://cam/1", runner=ffmpeg).record(output, 7)
    args = ffmpeg.calls[0]
    assert ["-t", "7"] == args[args.index("-t") : args.index("-t") + 2]
    assert args[-1] == str(output) and output.exists()


def test_failure_never_leaks_stderr(ffmpeg):
    ffmpeg.returncode = 1
    with pytest.raises(CameraError) as info:
        Camera("rtsp://user:pw@cam/1", runner=ffmpeg).snapshot()
    assert "secret" not in str(info.value) and "pw" not in str(info.value)


def test_empty_snapshot_is_error(ffmpeg):
    ffmpeg.stdout = b""
    with pytest.raises(CameraError, match="empty"):
        Camera("rtsp://cam/1", runner=ffmpeg).snapshot()


@pytest.mark.parametrize("exc", [OSError("no ffmpeg"), subprocess.TimeoutExpired("ffmpeg", 1)])
def test_runner_errors(ffmpeg, exc):
    ffmpeg.raises = exc
    with pytest.raises(CameraError, match="unavailable"):
        Camera("rtsp://cam/1", runner=ffmpeg).snapshot()


def test_busy_lock_rejects_concurrent_capture():
    started, release = threading.Event(), threading.Event()

    def slow_runner(args, capture_output, timeout):
        started.set()
        release.wait(5)
        return subprocess.CompletedProcess(args, 0, b"jpeg", b"")

    camera = Camera("rtsp://cam/1", runner=slow_runner)
    worker = threading.Thread(target=camera.snapshot)
    worker.start()
    assert started.wait(5)
    with pytest.raises(CameraError, match="busy"):
        camera.snapshot()
    release.set()
    worker.join(5)
    camera.snapshot()  # lock released after the first capture finished
