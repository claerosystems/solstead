import json

import pytest

from solstead.zones import (
    MotionSettings,
    MotionSettingsError,
    load_motion_settings,
    save_motion_settings,
)

LEFT_HALF = {"name": "Driveway", "points": [[0, 0], [0.5, 0], [0.5, 1], [0, 1]]}


def test_defaults_watch_the_whole_view():
    settings = MotionSettings()
    assert settings.zones == () and settings.ignore_shadows and settings.verify
    assert sum(settings.mask(8, 4)) == 32


def test_mask_covers_only_the_areas():
    settings = MotionSettings.from_dict({"zones": [LEFT_HALF]})
    mask = settings.mask(8, 4)
    assert sum(mask) == 16
    assert all(mask[row * 8 + col] == (1 if col < 4 else 0) for row in range(4) for col in range(8))


def test_triangle_and_partial_update():
    triangle = {"points": [[0, 0], [1, 0], [0, 1]]}
    settings = MotionSettings.from_dict({"zones": [triangle], "sensitivity": "high"})
    assert settings.zones[0].name == "Area 1"
    assert settings.zones[0].contains(0.1, 0.1) and not settings.zones[0].contains(0.9, 0.9)
    updated = MotionSettings.from_dict({"ignore_shadows": False}, base=settings)
    assert updated.zones == settings.zones and updated.sensitivity == "high"
    assert not updated.ignore_shadows


@pytest.mark.parametrize(
    "payload",
    [
        {"zones": "everywhere"},
        {"zones": [{"points": [[0, 0], [1, 1]]}]},
        {"zones": [{"points": [[0, 0], [2, 0], [0, 1]]}]},
        {"zones": [{"points": [[0, 0], [True, 0], [0, 1]]}]},
        {"zones": [{"points": [[0, 0], [0.001, 0], [0, 0.001]]}]},
        {"zones": [LEFT_HALF] * 9},
        {"sensitivity": "extreme"},
        {"ignore_shadows": "yes"},
        [],
    ],
)
def test_rejects_bad_settings(payload):
    with pytest.raises(MotionSettingsError):
        MotionSettings.from_dict(payload)


def test_save_and_load(tmp_path):
    path = tmp_path / "motion.json"
    assert load_motion_settings(path) == MotionSettings()
    settings = MotionSettings.from_dict({"zones": [LEFT_HALF], "ignore_shadows": False})
    save_motion_settings(path, settings)
    assert load_motion_settings(path) == settings
    assert json.loads(path.read_text())["zones"][0]["name"] == "Driveway"
    path.write_text("{broken")
    assert load_motion_settings(path) == MotionSettings()
