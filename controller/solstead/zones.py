"""Owner-drawn motion areas and the shadow filter switch, stored beside config.json.

Areas are polygons in fractions of the camera image (0,0 top left, 1,1 bottom right), so they do
not depend on which stream or resolution the controller analyses. No areas means the whole view.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

MAX_ZONES = 8
MIN_POINTS = 3
MAX_POINTS = 16
MAX_NAME = 40
SENSITIVITIES = ("low", "medium", "high")
OBJECT_KINDS = ("person", "vehicle", "animal")


class MotionSettingsError(ValueError):
    pass


@dataclass(frozen=True)
class Zone:
    name: str
    points: tuple[tuple[float, float], ...]

    def to_dict(self) -> dict:
        return {"name": self.name, "points": [list(p) for p in self.points]}

    def contains(self, x: float, y: float) -> bool:
        """Even-odd rule point-in-polygon test."""
        inside = False
        points = self.points
        j = len(points) - 1
        for i, (xi, yi) in enumerate(points):
            xj, yj = points[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
        return inside


@dataclass(frozen=True)
class MotionSettings:
    zones: tuple[Zone, ...] = ()
    ignore_shadows: bool = True
    sensitivity: str = "medium"
    # Checking on the controller at all; off means every camera motion report records.
    verify: bool = True
    # Record only when recognition finds one of these in an area (when recognition can run).
    recognize: bool = True
    objects: tuple[str, ...] = ("person", "vehicle")

    @classmethod
    def from_dict(cls, data: object, base: MotionSettings | None = None) -> MotionSettings:
        if not isinstance(data, dict):
            raise MotionSettingsError("Motion settings must be a JSON object")
        base = base or cls()
        zones = base.zones
        if "zones" in data:
            zones = parse_zones(data["zones"])
        ignore_shadows = data.get("ignore_shadows", base.ignore_shadows)
        if not isinstance(ignore_shadows, bool):
            raise MotionSettingsError("ignore_shadows must be true or false")
        verify = data.get("verify", base.verify)
        if not isinstance(verify, bool):
            raise MotionSettingsError("verify must be true or false")
        sensitivity = data.get("sensitivity", base.sensitivity)
        if sensitivity not in SENSITIVITIES:
            raise MotionSettingsError("sensitivity must be low, medium or high")
        recognize = data.get("recognize", base.recognize)
        if not isinstance(recognize, bool):
            raise MotionSettingsError("recognize must be true or false")
        objects = data.get("objects", list(base.objects))
        if (
            not isinstance(objects, list)
            or not objects
            or not all(kind in OBJECT_KINDS for kind in objects)
        ):
            raise MotionSettingsError("objects must list one or more of person, vehicle, animal")
        objects = tuple(kind for kind in OBJECT_KINDS if kind in objects)
        return cls(zones, ignore_shadows, sensitivity, verify, recognize, objects)

    def to_dict(self) -> dict:
        return {
            "zones": [zone.to_dict() for zone in self.zones],
            "ignore_shadows": self.ignore_shadows,
            "sensitivity": self.sensitivity,
            "verify": self.verify,
            "recognize": self.recognize,
            "objects": list(self.objects),
        }

    def in_areas(self, x: float, y: float) -> bool:
        return not self.zones or any(zone.contains(x, y) for zone in self.zones)

    def mask(self, width: int, height: int) -> bytearray:
        """1 for each analysed pixel inside an area (all pixels when there are no areas)."""
        if not self.zones:
            return bytearray([1]) * (width * height)
        mask = bytearray(width * height)
        for row in range(height):
            y = (row + 0.5) / height
            for column in range(width):
                x = (column + 0.5) / width
                if any(zone.contains(x, y) for zone in self.zones):
                    mask[row * width + column] = 1
        return mask


def parse_zones(value: object) -> tuple[Zone, ...]:
    if not isinstance(value, list):
        raise MotionSettingsError("zones must be a list")
    if len(value) > MAX_ZONES:
        raise MotionSettingsError(f"At most {MAX_ZONES} motion areas are allowed")
    zones = []
    for index, item in enumerate(value, 1):
        if not isinstance(item, dict):
            raise MotionSettingsError("Each motion area must be an object")
        name = item.get("name", f"Area {index}")
        if not isinstance(name, str) or len(name) > MAX_NAME:
            raise MotionSettingsError(f"Area names must be text of at most {MAX_NAME} characters")
        points = item.get("points")
        if not isinstance(points, list) or not MIN_POINTS <= len(points) <= MAX_POINTS:
            raise MotionSettingsError(
                f"Each motion area needs {MIN_POINTS} to {MAX_POINTS} corner points"
            )
        parsed = []
        for point in points:
            if (
                not isinstance(point, list | tuple)
                or len(point) != 2
                or not all(isinstance(v, int | float) and not isinstance(v, bool) for v in point)
                or not all(0.0 <= v <= 1.0 for v in point)
            ):
                raise MotionSettingsError("Area points must be [x, y] fractions between 0 and 1")
            parsed.append((round(float(point[0]), 4), round(float(point[1]), 4)))
        if polygon_area(parsed) < 0.0005:
            raise MotionSettingsError(f"Motion area {name!r} is too small")
        zones.append(Zone(name.strip() or f"Area {index}", tuple(parsed)))
    return tuple(zones)


def polygon_area(points: list[tuple[float, float]]) -> float:
    total = 0.0
    for i, (x1, y1) in enumerate(points):
        x2, y2 = points[(i + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def load_motion_settings(path: Path | None) -> MotionSettings:
    if path is None or not Path(path).is_file():
        return MotionSettings()
    try:
        return MotionSettings.from_dict(json.loads(Path(path).read_text()))
    except (OSError, ValueError):
        # A damaged file must not stop recording; fall back to defaults (whole view).
        return MotionSettings()


def save_motion_settings(path: Path, settings: MotionSettings) -> None:
    path = Path(path)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(settings.to_dict(), indent=2) + "\n")
    partial.replace(path)
