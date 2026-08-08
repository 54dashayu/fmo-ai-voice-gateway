#!/usr/bin/env bash
set -u

APP_DIR="/opt/fmo-ai-gateway"
ENV_FILE="/etc/fmo-ai-gateway.env"
CONFIG_FILE="/etc/fmo-ai-gateway/gateway-config.json"
failures=0

check() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "[OK] $label"; else echo "[FAIL] $label"; failures=$((failures+1)); fi
}

echo "FMO AI Voice Gateway doctor (no secrets are displayed)"
check "Linux" test "$(uname -s)" = "Linux"
check "application directory" test -f "$APP_DIR/server.py"
check "virtualenv Python" test -x "$APP_DIR/venv/bin/python"
check "libopus" "$APP_DIR/venv/bin/python" -c 'import ctypes.util; raise SystemExit(0 if ctypes.util.find_library("opus") else 1)'
check "environment file" test -f "$ENV_FILE"
check "environment permissions" bash -c 'mode=$(stat -c %a "$1"); test "$mode" = 600 || test "$mode" = 640' _ "$ENV_FILE"
check "gateway config" "$APP_DIR/venv/bin/python" -m json.tool "$CONFIG_FILE"
check "gateway service" systemctl is-active --quiet fmo-ai-gateway.service
check "MQTT monitor service" systemctl is-active --quiet fmo-ai-mqtt-monitor.service
check "gateway health" curl -fsS --max-time 3 http://127.0.0.1:18788/health

if ((failures)); then
  echo "$failures check(s) failed. PTT must remain disabled until all required checks pass."
  exit 1
fi
echo "All checks passed. This does not authorize or prove a real PTT transmission."
