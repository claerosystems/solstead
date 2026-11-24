import json
import subprocess

from solstead.network import (
    DEMOTED_METRIC,
    FAIL_CHECKS,
    PROBE_HOSTS,
    PROMOTED_METRIC,
    RECOVER_CHECKS,
    STRANDED_CHECKS,
    Failover,
    Route,
    kind_of,
    parse_default_routes,
    parse_mmcli,
    parse_routes,
    parse_wireless,
    probe_hosts,
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


# ---- failover ------------------------------------------------------------------------------


class Links:
    """Fakes ping and nmcli: `up` holds the interfaces that pass traffic."""

    def __init__(self, up=("wlan0", "wwan0"), nmcli_code=0, nmcli_error=""):
        self.up = set(up)
        self.nmcli_code = nmcli_code
        self.nmcli_error = nmcli_error
        self.calls: list[list[str]] = []

    def __call__(self, args, capture_output, text, timeout):
        self.calls.append(list(args))
        if args[0] == "ping":
            code = 0 if args[args.index("-I") + 1] in self.up else 1
            return subprocess.CompletedProcess(args, code, "", "")
        out = "1111-aaaa:802-11-wireless\n2222-bbbb:gsm\n" if "UUID,TYPE" in args else ""
        return subprocess.CompletedProcess(args, self.nmcli_code, out, self.nmcli_error)

    def pinged(self):
        return [c[c.index("-I") + 1] for c in self.calls if c[0] == "ping"]

    def nmcli(self):
        return [c[1:] for c in self.calls if c[0] == "nmcli"]


class Health:
    def __init__(self):
        self.entries = []

    def __call__(self, level, kind, message, **fields):
        self.entries.append((level, message))


WIFI_FIRST = [Route("wlan0", 600), Route("wwan0", 700)]
WIFI_DEMOTED = [Route("wwan0", 700), Route("wlan0", DEMOTED_METRIC)]


def test_probes_the_hub_when_there_is_one():
    assert probe_hosts("hub.example.org") == ("hub.example.org",)
    assert probe_hosts(None) == PROBE_HOSTS
    links = Links(up={"wlan0", "wwan0"})
    Failover(Health(), links, hosts=probe_hosts("203.0.113.7")).passes("wlan0")
    assert links.calls[0][-3:] == ["-I", "wlan0", "203.0.113.7"]


def test_routes_one_per_interface_best_first():
    text = json.dumps(
        [*ROUTES, {"dst": "default", "dev": "wlan0", "metric": 900}, {"dev": "x", "metric": "?"}]
    )
    assert parse_routes(text) == WIFI_FIRST
    assert parse_routes("nope") == []
    assert Route("wlan0", DEMOTED_METRIC).demoted and not Route("wlan0", 600).demoted


def test_healthy_preferred_link_spends_no_cellular_data():
    links, health = Links(), Health()
    failover = Failover(health, links, hosts=("1.1.1.1",))
    for _ in range(5):
        failover.check(WIFI_FIRST)
    assert set(links.pinged()) == {"wlan0"} and links.nmcli() == [] and health.entries == []


def test_single_link_is_never_probed():
    links = Links(up=())
    failover = Failover(Health(), links)
    for _ in range(5):
        failover.check([Route("wwan0", 700)])
    assert links.calls == [] and not failover.fallback


def test_switches_to_working_link_and_back():
    links, health = Links(up={"wwan0"}), Health()
    failover = Failover(health, links, hosts=("1.1.1.1",))
    for _ in range(FAIL_CHECKS - 1):
        failover.check(WIFI_FIRST)
    assert links.nmcli() == []  # one missed ping is not enough
    failover.check(WIFI_FIRST)
    assert links.nmcli() == [
        ["device", "modify", "wlan0", "ipv4.route-metric", str(DEMOTED_METRIC)]
    ]
    assert health.entries == [
        ("warning", "Wi-Fi stopped passing traffic; switched to cellular until it recovers")
    ]
    assert failover.fallback and failover.fallback_reason == "Wi-Fi is not passing traffic"

    # Still broken: keep checking Wi-Fi without touching the routes.
    failover.check(WIFI_DEMOTED)
    assert len(links.nmcli()) == 1

    links.up.add("wlan0")
    for _ in range(RECOVER_CHECKS - 1):
        failover.check(WIFI_DEMOTED)
    assert len(links.nmcli()) == 1
    failover.check(WIFI_DEMOTED)
    assert links.nmcli()[-1] == ["device", "reapply", "wlan0"]
    assert health.entries[-1] == ("info", "Wi-Fi is passing traffic again; switched back to it")
    assert not failover.fallback and failover.fallback_reason is None


def test_flapping_link_must_recover_in_a_row():
    links = Links(up={"wwan0"})
    failover = Failover(Health(), links, hosts=("1.1.1.1",))
    for _ in range(RECOVER_CHECKS * 2):
        links.up ^= {"wlan0"}
        failover.check(WIFI_DEMOTED)
    assert links.nmcli() == []


def test_stays_put_when_no_link_works():
    links, health = Links(up=()), Health()
    failover = Failover(health, links, hosts=("1.1.1.1",))
    for _ in range(FAIL_CHECKS + 3):
        failover.check(WIFI_FIRST)
    assert links.nmcli() == []
    assert health.entries == [("warning", "Wi-Fi is not passing traffic and no other link is")]
    assert not failover.fallback


def test_tries_second_probe_host_before_calling_a_link_down():
    def runner(args, capture_output, text, timeout):
        return subprocess.CompletedProcess(args, 0 if args[-1] == "8.8.8.8" else 1, "", "")

    assert Failover(Health(), runner).passes("wlan0") is True


def test_missing_ping_changes_nothing():
    def runner(args, capture_output, text, timeout):
        raise FileNotFoundError

    health = Health()
    failover = Failover(health, runner)
    for _ in range(FAIL_CHECKS + 1):
        failover.check(WIFI_FIRST)
    assert failover.passes("wlan0") is None and health.entries == []


def test_nmcli_refusal_is_logged_once():
    links = Links(up={"wwan0"}, nmcli_code=4, nmcli_error="Error: not authorized")
    health = Health()
    failover = Failover(health, links, hosts=("1.1.1.1",))
    for _ in range(FAIL_CHECKS * 3):
        failover.check(WIFI_FIRST)
    assert health.entries == [("error", "Could not change the route: Error: not authorized")]
    assert not failover.fallback


def test_restart_during_fallback_picks_up_from_the_route_table():
    failover = Failover(Health(), Links(up={"wwan0"}), hosts=("1.1.1.1",))
    failover.check(WIFI_DEMOTED)
    assert failover.fallback and failover.fallback_reason == "Wi-Fi is not passing traffic"


CELLULAR_FIRST = [Route("wwan0", 600), Route("wlan0", 700)]
WIFI_PROMOTED = [Route("wlan0", PROMOTED_METRIC), Route("wwan0", 600)]


def test_failing_cellular_is_never_modified_wifi_is_raised_instead():
    links, health = Links(up={"wlan0"}), Health()
    failover = Failover(health, links, hosts=("1.1.1.1",))
    for _ in range(FAIL_CHECKS):
        failover.check(CELLULAR_FIRST)
    assert links.nmcli() == [
        ["device", "modify", "wlan0", "ipv4.route-metric", str(PROMOTED_METRIC)]
    ]
    assert health.entries == [
        ("warning", "cellular stopped passing traffic; switched to Wi-Fi until it recovers")
    ]
    assert failover.fallback

    # A restarted controller sees the raised Wi-Fi and keeps probing cellular.
    failover = Failover(health, links, hosts=("1.1.1.1",))
    failover.check(WIFI_PROMOTED)
    assert failover.fallback and failover.fallback_reason == "cellular is not passing traffic"
    links.up.add("wwan0")
    for _ in range(RECOVER_CHECKS):
        failover.check(WIFI_PROMOTED)
    assert links.nmcli()[-1] == ["device", "reapply", "wlan0"]
    assert health.entries[-1] == ("info", "cellular is passing traffic again; switched back to it")
    assert not any("wwan0" in call for call in links.nmcli())


def test_cellular_preferred_with_only_another_modem_stays_put():
    links = Links(up={"wwan1"})
    failover = Failover(Health(), links, hosts=("1.1.1.1",))
    for _ in range(FAIL_CHECKS + 1):
        failover.check([Route("wwan0", 600), Route("wwan1", 700)])
    assert links.nmcli() == []


def test_connected_modem_without_a_route_is_reconnected():
    now = [0.0]
    links, health = Links(), Health()
    failover = Failover(health, links, hosts=("1.1.1.1",), clock=lambda: now[0])
    wifi_only = [Route("wlan0", 600)]
    for _ in range(STRANDED_CHECKS - 1):
        failover.check(wifi_only, modem_connected=True)
    assert links.nmcli() == []
    failover.check(wifi_only, modem_connected=True)
    assert links.nmcli()[-1] == ["connection", "up", "2222-bbbb"]
    assert health.entries == [
        ("warning", "Cellular is connected but has no route; reconnecting it")
    ]

    calls = len(links.nmcli())
    failover.check(wifi_only, modem_connected=True)  # not again within ten minutes
    assert len(links.nmcli()) == calls
    now[0] = 601
    failover.check(wifi_only, modem_connected=True)
    assert links.nmcli()[-1] == ["connection", "up", "2222-bbbb"]

    failover.check(wifi_only, modem_connected=False)  # a modem that is not connected is left alone
    failover.check([*wifi_only, Route("wwan0", 700)], modem_connected=True)
    assert len(health.entries) == 2
