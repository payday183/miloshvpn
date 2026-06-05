#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
DOCKER_BIN=${DOCKER_BIN:-docker}

cd "$REPO_DIR"

if command -v "$DOCKER_BIN" >/dev/null 2>&1; then
    "$DOCKER_BIN" compose up -d --build
else
    echo "$DOCKER_BIN command was not found" >&2
    exit 127
fi
