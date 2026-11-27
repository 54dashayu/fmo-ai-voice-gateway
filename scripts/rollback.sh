#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/opt/fmo-ai-gateway"
CURRENT_LINK="$APP_DIR/current"
PREVIOUS_FILE="/var/lib/fmo-ai-gateway/previous-release"
[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "run as root with sudo" >&2; exit 1; }
[[ -r "$PREVIOUS_FILE" ]] || { echo "no previous release is recorded" >&2; exit 1; }
previous="$(head -n 1 "$PREVIOUS_FILE")"
[[ "$previous" == "$APP_DIR/releases/"* && -d "$previous" ]] || { echo "recorded rollback target is invalid" >&2; exit 1; }
current="$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)"
ln -s "$previous" "$APP_DIR/.current-rollback"
mv -Tf "$APP_DIR/.current-rollback" "$CURRENT_LINK"
if [[ -n "$current" ]]; then printf '%s\n' "$current" > "$PREVIOUS_FILE"; chmod 0600 "$PREVIOUS_FILE"; fi
systemctl daemon-reload
systemctl restart fmo-ai-gateway.service fmo-ai-mqtt-monitor.service
echo "rolled back to $(basename "$previous"); configuration, secrets, schedules and state were preserved"
