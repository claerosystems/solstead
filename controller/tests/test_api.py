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
