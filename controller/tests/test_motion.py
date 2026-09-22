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
        self.times = getattr(self, "times", []) + [at]


def test_step_subscribes_then_pulls_into_recorder():
    camera = FakeCamera()
    recorder = RecorderSpy()
    watcher = MotionWatcher(PullPointClient("http://cam/onvif", "u", "p", poster=camera), recorder)
    watcher.step()
    assert watcher.subscribed and watcher.last_message_unix is not None
    assert watcher.clock_synced_unix is not None
    assert [c[1] for c in camera.calls][:2] == [
        "SetSystemDateAndTime",
        "CreatePullPointSubscriptionRequest",
    ]
    assert recorder.calls == [
        (True, "tns1:RuleEngine/CellMotionDetector/Motion"),
        (False, "tns1:VideoSource/MotionAlarm"),
    ]
    watcher.step()
    assert [c[1] for c in camera.calls].count("CreatePullPointSubscriptionRequest") == 1


def test_run_resubscribes_after_error():
    attempts = []

    def flaky(url, body, timeout):
        if b"CreatePullPointSubscription" in body:
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


def test_drifted_camera_timestamps_are_replaced_with_receipt_time():
    camera = FakeCamera()  # its sample events are stamped 2026-09-22T14:00Z, far from "now"
    recorder = RecorderSpy()
    watcher = MotionWatcher(PullPointClient("http://cam/onvif", "u", "p", poster=camera), recorder)
    before = time.time()
    watcher.step()
    assert all(before <= at <= time.time() for at in recorder.times)
    assert watcher.last_skew_seconds is not None and abs(watcher.last_skew_seconds) > 5


def test_clock_sync_failure_is_reported_and_retried_later():
    calls = []

    def poster(url, body, timeout):
        calls.append(url)
        if b"SetSystemDateAndTime" in body:
            raise OnvifError("Camera unreachable: timeout")
        return FakeCamera()(url, body, timeout)

    watcher = MotionWatcher(
        PullPointClient("http://cam/onvif", "u", "p", poster=poster), RecorderSpy()
    )
    watcher.step()
    assert watcher.clock_synced_unix is None
    assert watcher.subscribed  # subscription still proceeds
    assert watcher.next_clock_sync > time.time() + 500
