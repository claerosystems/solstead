from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from solstead.config import Config

TOKEN = "t" * 40


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    root = tmp_path / "ssd"
    root.mkdir()
    return root


@pytest.fixture
def config(storage_root: Path, tmp_path: Path) -> Config:
    return Config(
        storage_path=storage_root,
        api_token=TOKEN,
        tls_cert=tmp_path / "c.crt",
        tls_key=tmp_path / "c.key",
        camera_url="rtsp://cam.local/stream",
        clip_seconds=2,
        min_free_bytes=0,
        storage_require_mount=False,
        settings_path=tmp_path / "timelapse.json",
    )


class FakeFFmpeg:
    """Stands in for subprocess.run: records the command and fakes ffmpeg output."""

    def __init__(self, returncode: int = 0, stdout: bytes = b"\xff\xd8jpeg", raises=None):
        self.returncode = returncode
        self.stdout = stdout
        self.raises = raises
        self.calls: list[list[str]] = []

    def __call__(self, args, capture_output=False, timeout=None):
        self.calls.append(list(args))
        if self.raises:
            raise self.raises
        if args[-2] == "mp4":
            Path(args[-1]).write_bytes(b"\x00\x00\x00\x1cftypisom")
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, b"secret stderr")


@pytest.fixture
def ffmpeg() -> FakeFFmpeg:
    return FakeFFmpeg()
