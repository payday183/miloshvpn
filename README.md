# miloshvpn

Telegram-first VPN service without a domain.

Stack:

- FastAPI control center;
- Telegram bot frontend with long polling;
- PostgreSQL for users, orders, subscriptions and keys;
- Redis for cache/runtime state;
- DonationAlerts polling, no webhooks;
- test 3x-ui container for VLESS key provisioning.

## Development

```sh
./scripts/dev.sh up
./scripts/dev.sh logs
./scripts/dev.sh ps
```

Check code and Compose config:

```sh
./scripts/check.sh
```

Dev API:

```text
http://localhost:8081
```

Dev 3x-ui panel port:

```text
http://localhost:2054
```

The bot runs in polling mode, so a domain is not required.

If Docker Desktop is installed on Windows, enable WSL integration for this distro before running Compose.

## Telegram Bot

Main buttons:

- `Купить`
- `Профиль`
- `Инструкция`
- `Политика проекта`
- `Поддержка`
- `Админка` for admins

Admin commands:

```text
/add_admin TELEGRAM_ID
/admin_key
/rotate_free
/post_free
```

Admins are defined only by `ADMIN_IDS` in `.env`.

## Admin Panel

Web admin panel:

```text
http://localhost:8081/admin
```

Set `ADMIN_WEB_TOKEN` to protect it:

```env
ADMIN_WEB_TOKEN=change_me
ADMIN_PANEL_URL=http://localhost:8081/admin
```

The panel can:

- connect 3x-ui nodes;
- activate or disable nodes;
- show active subscriptions;
- show the node used by each active VPN key;
- show local keys created by the backend on each node;
- show remote clients reported by 3x-ui;
- show traffic up/down from 3x-ui inbounds;
- show node latency, CPU, RAM and disk metrics when `/panel/api/server/status` returns them;
- show pending DonationAlerts payments;
- force DonationAlerts polling;
- create admin VPN keys without payment;
- rotate the public free key;
- manually clean expired subscriptions.

The bundled `x3ui` Docker service is seeded as the first node. Remote nodes work the same way: run 3x-ui on another server, expose its panel URL to the backend, then add it in `/admin`.

Node monitoring uses official 3x-ui panel APIs:

- `/login`;
- `/panel/api/inbounds/list`;
- `/panel/api/server/status`.

The worker refreshes node status every `NODE_STATUS_POLL_INTERVAL_SECONDS`.

New VPN keys use automatic node selection by default:

```env
NODE_SELECTION_MODE=least_loaded
NODE_OVERLOAD_CPU_PERCENT=85
NODE_OVERLOAD_MEMORY_PERCENT=90
NODE_OVERLOAD_DISK_PERCENT=90
```

The backend skips offline or overloaded nodes and prefers the active online node with more free slots and lower CPU/RAM/disk/latency.

New server template:

```text
deploy/x3ui-node/
```

That folder contains a standalone 3x-ui Compose file, an Xray routing policy for blocking BitTorrent, and an nftables baseline for common abuse ports. Apply and review those rules on every new node before adding it to `/admin`.

## Payments

Plans:

- `75 RUB` - cheap plan for 30 days, 50 GB;
- `95 RUB` - unlimited plan for 30 days.
- free trial - 7 days, 10 GB by default.

Every paid plan is issued for one month (`30` days). Renewing an active subscription adds another 30 days to the current expiry date.

Payment matching works without webhooks:

1. User clicks `Купить`.
2. Bot creates a pending order with a unique code like `MILO-123456-ABCDEF`.
3. Bot sends a lightweight click-to-copy page; one click copies the code and opens DonationAlerts.
4. Worker polls `GET /api/v1/alerts/donations`.
5. Backend finds the code, checks currency and the required tariff amount, then activates the subscription for that Telegram user.

Repeated clicks on the same plan reuse the user's active pending order instead of creating duplicate unpaid orders. If the user switches to another plan, older active pending orders are expired.

Set these in `.env`:

```env
DONATIONALERTS_TOKEN=
DONATIONALERTS_DONATE_URL=
DONATIONALERTS_FETCH_LIMIT=50
DONATIONALERTS_FETCH_PAGES=3
```

Trial settings:

```env
FREE_TRIAL_ENABLED=true
FREE_TRIAL_DAYS=7
FREE_TRIAL_TRAFFIC_GB=10
```

DonationAlerts responses are paginated. The backend fetches up to `DONATIONALERTS_FETCH_PAGES` pages with `DONATIONALERTS_FETCH_LIMIT` items each, using a short delay between pages to stay under the API rate limit.

## 3x-ui

By default the app uses:

```env
X3UI_MODE=mock
```

Mock mode generates VLESS links and lets the business flow work before real 3x-ui is configured.

For live 3x-ui:

```env
X3UI_MODE=live
X3UI_USERNAME=admin
X3UI_PASSWORD=admin
X3UI_INBOUND_ID=1
VLESS_PUBLIC_HOST=your-server-ip
VLESS_PUBLIC_PORT=8443
VLESS_QUERY=type=tcp&security=none
```

The test limit is controlled by:

```env
X3UI_MAX_CLIENTS=10
```

### Admin-only VLESS TCP Reality test

Keep the public user node on the normal France inbound while testing Reality on the local admin 3x-ui container.

The local admin 3x-ui container exposes a dedicated Reality port while keeping the regular admin VLESS port on `8443`:

```env
X3UI_ADMIN_REALITY_PORT=443
```

In the local 3x-ui panel, create a separate inbound:

```text
Protocol: VLESS
Transport: TCP
Security: Reality
Port: 443
Flow: xtls-rprx-vision, if the inbound uses Vision
```

Keep the reserved admin node as the real admin server. Configure the Reality test inbound separately:

```text
node id: 1
regular VLESS inbound: 1 / port 8443
Reality test inbound: 2 / port 443
```

Then set:

```env
ADMIN_REALITY_INBOUND_ID=2
ADMIN_REALITY_PUBLIC_HOST=admin-server-ip
ADMIN_REALITY_PUBLIC_PORT=443
ADMIN_REALITY_VLESS_QUERY=type=tcp&security=reality&encryption=none&flow=xtls-rprx-vision&fp=chrome&sni=example.com&pbk=PUBLIC_KEY&sid=SHORT_ID&spx=%2F
```

Use `/admin_reality_key` or the `Создать Reality test key` button in `/admin` to issue test keys. `/admin_key` stays on the regular admin VLESS inbound. User, trial and public 24h keys are still issued from active non-reserved nodes, so the France user node stays on `type=tcp&security=none`.

## Public Free Key

The worker rotates the public key every 24 hours.

```env
PUBLIC_KEY_ENABLED=true
PUBLIC_KEY_ROTATE_HOURS=24
PUBLIC_KEY_TRAFFIC_GB=500
PUBLIC_KEY_CHAT_ID=
NODE_STATUS_POLL_INTERVAL_SECONDS=60
EXPIRED_SUBSCRIPTION_CLEANUP_ENABLED=true
EXPIRED_SUBSCRIPTION_CLEANUP_INTERVAL_SECONDS=300
```

If `PUBLIC_KEY_CHAT_ID` is set, the worker posts the new free key into that Telegram chat/channel. It accepts `@channel`, a numeric chat ID, or a public `https://t.me/channel` link. The bot must be an admin in the target channel. The public post text includes the free VLESS key in a copyable block and is selected from seeded templates.

Expired paid subscriptions are cleaned automatically. The worker marks them as expired, deletes the private VPN client from 3x-ui, and keeps the Telegram user/payment history in PostgreSQL.

## Production

```sh
cp .env.example .env
docker compose up -d --build
```

Production API:

```text
http://localhost:8080
```

## GitHub

Current branches:

- `main` - deploy branch;
- `dev` - development branch;
- `backup-old-github-main` - backup of previous GitHub contents.

Development flow:

```sh
git add .
git commit -m "Describe changes"
git push
```

The local `dev` branch tracks `origin/dev`, so `git push` is enough during development.

## Auto Deploy

```sh
./scripts/install-auto-deploy-user.sh
```

The timer pulls `origin/main` and runs:

```sh
docker compose up -d --build
```

It skips auto-deploy when the working copy is on `dev`.
