"""Exercise the HTTP handler over plain HTTP; TLS wrapping is covered in test_server."""

from __future__ import annotations

import http.client
import json
from http.server import ThreadingHTTPServer
from threading import Thread

import pytest

from solstead.api import Handler
from solstead.camera import Camera
from solstead.controller import Controller
from tests.conftest import TOKEN


@pytest.fixture
def api(config, ffmpeg):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.controller = Controller(config, camera=Camera(config.camera_url, runner=ffmpeg))
    Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def call(server, method, path, token=TOKEN):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    headers = {"Authorization": "Bearer " + token} if token else {}
    conn.request(method, path, headers=headers)
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response, body


def test_requires_token(api):
    response, body = call(api, "GET", "/v1/status", token=None)
    assert response.status == 401 and json.loads(body)["error"] == "Owner token required"
    response, _ = call(api, "GET", "/v1/status", token="wrong" * 8)
    assert response.status == 401


def test_status(api):
    response, body = call(api, "GET", "/v1/status")
    assert response.status == 200
    assert response.getheader("Cache-Control") == "no-store"
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert json.loads(body)["storage"]["ready"] is True


def test_snapshot(api, ffmpeg):
    response, body = call(api, "GET", "/v1/camera/snapshot")
    assert response.status == 200 and response.getheader("Content-Type") == "image/jpeg"
    assert body.startswith(b"\xff\xd8") and "scale=960:-2" in ffmpeg.calls[-1]
    response, body = call(api, "GET", "/v1/camera/snapshot?width=0")
    assert response.status == 200 and "-vf" not in ffmpeg.calls[-1]
    response, _ = call(api, "GET", "/v1/camera/snapshot?width=huge")
    assert response.status == 400
    response, _ = call(api, "GET", "/v1/camera/snapshot?width=9000")
    assert response.status == 400


def test_record_then_list(api):
    response, body = call(api, "POST", "/v1/recordings")
    assert response.status == 201
    clip = json.loads(body)
    response, body = call(api, "GET", "/v1/recordings/")
    assert response.status == 200 and json.loads(body)["recordings"][0]["id"] == clip["id"]


def test_recording_file_with_ranges(api):
    clip = json.loads(call(api, "POST", "/v1/recordings")[1])
    path = api.controller.config.storage_path / "manual" / clip["id"]
    response, body = call(api, "GET", f"/v1/recordings/{clip['id']}")
    assert response.status == 200 and response.getheader("Content-Type") == "video/mp4"
    assert response.getheader("Accept-Ranges") == "bytes" and body == path.read_bytes()

    conn = http.client.HTTPConnection("127.0.0.1", api.server_address[1], timeout=5)
    conn.request(
        "GET",
        f"/v1/recordings/{clip['id']}",
        headers={"Authorization": "Bearer " + TOKEN, "Range": "bytes=0-3"},
    )
    response = conn.getresponse()
    assert response.status == 206 and response.read() == path.read_bytes()[:4]
    conn.close()


def test_recording_file_refuses_unknown_and_unsafe_ids(api, config):
    folder = config.storage_path / "manual"
    folder.mkdir()
    (folder / "x.mp4.partial").write_bytes(b"half")
    (config.storage_path / "secret.mp4").write_bytes(b"outside")
    (folder / "link.mp4").symlink_to(config.storage_path / "secret.mp4")
    for path in (
        "/v1/recordings/missing.mp4",
        "/v1/recordings/x.mp4.partial",
        "/v1/recordings/link.mp4",
        "/v1/recordings/..%2Fsecret.mp4",
        "/v1/recordings/../secret.mp4",
        "/v1/recordings/notes.txt",
    ):
        response, _ = call(api, "GET", path)
        assert response.status == 404, path
    assert call(api, "GET", "/v1/recordings/x.mp4", token=None)[0].status == 401


def test_unknown_route(api):
    response, _ = call(api, "GET", "/v1/nope")
    assert response.status == 404
    response, _ = call(api, "POST", "/v1/status")
    assert response.status == 404


def test_camera_failure_is_503(api, ffmpeg):
    ffmpeg.returncode = 1
    response, body = call(api, "GET", "/v1/camera/snapshot")
    assert response.status == 503 and "secret" not in body.decode()


def test_events_list_and_clip_with_ranges(api, config):
    from tests.test_events import make_event

    store = api.controller.store
    event = make_event(store, "20260922T140005-abcdef01", 1790085605, size=1000)
    response, body = call(api, "GET", "/v1/events")
    assert response.status == 200
    listed = json.loads(body)["events"]
    assert listed[0]["id"] == event.id and listed[0]["duration_seconds"] == 50

    response, body = call(api, "GET", f"/v1/events/{event.id}/clip")
    assert response.status == 200 and response.getheader("Content-Type") == "video/mp4"
    assert response.getheader("Accept-Ranges") == "bytes" and len(body) == 1000

    conn = http.client.HTTPConnection("127.0.0.1", api.server_address[1], timeout=5)
    conn.request(
        "GET",
        f"/v1/events/{event.id}/clip",
        headers={"Authorization": "Bearer " + TOKEN, "Range": "bytes=10-19"},
    )
    response = conn.getresponse()
    assert response.status == 206 and response.getheader("Content-Range") == "bytes 10-19/1000"
    assert len(response.read()) == 10
    conn.request(
        "GET",
        f"/v1/events/{event.id}/clip",
        headers={"Authorization": "Bearer " + TOKEN, "Range": "bytes=990-"},
    )
    response = conn.getresponse()
    assert response.status == 206 and len(response.read()) == 10
    conn.request(
        "GET",
        f"/v1/events/{event.id}/clip",
        headers={"Authorization": "Bearer " + TOKEN, "Range": "bytes=5000-"},
    )
    response = conn.getresponse()
    assert response.status == 416 and response.read() == b""
    conn.request(
        "HEAD", f"/v1/events/{event.id}/clip", headers={"Authorization": "Bearer " + TOKEN}
    )
    response = conn.getresponse()
    assert (
        response.status == 200
        and response.getheader("Content-Length") == "1000"
        and response.read() == b""
    )
    conn.close()

    response, _ = call(api, "GET", "/v1/events/../../etc/passwd/clip")
    assert response.status == 404
    response, _ = call(api, "GET", "/v1/events/missing/clip")
    assert response.status == 404


def test_status_reports_events_block(api):
    response, body = call(api, "GET", "/v1/status")
    status = json.loads(body)
    assert status["recording_mode"] == "manual"
    assert status["events"]["enabled"] is False and status["events"]["reason"]


def test_live_routes(api, tmp_path):
    from solstead.live import LiveStream
    from tests.test_live import FakeHLS

    api.controller.live = LiveStream(
        "rtsp://cam/sub", "rtsp://cam/main", tmp_path / "live", spawner=FakeHLS()
    )
    response, body = call(api, "POST", "/v1/live?quality=medium")
    assert response.status == 400
    response, body = call(api, "POST", "/v1/live?quality=low")
    assert response.status == 200
    info = json.loads(body)
    assert info["active"] and info["ready"] and info["playlist"] == "/v1/live/index.m3u8"
    response, body = call(api, "GET", "/v1/live/index.m3u8")
    assert (
        response.status == 200
        and response.getheader("Content-Type") == "application/vnd.apple.mpegurl"
    )
    assert response.getheader("Cache-Control") == "no-store" and b"seg_00001.m4s" in body
    response, body = call(api, "GET", "/v1/live/init.mp4")
    assert (
        response.status == 200
        and response.getheader("Content-Type") == "video/mp4"
        and body == b"init"
    )
    response, body = call(api, "GET", "/v1/live/seg_00002.m4s")
    assert response.status == 200 and response.getheader("Content-Type") == "video/iso.segment"
    response, _ = call(api, "GET", "/v1/live/../../etc/passwd")
    assert response.status == 404
    response, body = call(api, "GET", "/v1/status")
    assert json.loads(body)["live"]["active"] is True
    response, body = call(api, "DELETE", "/v1/live")
    assert response.status == 200 and json.loads(body)["active"] is False
    response, _ = call(api, "GET", "/v1/live/index.m3u8")
    assert response.status == 404


def test_live_without_camera_is_503(api):
    api.controller.live.urls = {"low": None, "high": None}
    response, body = call(api, "POST", "/v1/live")
    assert response.status == 503 and "not configured" in json.loads(body)["error"]


def test_timelapse_routes(api, config, tmp_path):
    from datetime import datetime

    from solstead.timelapse import TimeLapse, TimeLapseSettings

    stamp = datetime(2026, 9, 22, 9, 0).timestamp()
    lapse = TimeLapse(
        config.storage_path, TimeLapseSettings(), lambda w: b"\xff\xd8jpg", clock=lambda: stamp
    )

    def runner(args, capture_output, timeout):
        __import__("pathlib").Path(args[-1]).write_bytes(b"mp4")
        return __import__("subprocess").CompletedProcess(args, 0, b"", b"")

    lapse.runner = runner
    api.controller.timelapse = lapse
    lapse.tick(stamp)

    response, body = call(api, "GET", "/v1/timelapse")
    status = json.loads(body)
    assert (
        response.status == 200
        and status["frames"] == 1
        and status["days"][0]["date"] == "2026-09-22"
    )
    assert status["interval_seconds"] == 300 and status["frames_per_day"] == 144

    response, body = call(api, "GET", "/v1/timelapse/2026-09-22")
    assert response.status == 200 and json.loads(body)["frames"][0]["name"] == "090000.jpg"
    response, body = call(api, "GET", "/v1/timelapse/2026-09-22/090000.jpg")
    assert (
        response.status == 200
        and response.getheader("Content-Type") == "image/jpeg"
        and body == b"\xff\xd8jpg"
    )
    response, body = call(api, "GET", "/v1/timelapse/2026-09-22/video.mp4")
    assert (
        response.status == 200
        and response.getheader("Content-Type") == "video/mp4"
        and body == b"mp4"
    )
    response, _ = call(api, "GET", "/v1/timelapse/2026-09-23")
    assert response.status == 404
    response, _ = call(api, "GET", "/v1/timelapse/2026-09-23/video.mp4")
    assert response.status == 400

    conn = http.client.HTTPConnection("127.0.0.1", api.server_address[1], timeout=5)
    conn.request("PUT", "/v1/timelapse", body=json.dumps({"interval_seconds": 60, "window": "08:00-16:00"}),
                 headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})  # fmt: skip
    response = conn.getresponse()
    updated = json.loads(response.read())
    assert (
        response.status == 200
        and updated["interval_seconds"] == 60
        and updated["frames_per_day"] == 480
    )
    conn.request("PUT", "/v1/timelapse", body=json.dumps({"interval_seconds": 1}),
                 headers={"Authorization": "Bearer " + TOKEN})  # fmt: skip
    response = conn.getresponse()
    assert response.status == 400 and "interval" in json.loads(response.read())["error"]
    conn.request("PUT", "/v1/timelapse", body="nope", headers={"Authorization": "Bearer " + TOKEN})
    response = conn.getresponse()
    assert response.status == 400
    conn.close()
    assert json.loads(call(api, "GET", "/v1/status")[1])["timelapse"]["interval_seconds"] == 60
    if config.settings_path:
        assert json.loads(config.settings_path.read_text())["interval_seconds"] == 60


def test_health_route(api):
    api.controller.health.record("warning", "camera", "Stream ended")
    api.controller.health.record("info", "events", "Event recorded")
    response, body = call(api, "GET", "/v1/health?level=warning&limit=10")
    entries = json.loads(body)["entries"]
    assert response.status == 200 and [e["kind"] for e in entries] == ["camera"]
    response, body = call(api, "GET", "/v1/health")
    assert len(json.loads(body)["entries"]) == 2
    response, _ = call(api, "GET", "/v1/health?level=debug")
    assert response.status == 400


def test_telemetry_history_and_retention(api, config):
    response, body = call(api, "GET", "/v1/telemetry?range=7d")
    history = json.loads(body)
    assert response.status == 200
    assert history["range"] == "7d" and history["bucket_seconds"] == 1800
    assert history["points"] == []
    response, body = call(api, "GET", "/v1/telemetry?range=forever")
    assert response.status == 400 and "range" in json.loads(body)["error"]

    conn = http.client.HTTPConnection("127.0.0.1", api.server_address[1], timeout=5)
    conn.request("PUT", "/v1/telemetry", body=json.dumps({"retention_days": 90}),
                 headers={"Authorization": "Bearer " + TOKEN})  # fmt: skip
    response = conn.getresponse()
    status = json.loads(response.read())
    assert response.status == 200 and status["retention_days"] == 90
    conn.request("PUT", "/v1/telemetry", body=json.dumps({"retention_days": 10}),
                 headers={"Authorization": "Bearer " + TOKEN})  # fmt: skip
    response = conn.getresponse()
    assert response.status == 400 and "retention_days" in json.loads(response.read())["error"]
    conn.close()
    assert json.loads(config.telemetry_settings_path.read_text()) == {"retention_days": 90}
    _, body = call(api, "GET", "/v1/status")
    assert json.loads(body)["telemetry"]["retention_days"] == 90


def test_charger_endpoints(api, config):
    from tests.test_victron import ADDRESS, KEY, OTHER_KEY, broadcast, plain

    response, body = call(api, "GET", "/v1/charger")
    assert response.status == 200 and json.loads(body)["configured"] is False
    api.controller.charger.handle(ADDRESS, "SmartSolar", -60, broadcast(plain()))
    status = json.loads(call(api, "GET", "/v1/charger")[1])
    assert status["seen"][0]["address"] == ADDRESS

    def put(payload):
        conn = http.client.HTTPConnection("127.0.0.1", api.server_address[1], timeout=5)
        conn.request("PUT", "/v1/charger", body=json.dumps(payload),
                     headers={"Authorization": "Bearer " + TOKEN})  # fmt: skip
        response = conn.getresponse()
        result = response.status, json.loads(response.read())
        conn.close()
        return result

    code, result = put({"address": ADDRESS, "key": OTHER_KEY.hex()})
    assert code == 400 and "does not match" in result["error"]
    code, result = put({"address": ADDRESS, "key": "short"})
    assert code == 400
    code, result = put({"address": ADDRESS, "key": KEY.hex()})
    assert code == 200 and result["configured"] and result["reading"]["battery_v"] == 12.77
    assert KEY.hex() not in json.dumps(result)
    assert json.loads(config.charger_settings_path.read_text())["address"] == ADDRESS
    response, body = call(api, "DELETE", "/v1/charger")
    assert response.status == 200 and json.loads(body)["configured"] is False
