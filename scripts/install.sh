#!/usr/bin/env bash
set -Eeuo pipefail

VERSION="$(tr -d '[:space:]' < "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/VERSION")"
APP_DIR="/opt/fmo-ai-gateway"
RELEASES_DIR="$APP_DIR/releases"
RELEASE_DIR="$RELEASES_DIR/$VERSION"
CURRENT_LINK="$APP_DIR/current"
CONFIG_DIR="/etc/fmo-ai-gateway"
ENV_FILE="/etc/fmo-ai-gateway.env"
STATE_DIR="/var/lib/fmo-ai-gateway"
PREVIOUS_FILE="$STATE_DIR/previous-release"
SERVICE_USER="fmo-ai"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKIP_PACKAGES=false
NON_INTERACTIVE=false
DRY_RUN=false

usage() { echo "Usage: sudo ./scripts/install.sh [--non-interactive] [--skip-packages] [--dry-run]"; }
log() { echo "[fmo-ai-installer] $*"; }
die() { echo "[fmo-ai-installer] ERROR: $*" >&2; exit 1; }

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
command -v systemctl >/dev/null 2>&1 || die "systemd is required"
case "$(uname -m)" in x86_64|aarch64|arm64) ;; *) die "unsupported architecture: $(uname -m)" ;; esac
[[ -f "$SOURCE_DIR/ai-gateway/server.py" ]] || die "run the installer from the extracted project package"
if $DRY_RUN; then
  log "dry-run: validate Linux/systemd, install dependencies, stage $RELEASE_DIR, preserve $ENV_FILE and gateway config"
  log "dry-run: atomically switch $CURRENT_LINK, restart only the two fmo-ai services, then run doctor"
  exit 0
fi
[[ ${EUID:-$(id -u)} -eq 0 ]] || die "run as root with sudo"

install_packages() {
  $SKIP_PACKAGES && return 0
  [[ -r /etc/os-release ]] || die "cannot detect Linux distribution"
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}" in
    ubuntu|debian)
      apt-get update
      env DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip libopus0 ffmpeg ca-certificates curl
      ;;
    rocky|almalinux|centos|rhel)
      command -v dnf >/dev/null 2>&1 || die "RHEL-family installation requires dnf"
      dnf install -y python3.11 python3.11-pip opus ffmpeg ca-certificates curl
      ;;
    *) die "unsupported distribution ID=${ID:-unknown}; use --skip-packages after installing Python 3.10-3.12, venv, libopus and ffmpeg" ;;
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
command -v ffmpeg >/dev/null 2>&1 || die "ffmpeg is required for bounded audio/image conversion"
log "using $($PYTHON_BIN --version 2>&1)"

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$STATE_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi
install -d -o root -g root -m 0755 "$APP_DIR" "$RELEASES_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 "$CONFIG_DIR" "$STATE_DIR" "$STATE_DIR/audio"

previous=""
if [[ -L "$CURRENT_LINK" ]]; then
  previous="$(readlink -f "$CURRENT_LINK")"
elif [[ -f "$APP_DIR/server.py" ]]; then
  legacy="$RELEASES_DIR/legacy-$(date +%Y%m%d%H%M%S)"
  install -d -o root -g root -m 0755 "$legacy"
  find "$APP_DIR" -mindepth 1 -maxdepth 1 ! -name releases ! -name current -exec cp -a -t "$legacy" -- {} +
  previous="$legacy"
fi

[[ ! -e "$RELEASE_DIR" ]] || die "release $VERSION is already installed; use rollback or a newer version"
candidate_ready=false
cleanup() {
  rm -f -- "$APP_DIR/.current-new"
  if ! $candidate_ready; then
    if [[ -L "$CURRENT_LINK" && "$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)" == "$RELEASE_DIR" ]]; then
      rm -f -- "$CURRENT_LINK"
    fi
    rm -rf -- "$RELEASE_DIR"
  fi
}
trap cleanup EXIT
install -d -o root -g root -m 0755 "$RELEASE_DIR"
cp -a "$SOURCE_DIR/ai-gateway/." "$RELEASE_DIR/"
install -d -o root -g root -m 0755 "$RELEASE_DIR/scripts"
install -o root -g root -m 0755 "$SOURCE_DIR/scripts/configure.py" "$RELEASE_DIR/scripts/configure.py"
install -o root -g root -m 0755 "$SOURCE_DIR/scripts/doctor.sh" "$RELEASE_DIR/scripts/doctor.sh"
install -o root -g root -m 0755 "$SOURCE_DIR/scripts/rollback.sh" "$RELEASE_DIR/scripts/rollback.sh"
"$PYTHON_BIN" -m venv "$RELEASE_DIR/venv"
"$RELEASE_DIR/venv/bin/pip" install --disable-pip-version-check --no-cache-dir -r "$RELEASE_DIR/requirements.txt"
PYTHONDONTWRITEBYTECODE=1 "$RELEASE_DIR/venv/bin/python" -m compileall -q "$RELEASE_DIR"
(cd "$RELEASE_DIR" && PYTHONDONTWRITEBYTECODE=1 "$RELEASE_DIR/venv/bin/python" -c 'import server, mqtt_monitor, audio_schedule, nas_gallery, parrot_replay')
chown -R root:root "$RELEASE_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
  install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0600 "$SOURCE_DIR/ai-gateway/.env.example" "$ENV_FILE"
else
  log "preserving existing $ENV_FILE"
fi
if [[ ! -f "$CONFIG_DIR/gateway-config.json" ]]; then
  install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0600 "$SOURCE_DIR/ai-gateway/gateway-config.example.json" "$CONFIG_DIR/gateway-config.json"
else
  log "preserving existing gateway-config.json"
fi
install -o root -g root -m 0644 "$SOURCE_DIR/deploy/nginx-ai.conf.example" "$CONFIG_DIR/nginx-ai.conf.example"
install -o root -g root -m 0644 "$SOURCE_DIR/deploy/emqx-ai-loopback.conf" "$CONFIG_DIR/emqx-ai-loopback.conf.example"
install -o root -g root -m 0644 "$SOURCE_DIR/ai-gateway/fmo-ai-gateway.service" /etc/systemd/system/fmo-ai-gateway.service
install -o root -g root -m 0644 "$SOURCE_DIR/ai-gateway/fmo-ai-mqtt-monitor.service" /etc/systemd/system/fmo-ai-mqtt-monitor.service
install -d -m 0755 /etc/systemd/system/fmo-ai-gateway.service.d
install -o root -g root -m 0644 "$SOURCE_DIR/deploy/20-product-config.conf" /etc/systemd/system/fmo-ai-gateway.service.d/20-product-config.conf

if [[ -n "$previous" ]]; then printf '%s\n' "$previous" > "$PREVIOUS_FILE"; chmod 0600 "$PREVIOUS_FILE"; chown root:root "$PREVIOUS_FILE"; fi
ln -s "$RELEASE_DIR" "$APP_DIR/.current-new"
mv -Tf "$APP_DIR/.current-new" "$CURRENT_LINK"

if ! $NON_INTERACTIVE && [[ -t 0 && -z "$previous" ]]; then
  "$CURRENT_LINK/venv/bin/python" "$CURRENT_LINK/scripts/configure.py" --env-file "$ENV_FILE" --config-file "$CONFIG_DIR/gateway-config.json"
fi
systemctl daemon-reload
systemctl enable fmo-ai-gateway.service fmo-ai-mqtt-monitor.service >/dev/null
if ! systemctl restart fmo-ai-gateway.service fmo-ai-mqtt-monitor.service; then
  log "new release failed to start"
  if [[ -n "$previous" && -d "$previous" ]]; then
    ln -s "$previous" "$APP_DIR/.current-new"
    mv -Tf "$APP_DIR/.current-new" "$CURRENT_LINK"
    systemctl restart fmo-ai-gateway.service fmo-ai-mqtt-monitor.service || true
    die "upgrade rolled back to the previous release"
  fi
  die "first installation failed; services remain unavailable"
fi
ready=false
for _ in {1..20}; do
  if systemctl is-active --quiet fmo-ai-gateway.service fmo-ai-mqtt-monitor.service && curl -fsS --max-time 2 http://127.0.0.1:18788/health >/dev/null; then
    ready=true
    break
  fi
  sleep 1
done
if ! $ready; then
  log "new release did not pass the bounded readiness check"
  if [[ -n "$previous" && -d "$previous" ]]; then
    ln -s "$previous" "$APP_DIR/.current-new"
    mv -Tf "$APP_DIR/.current-new" "$CURRENT_LINK"
    systemctl restart fmo-ai-gateway.service fmo-ai-mqtt-monitor.service || true
    die "upgrade rolled back to the previous release"
  fi
  die "first installation failed readiness; services must remain untrusted"
fi
candidate_ready=true

log "installation complete (version $VERSION)"
log "safety state: existing switches were preserved; first-install AI/ASR/TTS/auto-reply/hourly/audio/PTT defaults are disabled"
log "local management page: http://127.0.0.1:18788/ai/"
log "remote access: use an authenticated HTTPS Nginx reverse proxy; do not expose port 18788"
log "doctor: sudo $CURRENT_LINK/scripts/doctor.sh"
log "rollback: sudo $CURRENT_LINK/scripts/rollback.sh"
if $NON_INTERACTIVE; then
  log "configuration wizard: sudo $CURRENT_LINK/venv/bin/python $CURRENT_LINK/scripts/configure.py"
fi
