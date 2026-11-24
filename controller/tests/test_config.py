import json

import pytest

from solstead.config import Config, ConfigError

GOOD = {
    "storage_path": "/mnt/solstead",
    "api_token": "x" * 32,
    "tls_cert": "/etc/solstead/c.crt",
    "tls_key": "/etc/solstead/c.key",
}


def test_defaults():
    config = Config.from_dict(GOOD)
    assert config.bind == "127.0.0.1"
    assert config.port == 8443
    assert config.camera_url is None
    assert config.storage_require_mount is True


@pytest.mark.parametrize("missing", ["storage_path", "api_token", "tls_cert", "tls_key"])
def test_required_keys(missing):
    data = {k: v for k, v in GOOD.items() if k != missing}
    with pytest.raises(ConfigError, match=missing):
        Config.from_dict(data)


def test_hub_address():
    assert Config.from_dict(GOOD).hub_address is None
    assert Config.from_dict({**GOOD, "hub_address": "hub.example.org"}).hub_address == (
        "hub.example.org"
    )
    assert Config.from_dict({**GOOD, "hub_address": "203.0.113.7"}).hub_address == "203.0.113.7"
    for bad in ("-I eth0", "hub example", "a;b", 7, "x" * 300):
        with pytest.raises(ConfigError, match="hub_address"):
            Config.from_dict({**GOOD, "hub_address": bad})


def test_short_token_rejected():
    with pytest.raises(ConfigError, match="api_token"):
        Config.from_dict({**GOOD, "api_token": "short"})


@pytest.mark.parametrize("port", [0, 70000, "8443"])
def test_bad_port(port):
    with pytest.raises(ConfigError, match="port"):
        Config.from_dict({**GOOD, "port": port})


def test_camera_url_must_be_rtsp():
    with pytest.raises(ConfigError, match="camera_url"):
        Config.from_dict({**GOOD, "camera_url": "ftp://cam"})
    assert Config.from_dict({**GOOD, "camera_url": "http://cam:8554/live"}).camera_url
    assert Config.from_dict({**GOOD, "camera_url": ""}).camera_url is None


def test_load_and_round_trip(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({**GOOD, "camera_url": "rtsp://cam/1"}))
    config = Config.load(path)
    assert Config.from_dict(config.to_dict()) == config


def test_load_invalid_json(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{")
    with pytest.raises(ConfigError, match="valid JSON"):
        Config.load(path)


def test_load_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="Cannot read"):
        Config.load(tmp_path / "nope.json")


def test_example_config_parses():
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / "config.example.json"
    config = Config.load(example)
    assert config.camera_url.startswith("rtsp://")


def test_event_settings():
    config = Config.from_dict({**GOOD, "camera_url": "rtsp://cam/1", "camera_onvif_url": "http://cam:2020/onvif/device_service",
                               "camera_user": "admin", "camera_password": "pw", "motion_topics": ["Motion", "Human"],
                               "event_post_seconds": 20})  # fmt: skip
    assert config.events_enabled and config.motion_topics == ("Motion", "Human")
    assert config.event_post_seconds == 20 and config.event_pre_seconds == 10
    assert Config.from_dict(config.to_dict()) == config
    assert not Config.from_dict(GOOD).events_enabled


@pytest.mark.parametrize("bad", [{"camera_onvif_url": "cam:2020"}, {"event_pre_seconds": 61},
                                 {"segment_seconds": 0}, {"motion_topics": "Motion"}])  # fmt: skip
def test_bad_event_settings(bad):
    with pytest.raises(ConfigError):
        Config.from_dict({**GOOD, **bad})
