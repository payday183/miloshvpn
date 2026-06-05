#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
PROJECT_NAME=${DEV_COMPOSE_PROJECT_NAME:-miloshvpn-dev}
DOCKER_BIN=${DOCKER_BIN:-docker}

cd "$REPO_DIR"

compose() {
    APP_PORT="${DEV_APP_PORT:-8081}" \
    X3UI_PANEL_PORT="${X3UI_DEV_PANEL_PORT:-2054}" \
    X3UI_VLESS_PORT="${X3UI_DEV_VLESS_PORT:-8444}" \
    VLESS_PUBLIC_PORT="${X3UI_DEV_VLESS_PORT:-8444}" \
    "$DOCKER_BIN" compose -p "$PROJECT_NAME" -f compose.yaml -f compose.dev.yaml "$@"
}

case "${1:-up}" in
    up)
        compose up -d --build
        ;;
    down)
        compose down
        ;;
    restart)
        compose restart
        ;;
    rebuild)
        compose up -d --build --force-recreate
        ;;
    logs)
        compose logs -f --tail=100
        ;;
    ps|status)
        compose ps
        ;;
    check)
        compose config >/dev/null
        ;;
    *)
        echo "Usage: $0 [up|down|restart|rebuild|logs|ps|status|check]" >&2
        exit 2
        ;;
esac
