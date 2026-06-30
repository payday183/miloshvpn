#!/bin/sh
set -eu

XRAY_CONFIG=${XRAY_CONFIG:-/usr/local/x-ui/bin/config.json}
PROFILE_RATE=${PROFILE_RATE:-40mbit}
PROFILE_BURST=${PROFILE_BURST:-4mb}
VPN_INTERFACE=${VPN_INTERFACE:-}
STATE_FILE=${STATE_FILE:-/run/miloshvpn-x3ui-tc-sync.state}
SYNC_VERSION=2

if [ ! -r "$XRAY_CONFIG" ]; then
    echo "Xray config is not readable: $XRAY_CONFIG" >&2
    exit 1
fi

if [ -z "$VPN_INTERFACE" ]; then
    VPN_INTERFACE=$(ip -4 route show default | awk 'NR == 1 { for (i = 1; i <= NF; i++) if ($i == "dev") { print $(i + 1); exit } }')
fi
if [ -z "$VPN_INTERFACE" ]; then
    echo "Could not determine the public network interface" >&2
    exit 1
fi

PORTS=$(jq -r '
    .inbounds[]
    | select(.tag != "api")
    | select((.listen // "") | test("^(127\\.|::1$)") | not)
    | select(.protocol == "vless" or .protocol == "vmess" or .protocol == "trojan"
        or .protocol == "shadowsocks" or .protocol == "hysteria")
    | .port
' "$XRAY_CONFIG" | sort -nu)

if [ -z "$PORTS" ]; then
    echo "No public Xray inbound ports found; leaving qdiscs unchanged"
    exit 0
fi

FINGERPRINT=$(printf '%s\n%s\n%s\n%s\n' "$SYNC_VERSION" "$VPN_INTERFACE" "$PROFILE_RATE" "$PORTS" | sha256sum | awk '{print $1}')
if [ -r "$STATE_FILE" ] && [ "$(sed -n '1p' "$STATE_FILE")" = "$FINGERPRINT" ]; then
    if tc qdisc show dev "$VPN_INTERFACE" | grep -q 'htb 1:' \
        && tc qdisc show dev "$VPN_INTERFACE" | grep -q 'clsact'; then
        echo "Traffic shaping is already current on $VPN_INTERFACE"
        exit 0
    fi
fi

restore_default_qdisc() {
    if [ "${APPLIED:-0}" != "1" ]; then
        tc qdisc del dev "$VPN_INTERFACE" root 2>/dev/null || true
        tc qdisc del dev "$VPN_INTERFACE" clsact 2>/dev/null || true
        tc qdisc add dev "$VPN_INTERFACE" root fq_codel 2>/dev/null || true
    fi
}
trap restore_default_qdisc EXIT INT TERM

tc qdisc del dev "$VPN_INTERFACE" root 2>/dev/null || true
tc qdisc del dev "$VPN_INTERFACE" clsact 2>/dev/null || true
tc qdisc add dev "$VPN_INTERFACE" root handle 1: htb default fff0
tc class add dev "$VPN_INTERFACE" parent 1: classid 1:1 htb rate 10gbit ceil 10gbit quantum 1514
tc class add dev "$VPN_INTERFACE" parent 1:1 classid 1:fff0 htb rate 10gbit ceil 10gbit quantum 1514
tc qdisc add dev "$VPN_INTERFACE" parent 1:fff0 handle fff0: fq_codel
tc qdisc add dev "$VPN_INTERFACE" clsact

INDEX=16
for PORT in $PORTS; do
    MINOR=$(printf '%x' "$INDEX")
    PREF=$((1000 + INDEX * 4))

    tc class add dev "$VPN_INTERFACE" parent 1:1 classid "1:$MINOR" htb \
        rate "$PROFILE_RATE" ceil "$PROFILE_RATE" burst 256k cburst 256k quantum 1514
    tc qdisc add dev "$VPN_INTERFACE" parent "1:$MINOR" handle "$MINOR:" fq_codel

    tc filter add dev "$VPN_INTERFACE" parent 1: protocol ip pref "$PREF" \
        flower ip_proto tcp src_port "$PORT" classid "1:$MINOR"
    tc filter add dev "$VPN_INTERFACE" parent 1: protocol ip pref "$((PREF + 1))" \
        flower ip_proto udp src_port "$PORT" classid "1:$MINOR"
    tc filter add dev "$VPN_INTERFACE" parent 1: protocol ipv6 pref "$((PREF + 2))" \
        flower ip_proto tcp src_port "$PORT" classid "1:$MINOR"
    tc filter add dev "$VPN_INTERFACE" parent 1: protocol ipv6 pref "$((PREF + 3))" \
        flower ip_proto udp src_port "$PORT" classid "1:$MINOR"

    tc filter add dev "$VPN_INTERFACE" ingress protocol ip pref "$PREF" \
        flower ip_proto tcp dst_port "$PORT" \
        action police rate "$PROFILE_RATE" burst "$PROFILE_BURST" conform-exceed drop/pipe
    tc filter add dev "$VPN_INTERFACE" ingress protocol ip pref "$((PREF + 1))" \
        flower ip_proto udp dst_port "$PORT" \
        action police rate "$PROFILE_RATE" burst "$PROFILE_BURST" conform-exceed drop/pipe
    tc filter add dev "$VPN_INTERFACE" ingress protocol ipv6 pref "$((PREF + 2))" \
        flower ip_proto tcp dst_port "$PORT" \
        action police rate "$PROFILE_RATE" burst "$PROFILE_BURST" conform-exceed drop/pipe
    tc filter add dev "$VPN_INTERFACE" ingress protocol ipv6 pref "$((PREF + 3))" \
        flower ip_proto udp dst_port "$PORT" \
        action police rate "$PROFILE_RATE" burst "$PROFILE_BURST" conform-exceed drop/pipe

    INDEX=$((INDEX + 1))
done

mkdir -p "$(dirname "$STATE_FILE")"
{
    printf '%s\n' "$FINGERPRINT"
    printf 'interface=%s\nrate=%s\nports=%s\n' "$VPN_INTERFACE" "$PROFILE_RATE" "$(printf '%s' "$PORTS" | tr '\n' ',')"
} > "$STATE_FILE"

APPLIED=1
trap - EXIT INT TERM
echo "Applied $PROFILE_RATE upload/download limit to $(printf '%s\n' "$PORTS" | wc -l) Xray inbound ports on $VPN_INTERFACE"
