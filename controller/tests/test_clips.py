import subprocess
from pathlib import Path

import pytest

from solstead.buffer import Segment
from solstead.clips import ClipError, assemble, concat_list


def segs(tmp_path, names):
    out = []
    for i, name in enumerate(names):
        p = tmp_path / name
        p.write_bytes(b"ts")
        out.append(Segment(p, i * 2, i * 2 + 2))
    return out


def test_concat_list_escapes_quotes(tmp_path):
    listing = concat_list(segs(tmp_path, ["a.ts", "it's.ts"]))
    assert listing.startswith("ffconcat version 1.0\n")
    assert "file '" + str(tmp_path / "a.ts") + "'" in listing
    assert r"it'\''s.ts" in listing


def test_assemble_runs_ffmpeg_concat(tmp_path):
    calls = []

    def runner(args, capture_output, timeout):
        calls.append(args)
        Path(args[-1]).write_bytes(b"mp4")
        listing = Path(args[args.index("-i") + 1])
        assert listing.exists() and "ffconcat" in listing.read_text()
        return subprocess.CompletedProcess(args, 0, b"", b"")

    output = tmp_path / "clip.mp4.partial"
    assemble(segs(tmp_path, ["a.ts", "b.ts"]), output, runner=runner)
    assert output.read_bytes() == b"mp4"
    assert not output.with_suffix(".txt").exists()
    assert calls[0][:2] == ["ffmpeg", "-nostdin"] and "concat" in calls[0]


def test_assemble_failures(tmp_path):
    def failing(args, capture_output, timeout):
        return subprocess.CompletedProcess(args, 1, b"", b"secret")

    with pytest.raises(ClipError, match="No buffered video"):
        assemble([], tmp_path / "x.mp4")
    with pytest.raises(ClipError, match="failed"):
        assemble(segs(tmp_path, ["a.ts"]), tmp_path / "x.mp4", runner=failing)
    assert not (tmp_path / "x.mp4").exists()

    def missing(args, capture_output, timeout):
        raise OSError("no ffmpeg")

    with pytest.raises(ClipError, match="unavailable"):
        assemble(segs(tmp_path, ["a.ts"]), tmp_path / "y.mp4", runner=missing)
