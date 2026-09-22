"""Assemble an event clip from buffered segments with ffmpeg's concat demuxer."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from .buffer import Segment

Runner = Callable[..., subprocess.CompletedProcess]


class ClipError(Exception):
    pass


def concat_list(segments: list[Segment]) -> str:
    lines = ["ffconcat version 1.0"]
    for segment in segments:
        escaped = str(segment.path).replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


def assemble(segments: list[Segment], output: Path, runner: Runner = subprocess.run,
             timeout: int = 120) -> None:  # fmt: skip
    if not segments:
        raise ClipError("No buffered video covers this event")
    listing = output.with_suffix(".txt")
    listing.write_text(concat_list(segments))
    args = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(listing),
        "-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(output),
    ]  # fmt: skip
    try:
        result = runner(args, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise ClipError("ffmpeg unavailable or clip assembly timed out") from None
    finally:
        listing.unlink(missing_ok=True)
    if result.returncode or not output.exists() or output.stat().st_size == 0:
        output.unlink(missing_ok=True)
        raise ClipError("Clip assembly failed")
