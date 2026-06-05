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

- `Профиль`
- `Моя подписка`
- `Купить пакет`
- `Продлить`
- `Бесплатный ключ`
- `Инструкция`
- `Админка` for admins

Admin commands:

```text
/add_admin TELEGRAM_ID
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
- rotate the public free key.
- manually clean expired subscriptions.

The bundled `x3ui` Docker service is seeded as the first node. Remote nodes work the same way: run 3x-ui on another server, expose its panel URL to the backend, then add it in `/admin`.

Node monitoring uses official 3x-ui panel APIs:

- `/login`;
- `/panel/api/inbounds/list`;
- `/panel/api/server/status`.

The worker refreshes node status every `NODE_STATUS_POLL_INTERVAL_SECONDS`.

## Payments

Plans:

- `75 RUB` - cheap plan for 30 days, 50 GB;
- `95 RUB` - unlimited plan for 30 days.

Every paid plan is issued for one month (`30` days). Renewing an active subscription adds another 30 days to the current expiry date.

Payment matching works without webhooks:

1. User clicks `Купить пакет`.
2. Bot creates a pending order with a unique code like `MILO-123456-ABCDEF`.
3. User sends a DonationAlerts donation and puts that code into the donation message.
4. Worker polls `GET /api/v1/alerts/donations`.
5. Backend finds the code, checks amount/currency and activates the subscription for that Telegram user.

Set these in `.env`:

```env
DONATIONALERTS_TOKEN=
DONATIONALERTS_DONATE_URL=
```

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

## Public Free Key

The worker rotates the public key every 24 hours.

```env
PUBLIC_KEY_ENABLED=true
PUBLIC_KEY_ROTATE_HOURS=24
PUBLIC_KEY_CHAT_ID=
NODE_STATUS_POLL_INTERVAL_SECONDS=60
EXPIRED_SUBSCRIPTION_CLEANUP_ENABLED=true
EXPIRED_SUBSCRIPTION_CLEANUP_INTERVAL_SECONDS=300
```

If `PUBLIC_KEY_CHAT_ID` is set, the worker posts the new free key into that Telegram chat/channel. The bot must be an admin in the target channel. The public post text includes the free VLESS key in a copyable block.

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
