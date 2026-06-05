# miloshvpn

Minimal Docker and git auto-deploy setup.

## Run locally

```sh
cp .env.example .env
docker compose up -d --build
```

Open `http://localhost:8080`.

## Configure git remote

```sh
git remote add origin <your-git-url>
git push -u origin main
```

## Manual deploy

```sh
./scripts/auto-deploy.sh
```

The deploy script:

- fetches `origin/main` or the branch from `DEPLOY_BRANCH`;
- refuses to run when local uncommitted changes exist;
- updates only with a fast-forward merge;
- rebuilds and restarts Docker Compose.

It does not remove orphan containers automatically.

## Automatic deploy

Install the per-user systemd timer:

```sh
./scripts/install-auto-deploy-user.sh
```

Useful commands:

```sh
systemctl --user status miloshvpn-auto-deploy.timer
journalctl --user -u miloshvpn-auto-deploy.service -f
```

The timer runs every minute. If `origin` is not configured yet, it exits without changing anything.

## Git hook

This repository uses `deploy/git-hooks` as `core.hooksPath`. After a successful `git pull`, the `post-merge` hook rebuilds the Docker Compose service.
