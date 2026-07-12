# workspace-scoped-resolution — Status

Last updated: 2026-07-12 · Roadmap: [workspace-scoped-resolution.md](workspace-scoped-resolution.md)

## State: ALL 6 PHASES IMPLEMENTED — cutover to the Pi pending (user-side steps)

| Phase | Status | Where |
|-------|--------|-------|
| 1 — users, key hashing, context helper | Done | commit `24ad3f5` |
| 2 — owner/machine schema + resolution | Done | commit `24ad3f5` |
| 3 — strict wiring + destructive cleanup | Done | commit `24ad3f5` |
| 4 — web signup page | Done | commit `24ad3f5` |
| 5 — plugin contract, installer, docs (v0.8.0) | Done | commit `3f3df46` |
| 6 — Pi infra, NATIVE (no containers) | Done | see below |
| E2E verification checklist (post-Phase 5) | 6/6 PASS | run 2026-07-11, fresh throwaway stack |

Every phase passed a two-blind-judge adversarial review; all confirmed findings fixed.
Test suite: **157 passed** (`uv run pytest -v`, requires the dev db container).

## Phase 6 decisions (deviations from the original roadmap)

- **No containers on the Pi** — native PG16 + pgvector (PGDG apt) + Apache AGE
  compiled from source (pin `PG16/v1.5.0-rc0`, same as `docker/Dockerfile.db`);
  memodi via uv + systemd (`docker/prod/memodi.service`); cloudflared is the
  user's pre-existing native systemd service. Docker remains for local dev only.
- **Deploy**: push-based SSH through the Cloudflare Tunnel
  (`cloudflared access ssh` + Access service token). Old Hetzner SSH deploy retired.
- **Backups**: deferred (fresh DB, no data to lose yet). Revisit once real
  memories accumulate.
- **Managed Postgres is not an option** while the knowledge graph exists —
  Apache AGE requires superuser/source install; self-hosted only.
  `MEMODI_DB_HOST` stays configurable.
- Hostnames: `https://memodi.valdoh.com` (MCP + signup), `pi.valdoh.com` (SSH).

## Pending — user-side cutover checklist

1. Provision the Pi: run `docs/pi-setup.md` steps 1-10 (creates the `memodi`
   OS user; everything runs under it).
2. Cloudflare: Access self-hosted app for `pi.valdoh.com` + **service token** +
   Service Auth policy; tunnel ingress `memodi.valdoh.com → http://localhost:8787`
   and `pi.valdoh.com → ssh://localhost:22`; rate-limit rule on
   `memodi.valdoh.com/signup` (~5 req/min/IP).
3. GitHub secrets: `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET`,
   `PI_SSH_USER` (**must be `memodi`**), `PI_SSH_KEY`.
4. Push to main → CI → deploy runs automatically.
5. After cutover: save round-trip from a laptop through the tunnel
   (last open item of the roadmap's E2E list); sign up, reinstall the plugin
   with the new key (old installs get `not_authenticated` until reinstalled).

## Gotchas worth remembering

- GET on `/mcp` returns **406 from a healthy server** (streamable-http needs
  `Accept: text/event-stream`) — never health-check it with `curl -f`.
  Deploy probes `GET /signup == 200`, which requires `MEMODI_SIGNUP_CODE`
  to be non-empty.
- The server fail-fasts at startup (`ensure_schema()` before `mcp.run`) —
  a broken DB/migration crash-loops the unit and turns the deploy red.
- Non-interactive SSH has no `~/.local/bin` on PATH — deploy uses the explicit
  `~/.local/bin/uv` path.
- Production binds `MEMODI_HOST=127.0.0.1` (LAN must not bypass the Cloudflare
  rate limit); default stays `0.0.0.0` for Docker dev.
- Engram context: topic keys `sdd/workspace-scoped-resolution/*`
  (apply-progress, deploy-decision, backup-decision, prod-hostnames,
  phase5-review).
