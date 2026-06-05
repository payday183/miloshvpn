#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
PROJECT_NAME=${DEV_COMPOSE_PROJECT_NAME:-miloshvpn-dev}

cd "$REPO_DIR"

compose() {
    docker compose -p "$PROJECT_NAME" -f compose.dev.yaml "$@"
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
    *)
        echo "Usage: $0 [up|down|restart|rebuild|logs|ps|status]" >&2
        exit 2
        ;;
esac
