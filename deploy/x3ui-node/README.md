# MiloshVPN 3x-ui node

Use this folder when you buy a new server and want to attach it to the backend as a VPN node.

## Start 3x-ui

```sh
cp node.env.example .env
docker compose -f compose.x3ui-node.yaml up -d
```

Open the panel:

```text
http://SERVER_IP:2053
```

Then add the node in backend admin:

```text
http://BACKEND_IP:8081/admin
```

Recommended values:

- `mode`: `live`
- `3x-ui URL`: `http://SERVER_IP:2053`
- `Inbound ID`: the VLESS inbound id from 3x-ui
- `Public host`: `SERVER_IP`
- `Public port`: VLESS inbound port

## Abuse policy

There is no perfect technical guarantee that a VPN can block every illegal resource. The baseline here is:

- block BitTorrent at Xray routing level;
- block common P2P/Tor/anonymous proxy ports at Xray and firewall levels;
- create backend-issued clients with `limitIp=1`, so one key can use one concurrent source IP;
- block common ad/tracker domains, including Google/YouTube ad domains where domain routing can help;
- force safe DNS and DNS blocklists where possible;
- enable HTTP/TLS/QUIC sniffing in route-only mode so protocol and domain rules actually match;
- send a Happ routing profile that keeps Russian IPs/domains and private networks outside the tunnel;
- shape every public Xray inbound to 40 Mbit/s (5 MB/s) in both directions;
- monitor traffic and active clients from backend admin;
- revoke expired users automatically.

Apply `xray-routing-policy.json` in 3x-ui Xray routing settings. The important rules are routed to the `blocked` outbound and cover BitTorrent protocol, common torrent ports, common Tor/proxy ports, Tor domains, and ad/tracker domains.

YouTube ads cannot be perfectly removed with VPN domain routing alone because some ads share normal YouTube delivery domains. The policy blocks common ad/tracker domains without blocking `googlevideo.com`, so playback should keep working.

The Happ preset is stored in `happ-routing-ru-bypass.json`. Other Xray clients can use the routing fragment in `xray-client-routing-ru-bypass.json`; ordinary VLESS/Hysteria share links cannot carry client routing rules by themselves.

Apply `nftables-abuse-guard.nft` on the host if you use nftables:

```sh
sudo nft -f nftables-abuse-guard.nft
```

Review the rules before applying them. Firewall policies are host-level and can interrupt existing services if ports overlap.

Install persistent firewall and per-inbound traffic shaping on a native 3x-ui node:

```sh
sudo install -d -m 0755 /etc/nftables.d
sudo install -m 0644 nftables-abuse-guard.nft /etc/nftables.d/miloshvpn-abuse-guard.nft
sudo install -m 0755 x3ui-tc-sync.sh /usr/local/sbin/miloshvpn-x3ui-tc-sync
sudo install -m 0644 ../systemd/miloshvpn-abuse-guard.service /etc/systemd/system/
sudo install -m 0644 ../systemd/miloshvpn-x3ui-tc-sync.service /etc/systemd/system/
sudo install -m 0644 ../systemd/miloshvpn-x3ui-tc-sync.path /etc/systemd/system/
sudo install -m 0644 ../systemd/miloshvpn-x3ui-tc-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now miloshvpn-abuse-guard.service
sudo systemctl enable --now miloshvpn-x3ui-tc-sync.path miloshvpn-x3ui-tc-sync.timer
sudo systemctl start miloshvpn-x3ui-tc-sync.service
```

Dedicated one-client inbounds receive a true per-key limit. Legacy inbounds shared by several clients receive a shared 40 Mbit/s cap because Linux traffic control cannot distinguish users inside the same encrypted listening port.

## Host network tuning

For small VPN nodes, apply the sysctl baseline from this repo to reduce swap stalls and make mobile TCP streams recover better from MTU issues:

```sh
sudo install -m 0644 ../systemd/99-miloshvpn-network.conf /etc/sysctl.d/99-miloshvpn-network.conf
sudo sysctl --system
```

## Backend load balancing

Backend uses `NODE_SELECTION_MODE=least_loaded` by default. It chooses an online active node with free slots and lower score by:

- active private keys / `max_clients`;
- clients reported by 3x-ui;
- CPU/RAM/disk usage;
- latency;
- node status.
