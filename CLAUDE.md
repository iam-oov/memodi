# memodi — Memoria Distribuida

## What is this?

MCP server (Python) that gives Claude Code persistent, distributed memory across workspaces and projects. Claude is the brain — memodi is the persistence layer.

## Architecture

```
Local dev:  Claude Code ──HTTP──► memodi-server (docker compose) ──► PostgreSQL (docker)
Production: Claude Code ──HTTPS──► Cloudflare Tunnel ──► memodi-server (uv + systemd, Raspberry Pi) ──► PostgreSQL (native)
```

### Storage layers (PostgreSQL)

| Layer | Extension | Purpose | Status |
|-------|-----------|---------|--------|
| Document Store | JSONB | State, tasks, decisions, metadata | Done |
| Full-text Search | tsvector (simple) | Keyword search multi-language | Done |
| Workflow Engine | JSONB | Plan/Apply/Verify/Unify cycle | Done |
| Workspace Scoping | FK relations | Multi-workspace isolation | Done |
| Vector Search | pgvector | Semantic similarity (cosine, HNSW, 384d) | Done |
| Knowledge Graph | Apache AGE | Dependencies, impact analysis (Cypher) | Done |

## Tech Stack

- **Language**: Python 3.12+
- **MCP SDK**: FastMCP (streamable-http transport)
- **Database**: PostgreSQL 16+ (pgvector + Apache AGE)
- **Embeddings**: paraphrase-multilingual-MiniLM-L12-v2 via fastembed (quantized ONNX runtime, 384d, ES+EN)
- **Local infra**: Docker Compose (DB from GHCR pre-built image, server from source)
- **Production infra**: uv + systemd (`memodi.service`) on a Raspberry Pi, native PostgreSQL + pgvector + Apache AGE, Cloudflare Tunnel (native cloudflared, TLS + DNS at `memodi.valdoh.com`); see `docs/pi-setup.md`; backups: deferred
- **Config**: System env vars with MEMODI_ prefix

## Auth Model

Real per-user accounts, not a single shared key:

- Log in with Google at `/login` (public route, no MCP auth by design) — `GET /oauth/callback` completes the flow, creates or reuses the user by email, and shows the `mmd_…` api key ONCE; only its hash is stored server-side. Each login mints an additional key in `api_keys` (one user, many keys) so logging in from a second machine never invalidates the first
- `X-Memodi-Api-Key` header — the caller's identity. This IS the app-level access control; there is no other gate in front of `/mcp`, nor in front of the three plain-HTTP hook routes (`POST /hooks/session-start`, `/hooks/session-close`, `/hooks/capture`) that share the same header contract
- `X-Memodi-Machine` header — per-machine identity, used to scope path registration (`memodi_workspace_start`) so the same filesystem path can resolve to different workspaces on different machines. Path registration is also per-owner: the same path on the same machine can belong to several accounts at once, each resolving their own workspace
- `path` (the caller's cwd) is an explicit per-call parameter on every project-scoped tool — never inferred from the api key or machine
- Unregistered path → `{"type": "not_started"}`; missing or invalid key → `{"type": "not_authenticated"}` — both self-describing errors, no silent auto-creation of projects or workspaces
- Key revocation is manual and explicit: `/memodi:logout` deletes the calling key's row from `api_keys` server-side and cleans up the local config; there is no other revocation path
- No-paste login: `install.sh` and `/memodi:login` obtain the key via a loopback listener on `127.0.0.1:<kernel-assigned-port>` — `GET /login?port=&nonce=` redirects the browser back to it with `?key=&nonce=&email=`, so the key never touches argv, shell history, or a paste prompt; falls back to the paste flow when python3, a browser, or the round-trip itself isn't available

## CI/CD Pipeline

4 GitHub Actions workflows, single responsibility each:

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `ci.yml` | PR to main, push to main | Lint + tests (525 tests, full coverage) |
| `deploy.yml` | `ci.yml` succeeds on main | SSH to the Pi through the Cloudflare Tunnel + `uv sync` + `systemctl restart memodi` + health check |
| `release.yml` | Tag `v*` | Auto-generated changelog + GitHub Release |
| `db-image.yml` | Changes to `Dockerfile.db` | Build + push to `ghcr.io/iam-oov/memodi-db` (dev-only image) |

Deploy authenticates to Cloudflare Access with a service token (`cloudflared access ssh` as SSH ProxyCommand), then verifies `/login` returns 302 and the installed version matches `__about__.py` — otherwise it dumps the last 30 `journalctl -u memodi` logs and fails the pipeline. A GET on `/mcp` returns 406 from a healthy server; never use it as a liveness probe.

## Plugin Structure

```
plugin/claude-code/
├── .claude-plugin/plugin.json    — plugin metadata
├── hooks/hooks.json              — SessionStart / SubagentStop / SessionEnd hooks
├── scripts/session-start.sh      — silent workspace resolution + session open on session start
├── scripts/session-digest.sh     — user-visible recap of the last 5 days at session start (systemMessage)
├── scripts/session-end.sh        — hygiene session close on session end (plain HTTP)
├── scripts/subagent-stop.sh      — captures subagent findings (plain HTTP)
├── scripts/login_listener.py     — loopback HTTP listener for the no-paste login hand-off
├── scripts/login.sh              — backs /memodi:login (re-login, no tty paste)
├── commands/start.md             — /memodi:start (user-driven activation)
├── commands/end.md               — /memodi:end (user-driven session close with a real summary)
├── commands/login.md             — /memodi:login (re-login via the loopback listener)
├── commands/logout.md            — /memodi:logout (revoke this machine's key, clean local config)
└── skills/memory/SKILL.md        — proactive memory instructions
```

The skill tells Claude WHEN and WHY to use memory. The MCP server handles HOW.

### Activation flow (user-driven, silent otherwise)

- `session-start.sh` resolves the workspace **silently**: if the path is registered it
  auto-loads context + starts a session with no announcement; if `not_started` it does
  nothing — no onboarding nudge.
- Registration happens ONLY when the user runs `/memodi:start` (slash commands are
  namespaced as `/<plugin>:<command>`, so `commands/start.md` → `/memodi:start`). That
  command registers the workspace (attaching to an existing name shares memories
  cross-machine, since observations hang off the workspace, not the machine) and loads
  workspace-wide memory.

### Session close (two doors, two audiences)

- `/memodi:end` (MCP, model-driven) — the only way a session gets a real summary; the
  model builds Goal / Accomplished / Next Steps from the conversation and calls
  `memodi_session_end`, which requires a non-empty summary.
- `SessionEnd` hook (automation) — plain HTTP (`/hooks/session-close`), never MCP: a
  shell hook cannot reliably speak the MCP protocol (no `mcp` package outside the
  project venv). It closes ONLY the session whose `client_session_id` matches the
  Claude Code session id from the hook payload, always with a NULL summary, and never
  creates a project — a hygiene net that can never clobber a real summary or close
  another window's session.

## Conventions

- Screaming architecture: folder names describe WHAT, not HOW
- No frameworks for DI — composition root pattern
- Tests required for every tool exposed via MCP
- Conventional commits (no AI attribution)
- Migrations in src/memodi/migrations/ (package-relative)

## Apache AGE Gotchas

- Every graph op needs `LOAD 'age'` + a transaction-scoped `SET LOCAL search_path = public, ag_catalog`. Never a session-level SET, and never `"$user"`: the AGE graph is a schema named `memodi` — same as the DB role — so `"$user"` resolves to it ahead of `public` and unqualified DDL (like migrations) lands in the wrong schema
- AGE does NOT support parameterized Cypher ($1, $2) — values are interpolated
- AGE does NOT support `|` union in variable-length paths (e.g. `[:A|B*1..5]`)
- `agtype` results must be cast to JSON/text for Python consumption
- Graph creation (`ensure_graph()`) is in Python, not SQL migrations, because of LOAD requirement

## Rules

- Never expose database internals through MCP tools — Claude sees domain concepts, not SQL
- Every MCP tool must have a clear, single responsibility
- PostgreSQL is the ONLY persistence — no local files for shared state
- Credentials come from system env vars only — never hardcode, never commit
- Docker Compose for local dev only; production runs natively on a Raspberry Pi behind a Cloudflare Tunnel
- Connection pool sets `idle_in_transaction_session_timeout=30s` — DB kills abandoned transactions automatically
