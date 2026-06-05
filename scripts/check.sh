#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
DOCKER_BIN=${DOCKER_BIN:-docker}

cd "$REPO_DIR"

python3 -m compileall app

if command -v "$DOCKER_BIN" >/dev/null 2>&1; then
    if ! "$DOCKER_BIN" compose config >/tmp/miloshvpn-compose-check.log 2>&1; then
        cat /tmp/miloshvpn-compose-check.log >&2
        exit 1
    fi
    APP_PORT="${DEV_APP_PORT:-8081}" \
    X3UI_PANEL_PORT="${X3UI_DEV_PANEL_PORT:-2054}" \
    X3UI_VLESS_PORT="${X3UI_DEV_VLESS_PORT:-8444}" \
    VLESS_PUBLIC_PORT="${X3UI_DEV_VLESS_PORT:-8444}" \
    "$DOCKER_BIN" compose -p "${DEV_COMPOSE_PROJECT_NAME:-miloshvpn-dev}" -f compose.yaml -f compose.dev.yaml config >/dev/null
else
    echo "$DOCKER_BIN command was not found; skipped compose config checks." >&2
fi
