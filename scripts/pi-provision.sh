#!/usr/bin/env bash
# memodi — Raspberry Pi production provisioning
#
# Automates the manual bring-up described in docs/pi-setup.md: turns a
# FRESH Raspberry Pi OS Lite 64-bit install into the full memodi production
# stack (native PostgreSQL 16 + pgvector + Apache AGE, the memodi user and
# systemd service, and cloudflared). Idempotent — safe to re-run after a
# partial failure or to pick up an updated memodi.service.
#
# Secrets are operator-owned: this script NEVER writes secret values. On the
# first run it seeds docker/prod/.env from the committed .env.prod.example
# (mode 600, owner memodi) and exits so the operator can fill it in; a second
# run validates that file and finishes the install. This two-run flow is
# intended and safe given the idempotency above.
#
# Target hardware: Raspberry Pi 3B v1.2 (1GB RAM, aarch64). Tuning choices
# in this script (zram, make -j1, small PostgreSQL settings) exist because
# of that constraint — do not relax them without more RAM.
#
# Usage (on the Pi, as a user with sudo):
#   sudo bash scripts/pi-provision.sh
#
# Optional environment overrides:
#   REPO_URL           git URL to clone (default: upstream GitHub)
#   DEPLOY_SSH_PUBKEY  deploy public key to seed into the memodi user's
#                      authorized_keys (paired with the PI_SSH_KEY secret)
#   TUNNEL_BACKUP_DIR  path to a backed-up /etc/cloudflared directory to
#                      restore (default: /home/memodi/cloudflared-backup)
#
# Out of scope (left manual — see final report):
#   - every secret value in docker/prod/.env (the operator fills the template)
#   - the deploy public key itself, unless DEPLOY_SSH_PUBKEY is set
#   - cloudflared tunnel creation, unless a credentials backup is restored

set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

readonly PG_VERSION="16"
readonly AGE_TAG="PG16/v1.5.0-rc0"
readonly MEMODI_USER="memodi"
readonly MEMODI_HOME="/home/${MEMODI_USER}"
readonly DB_NAME="memodi"
readonly DB_USER="memodi"
readonly REPO_URL="${REPO_URL:-https://github.com/iam-oov/memodi.git}"
readonly REPO_DIR="${MEMODI_HOME}/memodi"
readonly ENV_FILE="${REPO_DIR}/docker/prod/.env"
readonly EXAMPLE_FILE="${REPO_DIR}/docker/prod/.env.prod.example"
readonly PG_CONF_DIR="/etc/postgresql/${PG_VERSION}/main/conf.d"
readonly TUNNEL_BACKUP_DIR="${TUNNEL_BACKUP_DIR:-${MEMODI_HOME}/cloudflared-backup}"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Required keys come from the committed template, which mirrors the non-default
# fields of src/memodi/config.py Settings (MEMODI_DB_USER / MEMODI_DB_PASSWORD)
# plus the server-side config the unit needs. A key is bad if it is absent,
# empty, or still holds a CHANGE_ME placeholder.
validate_env_file() {
  local env_file="$1" example_file="$2" line key value
  local -a required=() missing=() placeholder=()

  while IFS= read -r line; do
    if [[ "$line" =~ ^(MEMODI_[A-Z0-9_]+)= ]]; then
      required+=("${BASH_REMATCH[1]}")
    fi
  done <"$example_file"

  for key in "${required[@]}"; do
    if ! grep -q "^${key}=" "$env_file"; then
      missing+=("$key")
      continue
    fi
    value="$(grep "^${key}=" "$env_file" | head -1 | cut -d= -f2-)"
    if [[ -z "$value" ]]; then
      missing+=("$key")
    elif [[ "$value" == *CHANGE_ME* ]]; then
      placeholder+=("$key")
    fi
  done

  if [[ "${#missing[@]}" -gt 0 || "${#placeholder[@]}" -gt 0 ]]; then
    printf 'ERROR: %s is not ready:\n' "$env_file" >&2
    if [[ "${#missing[@]}" -gt 0 ]]; then
      printf '  missing or empty key: %s\n' "${missing[@]}" >&2
    fi
    if [[ "${#placeholder[@]}" -gt 0 ]]; then
      printf '  still a CHANGE_ME placeholder: %s\n' "${placeholder[@]}" >&2
    fi
    printf 'Edit %s, replace every placeholder, then re-run this script.\n' "$env_file" >&2
    return 1
  fi
}

CURRENT_PHASE="startup"
on_error() {
  local exit_code=$?
  printf 'FAILED during %s (exit %s)\n' "$CURRENT_PHASE" "$exit_code" >&2
  exit "$exit_code"
}
trap on_error ERR

CURRENT_PHASE="[1/10] aarch64 + OS sanity guard"
log "$CURRENT_PHASE"

[[ "${EUID}" -eq 0 ]] || die "must run as root: sudo bash scripts/pi-provision.sh"

ARCH="$(uname -m)"
[[ "$ARCH" == "aarch64" ]] || die \
  "detected architecture '${ARCH}', not aarch64 - flash Raspberry Pi OS Lite (64-bit)," \
  "onnxruntime has no 32-bit ARM wheels."

DPKG_ARCH="$(dpkg --print-architecture)"
[[ "$DPKG_ARCH" == "arm64" ]] || die "dpkg architecture is '${DPKG_ARCH}', expected arm64."

[[ -f /etc/os-release ]] || die "/etc/os-release not found; unsupported OS."
# shellcheck source=/dev/null
source /etc/os-release
case "${ID:-}" in
  debian | raspbian) ;;
  *) die "unsupported OS id '${ID:-unknown}' - this script targets Raspberry Pi OS / Debian." ;;
esac

TOTAL_RAM_MB=$(($(awk '/MemTotal/{print $2}' /proc/meminfo) / 1024))
log "OS: ${PRETTY_NAME:-unknown}, codename ${VERSION_CODENAME:-unknown}, RAM ${TOTAL_RAM_MB}MB"

CURRENT_PHASE="[2/10] zram swap"
log "$CURRENT_PHASE"

if grep -q zram /proc/swaps; then
  log "zram swap already active (OS-provided, e.g. rpi-swap on Raspberry Pi OS 13+), skipping zram-tools"
else
  apt-get update
  apt-get install -y zram-tools

  ZRAM_CONF=/etc/default/zramswap
  ZRAM_DESIRED=$(
    cat <<'EOF'
ALGO=lz4
PERCENT=50
PRIORITY=100
EOF
  )
  if [[ ! -f "$ZRAM_CONF" ]] || [[ "$(cat "$ZRAM_CONF")" != "$ZRAM_DESIRED" ]]; then
    printf '%s\n' "$ZRAM_DESIRED" >"$ZRAM_CONF"
    systemctl restart zramswap
    log "zram configured at 50% of RAM and restarted"
  else
    log "zram already configured, skipping restart"
  fi
  systemctl enable zramswap >/dev/null 2>&1 || true
fi

CURRENT_PHASE="[3/10] apt prerequisites + PGDG repo + PostgreSQL ${PG_VERSION} + pgvector"
log "$CURRENT_PHASE"

apt-get install -y curl ca-certificates gnupg lsb-release git

install -d -m 0755 /usr/share/postgresql-common/pgdg
PGDG_KEY=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
if [[ ! -f "$PGDG_KEY" ]]; then
  PGDG_KEY_TMP="$(mktemp)"
  curl -fsSL -o "$PGDG_KEY_TMP" https://www.postgresql.org/media/keys/ACCC4CF8.asc
  [[ -s "$PGDG_KEY_TMP" ]] || die "PGDG signing key download was empty"
  gpg --dearmor <"$PGDG_KEY_TMP" >/dev/null 2>&1 || die "PGDG signing key is not a valid OpenPGP key"
  mv "$PGDG_KEY_TMP" "$PGDG_KEY"
  chmod 0644 "$PGDG_KEY"
fi

CODENAME="$(lsb_release -cs)"
PGDG_LIST=/etc/apt/sources.list.d/pgdg.list
PGDG_LINE="deb [signed-by=${PGDG_KEY}] https://apt.postgresql.org/pub/repos/apt ${CODENAME}-pgdg main"
if [[ ! -f "$PGDG_LIST" ]] || [[ "$(cat "$PGDG_LIST")" != "$PGDG_LINE" ]]; then
  printf '%s\n' "$PGDG_LINE" >"$PGDG_LIST"
fi

apt-get update
apt-get install -y "postgresql-${PG_VERSION}" "postgresql-${PG_VERSION}-pgvector"

CURRENT_PHASE="[4/10] Apache AGE build dependencies + compile (make -j1)"
log "$CURRENT_PHASE"

AGE_CONTROL="/usr/share/postgresql/${PG_VERSION}/extension/age.control"
if [[ -f "$AGE_CONTROL" ]]; then
  log "Apache AGE already installed, skipping compile"
else
  apt-get install -y build-essential "postgresql-server-dev-${PG_VERSION}" \
    libreadline-dev zlib1g-dev bison flex

  rm -rf /tmp/age
  git clone --branch "$AGE_TAG" --depth 1 https://github.com/apache/age.git /tmp/age
  (
    cd /tmp/age || exit 1
    export PG_CONFIG="/usr/lib/postgresql/${PG_VERSION}/bin/pg_config"
    # -j1: parallel make OOMs on this hardware's 1GB RAM even with zram.
    make -j1
    make -j1 install PG_CONFIG="$PG_CONFIG"
  )
  rm -rf /tmp/age

  apt-get purge -y build-essential "postgresql-server-dev-${PG_VERSION}" bison flex
  apt-get autoremove -y
fi

CURRENT_PHASE="[5/10] PostgreSQL tuning drop-in + restart"
log "$CURRENT_PHASE"

mkdir -p "$PG_CONF_DIR"
TUNING_CONF="${PG_CONF_DIR}/memodi-tuning.conf"
TUNING_DESIRED=$(
  cat <<'EOF'
shared_buffers = 128MB
max_connections = 20
work_mem = 4MB
maintenance_work_mem = 32MB
effective_cache_size = 256MB
EOF
)
RESTART_PG=0
if [[ ! -f "$TUNING_CONF" ]] || [[ "$(cat "$TUNING_CONF")" != "$TUNING_DESIRED" ]]; then
  printf '%s\n' "$TUNING_DESIRED" >"$TUNING_CONF"
  RESTART_PG=1
fi

PG_MAIN_CONF="/etc/postgresql/${PG_VERSION}/main/postgresql.conf"
if [[ -f "$PG_MAIN_CONF" ]] && ! grep -q "^include_dir" "$PG_MAIN_CONF"; then
  printf "include_dir = 'conf.d'\n" >>"$PG_MAIN_CONF"
  RESTART_PG=1
fi

if [[ "$RESTART_PG" -eq 1 ]]; then
  systemctl restart postgresql
  log "PostgreSQL tuned and restarted"
else
  log "PostgreSQL tuning already applied, skipping restart"
fi

CURRENT_PHASE="[6/10] memodi user + uv + repo clone + uv sync + env file"
log "$CURRENT_PHASE"

if ! id -u "$MEMODI_USER" >/dev/null 2>&1; then
  useradd -m -s /bin/bash "$MEMODI_USER"
fi
usermod -aG systemd-journal "$MEMODI_USER"

install -d -m 700 -o "$MEMODI_USER" -g "$MEMODI_USER" "${MEMODI_HOME}/.ssh"
touch "${MEMODI_HOME}/.ssh/authorized_keys"
chmod 600 "${MEMODI_HOME}/.ssh/authorized_keys"
chown "${MEMODI_USER}:${MEMODI_USER}" "${MEMODI_HOME}/.ssh/authorized_keys"
if [[ -n "${DEPLOY_SSH_PUBKEY:-}" ]]; then
  if ! grep -qF "$DEPLOY_SSH_PUBKEY" "${MEMODI_HOME}/.ssh/authorized_keys"; then
    printf '%s\n' "$DEPLOY_SSH_PUBKEY" >>"${MEMODI_HOME}/.ssh/authorized_keys"
    log "deploy public key added to authorized_keys"
  fi
else
  log "DEPLOY_SSH_PUBKEY not set - add the deploy public key to" \
    "${MEMODI_HOME}/.ssh/authorized_keys manually"
fi

install -d -m 0755 -o "$MEMODI_USER" -g "$MEMODI_USER" "${MEMODI_HOME}/.cache"
install -d -m 0755 -o "$MEMODI_USER" -g "$MEMODI_USER" "${MEMODI_HOME}/.cache/fastembed"

UV_BIN="${MEMODI_HOME}/.local/bin/uv"
if [[ ! -x "$UV_BIN" ]]; then
  sudo -iu "$MEMODI_USER" bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi

if [[ ! -d "${REPO_DIR}/.git" ]]; then
  sudo -u "$MEMODI_USER" -H git clone --depth 1 "$REPO_URL" "$REPO_DIR"
else
  sudo -u "$MEMODI_USER" -H git -C "$REPO_DIR" pull --ff-only
fi

sudo -u "$MEMODI_USER" -H bash -c "cd '${REPO_DIR}' && '${UV_BIN}' sync"

if [[ ! -f "$ENV_FILE" ]]; then
  [[ -f "$EXAMPLE_FILE" ]] || die "template ${EXAMPLE_FILE} missing - repo clone incomplete"
  install -m 600 -o "$MEMODI_USER" -g "$MEMODI_USER" "$EXAMPLE_FILE" "$ENV_FILE"
  trap - ERR
  cat <<EOF

Seeded ${ENV_FILE} from the committed template.
Fill in every CHANGE_ME value (at minimum MEMODI_DB_PASSWORD and
MEMODI_SIGNUP_CODE), then re-run to finish provisioning:

  sudo bash scripts/pi-provision.sh

EOF
  exit 0
fi

validate_env_file "$ENV_FILE" "$EXAMPLE_FILE" || { trap - ERR; exit 1; }

DB_PASSWORD="$(grep '^MEMODI_DB_PASSWORD=' "$ENV_FILE" | head -1 | cut -d= -f2-)"

CURRENT_PHASE="[7/10] database + role + extensions"
log "$CURRENT_PHASE"

ROLE_EXISTS="$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'")"
if [[ "$ROLE_EXISTS" == "1" ]]; then
  sudo -u postgres psql <<SQL
ALTER ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASSWORD}';
SQL
else
  sudo -u postgres psql <<SQL
CREATE USER ${DB_USER} WITH LOGIN PASSWORD '${DB_PASSWORD}';
SQL
fi

DB_EXISTS="$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'")"
if [[ "$DB_EXISTS" != "1" ]]; then
  sudo -u postgres psql -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}"
fi

sudo -u postgres psql -d "$DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS vector"
sudo -u postgres psql -d "$DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS age"
sudo -u postgres psql -d "$DB_NAME" -c "ALTER DATABASE ${DB_NAME} SET session_preload_libraries = 'age'"
sudo -u postgres psql -d "$DB_NAME" -c "GRANT USAGE ON SCHEMA ag_catalog TO ${DB_USER}"
sudo -u postgres psql -d "$DB_NAME" -c "GRANT ALL ON ALL TABLES IN SCHEMA ag_catalog TO ${DB_USER}"
sudo -u postgres psql -d "$DB_NAME" -c "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA ag_catalog TO ${DB_USER}"

systemctl restart postgresql

CURRENT_PHASE="[8/10] memodi.service install + deploy sudoers + start"
log "$CURRENT_PHASE"

SERVICE_SRC="${REPO_DIR}/docker/prod/memodi.service"
SERVICE_DST="/etc/systemd/system/memodi.service"
[[ -f "$SERVICE_SRC" ]] || die "memodi.service not found at ${SERVICE_SRC} - repo clone incomplete"

UNIT_CHANGED=0
if [[ ! -f "$SERVICE_DST" ]] || ! cmp -s "$SERVICE_SRC" "$SERVICE_DST"; then
  cp "$SERVICE_SRC" "$SERVICE_DST"
  UNIT_CHANGED=1
fi
systemctl daemon-reload

SUDOERS_FILE=/etc/sudoers.d/memodi
SUDOERS_LINE="${MEMODI_USER} ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart memodi"
if [[ ! -f "$SUDOERS_FILE" ]] || [[ "$(cat "$SUDOERS_FILE")" != "$SUDOERS_LINE" ]]; then
  printf '%s\n' "$SUDOERS_LINE" >"${SUDOERS_FILE}.tmp"
  visudo -cf "${SUDOERS_FILE}.tmp" || die "generated sudoers file failed validation"
  mv "${SUDOERS_FILE}.tmp" "$SUDOERS_FILE"
  chmod 440 "$SUDOERS_FILE"
fi

if [[ "$UNIT_CHANGED" -eq 1 ]]; then
  systemctl enable memodi
  systemctl restart memodi
else
  systemctl enable --now memodi
fi

CURRENT_PHASE="[9/10] cloudflared (arm64, additive-safe)"
log "$CURRENT_PHASE"

CLOUDFLARED_ETC=/etc/cloudflared
CLOUDFLARED_UNIT=/etc/systemd/system/cloudflared.service
CLOUDFLARED_ACTIVE="$(systemctl is-active cloudflared 2>/dev/null || true)"

if [[ -f "$CLOUDFLARED_UNIT" ]] || [[ -f /lib/systemd/system/cloudflared.service ]] \
  || [[ "$CLOUDFLARED_ACTIVE" == "active" ]]; then
  log "existing cloudflared unit or active service detected - skipping phase" \
    "(additive-safe: never overwrite /etc/cloudflared or restart a live connector)"
else
  if ! command -v cloudflared >/dev/null 2>&1; then
    curl -fsSL -o /tmp/cloudflared.deb \
      https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
    dpkg -i /tmp/cloudflared.deb || apt-get install -f -y
    rm -f /tmp/cloudflared.deb
  fi
  cloudflared --version

  if [[ -d "$TUNNEL_BACKUP_DIR" ]] && [[ -n "$(ls -A "$TUNNEL_BACKUP_DIR" 2>/dev/null)" ]]; then
    mkdir -p "$CLOUDFLARED_ETC"
    cp -a "${TUNNEL_BACKUP_DIR}/." "$CLOUDFLARED_ETC/"
    find "$CLOUDFLARED_ETC" -name '*.json' -exec chmod 600 {} \;
    chown -R root:root "$CLOUDFLARED_ETC"
    # Written directly against the restored config.yml rather than via
    # `cloudflared service install <token>`, which needs a live token this
    # script never has - restoring a backup means the tunnel was already
    # created and only its local credentials need to come back.
    cat >"$CLOUDFLARED_UNIT" <<'EOF'
[Unit]
Description=cloudflared tunnel
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
ExecStart=/usr/bin/cloudflared --config /etc/cloudflared/config.yml tunnel run
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now cloudflared
    log "restored tunnel credentials from ${TUNNEL_BACKUP_DIR} and started cloudflared"
  else
    log "no tunnel credentials backup found at ${TUNNEL_BACKUP_DIR}"
    log "cloudflared binary installed - complete the tunnel manually (docs/pi-setup.md step 10):"
    log "  - memodi.valdoh.com -> http://localhost:8787 (MCP server + /signup)"
    log "  - pi.valdoh.com -> ssh://localhost:22 (deploys over SSH)"
    log "  - Cloudflare Access service-token policy for CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET"
  fi
fi

CURRENT_PHASE="[10/10] final report"
log "$CURRENT_PHASE"

SERVICE_ENABLED="$(systemctl is-enabled memodi 2>/dev/null || echo 'not enabled')"
SERVICE_ACTIVE="$(systemctl is-active memodi 2>/dev/null || echo 'inactive')"
CLOUDFLARED_VERSION="$(cloudflared --version 2>/dev/null | head -1 || echo 'not installed')"

cat <<EOF

======================================================================
 memodi Raspberry Pi provisioning report
======================================================================

Installed:
  - PostgreSQL ${PG_VERSION} + pgvector + Apache AGE (${AGE_TAG})
  - zram swap (OS-provided, or zram-tools at 50% of RAM)
  - PostgreSQL tuning drop-in: ${TUNING_CONF}
  - OS user '${MEMODI_USER}', database '${DB_NAME}', role '${DB_USER}'
  - uv + repo cloned at ${REPO_DIR}
  - EnvironmentFile: ${ENV_FILE} (operator-owned, validated)
  - memodi.service installed at ${SERVICE_DST}
  - sudoers: passwordless 'systemctl restart memodi' for ${MEMODI_USER}
  - cloudflared: ${CLOUDFLARED_VERSION}

memodi.service: ${SERVICE_ENABLED} / ${SERVICE_ACTIVE}

Still manual:
  - Add the deploy public key to ${MEMODI_HOME}/.ssh/authorized_keys
    (unless DEPLOY_SSH_PUBKEY was set for this run)
  - cloudflared tunnel bring-up, unless a credentials backup was restored above
  - GitHub repo secrets: PI_SSH_USER=${MEMODI_USER}, PI_SSH_KEY,
    CF_ACCESS_CLIENT_ID, CF_ACCESS_CLIENT_SECRET
  - Cloudflare Access service-token policy authorizing the deploy service token

======================================================================
EOF
