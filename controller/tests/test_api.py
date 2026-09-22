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


def test_snapshot(api):
    response, body = call(api, "GET", "/v1/camera/snapshot")
    assert response.status == 200 and response.getheader("Content-Type") == "image/jpeg"
    assert body.startswith(b"\xff\xd8")


def test_record_then_list(api):
    response, body = call(api, "POST", "/v1/recordings")
    assert response.status == 201
    clip = json.loads(body)
    response, body = call(api, "GET", "/v1/recordings/")
    assert response.status == 200 and json.loads(body)["recordings"][0]["id"] == clip["id"]


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
