"""Recording encryption: enrolment, sealed event clips, sealing older clips, and the API."""

from __future__ import annotations

import http.client
import json

import pytest

from solstead import sealing
from solstead.recipients import RecipientsError, RecipientStore, parse
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
    bad["recipients"][0]["public_key"] = bad["recipients"][0]["public_key"][:-1] + "x"
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
    s = Scenario(tmp_path, work_folder=tmp_path / "ram", recipients=lambda: keys)
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
    s = Scenario(tmp_path, work_folder=tmp_path / "ram")
    event = record_one(s)
    assert not event.encrypted and s.store.clip_path(event).name == event.id + ".mp4"


def test_sealing_failure_is_reported_and_leaves_nothing_behind(tmp_path, monkeypatch):
    def broken(*_):
        raise OSError("disk full")

    monkeypatch.setattr("solstead.events.seal_file", broken)
    _, public = key()
    keys = [sealing.parse_recipient(public)]
    s = Scenario(tmp_path, work_folder=tmp_path / "ram", recipients=lambda: keys)
    s.fill_buffer(s.now)
    s.recorder.motion(True, s.now)
    s.advance(10)
    s.recorder.motion(False, s.now)
    for _ in range(5):
        assert s.advance(10) == []
    assert s.recorder.last_error == "Saving clip failed"
    assert not s.store.folder.exists() or list(s.store.folder.iterdir()) == []
    assert list((tmp_path / "ram").iterdir()) == []


def test_older_plain_clips_are_sealed_one_at_a_time(storage_root):
    from solstead.events import EventStore

    store = EventStore(storage_root, 10**9)
    make_event(store, "a", 1000)
    make_event(store, "b", 2000)
    secret, public = key()
    keys = [sealing.parse_recipient(public)]
    first = store.seal_next_plain(keys)
    assert first.encrypted and first.id == "b"
    assert not store.plain_path("b").exists()
    assert sealing.open_bytes(store.sealed_path("b").read_bytes(), secret) == b"v" * 100
    assert store.load("b").encrypted and store.load("b").size_bytes == first.size_bytes
    assert store.seal_next_plain(keys).id == "a"
    assert store.seal_next_plain(keys) is None
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

    api.controller._seal_backlog()
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
