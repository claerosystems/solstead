"""Person, vehicle and animal recognition on frames the motion checker has already accepted.

Uses SSD MobileNet v1 trained on COCO (ONNX model zoo, Apache 2.0) through onnxruntime. It runs
only while the checker sees motion inside the owner's areas, at most once a second, on a separate
thread so frame analysis never waits for it. Without onnxruntime or the model file it reports
itself unavailable and the checker relies on its motion filters alone.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

MODEL_NAME = "ssd_mobilenet_v1_12.onnx"
MODEL_URL = (
    "https://media.githubusercontent.com/media/onnx/models/main/validated/vision/"
    "object_detection_segmentation/ssd-mobilenetv1/model/ssd_mobilenet_v1_12.onnx"
)
MODEL_SHA256 = "b8fba5e404077d4048d27fcd1667e85e27e192eb9bf51e696c46a3acd7d21058"
KINDS = ("person", "vehicle", "animal")
# COCO category ids as the TensorFlow detection models number them.
CATEGORIES = {
    1: "person",
    2: "vehicle",  # bicycle
    3: "vehicle",  # car
    4: "vehicle",  # motorcycle
    6: "vehicle",  # bus
    8: "vehicle",  # truck
    **dict.fromkeys(range(16, 26), "animal"),  # bird, cat, dog, horse, sheep, cow, bear…
}
MIN_SCORE = 0.5
INTERVAL_SECONDS = 1.0


@dataclass(frozen=True)
class Detection:
    kind: str
    score: float
    box: tuple[float, float, float, float]  # left, top, right, bottom as picture fractions

    @property
    def foot(self) -> tuple[float, float]:
        """Bottom middle of the box: where a person or vehicle meets the ground."""
        return ((self.box[0] + self.box[2]) / 2, self.box[3])


def default_session(path: Path):
    import onnxruntime

    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = 2  # leave the Pi's other cores to recording and the API
    options.inter_op_num_threads = 1
    return onnxruntime.InferenceSession(
        str(path), sess_options=options, providers=["CPUExecutionProvider"]
    )


def onnxruntime_installed() -> bool:
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


class Recognizer:
    def __init__(self, model_path: Path | None, session_factory: Callable = default_session,
                 installed: Callable[[], bool] = onnxruntime_installed):  # fmt: skip
        self.model_path = Path(model_path) if model_path else None
        self.session_factory = session_factory
        self.installed = installed
        self._session = None
        self.last_error: str | None = None
        self.last_ms: float | None = None

    @property
    def available(self) -> bool:
        return (
            np is not None
            and self.model_path is not None
            and self.model_path.is_file()
            and self.installed()
            and self.last_error is None
        )

    @property
    def reason(self) -> str | None:
        """Why recognition is unavailable, for the app; None when it is available."""
        if self.last_error:
            return self.last_error
        if self.model_path is None or not self.model_path.is_file():
            return "Recognition model not installed"
        if not self.installed():
            return "onnxruntime not installed"
        return None

    def detect(self, frame) -> list[Detection]:
        """Detections of known kinds above MIN_SCORE in an H x W x 3 RGB uint8 frame."""
        if self._session is None:
            try:
                self._session = self.session_factory(self.model_path)
            except Exception as error:  # a damaged model must not take the checker down
                self.last_error = f"Recognition model failed to load: {type(error).__name__}"
                return []
        started = time.perf_counter()
        name = self._session.get_inputs()[0].name
        boxes, classes, scores, count = self._session.run(None, {name: frame[None, ...]})
        self.last_ms = round((time.perf_counter() - started) * 1000, 1)
        found = []
        for i in range(int(count[0])):
            kind = CATEGORIES.get(int(classes[0, i]))
            score = float(scores[0, i])
            if kind and score >= MIN_SCORE:
                top, left, bottom, right = (round(float(v), 4) for v in boxes[0, i])
                found.append(Detection(kind, round(score, 2), (left, top, right, bottom)))
        return found


class RecognitionWorker:
    """Runs the recognizer on the newest submitted frame on its own thread."""

    def __init__(self, recognizer: Recognizer, accept: Callable[[list[Detection]], None],
                 clock: Callable[[], float] = time.time):  # fmt: skip
        self.recognizer = recognizer
        self.accept = accept
        self.clock = clock
        self.last_submitted = 0.0
        self.runs = 0
        self._frame = None
        self._wake = threading.Condition()
        self._stop = False
        self._thread: threading.Thread | None = None

    def submit(self, frame, now: float) -> bool:
        """Offer a frame; taken only if a second has passed and no frame is waiting."""
        if now - self.last_submitted < INTERVAL_SECONDS:
            return False
        with self._wake:
            if self._frame is not None:
                return False
            self._frame = frame
            self.last_submitted = now
            self._wake.notify()
        if self._thread is None:
            self._stop = False
            self._thread = threading.Thread(target=self._run, name="recognition", daemon=True)
            self._thread.start()
        return True

    def run_once(self) -> None:
        """Process the waiting frame, if any (used by the thread and by tests)."""
        with self._wake:
            frame, self._frame = self._frame, None
        if frame is not None:
            self.accept(self.recognizer.detect(frame))
            self.runs += 1

    def _run(self) -> None:
        while True:
            with self._wake:
                while self._frame is None and not self._stop:
                    self._wake.wait(5)
                if self._stop:
                    return
            self.run_once()

    def stop(self) -> None:
        with self._wake:
            self._stop = True
            self._frame = None
            self._wake.notify()
        if self._thread:
            self._thread.join(5)
            self._thread = None
