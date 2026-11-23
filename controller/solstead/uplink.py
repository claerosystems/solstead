"""The owner's preferred uplink (Wi-Fi or cellular), applied through NetworkManager route metrics.

Both links stay connected. The preferred one gets the lower IPv4 route metric on its
NetworkManager profiles, so the kernel sends traffic over it while the other stands by with its
own default route. If the preferred link drops, NetworkManager removes its route and traffic moves
to the standby link by itself. The metrics are stored in the profiles, so the choice survives a
reboot even before the controller starts; uplink.json beside config.json keeps the choice for the
controller, which re-applies it at start in case a profile was added or edited since. Until the
owner picks, nothing is changed and NetworkManager's defaults (Wi-Fi first) apply.

nmcli needs polkit permission to modify system connections and reapply devices when the
controller runs as an unprivileged user; deploy/cellular.sh installs that rule.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

KINDS = ("wifi", "cellular")
DEFAULT_PREFERRED = "wifi"  # NetworkManager's own defaults (Wi-Fi 600, cellular 700) agree
PREFERRED_METRIC = 600
STANDBY_METRIC = 700  # Ethernet's 100 still beats both on the bench
NM_TYPES = {"802-11-wireless": "wifi", "gsm": "cellular"}
NAMES = {"wifi": "Wi-Fi", "cellular": "cellular"}
Runner = Callable[..., subprocess.CompletedProcess]


class UplinkError(ValueError):
    """The request names no valid uplink."""


class NoProfile(UplinkError):
    """There is no NetworkManager connection for the requested link."""


class NetworkManagerError(Exception):
    """nmcli is missing, refused (polkit) or failed."""


@dataclass(frozen=True)
class Profile:
    name: str
    uuid: str
    kind: str  # "wifi" or "cellular"
    device: str | None  # set while the profile is active


def split_terse(line: str) -> list[str]:
    """One line of `nmcli -t` output: fields split on ':', with '\\:' and '\\\\' escaped."""
    fields, current, escaped = [], [], False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    fields.append("".join(current))
    return fields


def parse_profiles(text: str) -> list[Profile]:
    """`nmcli -t -f NAME,UUID,TYPE,DEVICE connection show`, keeping Wi-Fi and cellular."""
    profiles = []
    for line in text.splitlines():
        fields = split_terse(line)
        if len(fields) != 4:
            continue
        name, uuid, nm_type, device = fields
        kind = NM_TYPES.get(nm_type)
        if kind and uuid:
            profiles.append(Profile(name, uuid, kind, device or None))
    return profiles


def metrics_for(preferred: str) -> dict[str, int]:
    return {kind: PREFERRED_METRIC if kind == preferred else STANDBY_METRIC for kind in KINDS}


def load_preferred(path: Path | None) -> str:
    if not path:
        return DEFAULT_PREFERRED
    try:
        value = json.loads(path.read_text()).get("preferred")
    except (OSError, ValueError, AttributeError):
        return DEFAULT_PREFERRED
    return value if value in KINDS else DEFAULT_PREFERRED


def save_preferred(path: Path, preferred: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"preferred": preferred}) + "\n")
    os.replace(temporary, path)


class Uplink:
    def __init__(self, settings_path: Path | None, runner: Runner = subprocess.run):
        self.settings_path = Path(settings_path) if settings_path else None
        self.runner = runner
        self.preferred = load_preferred(self.settings_path)
        # Set by the fallback check when traffic left the preferred link because it failed.
        self.fallback = False
        self.fallback_reason: str | None = None
        self._lock = threading.Lock()

    def status(self) -> dict:
        return {
            "preferred": self.preferred,
            "fallback": self.fallback,
            "fallback_reason": self.fallback_reason,
        }

    def set_preferred(self, value: object) -> bool:
        """Make `value` the preferred link now and remember it. True if anything changed."""
        if value not in KINDS:
            raise UplinkError("preferred must be wifi or cellular")
        with self._lock:
            profiles = self._profiles()
            if not any(p.kind == value for p in profiles):
                raise NoProfile(f"No {NAMES[value]} connection is set up on the controller")
            changed = self._apply(profiles, value)
            if self.settings_path:
                save_preferred(self.settings_path, value)
            changed = changed or value != self.preferred
            self.preferred = value
            return changed

    def apply(self) -> bool:
        """Re-apply the remembered choice, e.g. at start. True if a profile had to change.
        Until the owner has picked a link, NetworkManager's own metrics are left alone."""
        if not (self.settings_path and self.settings_path.exists()):
            return False
        with self._lock:
            return self._apply(self._profiles(), self.preferred)

    # ---- nmcli ---------------------------------------------------------------------------

    def _nmcli(self, *args: str) -> str:
        try:
            result = self.runner(["nmcli", *args], capture_output=True, text=True, timeout=15)
        except FileNotFoundError:
            raise NetworkManagerError("NetworkManager (nmcli) is not installed") from None
        except (OSError, subprocess.TimeoutExpired) as error:
            raise NetworkManagerError(f"nmcli failed: {type(error).__name__}") from None
        if result.returncode:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            reason = detail[-1] if detail else f"exit status {result.returncode}"
            raise NetworkManagerError(f"NetworkManager refused: {reason}")
        return result.stdout

    def _profiles(self) -> list[Profile]:
        return parse_profiles(
            self._nmcli("-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show")
        )

    def _metric(self, profile: Profile) -> int | None:
        text = self._nmcli("-g", "ipv4.route-metric", "connection", "show", profile.uuid).strip()
        try:
            return int(text)
        except ValueError:
            return None

    def _apply(self, profiles: list[Profile], preferred: str) -> bool:
        """Write each profile's metric where it differs and make it take effect on active ones:
        Wi-Fi is reapplied without dropping the link, cellular is re-activated."""
        wanted = metrics_for(preferred)
        changed = False
        for profile in profiles:
            metric = wanted[profile.kind]
            if self._metric(profile) == metric:
                continue
            self._nmcli("connection", "modify", profile.uuid, "ipv4.route-metric", str(metric))
            if profile.device and profile.kind == "cellular":
                # Reapplying a ModemManager device drops the bearer's IPv4 address and route
                # while NetworkManager still reports it activated, so re-activate instead: the
                # cellular link is down for a few seconds while Wi-Fi carries traffic.
                self._nmcli("connection", "up", profile.uuid)
            elif profile.device:
                self._nmcli("device", "reapply", profile.device)
            changed = True
        return changed
