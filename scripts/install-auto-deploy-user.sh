#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
UNIT_DIR=${XDG_CONFIG_HOME:-"$HOME/.config"}/systemd/user
SERVICE_NAME=miloshvpn-auto-deploy.service
TIMER_NAME=miloshvpn-auto-deploy.timer

mkdir -p "$UNIT_DIR"

sed "s#__REPO_DIR__#$REPO_DIR#g" "$REPO_DIR/deploy/systemd/$SERVICE_NAME" > "$UNIT_DIR/$SERVICE_NAME"
sed "s#__REPO_DIR__#$REPO_DIR#g" "$REPO_DIR/deploy/systemd/$TIMER_NAME" > "$UNIT_DIR/$TIMER_NAME"

systemctl --user daemon-reload
systemctl --user enable --now "$TIMER_NAME"

echo "Installed user timer: $TIMER_NAME"
echo "Check status: systemctl --user status $TIMER_NAME"
echo "View logs: journalctl --user -u $SERVICE_NAME -f"
