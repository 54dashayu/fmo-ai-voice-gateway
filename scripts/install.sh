#!/usr/bin/env bash
set -Eeuo pipefail

VERSION="$(tr -d '[:space:]' < "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/VERSION")"
APP_DIR="/opt/fmo-ai-gateway"
CONFIG_DIR="/etc/fmo-ai-gateway"
ENV_FILE="/etc/fmo-ai-gateway.env"
STATE_DIR="/var/lib/fmo-ai-gateway"
SERVICE_USER="fmo-ai"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKIP_PACKAGES=false
NON_INTERACTIVE=false
DRY_RUN=false

usage() {
  echo "Usage: sudo ./scripts/install.sh [--non-interactive] [--skip-packages] [--dry-run]"
}

log() { echo "[fmo-ai-installer] $*"; }
die() { echo "[fmo-ai-installer] ERROR: $*" >&2; exit 1; }
run() {
  if $DRY_RUN; then printf '[dry-run]'; printf ' %q' "$@"; printf '\n'; else "$@"; fi
}

while (($#)); do
  case "$1" in
    --non-interactive) NON_INTERACTIVE=true ;;
    --skip-packages) SKIP_PACKAGES=true ;;
    --dry-run) DRY_RUN=true ;;
    -h|--help) usage; exit 0 ;;
    *) usage; die "unknown option: $1" ;;
  esac
  shift
done

[[ "$(uname -s)" == "Linux" ]] || die "Linux is required"
case "$(uname -m)" in x86_64|aarch64|arm64) ;; *) die "unsupported architecture: $(uname -m)" ;; esac
if ! $DRY_RUN && [[ ${EUID:-$(id -u)} -ne 0 ]]; then die "run as root with sudo"; fi
[[ -f "$SOURCE_DIR/ai-gateway/server.py" ]] || die "run the installer from the extracted project package"

install_packages() {
  $SKIP_PACKAGES && return 0
  [[ -r /etc/os-release ]] || die "cannot detect Linux distribution"
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}" in
    ubuntu|debian)
      run apt-get update
      run env DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip libopus0 ca-certificates curl
      ;;
    rocky|almalinux|centos|rhel)
      if command -v dnf >/dev/null 2>&1; then
        run dnf install -y python3.11 python3.11-pip opus ca-certificates curl
      else
        die "RHEL-family installation requires dnf and Python 3.11"
      fi
      ;;
    *) die "unsupported distribution ID=${ID:-unknown}; use --skip-packages after installing Python 3.10-3.12, venv and libopus" ;;
  esac
}

select_python() {
  local candidate version_ok
  for candidate in python3.12 python3.11 python3.10 python3; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    version_ok="$($candidate -c 'import sys; print(int((3,10) <= sys.version_info[:2] < (3,13)))')"
    if [[ "$version_ok" == "1" ]]; then PYTHON_BIN="$(command -v "$candidate")"; return 0; fi
  done
  die "Python 3.10-3.12 is required (Python 3.13+ removed audioop)"
}

install_packages
select_python
log "using $($PYTHON_BIN --version 2>&1)"

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  run useradd --system --home-dir "$STATE_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi
run install -d -o root -g root -m 0755 "$APP_DIR"
run install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 "$CONFIG_DIR" "$STATE_DIR" "$STATE_DIR/audio"

if ! $DRY_RUN; then
  cp -a "$SOURCE_DIR/ai-gateway/." "$APP_DIR/"
  install -d -o root -g root -m 0755 "$APP_DIR/scripts"
  install -o root -g root -m 0755 "$SOURCE_DIR/scripts/configure.py" "$APP_DIR/scripts/configure.py"
  install -o root -g root -m 0755 "$SOURCE_DIR/scripts/doctor.sh" "$APP_DIR/scripts/doctor.sh"
  "$PYTHON_BIN" -m venv "$APP_DIR/venv"
  "$APP_DIR/venv/bin/pip" install --disable-pip-version-check --no-cache-dir -r "$APP_DIR/requirements.txt"
fi

if [[ ! -f "$ENV_FILE" ]]; then
  run install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0600 "$SOURCE_DIR/ai-gateway/.env.example" "$ENV_FILE"
else
  log "preserving existing $ENV_FILE"
fi
if [[ ! -f "$CONFIG_DIR/gateway-config.json" ]]; then
  run install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0600 "$SOURCE_DIR/ai-gateway/gateway-config.example.json" "$CONFIG_DIR/gateway-config.json"
else
  log "preserving existing gateway-config.json"
fi
run install -o root -g root -m 0644 "$SOURCE_DIR/deploy/nginx-ai.conf.example" "$CONFIG_DIR/nginx-ai.conf.example"
run install -o root -g root -m 0644 "$SOURCE_DIR/deploy/emqx-ai-loopback.conf" "$CONFIG_DIR/emqx-ai-loopback.conf.example"
run install -o root -g root -m 0644 "$SOURCE_DIR/ai-gateway/fmo-ai-gateway.service" /etc/systemd/system/fmo-ai-gateway.service
run install -o root -g root -m 0644 "$SOURCE_DIR/ai-gateway/fmo-ai-mqtt-monitor.service" /etc/systemd/system/fmo-ai-mqtt-monitor.service
run install -d -m 0755 /etc/systemd/system/fmo-ai-gateway.service.d
run install -o root -g root -m 0644 "$SOURCE_DIR/deploy/20-product-config.conf" /etc/systemd/system/fmo-ai-gateway.service.d/20-product-config.conf

if ! $DRY_RUN; then
  systemctl daemon-reload
  if ! $NON_INTERACTIVE && [[ -t 0 ]]; then
    "$APP_DIR/venv/bin/python" "$APP_DIR/scripts/configure.py" --env-file "$ENV_FILE" --config-file "$CONFIG_DIR/gateway-config.json"
  fi
  systemctl enable --now fmo-ai-gateway.service
  systemctl enable --now fmo-ai-mqtt-monitor.service
fi

log "installation complete (version $VERSION)"
log "safety state: AI/ASR/TTS/auto-reply/hourly/PTT remain disabled"
log "run: sudo $APP_DIR/scripts/doctor.sh"
log "Nginx and EMQX examples: $CONFIG_DIR/*.example"
if $NON_INTERACTIVE; then
  log "run the configuration wizard: sudo $APP_DIR/venv/bin/python $APP_DIR/scripts/configure.py"
fi
