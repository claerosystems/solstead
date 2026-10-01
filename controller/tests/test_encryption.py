"""Recording encryption: enrolment, sealed event clips, sealing older clips, and the API."""

from __future__ import annotations

import http.client
import json

import pytest

from solstead import sealing
from solstead.events import EventStore
from solstead.recipients import RecipientsError, RecipientStore, parse
from solstead.sealed import SealedRecordings
from tests.conftest import TOKEN
from tests.test_api import api, call  # noqa: F401  (fixture)
from tests.test_events import Scenario, make_event


def key():
    identity, recipient = sealing.generate_identity()
    return sealing.parse_identity(identity), recipient


def enrolment(*roles):
    secrets, items = [], []
    for role in roles:
        secret, public = key()
        secrets.append(secret)
        items.append({"role": role, "label": f"{role} key", "public_key": public})
    return secrets, {"recipients": items}


# ---- enrolment rules -------------------------------------------------------------------------


def test_enrolment_needs_a_device_and_exactly_one_recovery_key():
    _, ok = enrolment("device", "recovery")
    assert [r.role for r in parse(ok, now=5)] == ["device", "recovery"]
    _, two_phones = enrolment("device", "device", "recovery")
    assert len(parse(two_phones)) == 3
    for roles in [("device",), ("device", "device"), ("recovery", "recovery", "device")]:
        with pytest.raises(RecipientsError):
            parse(enrolment(*roles)[1])


def test_enrolment_rejects_bad_keys_and_duplicates():
    _, payload = enrolment("device", "recovery")
    bad = json.loads(json.dumps(payload))
    good = bad["recipients"][0]["public_key"]
    bad["recipients"][0]["public_key"] = good[:-1] + ("x" if good[-1] != "x" else "y")
    with pytest.raises(RecipientsError, match="checksum|invalid"):
        parse(bad)
    dup = json.loads(json.dumps(payload))
    dup["recipients"][0]["public_key"] = dup["recipients"][1]["public_key"]
    with pytest.raises(RecipientsError, match="twice"):
        parse(dup)
    for junk in (None, [], {"recipients": "x"}, {"recipients": [1, 2]}):
        with pytest.raises(RecipientsError):
            parse(junk)


def test_store_persists_and_reloads(tmp_path):
    path = tmp_path / "recipients.json"
    store = RecipientStore(path)
    assert not store.enrolled and store.keys() == []
    store.replace(parse(enrolment("device", "recovery")[1]))
    again = RecipientStore(path)
    assert again.enrolled and len(again.keys()) == 2
    assert not path.with_name("recipients.json.partial").exists()


def test_damaged_store_does_not_silently_disable_encryption(tmp_path):
    path = tmp_path / "recipients.json"
    path.write_text("{not json")
    with pytest.raises(ValueError):
        RecipientStore(path)


# ---- recording -------------------------------------------------------------------------------


def record_one(s: Scenario):
    s.fill_buffer(s.now)
    s.recorder.motion(True, s.now)
    s.advance(10)
    s.recorder.motion(False, s.now)
    for _ in range(5):
        events = s.advance(10)
        if events:
            return events[0]
    raise AssertionError("no event")


def test_enrolled_recorder_writes_only_sealed_clips_to_the_ssd(tmp_path):
    (device, device_pub), (recovery, recovery_pub) = key(), key()
    keys = [sealing.parse_recipient(device_pub), sealing.parse_recipient(recovery_pub)]
    s = Scenario(tmp_path, SealedRecordings(lambda: keys, tmp_path / "ram"))
    event = record_one(s)
    assert event.encrypted
    on_ssd = sorted(p.name for p in s.store.folder.iterdir())
    assert on_ssd == [event.id + ".json", event.id + ".mp4.age"]
    assert list((tmp_path / "ram").iterdir()) == []  # plaintext removed from RAM too
    sealed = s.store.clip_path(event).read_bytes()
    assert sealed.startswith(b"age-encryption.org/v1\n")
    assert event.size_bytes == len(sealed)
    expected = b"clip" * len(s.assembled[0])
    assert sealing.open_bytes(sealed, device) == expected
    assert sealing.open_bytes(sealed, recovery) == expected


def test_unenrolled_recorder_still_records_plain_clips(tmp_path):
    s = Scenario(tmp_path, SealedRecordings(list, tmp_path / "ram"))
    event = record_one(s)
    assert not event.encrypted and s.store.clip_path(event).name == event.id + ".mp4"


def test_sealing_failure_is_reported_and_leaves_nothing_behind(tmp_path, monkeypatch):
    def broken(*_):
        raise OSError("disk full")

    monkeypatch.setattr("solstead.sealed.seal_file", broken)
    _, public = key()
    keys = [sealing.parse_recipient(public)]
    s = Scenario(tmp_path, SealedRecordings(lambda: keys, tmp_path / "ram"))
    s.fill_buffer(s.now)
    s.recorder.motion(True, s.now)
    s.advance(10)
    s.recorder.motion(False, s.now)
    for _ in range(5):
        assert s.advance(10) == []
    assert s.recorder.last_error == "Saving clip failed"
    assert not s.store.folder.exists() or list(s.store.folder.iterdir()) == []
    assert list((tmp_path / "ram").iterdir()) == []


def test_older_plain_clips_are_sealed_and_the_file_decides(storage_root):
    secret, public = key()
    keys = [sealing.parse_recipient(public)]
    sealed = SealedRecordings(lambda: keys, storage_root / "ram")
    store = EventStore(storage_root, 10**9, sealed)
    sealed.add_source(store.plain_clips)
    make_event(store, "a", 1000)
    make_event(store, "b", 2000)
    recorded = (store.folder / "a.json").read_text()

    assert sealed.seal_older(budget_bytes=1) == 1  # oldest first, one clip per budget
    assert store.load("a").encrypted and not store.load("b").encrypted
    assert not store.plain_path("a").exists()
    assert sealing.open_bytes(store.sealed_path("a").read_bytes(), secret) == b"v" * 100
    assert store.load("a").size_bytes == store.sealed_path("a").stat().st_size
    assert (store.folder / "a.json").read_text() == recorded  # the JSON is never rewritten
    assert sealed.seal_older(budget_bytes=1) == 1
    assert sealed.seal_older(budget_bytes=1) == 0 and not sealed.pending
    assert store.prune() == [] and len(store.events()) == 2


# ---- controller and API ----------------------------------------------------------------------


def put(server, path, payload):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    body = payload if isinstance(payload, str) else json.dumps(payload)
    conn.request("PUT", path, body=body, headers={"Authorization": "Bearer " + TOKEN})
    response = conn.getresponse()
    data = json.loads(response.read())
    conn.close()
    return response, data


def test_enrol_through_the_api_then_older_clips_are_sealed(api):  # noqa: F811
    response, body = call(api, "GET", "/v1/encryption")
    status = json.loads(body)
    assert response.status == 200 and status["enrolled"] is False
    assert status["format"] == "age-encryption.org/v1"

    make_event(api.controller.store, "20260922T140005-abcdef01", 1790085605, size=1000)
    assert json.loads(call(api, "GET", "/v1/status")[1])["encryption"]["unencrypted_events"] == 1

    response, body = put(api, "/v1/encryption", {"recipients": []})
    assert response.status == 400 and "recipients" in body["error"]
    response, body = put(api, "/v1/encryption", "nope")
    assert response.status == 400

    (device, recovery), payload = enrolment("device", "recovery")
    response, body = put(api, "/v1/encryption", payload)
    assert response.status == 200 and body["enrolled"] is True
    assert [r["role"] for r in body["recipients"]] == ["device", "recovery"]

    api.controller.sealed.seal_older()
    assert api.controller.encryption_status()["unencrypted_events"] == 0
    response, clip = call(api, "GET", "/v1/events/20260922T140005-abcdef01/clip")
    assert response.status == 200
    assert response.getheader("Content-Type") == "application/octet-stream"
    assert sealing.open_bytes(clip, recovery) == b"v" * 1000
    listed = json.loads(call(api, "GET", "/v1/events")[1])["events"][0]
    assert listed["encrypted"] is True and listed["size_bytes"] == len(clip)


def test_legacy_ssd_buffer_is_removed_when_recording_starts(config):
    from solstead.controller import Controller

    legacy = config.storage_path / "buffer"
    legacy.mkdir()
    (legacy / "20260922T140000.ts").write_bytes(b"plain video")
    controller = Controller(config, watcher=None)
    controller.buffer.start = lambda: None
    controller.config = config.__class__(**{**config.__dict__, "camera_onvif_url": "http://c"})
    controller._ensure_events()
    assert not legacy.exists()
    assert controller.buffer.folder == config.buffer_path / "segments"


def test_cli_keygen_then_decrypt(tmp_path, capsys):
    from solstead.cli import main

    assert main(["keygen"]) == 0
    printed = capsys.readouterr().out
    identity_file = tmp_path / "key.txt"
    identity_file.write_text(printed)
    recipient = printed.split("public key: ")[1].split()[0]
    sealed = tmp_path / "clip.mp4.age"
    sealed.write_bytes(sealing.seal_bytes(b"footage", [sealing.parse_recipient(recipient)]))
    out = tmp_path / "clip.mp4"
    assert main(["decrypt", str(identity_file), str(sealed), str(out)]) == 0
    assert out.read_bytes() == b"footage"
    other = tmp_path / "other.txt"
    other.write_text(sealing.generate_identity()[0])
    assert main(["decrypt", str(other), str(sealed), str(tmp_path / "x.mp4")]) == 1
    assert "cannot open" in capsys.readouterr().err


def test_turning_encryption_off_keeps_sealed_clips_and_records_plain(api):  # noqa: F811
    _, payload = enrolment("device", "recovery")
    put(api, "/v1/encryption", payload)
    make_event(api.controller.store, "20260922T140005-abcdef01", 1790085605, size=10)
    api.controller.sealed.seal_older()
    response, body = call(api, "DELETE", "/v1/encryption")
    status = json.loads(body)
    assert response.status == 200 and status["enrolled"] is False and status["recipients"] == []
    assert not api.controller.config.recipients_path.exists()
    assert api.controller.recipients.keys() == []
    assert api.controller.store.events()[0].encrypted  # already sealed stays sealed
    assert call(api, "DELETE", "/v1/encryption")[0].status == 200  # idempotent


# ---- restoring access: re-wrapping older clips for a new phone ----------------------------------


def header_of(sealed: bytes) -> bytes:
    from io import BytesIO

    return sealing.read_header(BytesIO(sealed))[0]


def sealed_event(store, event_id, recipients, data=b"footage" * 20000):
    store.folder.mkdir(parents=True, exist_ok=True)
    sealed = sealing.seal_bytes(data, recipients)
    store.sealed_path(event_id).write_bytes(sealed)
    event = make_event(store, event_id, 1790085605, size=len(sealed))
    (store.folder / (event_id + ".json")).write_text(
        json.dumps({**event.__dict__, "encrypted": True})
    )
    store.plain_path(event_id).unlink()
    return data


def put_raw(server, path, body: bytes):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    conn.request("PUT", path, body=body, headers={"Authorization": "Bearer " + TOKEN})
    response = conn.getresponse()
    data = json.loads(response.read())
    conn.close()
    return response, data


def test_new_phone_gains_access_to_an_older_clip_by_rewrapping_its_header(api):  # noqa: F811
    store = api.controller.store
    (old_phone, old_pub), (recovery, recovery_pub), (new_phone, new_pub) = key(), key(), key()
    old_keys = [sealing.parse_recipient(old_pub), sealing.parse_recipient(recovery_pub)]
    data = sealed_event(store, "e1", old_keys)
    with pytest.raises(sealing.SealingError):
        sealing.open_bytes(store.sealed_path("e1").read_bytes(), new_phone)

    response, header = call(api, "GET", "/v1/events/e1/header")
    assert response.status == 200 and header.startswith(b"age-encryption.org/v1\n")
    assert header.endswith(b"\n") and store.sealed_path("e1").read_bytes().startswith(header)
    assert len(header) < 1024  # only the header travels, never the video

    rewrapped = sealing.rewrap_header(header, recovery, sealing.parse_recipient(new_pub))
    response, body = put_raw(api, "/v1/events/e1/header", rewrapped)
    assert response.status == 200 and body["encrypted"] is True
    sealed = store.sealed_path("e1").read_bytes()
    assert body["size_bytes"] == len(sealed)
    for secret in (new_phone, old_phone, recovery):
        assert sealing.open_bytes(sealed, secret) == data
    assert not list(store.folder.glob("*.partial"))


def test_header_replacement_cannot_drop_keys_or_touch_the_payload(api):  # noqa: F811
    store = api.controller.store
    (_, a_pub), (recovery, recovery_pub), (_, other_pub) = key(), key(), key()
    sealed_event(
        store, "e1", [sealing.parse_recipient(a_pub), sealing.parse_recipient(recovery_pub)]
    )
    before = store.sealed_path("e1").read_bytes()
    header = call(api, "GET", "/v1/events/e1/header")[1]

    # A header for a different file key that lists only the recovery key: drops a recipient.
    only_recovery = header_of(sealing.seal_bytes(b"x", [sealing.parse_recipient(recovery_pub)]))
    response, body = put_raw(api, "/v1/events/e1/header", only_recovery)
    assert response.status == 400 and "every existing key" in body["error"]

    # Trailing payload bytes smuggled after the header are refused.
    grown = sealing.rewrap_header(header, recovery, sealing.parse_recipient(other_pub))
    response, body = put_raw(api, "/v1/events/e1/header", grown + b"extra")
    assert response.status == 400
    response, _ = put_raw(api, "/v1/events/e1/header", b"not a header\n")
    assert response.status == 400
    assert store.sealed_path("e1").read_bytes() == before  # nothing changed on refusal

    make_event(store, "plain", 1790000000)
    assert call(api, "GET", "/v1/events/plain/header")[0].status == 404
    assert put_raw(api, "/v1/events/plain/header", grown)[0].status == 404
    assert call(api, "GET", "/v1/events/missing/header")[0].status == 404


def test_rewrap_refuses_a_key_that_cannot_open_the_file():
    (_, pub), (stranger, _) = key(), key()
    sealed = sealing.seal_bytes(b"x", [sealing.parse_recipient(pub)])
    header = header_of(sealed)
    with pytest.raises(sealing.SealingError, match="cannot open"):
        sealing.rewrap_header(header, stranger, sealing.parse_recipient(pub))


def test_events_list_accepts_a_limit(api):  # noqa: F811
    for i in range(3):
        make_event(api.controller.store, f"e{i}", 1790000000 + i)
    assert len(json.loads(call(api, "GET", "/v1/events?limit=2")[1])["events"]) == 2
    assert len(json.loads(call(api, "GET", "/v1/events")[1])["events"]) == 3


# ---- deleting events --------------------------------------------------------------------------


def test_owner_deletes_plain_and_sealed_events(api):  # noqa: F811
    store = api.controller.store
    make_event(store, "plain", 1790000000)
    _, pub = key()
    sealed_event(store, "sealed", [sealing.parse_recipient(pub)])
    for event_id in ("plain", "sealed"):
        response, body = call(api, "DELETE", f"/v1/events/{event_id}")
        assert response.status == 200 and json.loads(body) == {"deleted": event_id}
    assert list(store.folder.iterdir()) == []
    assert call(api, "DELETE", "/v1/events/plain")[0].status == 404
    assert call(api, "DELETE", "/v1/events/../../etc")[0].status == 404
    assert call(api, "DELETE", "/v1/events/x", token="wrong" * 8)[0].status == 401
    logged = api.controller.health.recent(10, "info")
    assert any(e["message"] == "Event deleted by the owner" for e in logged)


def test_older_event_clips_are_sealed_while_event_recording_is_off(config, ffmpeg):
    """Regression: older event clips were only sealed while motion recording was running."""
    import time

    from solstead.camera import Camera
    from solstead.controller import Controller

    controller = Controller(config, camera=Camera(config.camera_url, runner=ffmpeg))
    assert not config.events_enabled
    make_event(controller.store, "20260922T140005-abcdef01", 1790085605)
    (_, recovery), payload = enrolment("device", "recovery")
    controller.enroll(payload)
    controller.start()
    try:
        deadline = time.time() + 5
        while controller.encryption_status()["unencrypted_events"] and time.time() < deadline:
            time.sleep(0.05)
    finally:
        controller.stop()
    assert not controller.events_running
    assert controller.encryption_status()["unencrypted_events"] == 0
    sealed = controller.store.sealed_path("20260922T140005-abcdef01").read_bytes()
    assert sealing.open_bytes(sealed, recovery) == b"v" * 100
