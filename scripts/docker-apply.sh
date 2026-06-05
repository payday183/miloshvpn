#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

cd "$REPO_DIR"

if command -v docker >/dev/null 2>&1; then
    docker compose up -d --build
else
    echo "docker command was not found" >&2
    exit 127
fi
