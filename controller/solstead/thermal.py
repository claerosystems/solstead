"""CPU temperature and the Raspberry Pi's throttle and under-voltage flags."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

THERMAL_ZONE = Path("/sys/class/thermal/thermal_zone0/temp")
# BCM2711 (Pi 4): frequency capping starts at 80 C and the hard limit is 85 C. Anything that
# stays above 75 C is heading there; 50-60 C is normal in a case.
WARNING_C = 75.0
THROTTLE_C = 80.0
Runner = Callable[..., subprocess.CompletedProcess]

# Bits of `vcgencmd get_throttled`.
UNDERVOLTAGE_NOW = 1 << 0
FREQ_CAPPED_NOW = 1 << 1
THROTTLED_NOW = 1 << 2
SOFT_TEMP_LIMIT_NOW = 1 << 3
UNDERVOLTAGE_OCCURRED = 1 << 16
FREQ_CAPPED_OCCURRED = 1 << 17
THROTTLED_OCCURRED = 1 << 18
SOFT_TEMP_LIMIT_OCCURRED = 1 << 19


@dataclass(frozen=True)
class Thermal:
    temperature_c: float | None
    warning_c: float = WARNING_C
    throttle_c: float = THROTTLE_C
    flags_available: bool = False
    undervoltage_now: bool = False
    throttled_now: bool = False
    undervoltage_since_boot: bool = False
    throttled_since_boot: bool = False

    @property
    def level(self) -> str:
        """good, warning or danger, on the same scale the app uses."""
        hot = self.temperature_c is not None
        if (
            self.undervoltage_now
            or self.throttled_now
            or (hot and self.temperature_c >= self.throttle_c)
        ):
            return "danger"
        if (
            (hot and self.temperature_c >= self.warning_c)
            or self.undervoltage_since_boot
            or self.throttled_since_boot
        ):
            return "warning"
        return "good"

    def to_dict(self) -> dict:
        return {**asdict(self), "level": self.level}


def read_temperature(zone: Path = THERMAL_ZONE) -> float | None:
    try:
        return round(float(zone.read_text()) / 1000, 1)
    except (OSError, ValueError):
        return None


def parse_throttled(text: str) -> int | None:
    """'throttled=0x50005' -> 0x50005; None if unparseable."""
    try:
        return int(text.strip().split("=", 1)[1], 16)
    except (IndexError, ValueError):
        return None


def read_throttled(runner: Runner = subprocess.run) -> int | None:
    try:
        result = runner(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    return parse_throttled(result.stdout)


def read_thermal(zone: Path = THERMAL_ZONE, runner: Runner = subprocess.run) -> Thermal:
    flags = read_throttled(runner)
    return Thermal(
        temperature_c=read_temperature(zone),
        flags_available=flags is not None,
        undervoltage_now=bool(flags and flags & UNDERVOLTAGE_NOW),
        throttled_now=bool(
            flags and flags & (THROTTLED_NOW | FREQ_CAPPED_NOW | SOFT_TEMP_LIMIT_NOW)
        ),
        undervoltage_since_boot=bool(flags and flags & UNDERVOLTAGE_OCCURRED),
        throttled_since_boot=bool(
            flags and flags & (THROTTLED_OCCURRED | FREQ_CAPPED_OCCURRED | SOFT_TEMP_LIMIT_OCCURRED)
        ),
    )
