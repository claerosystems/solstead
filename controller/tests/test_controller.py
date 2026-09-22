import pytest

from solstead.camera import Camera, CameraError
from solstead.controller import Controller
from solstead.storage import StorageUnavailable


def make(config, ffmpeg, camera_url="rtsp://cam/1"):
    return Controller(config, camera=Camera(camera_url, runner=ffmpeg))


def test_status_shape(config, ffmpeg):
    status = make(config, ffmpeg).status()
    assert status["name"] == "Solstead"
    assert status["storage"]["ready"] is True
    assert status["camera"] == {"configured": True, "last_success_unix": None}
    assert status["recording_mode"] == "manual" and status["remote_access"] is False
    assert "temperature_c" in status and status["uptime_seconds"] >= 0


def test_snapshot_updates_last_success(config, ffmpeg):
    controller = make(config, ffmpeg)
    assert controller.snapshot().startswith(b"\xff\xd8")
    assert controller.status()["camera"]["last_success_unix"] is not None


def test_record_creates_listed_clip(config, ffmpeg):
    controller = make(config, ffmpeg)
    clip = controller.record()
    assert clip["id"].endswith(".mp4") and clip["size_bytes"] > 0
    assert [c["id"] for c in controller.recordings()] == [clip["id"]]
    assert not list((config.storage_path / "manual").glob("*.partial"))


def test_record_failure_cleans_partial(config, ffmpeg):
    ffmpeg.returncode = 1
    controller = make(config, ffmpeg)
    with pytest.raises(CameraError):
        controller.record()
    assert controller.recordings() == []
    assert not list((config.storage_path / "manual").glob("*"))


def test_record_without_storage(config, ffmpeg, tmp_path):
    from dataclasses import replace

    unmounted = replace(config, storage_path=tmp_path / "gone", storage_require_mount=True)
    with pytest.raises(StorageUnavailable):
        Controller(unmounted, camera=Camera("rtsp://cam/1", runner=ffmpeg)).record()
    assert ffmpeg.calls == []


def test_events_enabled_controller_starts_and_stops(config, ffmpeg, tmp_path):
    from dataclasses import replace

    from solstead.buffer import SegmentBuffer
    from solstead.motion import MotionWatcher
    from solstead.onvif import PullPointClient
    from tests.test_onvif import FakeCamera

    enabled = replace(config, camera_onvif_url="http://cam:2020/onvif/device_service", camera_user="u",
                      camera_password="p")  # fmt: skip
    assert enabled.events_enabled
    spawned = []

    class Sleeping:
        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    buffer = SegmentBuffer(
        enabled.camera_url,
        tmp_path / "buf",
        spawner=lambda *a, **k: spawned.append(a) or Sleeping(),
    )
    controller = Controller(
        enabled, camera=Camera(enabled.camera_url, runner=ffmpeg), buffer=buffer
    )
    controller.watcher = MotionWatcher(
        PullPointClient("http://cam", "u", "p", poster=FakeCamera()), controller.recorder
    )
    controller.start()
    import time

    deadline = time.time() + 3
    while (
        not (controller.events_running and controller.watcher.subscribed) and time.time() < deadline
    ):
        time.sleep(0.02)
    status = controller.status()
    assert status["recording_mode"] == "events"
    assert (
        status["events"]["running"]
        and status["events"]["buffer_running"]
        and status["events"]["camera_subscribed"]
    )
    # The fake pull reported motion, so the post-roll is running.
    assert spawned and status["events"]["motion_active"] is True
    controller.stop()
    assert not controller.events_running


def test_events_wait_for_storage(config, ffmpeg, tmp_path):
    from dataclasses import replace

    unmounted = replace(config, storage_path=tmp_path / "gone", storage_require_mount=True,
                        camera_onvif_url="http://cam/onvif")  # fmt: skip
    controller = Controller(unmounted, camera=Camera(unmounted.camera_url, runner=ffmpeg))
    controller._ensure_events()
    assert not controller.events_running and "not mounted" in controller.events_reason
    assert not (tmp_path / "gone").exists()


def test_snapshot_and_record_come_from_buffer_when_running(config, ffmpeg, tmp_path):
    from dataclasses import replace

    from solstead.buffer import SegmentBuffer
    from tests.test_buffer import make_segment

    enabled = replace(config, camera_onvif_url="http://cam/onvif")
    buffer = SegmentBuffer(enabled.camera_url, tmp_path / "buf")
    import time

    base = time.time() - 12
    for offset in range(0, 16, 2):
        make_segment(buffer.folder, base + offset)

    class Alive:
        def poll(self):
            return None

    buffer.process = Alive()
    calls = []

    def runner(args, capture_output, timeout):
        calls.append(args)
        if args[-1] != "pipe:1":
            __import__("pathlib").Path(args[-1]).write_bytes(b"mp4")
        return __import__("subprocess").CompletedProcess(args, 0, b"\xff\xd8", b"")

    controller = Controller(
        enabled, camera=Camera(enabled.camera_url, runner=runner), buffer=buffer
    )
    controller.events_running = True
    assert controller.snapshot().startswith(b"\xff\xd8")
    assert calls[-1][calls[-1].index("-i") + 1].endswith(".ts")  # from the buffer, not rtsp
    clip = controller.record()
    assert clip["id"].endswith(".mp4") and "concat" in calls[-1]
    controller.events_running = False
    controller.snapshot()
    assert "rtsp://" in calls[-1][calls[-1].index("-i") + 1]


def test_controller_loop_reaps_idle_live_view(config, ffmpeg, tmp_path):
    import time

    from solstead.live import LiveStream
    from tests.test_live import FakeHLS

    live = LiveStream("rtsp://cam/sub", None, tmp_path / "live", idle_seconds=5, spawner=FakeHLS())
    controller = Controller(config, camera=Camera(config.camera_url, runner=ffmpeg), live=live)
    controller.start_live("low")
    assert controller.status()["live"]["active"]
    live.last_touch = time.time() - 10  # pretend the viewer left a while ago
    controller.start()
    deadline = time.time() + 3
    while live.running and time.time() < deadline:
        time.sleep(0.05)
    controller.stop()
    assert not live.running
