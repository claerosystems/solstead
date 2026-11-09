"""Which link the controller reaches the internet over, and how strong its radios are."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

PROC_WIRELESS = Path("/proc/net/wireless")
# brcmfmac (the Pi's Wi-Fi) reports link quality out of 70.
WIFI_QUALITY_MAX = 70
Runner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class WiFi:
    interface: str
    signal_percent: int
    signal_dbm: int | None


@dataclass(frozen=True)
class Cellular:
    signal_percent: int | None
    technology: str | None  # "lte", "umts", ...
    operator: str | None
    state: str | None  # ModemManager's modem state: "connected", "registered", ...


@dataclass(frozen=True)
class Network:
    uplink: str | None  # "wifi", "cellular", "ethernet", "other", or None with no default route
    interface: str | None
    wifi: WiFi | None = None
    cellular: Cellular | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def kind_of(interface: str) -> str:
    if interface.startswith(("wlan", "wlp")):
        return "wifi"
    if interface.startswith(("wwan", "ppp", "wwp")):
        return "cellular"
    if interface.startswith(("eth", "en")):
        return "ethernet"
    return "other"


def parse_default_routes(text: str) -> str | None:
    """The interface of the preferred default route in `ip -j route show default` output."""
    try:
        routes = json.loads(text or "[]")
    except ValueError:
        return None
    routes = [r for r in routes if isinstance(r, dict) and r.get("dev")]
    if not routes:
        return None
    return min(routes, key=lambda r: r.get("metric", 0))["dev"]


def parse_wireless(text: str) -> WiFi | None:
    """The first associated interface in /proc/net/wireless."""
    for line in text.splitlines()[2:]:
        name, _, rest = line.partition(":")
        fields = rest.split()
        if len(fields) < 3:
            continue
        try:
            link = float(fields[1].rstrip("."))
            level = float(fields[2].rstrip("."))
        except ValueError:
            continue
        if link <= 0:
            continue  # not associated
        percent = min(100, round(link / WIFI_QUALITY_MAX * 100))
        dbm = int(level) if level < 0 else None
        return WiFi(interface=name.strip(), signal_percent=percent, signal_dbm=dbm)
    return None


def parse_mmcli(text: str) -> Cellular | None:
    """Key/value output of `mmcli -m any -K`."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            values[key.strip()] = value.strip()
    if not values:
        return None

    def get(key: str) -> str | None:
        value = values.get(key)
        return None if value in (None, "", "--") else value

    quality = get("modem.generic.signal-quality.value")
    return Cellular(
        signal_percent=int(quality) if quality and quality.isdigit() else None,
        technology=get("modem.generic.access-technologies.value[1]"),
        operator=get("modem.3gpp.operator-name"),
        state=get("modem.generic.state"),
    )


def _run(runner: Runner, args: list[str]) -> str | None:
    try:
        result = runner(args, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return None if result.returncode else result.stdout


def read_network(runner: Runner = subprocess.run, wireless: Path = PROC_WIRELESS) -> Network:
    interface = parse_default_routes(_run(runner, ["ip", "-j", "route", "show", "default"]) or "")
    try:
        wifi = parse_wireless(wireless.read_text())
    except OSError:
        wifi = None
    modem = _run(runner, ["mmcli", "-m", "any", "-K"])
    return Network(
        uplink=kind_of(interface) if interface else None,
        interface=interface,
        wifi=wifi,
        cellular=parse_mmcli(modem) if modem else None,
    )
