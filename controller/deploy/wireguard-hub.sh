#!/usr/bin/env bash
# WireGuard hub on an owner-controlled cloud server, so the phone can reach a controller that sits
# behind carrier NAT: the controller keeps a tunnel open to the hub and the phone joins the same
# tunnel network. The hub forwards only phone -> controller API; it holds no Solstead data or keys.
#
# Run as root on the server (Debian/Ubuntu):
#   wireguard-hub.sh setup                         install, create the hub key, firewall, start wg0
#   wireguard-hub.sh add-peer NAME IP PUBLIC_KEY   e.g. add-peer controller 10.77.0.2 <key>
#   wireguard-hub.sh phone [NAME] [IP]             new phone peer; shows its config as a QR code
#
# Addresses: hub 10.77.0.1, controller 10.77.0.2, phones 10.77.0.3 and up.
set -euo pipefail
NET=10.77.0
PORT=51820
API_PORT=8443
CONF=/etc/wireguard/wg0.conf
RULES=/etc/nftables.d/solstead-hub.nft
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }

public_ip() { curl -4 -s --max-time 10 https://api.ipify.org; }

firewall() {
	mkdir -p /etc/nftables.d
	cat > "$RULES" <<NFT
# Solstead hub: SSH and WireGuard in; between tunnel peers only phones -> controller API.
table inet solstead_hub
delete table inet solstead_hub
table inet solstead_hub {
	chain input {
		type filter hook input priority 0; policy drop;
		iif "lo" accept
		ct state established,related accept
		ip protocol icmp accept
		ip6 nexthdr ipv6-icmp accept
		tcp dport 22 accept
		udp dport $PORT accept
	}
	chain forward {
		type filter hook forward priority 0; policy drop;
		ct state established,related accept
		iifname "wg0" oifname "wg0" ip saddr $NET.3-$NET.254 ip daddr $NET.2 tcp dport $API_PORT accept
	}
}
NFT
	grep -q 'include "/etc/nftables.d/\*.nft"' /etc/nftables.conf 2>/dev/null \
		|| printf '\ninclude "/etc/nftables.d/*.nft"\n' >> /etc/nftables.conf
	nft -f "$RULES"
	systemctl enable nftables >/dev/null 2>&1 || true
}

setup() {
	DEBIAN_FRONTEND=noninteractive apt-get update -q
	DEBIAN_FRONTEND=noninteractive apt-get install -y -q wireguard-tools nftables qrencode curl
	umask 077
	if [ ! -f /etc/wireguard/hub.key ]; then
		wg genkey > /etc/wireguard/hub.key
	fi
	if [ ! -f "$CONF" ]; then
		cat > "$CONF" <<CONF
[Interface]
Address = $NET.1/24
ListenPort = $PORT
PrivateKey = $(cat /etc/wireguard/hub.key)
CONF
	fi
	echo 'net.ipv4.ip_forward = 1' > /etc/sysctl.d/90-solstead-hub.conf
	sysctl -q -p /etc/sysctl.d/90-solstead-hub.conf
	firewall
	systemctl enable --now wg-quick@wg0
	echo "Hub ready at $(public_ip):$PORT"
	echo "Hub public key: $(wg pubkey < /etc/wireguard/hub.key)"
}

add_peer() {
	local name=$1 ip=$2 key=$3
	[[ $ip =~ ^$NET\.([2-9]|[1-9][0-9]|1[0-9][0-9]|2[0-4][0-9]|25[0-4])$ ]] \
		|| { echo "IP must be in $NET.2-254" >&2; exit 1; }
	if grep -q "^# $name\$" "$CONF" || grep -q "AllowedIPs = $ip/32" "$CONF"; then
		echo "A peer called $name or using $ip already exists in $CONF" >&2
		exit 1
	fi
	printf '\n[Peer]\n# %s\nPublicKey = %s\nAllowedIPs = %s/32\n' "$name" "$key" "$ip" >> "$CONF"
	wg set wg0 peer "$key" allowed-ips "$ip/32"
	echo "Added $name at $ip"
}

phone() {
	local name=${1:-phone} ip=${2:-$NET.3}
	local key
	key=$(wg genkey)
	add_peer "$name" "$ip" "$(wg pubkey <<< "$key")"
	local config
	config=$(printf '[Interface]\nPrivateKey = %s\nAddress = %s/32\n\n[Peer]\nPublicKey = %s\nEndpoint = %s:%s\nAllowedIPs = %s.2/32\nPersistentKeepalive = 25\n' \
		"$key" "$ip" "$(wg pubkey < /etc/wireguard/hub.key)" "$(public_ip)" "$PORT" "$NET")
	# The phone's private key exists only in this QR code and the phone; it is not stored here.
	qrencode -t ansiutf8 <<< "$config"
	echo "Scan with the WireGuard app (+ > Create from QR code). The phone reaches only the controller, at https://$NET.2:$API_PORT"
}

case "${1:-}" in
	setup) setup ;;
	add-peer) [ $# -eq 4 ] || { echo "usage: $0 add-peer NAME IP PUBLIC_KEY" >&2; exit 1; }; add_peer "$2" "$3" "$4" ;;
	phone) phone "${2:-}" "${3:-}" ;;
	*) sed -n '2,12p' "$0"; exit 1 ;;
esac
