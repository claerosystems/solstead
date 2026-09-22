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
