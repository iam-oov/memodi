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
step 3), `MEMODI_GOOGLE_CLIENT_ID`, `MEMODI_GOOGLE_CLIENT_SECRET`, and
`MEMODI_GOOGLE_REDIRECT_URI` (all three non-empty — the deploy health check
depends on them; see "Google OAuth client" below). `MEMODI_HOST=127.0.0.1`
is already set (loopback only; the tunnel connects locally, the LAN must not
reach the port).

### Google OAuth client

Registration is open (any Google account) — there is no invite code. One
manual, one-time setup in Google Cloud Console before the first deploy:

1. Create an OAuth consent screen: External user type, scopes `openid` and
   `userinfo.email` only (non-sensitive — no verification review needed),
   skip the app logo (triggers brand review), and **publish** the app.
   Testing mode caps registration at 100 users, which contradicts open
   registration.
2. Create a Web application OAuth client. Add both redirect URIs:
   `https://memodi.valdoh.com/oauth/callback` (prod) and
   `http://localhost:8787/oauth/callback` (local dev/testing).
3. Copy the client ID and client secret into `MEMODI_GOOGLE_CLIENT_ID` and
   `MEMODI_GOOGLE_CLIENT_SECRET`; set `MEMODI_GOOGLE_REDIRECT_URI` to the
   exact prod redirect URI above — it is never derived from the incoming
   request (uvicorn behind the tunnel always sees plain `http`, and the
   `Host` header is client-controlled), so a mismatch here breaks login.

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

- `memodi.valdoh.com` -> `http://localhost:8787` (MCP server + `/login`)
- `pi.valdoh.com` -> `ssh://localhost:22` (deploys over SSH)
- A Cloudflare Access service-token policy authorizing the token used by
  `deploy.yml` (`CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`)

## Preconditions checklist (before the first deploy)

- [ ] PostgreSQL 16 + pgvector + Apache AGE installed, extensions created (steps 1-4)
- [ ] `memodi` OS user created, deploy public key in its `authorized_keys`, `PI_SSH_USER=memodi` (step 5)
- [ ] `memodi.service` installed and enabled (step 8)
- [ ] cloudflared tunnel running independently (step 10) — no chicken-and-egg with deploy
- [ ] `docker/prod/.env` present, all three `MEMODI_GOOGLE_*` vars non-empty (see "Google OAuth client" in step 7)
- [ ] sudoers line in place for passwordless `systemctl restart memodi` only (step 9)

## Day-2 operations

### Exposed routes

- `/login` and `/oauth/callback` are public by design (the only entry points without a key) and GET-only, so there is no request body to cap: a verified Google identity is the gate on who can complete a login, and there is no invite code (registration is open to anyone with such an account). There is NO app-level throttling — set a Cloudflare rate-limiting rule on `memodi.valdoh.com/login` and `/oauth/callback` (suggested: 5 req/min per IP).
- `/mcp` requires a valid per-user api key (`X-Memodi-Api-Key`); missing or invalid → `not_authenticated`.
- Four plain-HTTP hook routes share the same auth contract (`X-Memodi-Api-Key` + `X-Memodi-Machine`) with no other gate in front: `POST /hooks/session-start`, `/hooks/session-close`, `/hooks/capture`, `/hooks/prompt-search`. Each validates and bounds its fields and caps the body at app level (4KB; 64KB for `capture`). Rate limiting on `/hooks/*` is an operator action in Cloudflare.

### Health checks

`deploy.yml` fails with `exit 1` if `/login` doesn't return 302 after the restart, or if the installed version doesn't match `__about__.py`, and dumps the last 30 lines of `journalctl -u memodi`. A 503 there means the Google OAuth vars are missing, which also fails the deploy. A GET on `/mcp` returns 406 from a HEALTHY server (streamable-http requires MCP headers) — never use it as a liveness probe.

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

> **2026-08-13 role inversion**: production migrated to the x86 home server
> the same day this section was written (Pi PSU undervoltage incident). The
> dump cron now runs on the home server and the Pi will become the puller
> once it has a new power supply. Read "Pi" and "home server" below as
> swapped until the full doc rewrite lands.

Two layers, decided 2026-08-13: a daily `pg_dump` on the Pi with 7-day
retention, plus an always-on home server on the same LAN pulling the dumps
off-device. The AGE graph is NOT in the dump — it is derived state, rebuilt
after a restore by `ensure_graph()` (app startup) + `memodi_backfill_links` —
so only schema `public` is dumped. Keep the `-n public` flag: dumping the AGE
catalogs (`ag_catalog`, the `memodi` graph schema) produces dumps that do not
restore cleanly.

**On the Pi** (as the `memodi` user — peer auth over the unix socket, no
password needed):

```bash
crontab -e
# 15 3 * * * /home/memodi/memodi/scripts/backup-dump.sh
```

Dumps land in `~/backups/memodi-YYYY-MM-DD.dump` (KB–MB each). The script
writes to a `.tmp` file and renames on success, so a torn dump is never
picked up by the pull. Dumps older than `MEMODI_BACKUP_RETENTION_DAYS`
(default 7) are deleted.

**On the home server** (Ubuntu Server, same LAN — pulls directly over local
SSH, no cloudflared involved):

```bash
sudo apt install -y rsync
ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519
ssh-copy-id memodi@<pi-lan-address>
scp memodi@<pi-lan-address>:memodi/scripts/backup-pull.sh ~/backup-pull.sh

crontab -e
# 45 3 * * * MEMODI_PI=memodi@<pi-lan-address> $HOME/backup-pull.sh
```

Pulled dumps accumulate in `~/memodi-backups` without pruning (they are
tiny); add server-side retention only when disk space starts to matter.
Known remaining gap: both copies live under the same roof — an offsite copy
is a possible later add-on.

**Restore drill** (into the local docker compose DB):

The dump contains a `CREATE SCHEMA public` entry (a side effect of
`-n public`) that collides with the schema every fresh database already has,
and `--clean` cannot drop it either because the `vector` extension lives in
it — so filter that one entry out of the restore list instead of ignoring
restore errors:

```bash
docker compose up -d db
export PGPASSWORD=memodi_dev
createdb -h localhost -U memodi memodi_restore
psql -h localhost -U memodi -d memodi_restore \
  -c 'CREATE EXTENSION vector; CREATE EXTENSION age;'
pg_restore -l memodi-YYYY-MM-DD.dump | grep -v 'SCHEMA - public' \
  > /tmp/restore.list
pg_restore -h localhost -U memodi -d memodi_restore --no-owner \
  --exit-on-error -L /tmp/restore.list memodi-YYYY-MM-DD.dump
psql -h localhost -U memodi -d memodi_restore \
  -c 'SELECT count(*) FROM observations;'
dropdb -h localhost -U memodi memodi_restore
```

Drill executed 2026-08-13 against the local compose DB (dump of its own
`public` schema restored into a fresh `memodi_restore` with AGE installed):
15 observations and 9 tables restored, `--exit-on-error` clean.

To also rebuild the graph, point a local memodi server at the restored DB
(`MEMODI_DB_NAME=memodi_restore`) and call `memodi_backfill_links` — the
2026-08-12 audit verified every edge is a derived `LINKS_TO` (zero manual
`memodi_relate` edges), so the rebuild is complete.
