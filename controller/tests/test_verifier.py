import io

import numpy as np
import pytest

from solstead.verifier import (
    FRAME_HEIGHT as H,
)
from solstead.verifier import (
    FRAME_WIDTH as W,
)
from solstead.verifier import (
    FrameDetector,
    MotionGate,
    MotionVerifier,
    largest_object,
    read_exactly,
)
from solstead.zones import MotionSettings

RIGHT_HALF = {"points": [[0.5, 0], [1, 0], [1, 1], [0.5, 1]]}


def ground(colour=True, seed=1):
    """A textured surface: gravel-like noise on a tan (or grey, as under infrared) base."""
    rng = np.random.default_rng(seed)
    texture = rng.integers(-40, 40, size=(H, W, 1))
    base = np.array([170, 150, 120] if colour else [140, 140, 140])
    return np.clip(base + texture, 0, 255).astype(np.uint8)


def with_shadow(image, top, left, height, width, factor=0.6):
    shaded = image.astype(np.float32)
    shaded[top : top + height, left : left + width] *= factor
    return shaded.astype(np.uint8)


def with_object(image, top, left, height=20, width=8, colour=(40, 60, 200)):
    out = image.copy()
    out[top : top + height, left : left + width] = colour
    return out


def warm(detector, image, frames=10):
    for _ in range(frames):
        detector.analyse(image.tobytes())


def run(detector, frames):
    return [detector.analyse(f.tobytes()) for f in frames]


def test_still_scene_is_quiet():
    detector = FrameDetector(MotionSettings())
    image = ground()
    warm(detector, image)
    assert not any(v.active for v in run(detector, [image] * 10))


def test_moving_object_is_seen():
    detector = FrameDetector(MotionSettings())
    image = ground()
    warm(detector, image)
    verdicts = run(detector, [with_object(image, 60, 40 + 6 * i) for i in range(5)])
    assert verdicts[-1].active and verdicts[-1].largest >= 100


def test_object_outside_the_areas_is_ignored():
    detector = FrameDetector(MotionSettings.from_dict({"zones": [RIGHT_HALF]}))
    image = ground()
    warm(detector, image)
    left = run(detector, [with_object(image, 60, 10 + 6 * i) for i in range(5)])
    assert not any(v.active for v in left)
    right = run(detector, [with_object(image, 60, 150 + 6 * i) for i in range(5)])
    assert right[-1].active


@pytest.mark.parametrize("colour", [True, False])
def test_shadow_is_ignored_in_colour_and_infrared(colour):
    image = ground(colour)
    frames = [with_shadow(image, 40, 30 + 8 * i, 50, 60) for i in range(6)]
    filtered = FrameDetector(MotionSettings())
    warm(filtered, image)
    verdicts = run(filtered, frames)
    assert not any(v.active for v in verdicts)
    assert verdicts[-1].shadow > 0.8
    counted = FrameDetector(MotionSettings.from_dict({"ignore_shadows": False}))
    warm(counted, image)
    assert run(counted, frames)[-1].active


def test_dark_person_on_grey_infrared_ground_still_counts():
    image = ground(colour=False)
    detector = FrameDetector(MotionSettings())
    warm(detector, image)
    frames = [with_object(image, 50, 40 + 6 * i, colour=(15, 15, 15)) for i in range(5)]
    assert run(detector, frames)[-1].active


def test_whole_scene_lighting_change_is_dismissed():
    image = ground()
    detector = FrameDetector(MotionSettings())
    warm(detector, image)
    darker = with_shadow(image, 0, 0, H, W, 0.5)
    verdicts = run(detector, [darker] * 2)
    assert verdicts[0].lighting and not any(v.active for v in verdicts)


def test_largest_object_counts_connected_pixels():
    moving = np.zeros((10, 10), dtype=bool)
    moving[1:3, 1:3] = True
    moving[6:9, 6:9] = True
    assert largest_object(moving) == 9


class FakeChecker:
    enabled = True

    def __init__(self):
        self.ok = True

    def healthy(self, now):
        return self.ok


class Spy:
    def __init__(self):
        self.calls = []

    def motion(self, active, at=None, trigger="motion"):
        self.calls.append((active, at, trigger))


def make_gate():
    clock = [1000.0]
    recorder = Spy()
    gate = MotionGate(recorder, clock=lambda: clock[0])
    gate.checker = FakeChecker()
    return gate, recorder, clock


def test_gate_records_confirmed_motion_from_the_camera_trigger():
    gate, recorder, clock = make_gate()
    gate.motion(True, 998.0, "cell")
    assert recorder.calls == []
    clock[0] = 1001.0
    gate.verdict(True)
    assert recorder.calls == [(True, 998.0, "cell")]
    clock[0] = 1003.0
    gate.motion(False, 1003.0)
    gate.verdict(False)
    assert recorder.calls[-1] == (False, 1003.0, "motion")
    assert gate.confirmed == 1 and gate.ignored == 0


def test_gate_ignores_camera_motion_the_checker_does_not_see():
    gate, recorder, clock = make_gate()
    gate.motion(True, 1000.0)
    gate.verdict(False)
    gate.motion(False, 1004.0)
    clock[0] = 1010.0
    gate.tick()
    assert recorder.calls == [] and gate.ignored == 1


def test_gate_ignores_checker_motion_without_a_camera_report():
    gate, recorder, _ = make_gate()
    gate.verdict(True)
    assert recorder.calls == []


def test_gate_passes_reports_through_when_the_checker_is_down():
    gate, recorder, _ = make_gate()
    gate.checker.ok = False
    gate.motion(True, 999.0, "cell")
    gate.motion(False, 1000.0)
    assert recorder.calls == [(True, 999.0, "cell"), (False, 1000.0, "motion")]
    assert gate.passed_through == 1


def test_gate_backdates_no_further_than_the_pre_roll():
    gate, recorder, clock = make_gate()
    gate.motion(True, 900.0)
    clock[0] = 905.0
    gate.motion(True, 905.0)
    clock[0] = 950.0
    gate.motion(True, 950.0)
    gate.verdict(True)
    assert recorder.calls[0][1] >= 940.0


class FakeProcess:
    def __init__(self, data):
        self.stdout = io.BytesIO(data)
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return 0


def test_verifier_reads_frames_and_feeds_the_gate():
    image = ground()
    frames = [image] * 12 + [with_object(image, 60, 40 + 6 * i) for i in range(4)]
    commands = []

    def spawner(command, **kwargs):
        commands.append(command)
        assert kwargs["stderr"] is not None  # never inherit: it can carry credentials
        return FakeProcess(b"".join(f.tobytes() for f in frames))

    gate, recorder, clock = make_gate()
    verifier = MotionVerifier(
        "rtsp://u:p@cam/sub", gate, MotionSettings(), spawner=spawner, clock=lambda: clock[0]
    )
    gate.motion(True, 999.0)
    verifier.read_stream()
    assert "rawvideo" in commands[0] and f"fps=4,scale={W}:{H}" in commands[0]
    assert verifier.frames == 16 and verifier.last_verdict.active
    assert recorder.calls[0][0] is True
    assert verifier.status()["seeing_motion"] is True


def test_read_exactly_handles_short_reads():
    class Trickle:
        def __init__(self):
            self.data = [b"ab", b"c", b""]

        def read(self, n):
            return self.data.pop(0)

    assert read_exactly(Trickle(), 3) == b"abc"
    assert read_exactly(io.BytesIO(b"ab"), 3) is None
