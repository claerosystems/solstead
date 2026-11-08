"""Victron solar charger over Bluetooth: its Instant Readout broadcast, decrypted with the
owner's key.

A SmartSolar charger broadcasts its state about once a second as Bluetooth LE manufacturer data
(company 0x02E1), encrypted with AES-128-CTR under a per-charger advertisement key that the owner
copies from VictronConnect. Reading needs no pairing or connection, so the charger's PIN is never
used. The record layout is from Victron's "Extra manufacturer data" document; the open-source
victron-ble project decodes the same records.

Scanning uses bleak (BlueZ over D-Bus, no root needed), installed with the package's `ble` extra.
Without it the controller still runs and reports that Bluetooth support is missing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

log = logging.getLogger("solstead")

VICTRON_COMPANY = 0x02E1
PRODUCT_ADVERTISEMENT = 0x10
SOLAR_CHARGER = 0x01
SEEN_SECONDS = 600  # chargers heard within this long appear in the owner's pick list
RETRY_SECONDS = 30
KEY_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")

# VE.Direct device states, as the charger reports them.
STATES = {
    0: "off",
    1: "low power",
    2: "fault",
    3: "bulk",
    4: "absorption",
    5: "float",
    6: "storage",
    7: "equalize",
    245: "starting up",
    246: "repeated absorption",
    247: "recondition",
    248: "battery safe",
    252: "external control",
}


class ChargerError(Exception):
    pass


class KeyMismatch(ChargerError):
    pass


@dataclass(frozen=True)
class Frame:
    """One Instant Readout broadcast before decryption."""

    model_id: int
    record_type: int
    nonce: bytes  # two bytes, the low end of the CTR counter block
    key_check: int  # equals the first byte of the right key
    ciphertext: bytes


@dataclass(frozen=True)
class ChargerReading:
    unix: float
    state: str
    error: int
    battery_v: float | None
    battery_a: float | None  # into the battery; negative when it is discharging through the charger
    solar_w: int | None
    load_a: float | None  # the charger's load output
    yield_today_wh: int | None

    @property
    def load_w(self) -> float | None:
        if self.load_a is None or self.battery_v is None:
            return None
        return round(self.load_a * self.battery_v, 1)

    def to_dict(self) -> dict:
        return {**asdict(self), "load_w": self.load_w}


def parse_frame(data: bytes) -> Frame | None:
    """The manufacturer data under company 0x02E1, or None if it is not an Instant Readout."""
    if len(data) < 9 or data[0] != PRODUCT_ADVERTISEMENT:
        return None
    return Frame(
        model_id=int.from_bytes(data[2:4], "little"),
        record_type=data[4],
        nonce=bytes(data[5:7]),
        key_check=data[7],
        ciphertext=bytes(data[8:]),
    )


def parse_key(text: object) -> bytes:
    if not isinstance(text, str) or not KEY_PATTERN.match(text.strip()):
        raise ChargerError("The key must be the 32-character advertisement key from VictronConnect")
    return bytes.fromhex(text.strip())


def decrypt(frame: Frame, key: bytes) -> bytes:
    if frame.key_check != key[0]:
        raise KeyMismatch("That key does not match this charger")
    counter = frame.nonce + bytes(14)  # a 128-bit little-endian counter starting at the nonce
    decryptor = Cipher(algorithms.AES(key), modes.CTR(counter)).decryptor()
    return decryptor.update(frame.ciphertext) + decryptor.finalize()


def decode_solar(plain: bytes, unix: float) -> ChargerReading:
    if len(plain) < 10:
        raise ChargerError("Solar charger record is too short")

    def signed(at: int, scale: float) -> float | None:
        value = int.from_bytes(plain[at : at + 2], "little", signed=True)
        return None if value == 0x7FFF else round(value * scale, 2)

    def unsigned(at: int) -> int | None:
        value = int.from_bytes(plain[at : at + 2], "little")
        return None if value == 0xFFFF else value

    load = None
    if len(plain) >= 12:
        raw = int.from_bytes(plain[10:12], "little") & 0x1FF
        load = None if raw == 0x1FF else round(raw * 0.1, 1)
    yield_today = unsigned(6)
    return ChargerReading(
        unix=unix,
        state=STATES.get(plain[0], f"state {plain[0]}"),
        error=plain[1],
        battery_v=signed(2, 0.01),
        battery_a=signed(4, 0.1),
        solar_w=unsigned(8),
        load_a=load,
        yield_today_wh=None if yield_today is None else yield_today * 10,
    )


def read_frame(frame: Frame, key: bytes, unix: float) -> ChargerReading:
    if frame.record_type != SOLAR_CHARGER:
        raise ChargerError("Only Victron solar chargers are supported")
    return decode_solar(decrypt(frame, key), unix)


@dataclass(frozen=True)
class ChargerSettings:
    address: str
    name: str
    key: str  # hex; stays on the controller and is never sent back to the app


def load_settings(path: Path | None) -> ChargerSettings | None:
    try:
        data = json.loads(path.read_text())
        return ChargerSettings(str(data["address"]), str(data.get("name", "")), str(data["key"]))
    except (AttributeError, OSError, ValueError, KeyError, TypeError):
        return None


def save_settings(path: Path, settings: ChargerSettings | None) -> None:
    if settings is None:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(json.dumps(asdict(settings)) + "\n")
    temporary.replace(path)


class Charger:
    """Listens for Victron broadcasts; decodes those from the owner's chosen charger."""

    def __init__(self, settings_path: Path | None, clock: Callable[[], float] = time.time):
        self.settings_path = Path(settings_path) if settings_path else None
        self.settings = load_settings(self.settings_path)
        self.clock = clock
        self.latest: ChargerReading | None = None
        self.key_mismatch = False
        self.error: str | None = None
        self.scanning = False
        self._seen: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- broadcasts ------------------------------------------------------------------------

    def handle(self, address: str, name: str | None, rssi: int | None, data: bytes) -> None:
        """One broadcast heard from `address`; called from the scanning thread."""
        frame = parse_frame(data)
        if frame is None:
            return
        now = self.clock()
        address = address.upper()
        with self._lock:
            entry = self._seen.setdefault(address, {"address": address})
            entry.update(
                last_heard_unix=now,
                rssi=rssi,
                model_id=frame.model_id,
                solar_charger=frame.record_type == SOLAR_CHARGER,
                frame=frame,
            )
            if name:
                entry["name"] = name
            settings = self.settings
        if not settings or settings.address != address:
            return
        try:
            reading = read_frame(frame, bytes.fromhex(settings.key), now)
        except KeyMismatch:
            self.key_mismatch = True
            return
        except ChargerError:
            return
        self.key_mismatch = False
        self.latest = reading

    def seen(self) -> list[dict]:
        cutoff = self.clock() - SEEN_SECONDS
        with self._lock:
            entries = [e for e in self._seen.values() if e["last_heard_unix"] >= cutoff]
        chosen = self.settings.address if self.settings else None
        return sorted(
            (
                {
                    "address": e["address"],
                    "name": e.get("name") or e["address"],
                    "rssi": e.get("rssi"),
                    "last_heard_unix": int(e["last_heard_unix"]),
                    "solar_charger": e["solar_charger"],
                    "chosen": e["address"] == chosen,
                }
                for e in entries
            ),
            key=lambda e: -(e["rssi"] or -999),
        )

    # ---- owner's choice --------------------------------------------------------------------

    def choose(self, address: object, key: object) -> ChargerSettings:
        """Use the charger at `address` with its advertisement key, after checking the key
        decrypts a broadcast heard from it."""
        if not isinstance(address, str) or not address.strip():
            raise ChargerError("Pick a charger from the list")
        address = address.strip().upper()
        secret = parse_key(key)
        with self._lock:
            entry = self._seen.get(address)
        if not entry or entry["last_heard_unix"] < self.clock() - SEEN_SECONDS:
            raise ChargerError("That charger has not been heard recently; check it is powered")
        if not entry["solar_charger"]:
            raise ChargerError("Only Victron solar chargers are supported")
        reading = read_frame(entry["frame"], secret, entry["last_heard_unix"])
        if reading.battery_v is not None and not 5 <= reading.battery_v <= 70:
            raise KeyMismatch("That key does not match this charger")
        settings = ChargerSettings(address, entry.get("name") or address, secret.hex())
        if self.settings_path:
            save_settings(self.settings_path, settings)
        self.settings = settings
        self.latest = reading
        self.key_mismatch = False
        return settings

    def forget(self) -> None:
        if self.settings_path:
            save_settings(self.settings_path, None)
        self.settings = None
        self.latest = None
        self.key_mismatch = False

    def fresh(self, within: float) -> ChargerReading | None:
        reading = self.latest
        if reading and self.clock() - reading.unix <= within:
            return reading
        return None

    def status(self) -> dict:
        reading = self.latest
        return {
            "configured": self.settings is not None,
            "address": self.settings.address if self.settings else None,
            "name": self.settings.name if self.settings else None,
            "scanning": self.scanning,
            "error": self.error,
            "key_mismatch": self.key_mismatch,
            "last_heard_unix": int(reading.unix) if reading else None,
            "reading": reading.to_dict() if reading else None,
        }

    # ---- scanning thread -------------------------------------------------------------------

    def start(self) -> None:
        if self._thread:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="charger", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(5)
            self._thread = None

    def _run(self) -> None:
        try:
            from bleak import BleakScanner
        except ImportError:
            self.error = "Bluetooth support is not installed on the controller"
            return
        asyncio.run(self._scan(BleakScanner))

    async def _scan(self, scanner_class) -> None:
        def detected(device, advertisement) -> None:
            data = advertisement.manufacturer_data.get(VICTRON_COMPANY)
            if data:
                self.handle(
                    device.address,
                    advertisement.local_name or device.name,
                    advertisement.rssi,
                    data,
                )

        while not self._stop.is_set():
            try:
                async with scanner_class(detection_callback=detected):
                    self.scanning, self.error = True, None
                    while not self._stop.is_set():
                        await asyncio.sleep(1)
            except Exception as error:  # adapter off, BlueZ restarting, D-Bus refused
                message = str(error) or type(error).__name__
                if message != self.error:
                    log.warning("charger: Bluetooth scan failed: %s", message)
                self.error = f"Bluetooth: {message}"
            self.scanning = False
            for _ in range(RETRY_SECONDS):
                if self._stop.is_set():
                    return
                await asyncio.sleep(1)
