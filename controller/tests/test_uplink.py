import http.client
import json
import subprocess
from http.server import ThreadingHTTPServer
from threading import Thread

import pytest

from solstead.api import Handler
from solstead.camera import Camera
from solstead.controller import Controller
from solstead.network import Network
from solstead.uplink import (
    NetworkManagerError,
    NoProfile,
    Uplink,
    UplinkError,
    parse_profiles,
    split_terse,
)
from tests.conftest import TOKEN

PROFILES = (
    "preconfigured:1111:802-11-wireless:wlan0\n"
    "Cabin\\: guest:2222:802-11-wireless:\n"
    "solstead-cellular:3333:gsm:cdc-wdm0\n"
    "Wired connection 1:4444:802-3-ethernet:eth0\n"
    "lo:5555:loopback:lo\n"
)


class FakeNmcli:
    """Stands in for subprocess.run with a tiny in-memory NetworkManager."""

    def __init__(self, profiles=PROFILES, metrics=None, refuse=False):
        self.profiles = profiles
        self.metrics = metrics or {"1111": -1, "2222": -1, "3333": 700}
        self.refuse = refuse
        self.calls: list[list[str]] = []

    def __call__(self, args, capture_output, text, timeout):
        assert args[0] == "nmcli"
        args = args[1:]
        self.calls.append(args)
        if self.refuse and args[0] in ("connection", "device") and args[1] != "show":
            return subprocess.CompletedProcess(args, 4, "", "Error: Insufficient privileges.\n")
        if args[:4] == ["-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection"]:
            return subprocess.CompletedProcess(args, 0, self.profiles, "")
        if args[:2] == ["-g", "ipv4.route-metric"]:
            return subprocess.CompletedProcess(args, 0, f"{self.metrics[args[-1]]}\n", "")
        if args[:2] == ["connection", "modify"]:
            self.metrics[args[2]] = int(args[4])
        return subprocess.CompletedProcess(args, 0, "", "")

    def writes(self):
        return [c for c in self.calls if c[0] in ("connection", "device") and c[1] != "show"]


def test_terse_fields_unescape():
    assert split_terse("a\\:b:c\\\\d:") == ["a:b", "c\\d", ""]


def test_profiles_keep_wifi_and_cellular():
    profiles = parse_profiles(PROFILES)
    assert [(p.name, p.kind, p.device) for p in profiles] == [
        ("preconfigured", "wifi", "wlan0"),
        ("Cabin: guest", "wifi", None),
        ("solstead-cellular", "cellular", "cdc-wdm0"),
    ]


def test_prefer_cellular_swaps_metrics_and_persists(tmp_path):
    nm = FakeNmcli()
    path = tmp_path / "uplink.json"
    uplink = Uplink(path, runner=nm)
    assert uplink.status() == {"preferred": "wifi", "fallback": False, "fallback_reason": None}
    assert uplink.set_preferred("cellular") is True
    assert nm.metrics == {"1111": 700, "2222": 700, "3333": 600}
    # Only active profiles are reapplied, and nothing is brought down.
    assert ["device", "reapply", "wlan0"] in nm.writes()
    # A modem loses its IP on reapply, so the cellular connection is re-activated instead.
    assert ["connection", "up", "3333"] in nm.writes()
    assert not any(c[:2] == ["device", "reapply"] and c[2] == "cdc-wdm0" for c in nm.writes())
    assert not any(c[1] == "down" for c in nm.writes())
    assert json.loads(path.read_text()) == {"preferred": "cellular"}
    assert Uplink(path, runner=nm).preferred == "cellular"

    nm.calls.clear()
    assert uplink.set_preferred("cellular") is False and nm.writes() == []
    uplink.set_preferred("wifi")
    assert nm.metrics == {"1111": 600, "2222": 600, "3333": 700}


def test_apply_at_start_fixes_drifted_profiles(tmp_path):
    nm = FakeNmcli()
    path = tmp_path / "uplink.json"
    assert Uplink(path, runner=nm).apply() is False and nm.calls == []  # owner never picked
    path.write_text('{"preferred": "cellular"}')
    uplink = Uplink(path, runner=nm)
    assert uplink.apply() is True and nm.metrics["1111"] == 700
    nm.calls.clear()
    assert uplink.apply() is False and nm.writes() == []


def test_bad_values_and_missing_profiles(tmp_path):
    nm = FakeNmcli(profiles="preconfigured:1111:802-11-wireless:wlan0\n")
    uplink = Uplink(tmp_path / "uplink.json", runner=nm)
    for bad in ("ethernet", None, 1, "automatic"):
        with pytest.raises(UplinkError):
            uplink.set_preferred(bad)
    with pytest.raises(NoProfile, match="No cellular connection"):
        uplink.set_preferred("cellular")
    assert uplink.preferred == "wifi" and not (tmp_path / "uplink.json").exists()


def test_networkmanager_refusal_keeps_the_old_choice(tmp_path):
    uplink = Uplink(tmp_path / "uplink.json", runner=FakeNmcli(refuse=True))
    with pytest.raises(NetworkManagerError, match="Insufficient privileges"):
        uplink.set_preferred("cellular")
    assert uplink.preferred == "wifi" and not (tmp_path / "uplink.json").exists()

    def missing(args, capture_output, text, timeout):
        raise FileNotFoundError

    with pytest.raises(NetworkManagerError, match="not installed"):
        Uplink(None, runner=missing).set_preferred("wifi")


@pytest.fixture
def api(config, ffmpeg, tmp_path):
    nm = FakeNmcli()
    controller = Controller(
        config, camera=Camera(config.camera_url, runner=ffmpeg),
        uplink=Uplink(tmp_path / "uplink.json", runner=nm),
    )  # fmt: skip
    readings = iter(
        [Network(uplink="cellular", interface="wwan0"), Network(uplink="wifi", interface="wlan0")]
    )
    controller.network_reader = lambda: next(readings)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.controller = controller
    server.nm = nm
    Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def request(server, method, path, payload=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    body = json.dumps(payload) if payload is not None else None
    conn.request(method, path, body=body, headers={"Authorization": "Bearer " + TOKEN})
    response = conn.getresponse()
    result = response.status, json.loads(response.read())
    conn.close()
    return result


def test_network_endpoints(api):
    code, body = request(api, "GET", "/v1/network")
    assert code == 200 and body["preferred"] == "wifi" and body["fallback"] is False
    assert "fallback_reason" in body and "uplink" in body

    code, body = request(api, "PUT", "/v1/network", {"preferred": "cellular"})
    assert code == 200 and body["preferred"] == "cellular" and body["uplink"] == "cellular"
    code, status = request(api, "GET", "/v1/status")
    assert status["network"]["preferred"] == "cellular"
    entries = api.controller.health.recent(5, "info")
    assert any("Owner set cellular as the preferred link" in e["message"] for e in entries)

    code, body = request(api, "PUT", "/v1/network", {"preferred": "satellite"})
    assert code == 400 and "wifi or cellular" in body["error"]
    api.nm.profiles = "preconfigured:1111:802-11-wireless:wlan0\n"
    code, body = request(api, "PUT", "/v1/network", {"preferred": "cellular"})
    assert code == 409
    api.nm.refuse = True
    code, body = request(api, "PUT", "/v1/network", {"preferred": "wifi"})
    assert code == 503 and "NetworkManager refused" in body["error"]
