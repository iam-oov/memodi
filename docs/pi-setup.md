# Raspberry Pi Production Setup

One-time bring-up for native PostgreSQL + `memodi.service` on the Pi (arm64).
Docker is dev-only — production runs everything natively. cloudflared is out
of scope here: it's the user's existing native systemd service, independent
of this repo.

## Automated provisioning

`scripts/pi-provision.sh` automates steps 1-9 below on a fresh Raspberry Pi
OS Lite 64-bit install (idempotent, re-runnable), plus an OPTIONAL restore of
cloudflared from a credentials backup on a fresh box. Step 10 (cloudflared
tunnel bring-up) otherwise remains manual and out of repo scope. On its first
run the script seeds `docker/prod/.env` from `.env.prod.example` and stops so
you can fill in the secrets; re-run it to validate that file and finish. Run
it as root on the Pi:

```bash
sudo bash scripts/pi-provision.sh
```

The manual steps below remain as the reference contract and as a fallback
if the script needs to be adapted or debugged.

## 1GB-hardware tuning (zram + PostgreSQL drop-in)

On a 1GB Pi 3B, `scripts/pi-provision.sh` also applies two tuning steps the
numbered steps below leave out. Reproduce them by hand only if you are not
running the script — the script is the source of truth.

**zram swap** (script phase 2) — compressed RAM-backed swap so the AGE build
and the first model download don't OOM. Raspberry Pi OS 13 (trixie) already
ships zram swap by default via `rpi-swap`; if `grep zram /proc/swaps` shows an
active device, skip this step entirely — installing `zram-tools` on top of it
fails with `mkswap: /dev/zram0 is mounted`. Manual setup for older images only:

```bash
sudo apt install -y zram-tools
printf 'ALGO=lz4\nPERCENT=50\nPRIORITY=100\n' | sudo tee /etc/default/zramswap
sudo systemctl restart zramswap
sudo systemctl enable zramswap
```

**PostgreSQL tuning drop-in** (script phase 5) — small buffers sized for 1GB,
loaded through `conf.d`:

```bash
sudo tee /etc/postgresql/16/main/conf.d/memodi-tuning.conf <<'EOF'
shared_buffers = 128MB
max_connections = 20
work_mem = 4MB
maintenance_work_mem = 32MB
effective_cache_size = 256MB
EOF
grep -q '^include_dir' /etc/postgresql/16/main/postgresql.conf \
  || echo "include_dir = 'conf.d'" | sudo tee -a /etc/postgresql/16/main/postgresql.conf
sudo systemctl restart postgresql
```

## 1. PostgreSQL 16 + pgvector (PGDG apt repo)

```bash
sudo apt install -y curl ca-certificates gnupg lsb-release
sudo install -d /usr/share/postgresql-common/pgdg
sudo curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
  https://www.postgresql.org/media/keys/ACCC4CF8.asc
echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
  | sudo tee /etc/apt/sources.list.d/pgdg.list
sudo apt update
sudo apt install -y postgresql-16 postgresql-16-pgvector
```

## 2. Apache AGE (build from source)

Pinned to `PG16/v1.5.0-rc0` — must match `docker/Dockerfile.db` exactly
(dev/prod extension parity). Check that file if this doc drifts.

```bash
sudo apt install -y build-essential git postgresql-server-dev-16 \
  libreadline-dev zlib1g-dev bison flex
git clone --branch PG16/v1.5.0-rc0 https://github.com/apache/age.git /tmp/age
cd /tmp/age
export PG_CONFIG=/usr/lib/postgresql/16/bin/pg_config
make
sudo make install PG_CONFIG=$PG_CONFIG
cd - && rm -rf /tmp/age
sudo apt purge -y build-essential postgresql-server-dev-16 bison flex
sudo apt autoremove -y
```

## 3. Database + role

```bash
sudo -u postgres psql <<'SQL'
CREATE USER memodi WITH PASSWORD 'CHANGE_ME';
CREATE DATABASE memodi OWNER memodi;
SQL
```

## 4. Extensions + grants

Translated from `docker/init-extensions.sql` (superuser section — the app
user here is not superuser, unlike the Docker image's `POSTGRES_USER`).

```bash
sudo -u postgres psql -d memodi <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS age;
ALTER DATABASE memodi SET session_preload_libraries = 'age';
GRANT USAGE ON SCHEMA ag_catalog TO memodi;
GRANT ALL ON ALL TABLES IN SCHEMA ag_catalog TO memodi;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA ag_catalog TO memodi;
SQL
sudo systemctl restart postgresql
```

## 5. memodi service user

Everything from here runs AS this user: `memodi.service` hardcodes
`User=memodi` and `/home/memodi/...`, the deploy SSHes in as it, and the
sudoers line (step 9) targets it. The GitHub secret `PI_SSH_USER` MUST be
`memodi` — its `authorized_keys` gets the deploy public key.

```bash
sudo useradd -m -s /bin/bash memodi
sudo usermod -aG systemd-journal memodi   # so the deploy's `journalctl -u memodi` prints

# Authorize the deploy public key (paired with the PI_SSH_KEY secret):
sudo -u memodi mkdir -p /home/memodi/.ssh
echo 'ssh-ed25519 AAAA...deploy-public-key...' | sudo tee -a /home/memodi/.ssh/authorized_keys
sudo chown -R memodi:memodi /home/memodi/.ssh
sudo chmod 700 /home/memodi/.ssh
sudo chmod 600 /home/memodi/.ssh/authorized_keys
```

## 6. uv + repo (as the memodi user)

```bash
sudo -iu memodi bash <<'EOF'
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
git clone https://github.com/iam-oov/memodi.git ~/memodi
cd ~/memodi && uv sync
EOF
```

## 7. Env file (as the memodi user)

```bash
sudo -iu memodi bash <<'EOF'
cd ~/memodi
cp docker/prod/.env.prod.example docker/prod/.env
EOF
```

Edit `/home/memodi/memodi/docker/prod/.env`: `MEMODI_DB_PASSWORD` (match
step 3), `MEMODI_SIGNUP_CODE` (non-empty — the deploy health check depends on
it). `MEMODI_HOST=127.0.0.1` is already set (loopback only; the tunnel
connects locally, the LAN must not reach the port).

## 8. memodi.service

```bash
sudo cp /home/memodi/memodi/docker/prod/memodi.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now memodi
systemctl status memodi
```

The unit calls `ensure_schema()` at process startup (DB connectivity +
migrations). A broken DB crash-loops the unit — that's intentional; the
deploy's health check is what surfaces it, not the unit itself.

## 9. Sudoers (deploy.yml restarts the service over SSH)

```bash
echo 'memodi ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart memodi' \
  | sudo tee /etc/sudoers.d/memodi
sudo chmod 440 /etc/sudoers.d/memodi
```

Scope this to `systemctl restart memodi` only — do not grant broader sudo.

## 10. cloudflared (out of repo scope)

Not installed or configured by this repo. Before the first deploy, confirm
the user's existing native cloudflared systemd service is already running,
independently of any deploy:

- `memodi.valdoh.com` -> `http://localhost:8787` (MCP server + `/signup`)
- `pi.valdoh.com` -> `ssh://localhost:22` (deploys over SSH)
- A Cloudflare Access service-token policy authorizing the token used by
  `deploy.yml` (`CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`)

## Preconditions checklist (before the first deploy)

- [ ] PostgreSQL 16 + pgvector + Apache AGE installed, extensions created (steps 1-4)
- [ ] `memodi` OS user created, deploy public key in its `authorized_keys`, `PI_SSH_USER=memodi` (step 5)
- [ ] `memodi.service` installed and enabled (step 8)
- [ ] cloudflared tunnel running independently (step 10) — no chicken-and-egg with deploy
- [ ] `docker/prod/.env` present, `MEMODI_SIGNUP_CODE` non-empty
- [ ] sudoers line in place for passwordless `systemctl restart memodi` only (step 9)

## Day-2 operations

### Exposed routes

- `/signup` is public by design (the only entry point without a key): invite code (`MEMODI_SIGNUP_CODE`) + app-level body limit. There is NO app-level throttling — set a Cloudflare rate-limiting rule on `memodi.valdoh.com/signup` (suggested: 5 req/min per IP).
- `/mcp` requires a valid per-user api key (`X-Memodi-Api-Key`); missing or invalid → `not_authenticated`.
- Four plain-HTTP hook routes share the same auth contract (`X-Memodi-Api-Key` + `X-Memodi-Machine`) with no other gate in front: `POST /hooks/session-start`, `/hooks/session-close`, `/hooks/capture`, `/hooks/prompt-search`. Each validates and bounds its fields and caps the body at app level (4KB; 64KB for `capture`). Rate limiting on `/hooks/*` is an operator action in Cloudflare.

### Health checks

`deploy.yml` fails with `exit 1` if `/signup` doesn't return 200 after the restart, or if the installed version doesn't match `__about__.py`, and dumps the last 30 lines of `journalctl -u memodi`. A GET on `/mcp` returns 406 from a HEALTHY server (streamable-http requires MCP headers) — never use it as a liveness probe.

### Release

```bash
# Bump version in pyproject.toml, commit, then:
git tag v0.18.0
git push origin v0.18.0
```

`release.yml` generates the changelog from the previous tag and creates the GitHub Release.

### Manual deploy

```bash
ssh -o "ProxyCommand=cloudflared access ssh --hostname %h" memodi@pi.valdoh.com
cd ~/memodi && git fetch origin main && git reset --hard origin/main
uv sync --reinstall-package memodi
sudo systemctl restart memodi
```

### Backups

Deferred — the Pi starts with a fresh DB; offsite backup strategy is a future change.
