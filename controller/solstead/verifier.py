"""Controller-side check of the camera's motion reports: areas and shadows.

The camera's own detector stays the trigger. The controller watches a small, low-rate copy of the
camera stream, keeps a background model, and only passes motion to the recorder while the camera
reports motion AND the controller sees a solid moving object inside an owner-drawn area that is not
a shadow or a whole-scene lighting change. The first confirmation is backdated to the camera's
trigger, so the pre-roll still covers the start of the activity.

If the checker cannot run (no numpy, stream down, no frames), the gate passes the camera's reports
straight through: a failure here must never silence recording.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from .camera import input_args
from .recognition import Detection, RecognitionWorker, Recognizer
from .zones import MotionSettings

try:  # numpy is only needed for checking; recording still works without it.
    import numpy as np
except ImportError:  # pragma: no cover - exercised on machines without numpy
    np = None

Spawner = Callable[..., subprocess.Popen]

FRAME_WIDTH = 256
FRAME_HEIGHT = 144
FRAMES_PER_SECOND = 4
WARMUP_FRAMES = 8
STALE_SECONDS = 10.0
RESTART_SECONDS = 10.0
RECOGNITION_SCALE = 2  # recognition sees frames twice the analysed size (512 x 288)
RECOGNITION_HOLD_SECONDS = 15.0  # a recognised person or vehicle keeps confirming this long

# Per sensitivity: brightness change counted as movement, smallest object in analysed pixels
# (a person at 20 m with the S445's 4 mm lens is roughly 4 x 14 pixels at 256 x 144), and how many
# of the last three frames must contain it.
SENSITIVITY = {
    "low": {"threshold": 32.0, "min_pixels": 60, "persist": 3},
    "medium": {"threshold": 24.0, "min_pixels": 30, "persist": 2},
    "high": {"threshold": 16.0, "min_pixels": 15, "persist": 2},
}
LIGHTING_CHANGE_FRACTION = 0.5  # more of the watched area than this changing at once is lighting
SHADOW_RATIO = (0.35, 0.92)  # a shadow darkens a surface to this fraction of its brightness
SHADOW_SMOOTHNESS = 0.07  # ...by the same factor as its neighbours (texture preserved)
SHADOW_CHROMA = 0.035  # ...without changing its colour (daylight only)
COLOUR_SCENE = 0.02  # mean chroma spread above this means a colour (not infrared) picture
BACKGROUND_RATE = 0.05
FOREGROUND_RATE = 0.005
LARGE_COUNT = 4000  # above this many moving pixels skip labelling: it is certainly big enough


@dataclass(frozen=True)
class Verdict:
    active: bool = False
    largest: int = 0  # pixels in the largest moving object
    changed: float = 0.0  # fraction of the watched area that changed
    shadow: float = 0.0  # fraction of changed pixels classed as shadow
    lighting: bool = False  # the frame was dismissed as a whole-scene change
    warming_up: bool = False


class FrameDetector:
    """Background subtraction on small RGB frames with area masking and shadow removal."""

    def __init__(self, settings: MotionSettings, width: int = FRAME_WIDTH,
                 height: int = FRAME_HEIGHT):  # fmt: skip
        if np is None:
            raise RuntimeError("numpy is not installed")
        self.width = width
        self.height = height
        self.background: np.ndarray | None = None  # H x W x 3 float32
        self.frames = 0
        self.history: deque[bool] = deque(maxlen=3)
        self.configure(settings)

    def configure(self, settings: MotionSettings) -> None:
        self.settings = settings
        self.mask = (
            np.frombuffer(bytes(settings.mask(self.width, self.height)), dtype=np.uint8)
            .reshape(self.height, self.width)
            .astype(bool)
        )
        self.watched = max(1, int(self.mask.sum()))
        self.tuning = SENSITIVITY[settings.sensitivity]
        self.history.clear()

    def analyse(self, frame) -> Verdict:
        """Analyse one frame: raw RGB bytes, or an H x W x 3 array, at the detector's size."""
        if isinstance(frame, bytes | bytearray):
            frame = np.frombuffer(frame, dtype=np.uint8).reshape(self.height, self.width, 3)
        image = frame.astype(np.float32)
        self.frames += 1
        if self.background is None:
            self.background = image.copy()
            return Verdict(warming_up=True)
        background = self.background
        luma = image @ np.float32([0.299, 0.587, 0.114])
        base = background @ np.float32([0.299, 0.587, 0.114])
        changed = (np.abs(luma - base) > self.tuning["threshold"]) & self.mask
        changed_fraction = float(changed.sum()) / self.watched
        if self.frames <= WARMUP_FRAMES:
            self._learn(image, changed)
            return Verdict(changed=changed_fraction, warming_up=True)
        if changed_fraction > LIGHTING_CHANGE_FRACTION:
            # Cloud, infrared switching, headlights sweeping: relearn quickly, report nothing.
            self.background = 0.5 * background + 0.5 * image
            self.history.append(False)
            return Verdict(changed=changed_fraction, lighting=True)
        moving = changed
        shadow_fraction = 0.0
        if self.settings.ignore_shadows and changed.any():
            shadow = changed & self._shadow(image, background, luma, base)
            moving = changed & ~shadow
            shadow_fraction = float(shadow.sum()) / max(1, int(changed.sum()))
        moving = self._denoise(moving)
        largest = largest_object(moving)
        self.history.append(largest >= self.tuning["min_pixels"])
        self._learn(image, moving)
        active = sum(self.history) >= self.tuning["persist"]
        return Verdict(active, largest, changed_fraction, shadow_fraction)

    def _shadow(self, image, background, luma, base):
        ratio = (luma + 1.0) / (base + 1.0)
        darker = (ratio > SHADOW_RATIO[0]) & (ratio < SHADOW_RATIO[1])
        # Texture test: a shadow scales the surface by the same factor as its neighbours, so the
        # surface's texture survives. Two of four similar neighbours lets the shadow's own edge
        # and corners pass while an object, which replaces the texture, does not.
        padded = np.pad(ratio, 1, mode="edge")
        similar = sum(
            (np.abs(neighbour - ratio) < SHADOW_SMOOTHNESS).astype(np.uint8)
            for neighbour in (
                padded[1:-1, :-2],
                padded[1:-1, 2:],
                padded[:-2, 1:-1],
                padded[2:, 1:-1],
            )
        )
        shadow = darker & (similar >= 2)
        if self._colour_scene(background):
            # Colour test: a shadow keeps the surface's chromaticity. Meaningless under infrared.
            now = image / (image.sum(axis=2, keepdims=True) + 1.0)
            was = background / (background.sum(axis=2, keepdims=True) + 1.0)
            shadow &= np.abs(now - was).max(axis=2) < SHADOW_CHROMA
        return shadow

    def _colour_scene(self, background) -> bool:
        chroma = background / (background.sum(axis=2, keepdims=True) + 1.0)
        return float(np.abs(chroma - 1 / 3).max(axis=2)[self.mask].mean()) > COLOUR_SCENE

    @staticmethod
    def _denoise(moving):
        """Drop isolated pixels (sensor noise, compression artefacts, rain)."""
        padded = np.pad(moving.astype(np.uint8), 1)
        neighbours = sum(
            padded[1 + dy : padded.shape[0] - 1 + dy, 1 + dx : padded.shape[1] - 1 + dx]
            for dy in (-1, 0, 1)
            for dx in (-1, 0, 1)
            if dy or dx
        )
        return moving & (neighbours >= 2)

    def _learn(self, image, moving) -> None:
        rate = np.where(moving, FOREGROUND_RATE, BACKGROUND_RATE).astype(np.float32)[..., None]
        self.background += rate * (image - self.background)


def largest_object(moving) -> int:
    """Pixels in the largest 8-connected group of moving pixels."""
    count = int(moving.sum())
    if count == 0 or count > LARGE_COUNT:
        return count
    rows, columns = np.nonzero(moving)
    remaining = set(zip(rows.tolist(), columns.tolist(), strict=True))
    largest = 0
    while remaining:
        stack = [remaining.pop()]
        size = 0
        while stack:
            row, column = stack.pop()
            size += 1
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    neighbour = (row + dy, column + dx)
                    if neighbour in remaining:
                        remaining.remove(neighbour)
                        stack.append(neighbour)
        largest = max(largest, size)
    return largest


class MotionGate:
    """Sits between the camera's motion reports and the recorder.

    `motion()` has the recorder's signature, so the ONVIF watcher feeds it unchanged. While the
    checker is healthy and enabled, the recorder only hears about motion the checker confirms
    during (or just after) a camera report. Otherwise reports pass straight through.
    """

    def __init__(self, recorder, clock: Callable[[], float] = time.time, lookback: float = 10.0,
                 hold_seconds: float = 10.0, grace_seconds: float = 3.0):  # fmt: skip
        self.recorder = recorder
        self.clock = clock
        self.lookback = lookback
        self.hold_seconds = hold_seconds  # cameras that only ever report "on"
        self.grace_seconds = grace_seconds  # the checker may confirm just after the camera stops
        self.checker: MotionVerifier | None = None
        self.camera_active = False
        self.camera_since: float | None = None
        self.camera_last: float | None = None
        self.trigger = "motion"
        self.checker_active = False
        self.reporting = False
        self.confirmed_in_window = False
        self.confirmed = 0  # camera reports the checker agreed with
        self.ignored = 0  # camera reports dismissed as outside the areas, shadow or lighting
        self.passed_through = 0  # camera reports recorded unchecked (checker off or down)
        self._lock = threading.RLock()

    def checking(self, now: float) -> bool:
        return bool(self.checker and self.checker.enabled and self.checker.healthy(now))

    def motion(self, active: bool, at: float | None = None, trigger: str = "motion") -> None:
        now = self.clock()
        at = now if at is None else at
        with self._lock:
            if not self.checking(now):
                if active and not self.camera_active:
                    self.passed_through += 1
                self.camera_active = active
                self.camera_last = at
                self.reporting = active
                self.recorder.motion(active, at, trigger)
                return
            if active:
                if not self._window_open(now):
                    self.camera_since = at
                    self.confirmed_in_window = False
                self.camera_active = True
                self.trigger = trigger
            else:
                self.camera_active = False
            self.camera_last = at
            self._evaluate(now)

    def verdict(self, active: bool, now: float | None = None, label: str | None = None) -> None:
        now = self.clock() if now is None else now
        with self._lock:
            self.checker_active = active
            if active and label:
                self.trigger = label
            self._evaluate(now)

    def tick(self, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        with self._lock:
            if self.reporting and not self.checking(now) and not self.camera_active:
                # The checker went away mid-event; close it as the camera would have.
                self.recorder.motion(False, now)
                self.reporting = False
            elif self.checking(now):
                self._evaluate(now)

    def camera_window(self, now: float | None = None) -> bool:
        """The camera is reporting motion now, or stopped only moments ago."""
        with self._lock:
            return self._window_open(self.clock() if now is None else now)

    def _window_open(self, now: float) -> bool:
        if self.camera_last is None:
            return False
        if self.camera_active:
            return now - self.camera_last <= self.hold_seconds
        return now - self.camera_last <= self.grace_seconds

    def _evaluate(self, now: float) -> None:
        window = self._window_open(now)
        if window and self.checker_active:
            if not self.reporting:
                start = self.camera_since if self.camera_since is not None else now
                start = max(start, now - self.lookback)
                self.recorder.motion(True, start, self.trigger)
                self.reporting = True
                if not self.confirmed_in_window:
                    self.confirmed += 1
                    self.confirmed_in_window = True
            else:
                self.recorder.motion(True, now, self.trigger)
            return
        if self.reporting:
            self.recorder.motion(False, now)
            self.reporting = False
        if not window and self.camera_since is not None:
            if not self.confirmed_in_window:
                self.ignored += 1
            self.camera_since = None
            self.confirmed_in_window = False

    def status(self) -> dict:
        now = self.clock()
        return {
            "checking": self.checking(now),
            "confirmed": self.confirmed,
            "ignored": self.ignored,
            "passed_through": self.passed_through,
        }


class MotionVerifier:
    """Reads small RGB frames from ffmpeg and feeds the detector's verdicts to the gate."""

    def __init__(self, url: str | None, gate: MotionGate, settings: MotionSettings,
                 width: int = FRAME_WIDTH, height: int = FRAME_HEIGHT,
                 fps: int = FRAMES_PER_SECOND, spawner: Spawner = subprocess.Popen,
                 clock: Callable[[], float] = time.time,
                 recognizer: Recognizer | None = None):  # fmt: skip
        self.url = url
        self.gate = gate
        self.settings = settings
        self.width = width
        self.height = height
        self.fps = fps
        self.spawner = spawner
        self.clock = clock
        self.process: subprocess.Popen | None = None
        self.detector: FrameDetector | None = None
        self.last_frame: float | None = None
        self.last_verdict = Verdict()
        self.frames = 0
        self.restarts = 0
        self.last_error: str | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.recognizer = recognizer
        self.worker = RecognitionWorker(recognizer, self.recognized) if recognizer else None
        self.recognized_at: float | None = None
        self.last_detection: Detection | None = None
        gate.checker = self

    @property
    def available(self) -> bool:
        return bool(self.url) and np is not None

    @property
    def enabled(self) -> bool:
        return self.settings.verify and self.available

    def healthy(self, now: float) -> bool:
        return (
            self.last_frame is not None
            and now - self.last_frame <= STALE_SECONDS
            and self.frames > WARMUP_FRAMES
        )

    @property
    def recognizing(self) -> bool:
        """Recognition is wanted and can run; otherwise motion filters alone decide."""
        return bool(self.recognizer and self.settings.recognize and self.recognizer.available)

    @property
    def scale(self) -> int:
        return RECOGNITION_SCALE if self.recognizer else 1

    def command(self) -> list[str]:
        width, height = self.width * self.scale, self.height * self.scale
        return input_args(self.url) + [
            "-an", "-vf", f"fps={self.fps},scale={width}:{height}",
            "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
        ]  # fmt: skip

    def update(self, settings: MotionSettings) -> None:
        with self._lock:
            self.settings = settings
            if self.detector:
                self.detector.configure(settings)
        if settings.verify:
            self.start()
        else:
            self.stop()

    def start(self) -> None:
        if self._thread or not self.enabled:
            if np is None:
                self.last_error = "numpy is not installed; motion is not checked"
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="motion-checker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        process = self.process
        if process and process.poll() is None:
            process.terminate()
        if self._thread:
            self._thread.join(10)
            self._thread = None
        if self.worker:
            self.worker.stop()
        self.last_frame = None
        self.gate.verdict(False)

    def run(self) -> None:
        while not self._stop.is_set():
            self.read_stream()
            if self._stop.is_set():
                break
            self.restarts += 1
            self._stop.wait(RESTART_SECONDS)

    def read_stream(self) -> None:
        """Run ffmpeg once and analyse frames until it exits or we are stopped."""
        scale = self.scale
        size = self.width * scale * self.height * scale * 3
        try:
            # ffmpeg's stderr may contain camera credentials; never capture or log it.
            self.process = self.spawner(
                self.command(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )  # fmt: skip
        except OSError:
            self.last_error = "Could not start ffmpeg for motion checking"
            return
        with self._lock:
            self.detector = FrameDetector(self.settings, self.width, self.height)
            self.frames = 0
        try:
            while not self._stop.is_set():
                frame = read_exactly(self.process.stdout, size)
                if frame is None:
                    self.last_error = "Motion checking stream ended; restarting"
                    break
                full = np.frombuffer(frame, dtype=np.uint8).reshape(
                    self.height * scale, self.width * scale, 3
                )
                small = full
                if scale > 1:
                    small = full.reshape(self.height, scale, self.width, scale, 3).mean(axis=(1, 3))
                with self._lock:
                    verdict = self.detector.analyse(small)
                now = self.clock()
                self.frames += 1
                self.last_frame = now
                self.last_verdict = verdict
                self.last_error = None
                self.gate.verdict(*self.decide(verdict.active, full, now))
        finally:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            self.process = None

    def decide(self, moving: bool, full, now: float) -> tuple[bool, float, str | None]:
        """Whether this frame confirms motion: filters alone, or filters plus recognition."""
        if not self.recognizing:
            return moving, now, None
        # Recognition is the costly step: run it only while the camera itself reports motion,
        # not for every movement the checker sees (trees, traffic outside the areas, rain).
        if moving and self.gate.camera_window(now):
            self.worker.submit(full.copy(), now)
        recent = self.recognized_at is not None and now - self.recognized_at <= (
            RECOGNITION_HOLD_SECONDS
        )
        label = self.last_detection.kind if recent and self.last_detection else None
        return moving and recent, now, label

    def recognized(self, detections: list[Detection]) -> None:
        """Called by the recognition worker with what it found in a submitted frame."""
        settings = self.settings
        wanted = [
            d
            for d in detections
            if d.kind in settings.objects
            and (settings.in_areas(*d.foot) or settings.in_areas((d.box[0] + d.box[2]) / 2,
                                                                (d.box[1] + d.box[3]) / 2))
        ]  # fmt: skip
        if wanted:
            self.last_detection = max(wanted, key=lambda d: d.score)
            self.recognized_at = self.clock()

    def status(self) -> dict:
        verdict = self.last_verdict
        recognizer = self.recognizer
        detection = self.last_detection
        recognition = {
            "wanted": self.settings.recognize,
            "available": bool(recognizer and recognizer.available),
            "reason": recognizer.reason if recognizer else "Recognition not configured",
            "runs": self.worker.runs if self.worker else 0,
            "inference_ms": recognizer.last_ms if recognizer else None,
            "last_kind": detection.kind if detection else None,
            "last_score": detection.score if detection else None,
            "last_unix": int(self.recognized_at) if self.recognized_at else None,
        }
        return {
            "recognition": recognition,
            "available": self.available,
            "enabled": self.enabled,
            "running": self._thread is not None,
            "frames": self.frames,
            "restarts": self.restarts,
            "last_error": self.last_error,
            "seeing_motion": verdict.active,
            "largest_object_pixels": verdict.largest,
            "shadow_fraction": round(verdict.shadow, 2),
        }


def read_exactly(stream, size: int) -> bytes | None:
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            return None
        data += chunk
    return bytes(data)
