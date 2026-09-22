import subprocess

from solstead.thermal import Thermal, parse_throttled, read_thermal


def runner_for(stdout, returncode=0):
    def runner(args, capture_output, text, timeout):
        return subprocess.CompletedProcess(args, returncode, stdout, "")

    return runner


def test_parse_flags():
    assert parse_throttled("throttled=0x0") == 0
    assert parse_throttled("throttled=0x50005") == 0x50005
    assert parse_throttled("garbage") is None


def test_read_thermal_levels(tmp_path):
    zone = tmp_path / "temp"
    zone.write_text("51121\n")
    healthy = read_thermal(zone, runner_for("throttled=0x0\n"))
    assert healthy.temperature_c == 51.1 and healthy.flags_available and healthy.level == "good"
    undervolt = read_thermal(zone, runner_for("throttled=0x50005\n"))
    assert undervolt.undervoltage_now and undervolt.throttled_now and undervolt.level == "danger"
    assert undervolt.undervoltage_since_boot and undervolt.throttled_since_boot
    past = read_thermal(zone, runner_for("throttled=0x50000\n"))
    assert not past.undervoltage_now and past.undervoltage_since_boot and past.level == "warning"
    zone.write_text("76500\n")
    assert read_thermal(zone, runner_for("throttled=0x0\n")).level == "warning"
    zone.write_text("80100\n")
    assert read_thermal(zone, runner_for("throttled=0x0\n")).level == "danger"


def test_missing_vcgencmd_and_zone(tmp_path):
    def missing(args, capture_output, text, timeout):
        raise FileNotFoundError

    thermal = read_thermal(tmp_path / "nope", missing)
    assert thermal.temperature_c is None and not thermal.flags_available and thermal.level == "good"
    assert Thermal(temperature_c=None).to_dict()["level"] == "good"
