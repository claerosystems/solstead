import subprocess
import time

import pytest

from solstead.live import LiveStream


class FakeHLS:
    """Pretends to be ffmpeg writing HLS: creates init and segments when started."""

    def __init__(self):
        self.commands = []
        self.alive = True

    def __call__(self, args, stdout=None, stderr=None):
        self.commands.append(args)
        folder = __import__("pathlib").Path(args[-1]).parent
        (folder / "init.mp4").write_bytes(b"init")
        (folder / "seg_00001.m4s").write_bytes(b"s1")
        (folder / "seg_00002.m4s").write_bytes(b"s2")
        (folder / "index.m3u8").write_text(
            '#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:1.0,\nseg_00001.m4s\n#EXTINF:1.0,\nseg_00002.m4s\n'
        )
        fake = self

        class Proc:
            def poll(self_inner):
                return None if fake.alive else 0

            def terminate(self_inner):
                fake.alive = False

            def wait(self_inner, timeout=None):
                return 0

        return Proc()


def test_start_touch_reap_cycle(tmp_path):
    spawner = FakeHLS()
    live = LiveStream(
        "rtsp://cam/sub", "rtsp://cam/main", tmp_path / "live", idle_seconds=20, spawner=spawner
    )
    assert live.available and not live.running
    live.start("low", now=1000)
    assert live.running and live.quality == "low" and live.ready()
    assert "rtsp://cam/sub" in spawner.commands[0] and "hls" in spawner.commands[0]
    assert live.file("index.m3u8") and live.file("init.mp4") and live.file("seg_00001.m4s")
    assert live.file("../etc/passwd") is None and live.file("other.txt") is None
    live.start("low", now=1010)  # idempotent
    assert len(spawner.commands) == 1
    assert live.reap(now=1015) is False
    live.touch(now=1030)
    assert live.reap(now=1049) is False
    assert live.reap(now=1051) is True
    assert not live.running and not (tmp_path / "live").exists()
    assert live.status()["active"] is False


def test_quality_switch_restarts(tmp_path):
    spawner = FakeHLS()
    live = LiveStream("rtsp://cam/sub", "rtsp://cam/main", tmp_path / "live", spawner=spawner)
    live.start("low")
    live.start("high")
    assert len(spawner.commands) == 2 and "rtsp://cam/main" in spawner.commands[1]
    assert live.quality == "high"
    with pytest.raises(ValueError):
        live.start("medium")
    live.stop()


def test_falls_back_to_main_stream_and_unavailable_without_camera(tmp_path):
    live = LiveStream(None, "rtsp://cam/main", tmp_path / "live", spawner=FakeHLS())
    live.start("low")
    assert "rtsp://cam/main" in live.command("low")
    live.stop()
    none = LiveStream(None, None, tmp_path / "live2")
    assert not none.available
    with pytest.raises(RuntimeError):
        none.start()


def test_wait_ready_gives_up_when_ffmpeg_dies(tmp_path):
    class Dead:
        def __call__(self, args, stdout=None, stderr=None):
            return subprocess.Popen(["true"])

    live = LiveStream("rtsp://cam/sub", None, tmp_path / "live", spawner=Dead())
    live.start()
    time.sleep(0.2)
    assert live.wait_ready(timeout=1) is False
    assert live.reap() is True
