import json
import stat
import time

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from solstead.camera import Camera
from solstead.controller import Controller
from solstead.victron import (
    Charger,
    ChargerError,
    KeyMismatch,
    decode_solar,
    parse_frame,
    parse_key,
    read_frame,
)

KEY = bytes.fromhex("00112233445566778899aabbccddeeff")
OTHER_KEY = bytes.fromhex("ffeeddccbbaa99887766554433221100")
ADDRESS = "F9:C2:B8:CB:0A:F5"


def plain(state=3, error=0, volts=12.77, amps=2.5, yield_wh=120, solar=35, load=0.6) -> bytes:
    load_raw = 0x1FF if load is None else round(load * 10)
    return (
        bytes([state, error])
        + round(volts * 100).to_bytes(2, "little", signed=True)
        + round(amps * 10).to_bytes(2, "little", signed=True)
        + (yield_wh // 10).to_bytes(2, "little")
        + solar.to_bytes(2, "little")
        + load_raw.to_bytes(2, "little")
    )


def broadcast(
    record: bytes, key: bytes = KEY, nonce: int = 0xAB63, record_type: int = 0x01
) -> bytes:
    """Manufacturer data as a SmartSolar sends it: header, key check byte, AES-CTR ciphertext."""
    counter = nonce.to_bytes(2, "little") + bytes(14)
    encryptor = Cipher(algorithms.AES(key), modes.CTR(counter)).encryptor()
    header = bytes([0x10, 0x02]) + (0xA075).to_bytes(2, "little") + bytes([record_type])
    return header + nonce.to_bytes(2, "little") + bytes([key[0]]) + encryptor.update(record)


def test_frame_round_trip():
    frame = parse_frame(broadcast(plain()))
    assert frame.model_id == 0xA075 and frame.record_type == 1 and frame.key_check == KEY[0]
    reading = read_frame(frame, KEY, 100.0)
    assert reading.state == "bulk" and reading.error == 0
    assert reading.battery_v == 12.77 and reading.battery_a == 2.5
    assert reading.solar_w == 35 and reading.yield_today_wh == 120
    assert reading.load_a == 0.6 and reading.load_w == 7.7


def test_wrong_key_and_other_records_are_refused():
    frame = parse_frame(broadcast(plain()))
    with pytest.raises(KeyMismatch):
        read_frame(frame, OTHER_KEY, 0)
    with pytest.raises(ChargerError):
        read_frame(parse_frame(broadcast(plain(), record_type=0x02)), KEY, 0)
    assert parse_frame(b"\x10\x02") is None
    assert parse_frame(bytes([0x11]) + bytes(12)) is None


def test_unavailable_values_are_none():
    record = bytearray(plain(load=None))
    record[2:4] = (0x7FFF).to_bytes(2, "little")
    record[8:10] = (0xFFFF).to_bytes(2, "little")
    reading = decode_solar(bytes(record), 0)
    assert reading.battery_v is None and reading.solar_w is None and reading.load_a is None
    assert reading.load_w is None
    assert decode_solar(plain(state=99), 0).state == "state 99"
    assert decode_solar(plain(amps=-0.5), 0).battery_a == -0.5


def test_key_must_be_32_hex_characters():
    assert parse_key(" " + KEY.hex().upper() + " ") == KEY
    for bad in ("", "abc", KEY.hex() + "00", "g" * 32, None, 5):
        with pytest.raises(ChargerError):
            parse_key(bad)


def test_owner_picks_a_heard_charger(tmp_path):
    clock = [1000.0]
    charger = Charger(tmp_path / "charger.json", clock=lambda: clock[0])
    charger.handle(ADDRESS.lower(), "SmartSolar HQ2607", -60, broadcast(plain()))
    charger.handle("AA:BB:CC:DD:EE:FF", None, -80, broadcast(plain(), record_type=0x02))
    seen = charger.seen()
    assert [s["address"] for s in seen] == [ADDRESS, "AA:BB:CC:DD:EE:FF"]
    assert seen[0]["name"] == "SmartSolar HQ2607" and seen[0]["solar_charger"] is True
    assert charger.latest is None  # nothing decoded until the owner chooses

    with pytest.raises(KeyMismatch):
        charger.choose(ADDRESS, OTHER_KEY.hex())
    with pytest.raises(ChargerError, match="not been heard"):
        charger.choose("11:22:33:44:55:66", KEY.hex())
    with pytest.raises(ChargerError, match="solar chargers"):
        charger.choose("AA:BB:CC:DD:EE:FF", KEY.hex())
    charger.choose(ADDRESS, KEY.hex())

    path = tmp_path / "charger.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["key"] == KEY.hex()
    status = charger.status()
    assert status["configured"] and status["name"] == "SmartSolar HQ2607"
    assert "key" not in json.dumps(status).replace("key_mismatch", "")
    assert charger.seen()[0]["chosen"] is True

    clock[0] = 1005.0
    charger.handle(ADDRESS, None, -61, broadcast(plain(volts=13.1, solar=80), nonce=0xAB64))
    assert charger.latest.battery_v == 13.1 and charger.latest.unix == 1005.0
    assert charger.fresh(30) is charger.latest
    clock[0] = 1100.0
    assert charger.fresh(30) is None

    reloaded = Charger(path, clock=lambda: clock[0])
    assert reloaded.settings.address == ADDRESS
    reloaded.handle(ADDRESS, None, -60, broadcast(plain(), key=OTHER_KEY))
    assert reloaded.key_mismatch is True and reloaded.latest is None

    charger.forget()
    assert not path.exists() and charger.status()["configured"] is False


def test_seen_list_forgets_chargers_after_ten_minutes(tmp_path):
    clock = [0.0]
    charger = Charger(None, clock=lambda: clock[0])
    charger.handle(ADDRESS, None, -60, broadcast(plain()))
    clock[0] = 601.0
    assert charger.seen() == []


def test_controller_logs_charger_and_battery(config, ffmpeg):
    controller = Controller(config, camera=Camera(config.camera_url, runner=ffmpeg))
    charger = controller.charger
    clock = [time.time()]
    charger.clock = lambda: clock[0]

    def hear(**values):
        charger.handle(ADDRESS, "SmartSolar", -60, broadcast(plain(**values)))
        controller._last_charger_check = 0.0
        controller._check_charger()

    hear()
    controller._check_charger()
    assert controller.health.recent() == []  # no charger chosen yet
    charger.choose(ADDRESS, KEY.hex())
    hear(volts=12.4)
    hear(volts=11.9)
    hear(volts=12.3)  # still low until it passes 12.6
    hear(volts=12.7, error=17)
    hear(volts=12.7)
    clock[0] += 400
    controller._last_charger_check = 0.0
    controller._check_charger()
    messages = [e["message"] for e in reversed(controller.health.recent())]
    assert messages == [
        "Reading solar charger SmartSolar",
        "Battery low at 11.90 V",
        "Battery recovered to 12.70 V",
        "Solar charger reports error 17",
        "Solar charger error cleared",
        "Solar charger not heard for 5 minutes",
    ]
    assert controller.status()["charger"]["configured"] is True
