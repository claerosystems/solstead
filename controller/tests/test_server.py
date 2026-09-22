"""Start the real TLS server with a freshly provisioned certificate and talk to it."""

from __future__ import annotations

import hashlib
import http.client
import json
import shutil
import ssl
from dataclasses import replace
from threading import Thread

import pytest

from solstead.camera import Camera
from solstead.config import Config
from solstead.controller import Controller
from solstead.provisioning import certificate_fingerprint, provision
from solstead.server import Server

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl required")


def test_tls_round_trip(tmp_path, storage_root, ffmpeg):
    result = provision(tmp_path / "etc", "localhost", storage_root, bind="127.0.0.1",
                       require_mount=False)  # fmt: skip
    config = replace(Config.load(result["config"]), port=0)
    server = Server(config, Controller(config, camera=Camera(None, runner=ffmpeg)))
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        context = ssl.create_default_context(cafile=str(config.tls_cert))
        conn = http.client.HTTPSConnection("localhost", server.server_address[1], context=context,
                                           timeout=5)  # fmt: skip
        conn.request("GET", "/v1/status", headers={"Authorization": "Bearer " + result["token"]})
        response = conn.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["camera"]["configured"] is False
        peer = conn.sock.getpeercert(binary_form=True)
        assert hashlib.sha256(peer).hexdigest() == result["fingerprint"]
        conn.close()
    finally:
        server.shutdown()
        server.server_close()


def test_bad_tls_client_does_not_kill_server(tmp_path, storage_root, ffmpeg):
    result = provision(tmp_path / "etc", "localhost", storage_root, bind="127.0.0.1",
                       require_mount=False)  # fmt: skip
    config = replace(Config.load(result["config"]), port=0)
    server = Server(config, Controller(config, camera=Camera(None, runner=ffmpeg)))
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        plain = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        with pytest.raises((http.client.HTTPException, OSError)):
            plain.request("GET", "/v1/status")
            plain.getresponse()
        context = ssl.create_default_context(cafile=str(config.tls_cert))
        conn = http.client.HTTPSConnection("localhost", server.server_address[1], context=context,
                                           timeout=5)  # fmt: skip
        conn.request("GET", "/v1/status")
        assert conn.getresponse().status == 401
        conn.close()
    finally:
        server.shutdown()
        server.server_close()


def test_fingerprint_matches_openssl(tmp_path, storage_root):
    import subprocess

    result = provision(tmp_path / "etc", "solstead.local", storage_root, require_mount=False)
    cert = tmp_path / "etc" / "controller.crt"
    out = subprocess.run(["openssl", "x509", "-in", str(cert), "-noout", "-fingerprint", "-sha256"],
                         capture_output=True, text=True, check=True).stdout  # fmt: skip
    expected = out.split("=")[1].strip().replace(":", "").lower()
    assert certificate_fingerprint(cert.read_text()) == expected == result["fingerprint"]


def test_serve_stops_controller_on_sigterm(tmp_path, storage_root):
    """Run the real server in a subprocess, send SIGTERM, and expect a clean exit."""
    import os
    import signal
    import subprocess
    import sys

    result = provision(tmp_path / "etc", "localhost", storage_root, bind="127.0.0.1", port=18443,
                       require_mount=False)  # fmt: skip
    process = subprocess.Popen([sys.executable, "-m", "solstead", "serve", result["config"]],
                               stdout=subprocess.PIPE, text=True)  # fmt: skip
    try:
        seen = []
        for _ in range(5):
            seen.append(process.stdout.readline())
            if "ready" in seen[-1]:
                break
        assert any("ready" in line for line in seen)
        os.kill(process.pid, signal.SIGTERM)
        out, _ = process.communicate(timeout=10)
        assert process.returncode == 0 and "stopped" in out
    finally:
        if process.poll() is None:
            process.kill()
