import time

import numpy as np
import pytest

from solstead.recognition import Detection, RecognitionWorker, Recognizer
from solstead.verifier import FRAME_HEIGHT as H
from solstead.verifier import FRAME_WIDTH as W
from solstead.verifier import MotionGate, MotionVerifier
from solstead.zones import MotionSettings

RIGHT_HALF = {"points": [[0.5, 0], [1, 0], [1, 1], [0.5, 1]]}


class FakeInput:
    name = "inputs"


class FakeSession:
    """Answers like SSD MobileNet: boxes are [top, left, bottom, right] fractions."""

    def __init__(self, found):
        self.found = found  # [(category, score, (top, left, bottom, right))]
        self.shapes = []

    def get_inputs(self):
        return [FakeInput()]

    def run(self, _outputs, feeds):
        self.shapes.append(feeds["inputs"].shape)
        n = len(self.found)
        boxes = np.zeros((1, 100, 4), np.float32)
        classes = np.zeros((1, 100), np.float32)
        scores = np.zeros((1, 100), np.float32)
        for i, (category, score, box) in enumerate(self.found):
            boxes[0, i], classes[0, i], scores[0, i] = box, category, score
        return boxes, classes, scores, np.array([n], np.float32)


def recognizer(tmp_path, found):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"model")
    session = FakeSession(found)
    return Recognizer(model, session_factory=lambda path: session, installed=lambda: True), session


def test_detect_maps_categories_and_drops_weak_or_unknown(tmp_path):
    rec, session = recognizer(
        tmp_path,
        [(1, 0.9, (0.2, 0.6, 0.9, 0.7)), (3, 0.4, (0, 0, 1, 1)), (62, 0.95, (0, 0, 1, 1)),
         (18, 0.7, (0.5, 0.1, 0.7, 0.3))],
    )  # fmt: skip
    found = rec.detect(np.zeros((288, 512, 3), np.uint8))
    assert [d.kind for d in found] == ["person", "animal"]
    assert found[0].box == (0.6, 0.2, 0.7, 0.9) and found[0].foot == pytest.approx((0.65, 0.9))
    assert session.shapes == [(1, 288, 512, 3)] and rec.last_ms is not None


def test_unavailable_without_model_or_runtime(tmp_path):
    assert Recognizer(None).reason == "Recognition model not installed"
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")
    missing = Recognizer(model, installed=lambda: False)
    assert not missing.available and missing.reason == "onnxruntime not installed"

    def broken(path):
        raise RuntimeError("bad model")

    damaged = Recognizer(model, session_factory=broken, installed=lambda: True)
    assert damaged.detect(np.zeros((2, 2, 3), np.uint8)) == []
    assert not damaged.available and "failed to load" in damaged.reason


def test_worker_takes_one_frame_a_second():
    seen = []

    class Instant:
        def detect(self, frame):
            return [Detection("person", 0.9, (0, 0, 1, 1))]

    worker = RecognitionWorker(Instant(), seen.append)
    worker._thread = object()  # run synchronously below
    assert worker.submit("frame", 100.0)
    assert not worker.submit("frame", 100.5)
    worker.run_once()
    assert seen and worker.runs == 1
    assert worker.submit("frame", 101.0)


class Spy:
    def __init__(self):
        self.calls = []

    def motion(self, active, at=None, trigger="motion"):
        self.calls.append((active, at, trigger))


def make(tmp_path, found, settings=None):
    rec, _ = recognizer(tmp_path, found)
    clock = [1000.0]
    gate = MotionGate(Spy(), clock=lambda: clock[0])
    verifier = MotionVerifier("rtsp://cam/sub", gate, settings or MotionSettings(),
                              clock=lambda: clock[0], recognizer=rec)  # fmt: skip
    verifier.worker._thread = object()  # process submissions synchronously in the test
    return verifier, clock


def test_motion_needs_a_recognised_person_or_vehicle_in_an_area(tmp_path):
    settings = MotionSettings.from_dict({"zones": [RIGHT_HALF]})
    person_on_road = (1, 0.9, (0.3, 0.1, 0.9, 0.2))  # left half: outside the area
    verifier, clock = make(tmp_path, [person_on_road], settings)
    frame = np.zeros((H * 2, W * 2, 3), np.uint8)
    assert "scale=512:288" in " ".join(verifier.command())
    assert verifier.decide(True, frame, clock[0]) == (False, clock[0], None)
    verifier.worker.run_once()
    assert verifier.recognized_at is None

    verifier, clock = make(tmp_path, [(3, 0.8, (0.4, 0.6, 0.9, 0.9))], settings)  # car in area
    verifier.decide(True, frame, clock[0])
    verifier.worker.run_once()
    clock[0] += 1
    assert verifier.decide(True, frame, clock[0]) == (True, clock[0], "vehicle")
    assert verifier.decide(False, frame, clock[0]) == (False, clock[0], "vehicle")
    clock[0] += 20
    assert verifier.decide(True, frame, clock[0])[0] is False  # recognition has gone stale
    assert verifier.status()["recognition"]["last_kind"] == "vehicle"


def test_unwanted_kinds_and_recognition_off(tmp_path):
    dog = (18, 0.9, (0.4, 0.4, 0.9, 0.6))
    verifier, clock = make(tmp_path, [dog])
    frame = np.zeros((H * 2, W * 2, 3), np.uint8)
    verifier.decide(True, frame, clock[0])
    verifier.worker.run_once()
    assert verifier.decide(True, frame, clock[0])[0] is False

    off = MotionSettings.from_dict({"recognize": False})
    verifier, clock = make(tmp_path, [], off)
    assert verifier.decide(True, frame, clock[0]) == (True, clock[0], None)


def test_gate_labels_events_with_the_recognised_kind():
    clock = [1000.0]
    recorder = Spy()
    gate = MotionGate(recorder, clock=lambda: clock[0])

    class Checker:
        enabled = True

        def healthy(self, now):
            return True

    gate.checker = Checker()
    gate.motion(True, 999.0, "tns1:RuleEngine/CellMotionDetector/Motion")
    gate.verdict(True, 1000.5, "person")
    assert recorder.calls == [(True, 999.0, "person")]


def test_recognition_runs_on_a_real_thread(tmp_path):
    rec, _ = recognizer(tmp_path, [(1, 0.9, (0.1, 0.1, 0.9, 0.9))])
    results = []
    worker = RecognitionWorker(rec, results.append)
    worker.submit(np.zeros((288, 512, 3), np.uint8), 1.0)
    deadline = time.time() + 5
    while not results and time.time() < deadline:
        time.sleep(0.01)
    worker.stop()
    assert results and results[0][0].kind == "person"
