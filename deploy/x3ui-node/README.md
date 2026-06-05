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
- block common P2P/anonymous proxy ports at firewall level;
- force safe DNS and DNS blocklists where possible;
- monitor traffic and active clients from backend admin;
- revoke expired users automatically.

Apply `xray-routing-policy.json` in 3x-ui Xray routing settings. The important rule is the BitTorrent protocol rule routed to `blocked`.

Apply `nftables-abuse-guard.nft` on the host if you use nftables:

```sh
sudo nft -f nftables-abuse-guard.nft
```

Review the rules before applying them. Firewall policies are host-level and can interrupt existing services if ports overlap.

## Backend load balancing

Backend uses `NODE_SELECTION_MODE=least_loaded` by default. It chooses an online active node with free slots and lower score by:

- active private keys / `max_clients`;
- clients reported by 3x-ui;
- CPU/RAM/disk usage;
- latency;
- node status.
