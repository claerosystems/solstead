import json

from solstead.buffer import SegmentBuffer
from solstead.clips import ClipError
from solstead.events import Event, EventRecorder, EventStore
from tests.test_buffer import make_segment


def make_event(store, event_id, started, size=100):
    store.folder.mkdir(parents=True, exist_ok=True)
    store.plain_path(event_id).write_bytes(b"v" * size)
    event = Event(
        event_id, started, started + 10, started - 10, started + 40, size, "motion", started + 40
    )
    (store.folder / (event_id + ".json")).write_text(json.dumps(event.__dict__))
    return event


def test_store_lists_newest_first_and_prunes_oldest(storage_root):
    store = EventStore(storage_root, allocation_bytes=2500)
    for i, started in enumerate([1000, 3000, 2000]):
        make_event(store, f"e{i}", started, size=1000)
    assert [e.id for e in store.events()] == ["e1", "e2", "e0"]
    assert store.used_bytes() > 2500
    assert store.prune() == ["e0"]
    assert [e.id for e in store.events()] == ["e1", "e2"]
    assert store.used_bytes() <= 2500  # clips plus their sidecars


def test_store_load_rejects_paths(storage_root):
    store = EventStore(storage_root, 10**9)
    make_event(store, "ok", 1)
    assert store.load("ok").id == "ok"
    assert store.load("../ok") is None and store.load("nope") is None
    assert store.load("a/b") is None


def test_event_to_dict_has_duration():
    event = Event("x", 100, 110, 90, 140, 5, "motion", 141)
    assert event.to_dict()["duration_seconds"] == 50


class Scenario:
    """Recorder with a fake clock, a directory-backed buffer and a fake assembler."""

    def __init__(self, tmp_path, sealed=None, **kwargs):
        self.now = 1_000_000.0
        self.buffer = SegmentBuffer(
            "rtsp://cam/1", tmp_path / "buf", segment_seconds=2, keep_seconds=600
        )
        self.store = EventStore(tmp_path / "ssd", 10**9, sealed)
        self.assembled = []
        self.fail = False
        self.recorder = EventRecorder(
            self.buffer, self.store, pre_seconds=10, post_seconds=30, hold_seconds=10,
            max_seconds=120, assembler=self.assemble, clock=lambda: self.now, **kwargs,
        )  # fmt: skip

    def assemble(self, segments, output):
        if self.fail:
            raise ClipError("boom")
        self.assembled.append(segments)
        output.write_bytes(b"clip" * len(segments))

    def fill_buffer(self, until):
        """Segments every 2 s from 200 s ago up to `until`."""
        start = self.now - 200
        while start <= until:
            make_segment(self.buffer.folder, start)
            start += 2

    def advance(self, seconds):
        self.now += seconds
        self.fill_buffer(self.now)
        return self.recorder.tick(self.now)


def test_isolated_event_produces_fifty_second_clip(tmp_path):
    s = Scenario(tmp_path)
    s.fill_buffer(s.now)
    s.recorder.motion(True, s.now, "tns1:RuleEngine/CellMotionDetector/Motion")
    assert s.recorder.active
    assert s.advance(10) == []
    s.recorder.motion(False, s.now)
    assert s.advance(1) == []  # post-roll running; new activity would extend this event
    assert s.recorder.active and not s.recorder.pending
    assert s.advance(20) == []
    events = s.advance(12)  # 30 s after the activity ended: closed and assembled
    assert not s.recorder.active
    assert len(events) == 1
    event = events[0]
    assert event.ended_unix - event.started_unix == 10
    assert event.trigger.endswith("Motion")
    assert 48 <= event.duration_seconds <= 52
    assert event.clip_from_unix <= event.started_unix - 10 < event.clip_from_unix + 2
    assert s.store.events()[0] == event and s.store.clip_path(event).exists()
    assert s.recorder.last_event_unix == int(s.now)


def test_repeated_activity_extends_one_event(tmp_path):
    s = Scenario(tmp_path)
    s.fill_buffer(s.now)
    s.recorder.motion(True, s.now)
    s.advance(5)
    s.recorder.motion(False, s.now)
    s.advance(10)
    s.recorder.motion(True, s.now)  # within the 30 s post-roll: same event
    s.advance(5)
    s.recorder.motion(False, s.now)
    events = []
    for _ in range(10):
        events += s.advance(5)
    assert len(events) == 1
    assert events[0].ended_unix - events[0].started_unix == 20


def test_on_only_camera_ends_after_hold(tmp_path):
    s = Scenario(tmp_path)
    s.fill_buffer(s.now)
    s.recorder.motion(True, s.now)
    events = []
    for _ in range(12):
        events += s.advance(5)
    assert len(events) == 1
    assert events[0].ended_unix - events[0].started_unix == 10  # hold_seconds


def test_stuck_detector_is_cut_at_max_seconds(tmp_path):
    s = Scenario(tmp_path)
    s.fill_buffer(s.now)
    events = []
    for _ in range(60):
        s.recorder.motion(True, s.now)
        events += s.advance(5)
    s.recorder.motion(False, s.now)
    for _ in range(12):
        events += s.advance(5)
    assert len(events) >= 3
    assert all(e.ended_unix - e.started_unix <= 120 for e in events)


def test_assembly_failure_is_reported_not_fatal(tmp_path):
    s = Scenario(tmp_path)
    s.fill_buffer(s.now)
    s.fail = True
    s.recorder.motion(True, s.now)
    s.advance(3)
    s.recorder.motion(False, s.now)
    events = []
    for _ in range(10):
        events += s.advance(5)
    assert events == [] and s.recorder.last_error == "boom"
    assert not list((tmp_path / "ssd" / "events").glob("*.partial"))


def test_stalled_buffer_gives_up_waiting(tmp_path):
    s = Scenario(tmp_path)
    s.fill_buffer(s.now)
    s.recorder.motion(True, s.now)
    s.now += 3
    s.recorder.tick(s.now)
    s.recorder.motion(False, s.now)
    # No new segments arrive; after clip_to + 2*segment + 5 s the recorder assembles what exists.
    s.now += 30
    assert s.recorder.tick(s.now) == []
    s.now += 10
    events = s.recorder.tick(s.now)
    assert len(events) == 1 and events[0].clip_to_unix <= int(events[0].ended_unix + 30)


def test_recorder_holds_buffer_while_event_open_or_pending(tmp_path):
    s = Scenario(tmp_path)
    s.fill_buffer(s.now)
    assert s.buffer.hold_from is None
    s.recorder.motion(True, s.now)
    assert s.buffer.hold_from == s.now - 10
    t0 = s.now
    s.advance(5)
    s.recorder.motion(False, s.now)
    s.now += 30
    s.recorder.tick(s.now)  # closed but pending: no new footage arrived yet
    assert s.recorder.pending and s.buffer.hold_from == t0 - 10
    s.fill_buffer(s.now)
    assert len(s.recorder.tick(s.now)) == 1
    assert s.buffer.hold_from is None
