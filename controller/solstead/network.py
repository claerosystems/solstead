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
    routes = parse_routes(text)
    return routes[0].interface if routes else None


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


# ---- failover --------------------------------------------------------------------------------
#
# The kernel already moves to the other default route when a link drops (Wi-Fi loses its access
# point, the modem detaches). It does not when a link stays up but stops passing traffic, such as
# Wi-Fi still joined to a router whose internet is down. Failover pings through the preferred link
# and, when it stops answering while another link works, raises the preferred link's route metric
# for the moment through NetworkManager (`nmcli device modify`, which changes only the device's
# running settings, never its saved profile). `nmcli device reapply` puts the profile's metric back
# once the link answers again. Because the lowered state lives only in the route table, a restart
# of the controller picks it up from the metric alone.

# Two public resolvers run by different operators, so one outage does not look like ours.
PROBE_HOSTS = ("1.1.1.1", "8.8.8.8")
PROBE_TIMEOUT_SECONDS = 2
# A demoted link's metric; anything at or above it is treated as set aside by failover.
DEMOTED_METRIC = 2000
FAIL_CHECKS = 2  # consecutive failed checks before switching away (about a minute at 30 s)
RECOVER_CHECKS = 4  # consecutive good checks before switching back (about two minutes)
NAMES = {"wifi": "Wi-Fi", "cellular": "cellular", "ethernet": "Ethernet"}


@dataclass(frozen=True)
class Route:
    interface: str
    metric: int

    @property
    def demoted(self) -> bool:
        return self.metric >= DEMOTED_METRIC


def parse_routes(text: str) -> list[Route]:
    """Default routes in `ip -j route show default` output, one per interface, best first."""
    try:
        routes = json.loads(text or "[]")
    except ValueError:
        return []
    best: dict[str, int] = {}
    for route in routes:
        if not isinstance(route, dict) or not route.get("dev"):
            continue
        metric = route.get("metric", 0)
        if not isinstance(metric, int):
            continue
        best[route["dev"]] = min(metric, best.get(route["dev"], metric))
    return sorted((Route(dev, metric) for dev, metric in best.items()), key=lambda r: r.metric)


def link_name(interface: str) -> str:
    return NAMES.get(kind_of(interface), interface)


class Failover:
    """Moves the default route off a link that has stopped passing traffic, and back again.

    `check()` runs every NETWORK_CHECK_SECONDS from its own thread, since a ping can take a
    couple of seconds. Only the preferred link is pinged while it works, so a healthy Wi-Fi
    link spends no cellular data; the other links are pinged only to decide a switch.
    """

    def __init__(
        self,
        record: Callable[..., object],
        runner: Runner = subprocess.run,
        hosts: tuple[str, ...] = PROBE_HOSTS,
    ):
        self.record = record  # HealthLog.record
        self.runner = runner
        self.hosts = hosts
        self._failures: dict[str, int] = {}
        self._successes: dict[str, int] = {}
        self._no_link_logged = False
        self._last_error: str | None = None
        # For the status: whether the route is on a fallback link right now, and why.
        self.fallback = False
        self.fallback_reason: str | None = None

    # -- probes and actions --------------------------------------------------------------

    def passes(self, interface: str) -> bool | None:
        """True when any probe host answers through `interface`; None when ping cannot run."""
        for host in self.hosts:
            args = ["ping", "-n", "-q", "-c", "1", "-W", str(PROBE_TIMEOUT_SECONDS)]
            try:
                result = self.runner(
                    [*args, "-I", interface, host],
                    capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS + 3,
                )  # fmt: skip
            except subprocess.TimeoutExpired:
                continue
            except OSError:
                return None
            if result.returncode == 0:
                return True
            if result.returncode not in (1, 2):  # 1: no reply; 2: no route or no address
                return None
        return False

    def _nmcli(self, args: list[str]) -> bool:
        try:
            result = self.runner(["nmcli", *args], capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as error:
            message = f"nmcli failed: {type(error).__name__}"
        else:
            if result.returncode == 0:
                self._last_error = None
                return True
            message = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
        if message != self._last_error:
            self._last_error = message
            self.record("error", "network", f"Could not change the route: {message}")
        return False

    def demote(self, interface: str) -> bool:
        metric = str(DEMOTED_METRIC)
        return self._nmcli(["device", "modify", interface, "ipv4.route-metric", metric])

    def restore(self, interface: str) -> bool:
        return self._nmcli(["device", "reapply", interface])

    # -- the check -----------------------------------------------------------------------

    def check(self, routes: list[Route]) -> None:
        present = {r.interface for r in routes}
        for counts in (self._failures, self._successes):
            for interface in list(counts):
                if interface not in present:
                    del counts[interface]
        if len(routes) < 2:
            # Nothing to fall back to; the kernel and NetworkManager handle a link going away.
            # A lone demoted route still carries traffic, and NetworkManager restores its metric
            # when the link reconnects.
            self._failures.clear()
            self._no_link_logged = False
            self._update_fallback(routes)
            return
        restored = {r.interface for r in routes if r.demoted and self._try_switch_back(r)}
        if restored:
            # The route table has changed under `routes`; look again at the next check.
            self._update_fallback([r for r in routes if r.interface not in restored])
            return
        active = [r for r in routes if not r.demoted]
        if active and self._watch_preferred(active[0], active[1:]):
            self.fallback = True  # the route table changed under `routes`
            return
        self._update_fallback(routes)

    def _update_fallback(self, routes: list[Route]) -> None:
        demoted = [r for r in routes if r.demoted]
        self.fallback = bool(demoted) and len(demoted) < len(routes)
        if not self.fallback:
            self.fallback_reason = None
        elif self.fallback_reason is None:
            self.fallback_reason = f"{link_name(demoted[0].interface)} is not passing traffic"

    def _try_switch_back(self, route: Route) -> bool:
        result = self.passes(route.interface)
        if result is None:
            return False
        count = self._successes.get(route.interface, 0) + 1 if result else 0
        self._successes[route.interface] = count
        if count < RECOVER_CHECKS or not self.restore(route.interface):
            return False
        del self._successes[route.interface]
        self.record(
            "info", "network",
            f"{link_name(route.interface)} is passing traffic again; switched back to it",
            interface=route.interface,
        )  # fmt: skip
        return True

    def _watch_preferred(self, preferred: Route, others: list[Route]) -> bool:
        """Ping the preferred link; True when it was just set aside for one of `others`."""
        result = self.passes(preferred.interface)
        if result is None:
            return False
        if result:
            self._failures.pop(preferred.interface, None)
            self._no_link_logged = False
            return False
        failures = self._failures.get(preferred.interface, 0) + 1
        self._failures[preferred.interface] = failures
        if failures < FAIL_CHECKS:
            return False
        backup = next((r for r in others if self.passes(r.interface)), None)
        if backup is None:
            if not self._no_link_logged:
                self._no_link_logged = True
                self.record(
                    "warning", "network",
                    f"{link_name(preferred.interface)} is not passing traffic and no other link is",
                    interface=preferred.interface,
                )  # fmt: skip
            return False
        if not self.demote(preferred.interface):
            return False
        self._failures.pop(preferred.interface, None)
        self._successes.pop(preferred.interface, None)
        self._no_link_logged = False
        self.fallback_reason = f"{link_name(preferred.interface)} is not passing traffic"
        self.record(
            "warning", "network",
            f"{link_name(preferred.interface)} stopped passing traffic; "
            f"switched to {link_name(backup.interface)} until it recovers",
            interface=preferred.interface, fallback=backup.interface,
        )  # fmt: skip
        return True


def read_routes(runner: Runner = subprocess.run) -> list[Route]:
    return parse_routes(_run(runner, ["ip", "-j", "route", "show", "default"]) or "")
