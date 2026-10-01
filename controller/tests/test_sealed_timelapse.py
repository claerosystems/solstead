"""Sealing time-lapse frames and manual test clips once recording keys are enrolled."""

from __future__ import annotations

import base64
import json
from io import BytesIO

import pytest

from solstead import sealing
from solstead.timelapse import TimeLapse, TimeLapseSealed, TimeLapseSettings
from tests.test_api import api, call  # noqa: F401  (fixture)
from tests.test_encryption import enrolment, key, put
from tests.test_timelapse import Capturer, local


def sealed_lapse(tmp_path, recipients):
    now = {"t": local(9)}
    lapse = TimeLapse(
        tmp_path, TimeLapseSettings(), Capturer(), clock=lambda: now["t"],
        recipients=lambda: recipients,
    )  # fmt: skip
    return lapse, now


def capture_at(lapse, now, *hours_minutes):
    for hour, minute in hours_minutes:
        now["t"] = local(hour, minute)
        assert lapse.tick() is not None


# ---- capture ---------------------------------------------------------------------------------


def test_enrolled_frames_are_sealed_one_file_each_and_never_written_plain(tmp_path):
    secret, public = key()
    lapse, now = sealed_lapse(tmp_path, [sealing.parse_recipient(public)])
    capture_at(lapse, now, (9, 0), (9, 5))
    folder = tmp_path / "timelapse" / "2026-09-22"
    assert sorted(p.name for p in folder.iterdir()) == ["090000.jpg.age", "090500.jpg.age"]
    frame = sealing.open_bytes((folder / "090000.jpg.age").read_bytes(), secret)
    assert frame.startswith(b"\xff\xd8") and len(frame) == 1000

    frames = lapse.frames("2026-09-22")
    assert [(f.name, f.encrypted) for f in frames] == [
        ("090000.jpg.age", True), ("090500.jpg.age", True),
    ]  # fmt: skip
    assert frames[0].unix == int(local(9))
    assert lapse.days() == [
        {"date": "2026-09-22", "frames": 2, "bytes": sum(f.size_bytes for f in frames),
         "encrypted_frames": 2},
    ]  # fmt: skip
    assert lapse.frame_path("2026-09-22", "090000.jpg.age") == folder / "090000.jpg.age"
    assert lapse.frame_path("2026-09-22", "090000.jpg") is None
    # The schedule resumes from sealed frames after a restart.
    assert lapse.newest_unix() == int(local(9, 5))


def test_unenrolled_frames_stay_plain(tmp_path):
    lapse, now = sealed_lapse(tmp_path, [])
    capture_at(lapse, now, (9, 0))
    assert lapse.frames("2026-09-22")[0].name == "090000.jpg"
    assert lapse.days()[0]["encrypted_frames"] == 0


def test_a_day_with_any_sealed_frame_is_not_exported_on_the_controller(tmp_path):
    _, public = key()
    keys = []
    lapse, now = sealed_lapse(tmp_path, keys)
    capture_at(lapse, now, (9, 0))
    keys.append(sealing.parse_recipient(public))  # encryption turned on mid-day
    capture_at(lapse, now, (9, 5))
    lapse.runner = lambda *a, **k: pytest.fail("ffmpeg must not run on sealed frames")
    with pytest.raises(TimeLapseSealed):
        lapse.export_video("2026-09-22")


def test_prune_counts_sealed_frames(tmp_path):
    _, public = key()
    lapse, now = sealed_lapse(tmp_path, [sealing.parse_recipient(public)])
    capture_at(lapse, now, (9, 0), (9, 5), (9, 10))
    size = lapse.frames("2026-09-22")[0].size_bytes
    lapse.settings = TimeLapseSettings(allocation_bytes=2 * size)
    assert lapse.prune() == 1
    assert [f.name for f in lapse.frames("2026-09-22")] == ["090500.jpg.age", "091000.jpg.age"]


# ---- older frames --------------------------------------------------------------------------


def test_older_plain_frames_are_sealed_in_batches(tmp_path):
    secret, public = key()
    keys = []
    lapse, now = sealed_lapse(tmp_path, keys)
    capture_at(lapse, now, (9, 0), (9, 5), (9, 10))
    folder = tmp_path / "timelapse" / "2026-09-22"
    plain = (folder / "090000.jpg").read_bytes()
    # A crash between writing the sealed copy and deleting the plain one leaves both.
    recipients = [sealing.parse_recipient(public)]
    (folder / "091000.jpg.age").write_bytes(sealing.seal_bytes(b"sealed earlier", recipients))

    assert lapse.seal_plain(recipients, limit=2) == 2
    assert lapse.seal_plain(recipients, limit=2) == 1
    assert lapse.seal_plain(recipients, limit=2) == 0
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["090000.jpg.age", "090500.jpg.age", "091000.jpg.age"]
    assert sealing.open_bytes((folder / "090000.jpg.age").read_bytes(), secret) == plain
    assert sealing.open_bytes((folder / "091000.jpg.age").read_bytes(), secret) == (
        b"sealed earlier"
    )


# ---- controller and API --------------------------------------------------------------------


def lapse_on(api):  # noqa: F811
    now = {"t": local(9)}
    controller = api.controller
    lapse = TimeLapse(
        controller.config.storage_path, TimeLapseSettings(), Capturer(),
        runner=controller.camera.runner, clock=lambda: now["t"],
        recipients=controller.recipients.keys,
    )  # fmt: skip
    controller.timelapse = lapse
    return lapse, now


def test_enrolment_seals_older_frames_and_clips_and_drops_plain_exports(api):  # noqa: F811
    controller = api.controller
    lapse, now = lapse_on(api)
    capture_at(lapse, now, (9, 0), (9, 5))
    assert call(api, "GET", "/v1/timelapse/2026-09-22/video.mp4")[0].status == 200
    assert list(lapse.exports.iterdir())
    clip = json.loads(call(api, "POST", "/v1/recordings")[1])
    assert clip["encrypted"] is False and clip["id"].endswith(".mp4")
    status = json.loads(call(api, "GET", "/v1/encryption")[1])
    assert status["unencrypted_frames"] == 2

    (device, recovery), payload = enrolment("device", "recovery")
    assert put(api, "/v1/encryption", payload)[0].status == 200
    assert not list(lapse.exports.iterdir())  # plaintext videos go at enrolment

    controller._seal_older_frames_and_clips()  # frames first
    assert controller.encryption_status()["unencrypted_frames"] == 0
    controller._seal_older_frames_and_clips()  # then the manual clip
    listed = json.loads(call(api, "GET", "/v1/recordings")[1])["recordings"]
    assert [(c["id"], c["encrypted"]) for c in listed] == [(clip["id"] + ".age", True)]
    assert listed[0]["created_unix"] == clip["created_unix"]
    manual = controller.config.storage_path / "manual" / listed[0]["id"]
    assert sealing.open_bytes(manual.read_bytes(), device).startswith(b"\x00\x00\x00\x1cftyp")
    for listed_id in (listed[0]["id"], clip["id"]):  # the id the phone listed before sealing too
        response, body = call(api, "GET", f"/v1/recordings/{listed_id}")
        assert response.status == 200
        assert response.getheader("Content-Type") == "application/octet-stream"
        assert body == manual.read_bytes()  # served as-is; the phone decrypts
    assert controller._backlog is True
    controller._seal_older_frames_and_clips()
    assert controller._backlog is False  # nothing left: stop scanning every tick

    response, body = call(api, "GET", "/v1/timelapse/2026-09-22/video.mp4")
    assert response.status == 409 and json.loads(body)["encrypted"] is True


def test_enrolled_manual_clip_is_assembled_in_ram_and_sealed(api):  # noqa: F811
    controller = api.controller
    (device, _), payload = enrolment("device", "recovery")
    put(api, "/v1/encryption", payload)
    response, body = call(api, "POST", "/v1/recordings")
    clip = json.loads(body)
    assert response.status == 201 and clip["encrypted"] is True
    assert clip["id"].endswith(".mp4.age")
    folder = controller.config.storage_path / "manual"
    assert [p.name for p in folder.iterdir()] == [clip["id"]]
    assert sealing.open_bytes((folder / clip["id"]).read_bytes(), device).startswith(
        b"\x00\x00\x00\x1cftyp"
    )
    # ffmpeg wrote the plaintext into the RAM work folder, which is emptied afterwards.
    written = controller.camera.runner.calls[-1][-1]
    assert written.startswith(str(controller.work_folder))
    assert not list(controller.work_folder.iterdir())


def test_sealed_frames_are_served_as_opaque_files(api):  # noqa: F811
    lapse, now = lapse_on(api)
    (device, _), payload = enrolment("device", "recovery")
    put(api, "/v1/encryption", payload)
    capture_at(lapse, now, (9, 0))
    day = json.loads(call(api, "GET", "/v1/timelapse")[1])["days"][0]
    assert day["encrypted_frames"] == 1
    frames = json.loads(call(api, "GET", "/v1/timelapse/2026-09-22")[1])["frames"]
    assert frames[0]["name"] == "090000.jpg.age" and frames[0]["encrypted"] is True
    response, body = call(api, "GET", "/v1/timelapse/2026-09-22/090000.jpg.age")
    assert response.status == 200
    assert response.getheader("Content-Type") == "application/octet-stream"
    assert sealing.open_bytes(body, device).startswith(b"\xff\xd8")
    assert call(api, "GET", "/v1/timelapse/2026-09-22/090000.jpg")[0].status == 404


def test_new_phone_rewraps_a_days_frame_headers_in_one_batch(api):  # noqa: F811
    lapse, now = lapse_on(api)
    (old_phone, recovery), payload = enrolment("device", "recovery")
    put(api, "/v1/encryption", payload)
    capture_at(lapse, now, (9, 0), (9, 5))
    new_phone, new_pub = key()

    response, body = call(api, "GET", "/v1/timelapse/2026-09-22/headers")
    assert response.status == 200
    headers = json.loads(body)["headers"]
    assert sorted(headers) == ["090000.jpg.age", "090500.jpg.age"]
    rewrapped = {
        name: base64.b64encode(
            sealing.rewrap_header(base64.b64decode(h), recovery, sealing.parse_recipient(new_pub))
        ).decode()
        for name, h in headers.items()
    }
    response, body = put(api, "/v1/timelapse/2026-09-22/headers", {"headers": rewrapped})
    assert response.status == 200 and body["replaced"] == 2
    folder = lapse.folder / "2026-09-22"
    for name in headers:
        sealed = (folder / name).read_bytes()
        for secret in (new_phone, old_phone, recovery):
            assert sealing.open_bytes(sealed, secret).startswith(b"\xff\xd8")
    assert not list(folder.glob("*.partial"))

    # A frame pruned meanwhile is skipped rather than resurrected.
    (folder / "090500.jpg.age").unlink()
    response, body = put(api, "/v1/timelapse/2026-09-22/headers", {"headers": rewrapped})
    assert response.status == 200 and body["replaced"] == 1
    assert not (folder / "090500.jpg.age").exists()


def test_frame_header_replacement_is_checked(api):  # noqa: F811
    lapse, now = lapse_on(api)
    _, payload = enrolment("device", "recovery")
    put(api, "/v1/encryption", payload)
    capture_at(lapse, now, (9, 0))
    before = (lapse.folder / "2026-09-22" / "090000.jpg.age").read_bytes()
    _, other = key()
    dropped = sealing.seal_bytes(b"x", [sealing.parse_recipient(other)])
    dropped_header = sealing.read_header(BytesIO(dropped))[0]
    path = "/v1/timelapse/2026-09-22/headers"
    for payload, message in [
        ({"headers": {"090000.jpg.age": base64.b64encode(dropped_header).decode()}}, "keep"),
        ({"headers": {"090000.jpg.age": "not base64!"}}, "base64"),
        ({"headers": {"../x.jpg.age": "AAAA"}}, "frame name"),
        ({"headers": ["nope"]}, "headers must"),
    ]:
        response, body = put(api, path, payload)
        assert response.status == 400 and message in body["error"], body
    assert (lapse.folder / "2026-09-22" / "090000.jpg.age").read_bytes() == before
