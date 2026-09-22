import time

from solstead.events import EventRecorder
from solstead.motion import MotionWatcher
from solstead.onvif import OnvifError, PullPointClient
from tests.test_onvif import FakeCamera


class RecorderSpy(EventRecorder):
    def __init__(self):
        self.calls = []

    def motion(self, active, at=None, trigger="motion"):
        self.calls.append((active, trigger))


def test_step_subscribes_then_pulls_into_recorder():
    camera = FakeCamera()
    recorder = RecorderSpy()
    watcher = MotionWatcher(PullPointClient("http://cam/onvif", "u", "p", poster=camera), recorder)
    watcher.step()
    assert watcher.subscribed and watcher.last_message_unix is not None
    assert recorder.calls == [
        (True, "tns1:RuleEngine/CellMotionDetector/Motion"),
        (False, "tns1:VideoSource/MotionAlarm"),
    ]
    watcher.step()
    assert [c[1] for c in camera.calls].count("CreatePullPointSubscriptionRequest") == 1


def test_run_resubscribes_after_error():
    attempts = []

    def flaky(url, body, timeout):
        attempts.append(url)
        if len(attempts) < 2:
            raise OnvifError("Camera unreachable: timeout")
        return FakeCamera()(url, body, timeout)

    recorder = RecorderSpy()
    watcher = MotionWatcher(
        PullPointClient("http://cam/onvif", "u", "p", poster=flaky), recorder, backoff_seconds=0.05
    )
    watcher.start()
    deadline = time.time() + 3
    while not recorder.calls and time.time() < deadline:
        time.sleep(0.02)
    watcher.stop()
    assert watcher.errors == 1 and watcher.last_error is None  # recovered after one failure
    assert recorder.calls and not watcher.subscribed
