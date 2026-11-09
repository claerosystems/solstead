import json
import subprocess

from solstead.network import (
    kind_of,
    parse_default_routes,
    parse_mmcli,
    parse_wireless,
    read_network,
)

WIRELESS = """Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
 wlan0: 0000   58.  -52.  -256        0      0      0      0     79        0
"""

MMCLI = """modem.generic.state                              : connected
modem.generic.access-technologies.length         : 1
modem.generic.access-technologies.value[1]       : lte
modem.generic.signal-quality.value               : 83
modem.generic.signal-quality.recent              : yes
modem.3gpp.operator-name                         : TELUS
"""

ROUTES = [
    {"dst": "default", "gateway": "10.0.0.1", "dev": "wwan0", "metric": 700},
    {"dst": "default", "gateway": "192.168.50.1", "dev": "wlan0", "metric": 600},
]


def test_preferred_default_route_is_lowest_metric():
    assert parse_default_routes(json.dumps(ROUTES)) == "wlan0"
    assert parse_default_routes(json.dumps(ROUTES[:1])) == "wwan0"
    assert parse_default_routes("[]") is None
    assert parse_default_routes("not json") is None


def test_interface_kinds():
    assert kind_of("wlan0") == "wifi"
    assert kind_of("wwan0") == "cellular"
    assert kind_of("eth0") == "ethernet"
    assert kind_of("wg0") == "other"


def test_wifi_signal():
    wifi = parse_wireless(WIRELESS)
    assert wifi.interface == "wlan0" and wifi.signal_percent == 83 and wifi.signal_dbm == -52
    unassociated = WIRELESS.replace("58.  -52.", "0.  0.")
    assert parse_wireless(unassociated) is None
    assert parse_wireless(WIRELESS.splitlines()[0]) is None


def test_modem_readings():
    cell = parse_mmcli(MMCLI)
    assert (cell.signal_percent, cell.technology, cell.operator, cell.state) == (
        83, "lte", "TELUS", "connected",
    )  # fmt: skip
    blank = parse_mmcli(MMCLI.replace("TELUS", "--").replace(": 83", ": --"))
    assert blank.operator is None and blank.signal_percent is None
    assert parse_mmcli("") is None


def test_read_network(tmp_path):
    wireless = tmp_path / "wireless"
    wireless.write_text(WIRELESS)

    def runner(args, capture_output, text, timeout):
        out = json.dumps(ROUTES[:1]) if args[0] == "ip" else MMCLI
        return subprocess.CompletedProcess(args, 0, out, "")

    network = read_network(runner, wireless)
    assert network.uplink == "cellular" and network.interface == "wwan0"
    assert network.wifi.signal_percent == 83 and network.cellular.signal_percent == 83
    assert network.to_dict()["cellular"]["operator"] == "TELUS"


def test_nothing_available(tmp_path):
    def missing(args, capture_output, text, timeout):
        raise FileNotFoundError

    network = read_network(missing, tmp_path / "nope")
    assert network.to_dict() == {"uplink": None, "interface": None, "wifi": None, "cellular": None}
