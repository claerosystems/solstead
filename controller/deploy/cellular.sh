#!/usr/bin/env bash
# Bring up cellular data on the Pi through ModemManager and NetworkManager.
# Run as root on the Pi:  sudo controller/deploy/cellular.sh [apn]      default sp.mb.com (Public Mobile)
# Creates a NetworkManager connection that reconnects by itself, with a higher route metric than
# Wi-Fi so a bench Pi keeps using Wi-Fi while it is there, and IPv6 off. Until the customer VPN
# exists nothing may be reached over cellular, so an nftables table drops every unsolicited inbound
# packet on the cellular interface (the controller API and SSH listen on all interfaces).
# It also lets the controller's user (solstead, or the user running sudo for the user-level
# service) change route metrics through NetworkManager, so the owner can pick the preferred link.
set -euo pipefail
APN="${1:-sp.mb.com}"
NAME=solstead-cellular
METRIC=700  # Wi-Fi is 600 and Ethernet 100 on the bench Pi; lower wins
RULES=/etc/nftables.d/solstead-cellular.nft
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
command -v nft >/dev/null || { echo "nftables is not installed (apt install nftables)" >&2; exit 1; }

mkdir -p /etc/nftables.d
cat > "$RULES" <<'NFT'
# Solstead: nothing on the controller is reachable over cellular until the VPN exists.
table inet solstead_cellular
delete table inet solstead_cellular
table inet solstead_cellular {
	chain input {
		type filter hook input priority -10; policy accept;
		iifname "wwan*" ct state established,related accept
		iifname "wwan*" counter drop comment "no inbound over cellular"
	}
	chain forward {
		type filter hook forward priority -10; policy accept;
		iifname "wwan*" counter drop comment "no routing from cellular"
		oifname "wwan*" counter drop comment "no routing to cellular"
	}
}
NFT
grep -q 'include "/etc/nftables.d/\*.nft"' /etc/nftables.conf || printf '\ninclude "/etc/nftables.d/*.nft"\n' >> /etc/nftables.conf
nft -f "$RULES"
systemctl enable nftables >/dev/null 2>&1 || true
echo "Inbound over cellular blocked ($RULES)."

# polkit only lets active login sessions change NetworkManager; the controller is a service.
POLKIT=/etc/polkit-1/rules.d/50-solstead-network.rules
USERS='"solstead"'
[ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ] && USERS="$USERS, \"$SUDO_USER\""
mkdir -p "$(dirname "$POLKIT")"
cat > "$POLKIT" <<RULES
// Solstead: the controller sets which uplink is preferred (route metrics) from the owner's app.
polkit.addRule(function(action, subject) {
  var users = [$USERS];
  if ((action.id == "org.freedesktop.NetworkManager.settings.modify.system" ||
       action.id == "org.freedesktop.NetworkManager.network-control") &&
      users.indexOf(subject.user) >= 0) {
    return polkit.Result.YES;
  }
});
RULES
chmod 644 "$POLKIT"
echo "Controller may change NetworkManager route metrics ($POLKIT, users: $USERS)."

if ! command -v mmcli >/dev/null; then
  apt-get install -y modemmanager libqmi-utils
fi
systemctl enable --now ModemManager
# A modem plugged in before ModemManager was installed never had its udev rules applied.
udevadm trigger --action=add --subsystem-match=tty --subsystem-match=usbmisc --subsystem-match=net
udevadm settle
echo "Waiting for ModemManager to find the modem..."
for _ in $(seq 30); do mmcli -L 2>/dev/null | grep -q /Modem/ && break; sleep 2; done
mmcli -L

nmcli connection delete "$NAME" >/dev/null 2>&1 || true
nmcli connection add type gsm ifname '*' con-name "$NAME" apn "$APN" \
  connection.autoconnect yes ipv4.route-metric "$METRIC" ipv6.method disabled
nmcli connection up "$NAME" || echo "Not up yet; NetworkManager keeps retrying on its own."
sleep 5
nmcli -f GENERAL.DEVICES,GENERAL.STATE,IP4.ADDRESS,IP4.GATEWAY connection show "$NAME" || true
ip route
