"""Time-lapse: one frame every few minutes during a daily window, in its own allocation."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

TIMELAPSE_FOLDER = "timelapse"
EXPORT_FOLDER = "timelapse-exports"
DAY_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FRAME_NAME = re.compile(r"^\d{6}\.jpg$")
WINDOW = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")
EXPORT_MAX_AGE = 24 * 3600
Runner = Callable[..., subprocess.CompletedProcess]


class TimeLapseError(Exception):
    pass


@dataclass(frozen=True)
class TimeLapseSettings:
    enabled: bool = True
    interval_seconds: int = 300
    window: str = "06:00-18:00"  # local time, start-end
    width: int = 1920
    allocation_bytes: int = 4 * 1024**3
    export_fps: int = 12

    @classmethod
    def from_dict(cls, data: dict, base: TimeLapseSettings | None = None) -> TimeLapseSettings:
        current = asdict(base or cls())
        for key in current:
            if key in data:
                current[key] = data[key]
        settings = cls(**current)
        settings.validate()
        return settings

    def validate(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TimeLapseError("enabled must be true or false")
        if not isinstance(self.interval_seconds, int) or not 10 <= self.interval_seconds <= 86400:
            raise TimeLapseError("interval_seconds must be between 10 and 86400")
        if not self.window_minutes():
            raise TimeLapseError("window must look like 06:00-18:00")
        if not isinstance(self.width, int) or not 320 <= self.width <= 4096:
            raise TimeLapseError("width must be between 320 and 4096")
        if not isinstance(self.allocation_bytes, int) or self.allocation_bytes < 64 * 1024**2:
            raise TimeLapseError("allocation_bytes must be at least 64 MiB")
        if not isinstance(self.export_fps, int) or not 1 <= self.export_fps <= 60:
            raise TimeLapseError("export_fps must be between 1 and 60")

    def window_minutes(self) -> tuple[int, int] | None:
        match = WINDOW.match(self.window)
        if not match:
            return None
        h1, m1, h2, m2 = (int(x) for x in match.groups())
        if h1 > 23 or h2 > 24 or m1 > 59 or m2 > 59:
            return None
        start, end = h1 * 60 + m1, h2 * 60 + m2
        return (start, end) if start < end else None

    def in_window(self, now: float) -> bool:
        start, end = self.window_minutes()
        local = time.localtime(now)
        minute = local.tm_hour * 60 + local.tm_min
        return start <= minute < end

    def frames_per_day(self) -> int:
        start, end = self.window_minutes()
        return max(1, (end - start) * 60 // self.interval_seconds)


@dataclass(frozen=True)
class Frame:
    day: str
    name: str
    unix: int
    size_bytes: int


class TimeLapse:
    """Captures frames on schedule, keeps the allocation, lists days, and exports videos."""

    def __init__(
        self,
        root: Path,
        settings: TimeLapseSettings,
        capture: Callable[[int], bytes],
        runner: Runner = subprocess.run,
        clock: Callable[[], float] = time.time,
    ):
        self.root = Path(root)
        self.folder = self.root / TIMELAPSE_FOLDER
        self.exports = self.root / EXPORT_FOLDER
        self.settings = settings
        self.capture = capture
        self.runner = runner
        self.clock = clock
        self.last_capture: float | None = None
        self.last_error: str | None = None

    # ---- schedule ------------------------------------------------------------------------

    def due(self, now: float) -> bool:
        if not self.settings.enabled or not self.settings.in_window(now):
            return False
        interval = self.settings.interval_seconds
        last = self.last_capture
        if last is None:
            last = self.newest_unix() or 0
        # Align to interval boundaries so frames land at :00, :05, :10 ...
        return int(now // interval) > int(last // interval)

    def tick(self, now: float | None = None) -> Frame | None:
        now = self.clock() if now is None else now
        if not self.due(now):
            return None
        try:
            frame = self.capture_frame(now)
            self.last_error = None
            return frame
        except Exception as error:  # capture failures must not stop the schedule
            self.last_error = str(error)
            self.last_capture = now
            return None

    def capture_frame(self, now: float) -> Frame:
        data = self.capture(self.settings.width)
        local = datetime.fromtimestamp(now)
        day = local.strftime("%Y-%m-%d")
        name = local.strftime("%H%M%S") + ".jpg"
        folder = self.folder / day
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        partial = path.with_name(name + ".partial")
        with partial.open("wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        partial.rename(path)
        self.last_capture = now
        self.prune()
        return Frame(day, name, int(now), len(data))

    # ---- listing -------------------------------------------------------------------------

    def days(self) -> list[dict]:
        if not self.folder.is_dir():
            return []
        result = []
        for entry in sorted(self.folder.iterdir(), reverse=True):
            if entry.is_dir() and DAY_NAME.match(entry.name):
                frames = [p for p in entry.iterdir() if FRAME_NAME.match(p.name)]
                if frames:
                    result.append(
                        {
                            "date": entry.name,
                            "frames": len(frames),
                            "bytes": sum(p.stat().st_size for p in frames),
                        }
                    )
        return result

    def frames(self, day: str) -> list[Frame]:
        folder = self.folder / day
        if not DAY_NAME.match(day) or not folder.is_dir():
            return []
        found = []
        for path in sorted(folder.iterdir()):
            if FRAME_NAME.match(path.name):
                stamp = datetime.strptime(day + path.stem, "%Y-%m-%d%H%M%S")
                found.append(Frame(day, path.name, int(stamp.timestamp()), path.stat().st_size))
        return found

    def frame_path(self, day: str, name: str) -> Path | None:
        if not DAY_NAME.match(day) or not FRAME_NAME.match(name):
            return None
        path = self.folder / day / name
        return path if path.is_file() else None

    def all_frames(self) -> list[Path]:
        if not self.folder.is_dir():
            return []
        return sorted(
            p for d in self.folder.iterdir() if d.is_dir() and DAY_NAME.match(d.name)
            for p in d.iterdir() if FRAME_NAME.match(p.name)
        )  # fmt: skip

    def newest_unix(self) -> int | None:
        days = self.days()
        if not days:
            return None
        frames = self.frames(days[0]["date"])
        return frames[-1].unix if frames else None

    def used_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.all_frames())

    def prune(self) -> int:
        """Delete oldest frames until the allocation fits. Returns how many were deleted."""
        frames = self.all_frames()
        used = sum(p.stat().st_size for p in frames)
        deleted = 0
        for path in frames:
            if used <= self.settings.allocation_bytes:
                break
            used -= path.stat().st_size
            path.unlink(missing_ok=True)
            deleted += 1
            if not any(path.parent.iterdir()):
                path.parent.rmdir()
        self.prune_exports()
        return deleted

    def prune_exports(self) -> None:
        if not self.exports.is_dir():
            return
        cutoff = self.clock() - EXPORT_MAX_AGE
        for path in self.exports.iterdir():
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass

    def status(self) -> dict:
        days = self.days()
        frames = sum(d["frames"] for d in days)
        used = sum(d["bytes"] for d in days)
        per_day = self.settings.frames_per_day()
        average_frame = used / frames if frames else 0
        estimated_days = None
        if average_frame:
            estimated_days = int(self.settings.allocation_bytes / (average_frame * per_day))
        return {
            **asdict(self.settings),
            "day_count": len(days),
            "frames": frames,
            "used_bytes": used,
            "frames_per_day": per_day,
            "estimated_retention_days": estimated_days,
            "oldest_date": days[-1]["date"] if days else None,
            "newest_unix": self.newest_unix(),
            "last_error": self.last_error,
        }

    # ---- export --------------------------------------------------------------------------

    def export_video(self, day: str) -> Path:
        """Assemble a day's frames into an H.264 MP4, cached under timelapse-exports/."""
        frames = self.frames(day)
        if not frames:
            raise TimeLapseError("No frames for that day")
        self.exports.mkdir(parents=True, exist_ok=True)
        output = self.exports / f"{day}-{frames[-1].name[:-4]}-{self.settings.export_fps}fps.mp4"
        if output.is_file():
            return output
        listing = output.with_suffix(".txt")
        listing.write_text("".join(f"file '{self.folder / day / f.name}'\n" for f in frames))
        partial = output.with_name(output.name + ".partial")
        args = [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-r", str(self.settings.export_fps), "-f", "concat", "-safe", "0", "-i", str(listing),
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-f", "mp4", str(partial),
        ]  # fmt: skip
        try:
            result = self.runner(args, capture_output=True, timeout=600)
        except (OSError, subprocess.TimeoutExpired):
            raise TimeLapseError("ffmpeg unavailable or export timed out") from None
        finally:
            listing.unlink(missing_ok=True)
        if result.returncode or not partial.is_file():
            partial.unlink(missing_ok=True)
            raise TimeLapseError("Time-lapse export failed")
        partial.rename(output)
        return output


def load_settings(path: Path) -> TimeLapseSettings:
    """Settings live in timelapse.json next to config.json so the app can change them."""
    if path.is_file():
        try:
            return TimeLapseSettings.from_dict(json.loads(path.read_text()))
        except (OSError, ValueError, TimeLapseError):
            pass
    return TimeLapseSettings()


def save_settings(path: Path, settings: TimeLapseSettings) -> None:
    partial = path.with_suffix(".json.partial")
    partial.write_text(json.dumps(asdict(settings), indent=2) + "\n")
    partial.replace(path)
