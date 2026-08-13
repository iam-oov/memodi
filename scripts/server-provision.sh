#!/usr/bin/env bash
# memodi — home server production provisioning (Ubuntu Server, x86_64)
#
# Adapted from scripts/pi-provision.sh for the 2026-08-13 Pi SD-failure
# migration: same stack (native PostgreSQL 16 + pgvector + Apache AGE,
# memodi user and systemd service), minus the 1GB-hardware tuning (zram,
# make -j1) and minus cloudflared (the dashboard-managed connector is
# installed separately via `cloudflared service install <token>`).
#
# Secrets are operator-owned: this script NEVER writes secret values. On the
# first run it seeds docker/prod/.env from the committed .env.prod.example
# (mode 600, owner memodi) and exits so the operator can fill it in; a second
# run validates that file and finishes the install.
#
# Usage (on the server, as a user with sudo):
#   sudo bash server-provision.sh
#
# Optional environment overrides:
#   REPO_URL           git URL to clone (default: upstream GitHub)
#   DEPLOY_SSH_PUBKEY  deploy public key to seed into the memodi user's
#                      authorized_keys

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

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

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

CURRENT_PHASE="[1/8] x86_64 + Ubuntu sanity guard"
log "$CURRENT_PHASE"

[[ "${EUID}" -eq 0 ]] || die "must run as root: sudo bash server-provision.sh"

ARCH="$(uname -m)"
[[ "$ARCH" == "x86_64" ]] || die "detected architecture '${ARCH}', expected x86_64."

[[ -f /etc/os-release ]] || die "/etc/os-release not found; unsupported OS."
# shellcheck source=/dev/null
source /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || die "unsupported OS id '${ID:-unknown}' - this script targets Ubuntu Server."

TOTAL_RAM_MB=$(($(awk '/MemTotal/{print $2}' /proc/meminfo) / 1024))
log "OS: ${PRETTY_NAME:-unknown}, codename ${VERSION_CODENAME:-unknown}, RAM ${TOTAL_RAM_MB}MB"

CURRENT_PHASE="[2/8] apt prerequisites + PGDG repo + PostgreSQL ${PG_VERSION} + pgvector"
log "$CURRENT_PHASE"

apt-get update
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

CURRENT_PHASE="[3/8] Apache AGE build dependencies + compile"
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
    make -j"$(nproc)"
    make install PG_CONFIG="$PG_CONFIG"
  )
  rm -rf /tmp/age

  apt-get purge -y build-essential "postgresql-server-dev-${PG_VERSION}" bison flex
  apt-get autoremove -y
fi

CURRENT_PHASE="[4/8] PostgreSQL tuning drop-in + restart"
log "$CURRENT_PHASE"

mkdir -p "$PG_CONF_DIR"
TUNING_CONF="${PG_CONF_DIR}/memodi-tuning.conf"
TUNING_DESIRED=$(
  cat <<'EOF'
shared_buffers = 512MB
max_connections = 50
work_mem = 8MB
maintenance_work_mem = 128MB
effective_cache_size = 2GB
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

CURRENT_PHASE="[5/8] memodi user + uv + repo clone + uv sync + env file"
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

  sudo bash server-provision.sh

EOF
  exit 0
fi

validate_env_file "$ENV_FILE" "$EXAMPLE_FILE" || { trap - ERR; exit 1; }

DB_PASSWORD="$(grep '^MEMODI_DB_PASSWORD=' "$ENV_FILE" | head -1 | cut -d= -f2-)"

CURRENT_PHASE="[6/8] database + role + extensions"
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

CURRENT_PHASE="[7/8] memodi.service install + deploy sudoers + start"
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

CURRENT_PHASE="[8/8] final report"
log "$CURRENT_PHASE"

SERVICE_ENABLED="$(systemctl is-enabled memodi 2>/dev/null || echo 'not enabled')"
SERVICE_ACTIVE="$(systemctl is-active memodi 2>/dev/null || echo 'inactive')"

cat <<EOF

======================================================================
 memodi home server provisioning report
======================================================================

Installed:
  - PostgreSQL ${PG_VERSION} + pgvector + Apache AGE (${AGE_TAG})
  - PostgreSQL tuning drop-in: ${TUNING_CONF}
  - OS user '${MEMODI_USER}', database '${DB_NAME}', role '${DB_USER}'
  - uv + repo cloned at ${REPO_DIR}
  - EnvironmentFile: ${ENV_FILE} (operator-owned, validated)
  - memodi.service installed at ${SERVICE_DST}
  - sudoers: passwordless 'systemctl restart memodi' for ${MEMODI_USER}

memodi.service: ${SERVICE_ENABLED} / ${SERVICE_ACTIVE}

Still manual:
  - cloudflared connector: create/move the tunnel in the Cloudflare
    dashboard, then run 'cloudflared service install <token>' here
  - GitHub repo secrets: PI_SSH_USER=${MEMODI_USER}, PI_SSH_KEY,
    CF_ACCESS_CLIENT_ID, CF_ACCESS_CLIENT_SECRET
  - Database restore from the Pi (pg_dump or SD data dir)

======================================================================
EOF
