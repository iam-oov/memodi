# Roadmap: workspace-scoped-resolution

Status: approved 2026-07-12 · Artifacts: engram topic `sdd/workspace-scoped-resolution/product-decisions`

## Context

memodi will serve as the persistent-memory server for a multi-repo, multi-machine, multi-user setup. The current code has a critical flaw: project resolution is by bare name only (`get_or_create_project`, `src/memodi/database/repository.py:35-51` — `ORDER BY workspace_id ASC NULLS LAST LIMIT 1`), so two repos with the same folder name silently read/write each other's memories.

### Product decisions

1. **Explicit opt-in**: memodi is inert for unregistered paths — hard `not_started` error, no auto-creation of projects or workspaces.
2. **`memodi_workspace_start(path, workspace)`** is the only onboarding gate; register the parent folder per machine (VS Code multi-root model, prefix matching, longest-prefix-wins, exact `(machine, path)` duplicate → error naming the owning workspace).
3. **Machine identity**: path registration keyed by `(machine, path)`; hostname sent automatically via header.
4. **Real user accounts**: signup on a memodi web page → api-key shown once (only hash stored). Key → user → workspace-owner scoping. `UNIQUE(owner_user_id, name)` on workspaces.
5. **`memodi_merge_projects`** repair tool included.
6. **Destructive cleanup** approved: delete workspace-less projects and owner-less workspaces (test data).
7. **New prod infra**: Raspberry Pi + Cloudflare Tunnel + custom domain hosts DB, server, and signup page. Hetzner is gone; CLAUDE.md/deploy.yml are stale until Phase 6.

### Out of scope (future changes)

Connection pooling (`psycopg_pool`), knowledge-graph tenancy, key rotation/revocation, email verification/SMTP, multi-user-per-machine path registration, cloud scaling beyond the Pi.

Notes for the pooling change (from the Phase 2-3 reviews): `resolve_workspace` will run on every tool call after Phase 3 — cache the resolved workspace per session (invariant for a session's lifetime) and pre-filter by the user's workspaces to keep the path scan bounded; `get_connection()` also runs a `SELECT 1` probe before every query, doubling round-trips; one `memodi_save` now costs ~6 sequential round trips — the active-session lookup can fold into the observation INSERT as a subquery.

## Verified technical facts

- SDK is the official `mcp` package (1.27.0) bundled FastMCP — `get_http_headers()` does NOT exist. Tools declare `ctx: Context` (excluded from the client-facing schema); over streamable-HTTP `ctx.request_context.request.headers` holds per-request headers; over stdio it is `None` → helper must fall back.
- `@mcp.custom_route(path, methods)` exists and mounts into `streamable_http_app()` — zero-framework signup surface in the same process.
- `install.sh` already uses `claude mcp add -H "X-Api-Key: …"` — headers are an established mechanism.
- The name-only fallback is load-bearing for ~40-48 of the 81 tests (they auto-create workspace-less projects). Tests call `tools/*` functions directly, so client context must be plain function params at that layer; only `server.py` touches `ctx`/headers.

## Client-context contract

**Hybrid, server-enforced**: `X-Memodi-Api-Key` + `X-Memodi-Machine` as static per-machine HTTP headers in the MCP client config; `path` (cwd) as an explicit per-call parameter. `server.py` extracts `(user_id, machine)` via a `client_context(ctx)` helper and passes them + `path` as kwargs into `tools/*`, which enforce. Claude never types credentials; scoping is not model-trusted. Path mistakes fail closed (`not_started` error, never bleed).

---

## Phase 1 — Foundation: users, key hashing, context helper (no behavior change)

- `src/memodi/migrations/010_users.sql`: `users(id UUID PK, email TEXT UNIQUE NOT NULL, api_key_hash TEXT UNIQUE NOT NULL, created_at)`.
- `repository.py`: `create_user`, `get_user_by_api_key_hash`; key gen `secrets.token_urlsafe(32)` prefixed `mmd_`, stored as sha256 hex, compared with `hmac.compare_digest` (high-entropy key → fast hash OK; KDF out of scope).
- `src/memodi/tools/context.py` (new): `client_context(ctx)` reads headers with `socket.gethostname()`/config fallbacks, safe when `request is None` (stdio/tests).
- `src/memodi/config.py`: optional `user_api_key`/`machine` fallbacks for local dev.
- New tests: `tests/test_auth_users.py`, `tests/test_client_context.py`.
- Verify: `uv run pytest tests/test_auth_users.py tests/test_client_context.py -v`; full suite still green.

## Phase 2 — Owner/machine schema + resolution repository (additive, suite stays green)

- `011_workspace_owner.sql`: add `workspaces.owner_user_id FK`, drop global name unique, `UNIQUE INDEX (owner_user_id, name)`.
- `012_workspace_paths_machine.sql`: add `machine TEXT`, drop global path unique, `UNIQUE INDEX (machine, path)`. **Wipes existing `workspace_paths` rows first** — legacy rows have no machine attribution and belong to the exact-match era; forcing re-onboarding via `workspace_start` is safer than guessing (test data, approved).
- `repository.py` new fns: `resolve_workspace(user_id, machine, path)` — longest-prefix, owner-filtered:
  ```sql
  WHERE w.owner_user_id = %(user)s AND wp.machine = %(machine)s
    AND (%(path)s = wp.path OR %(path)s LIKE wp.path || '/%')
  ORDER BY length(wp.path) DESC LIMIT 1
  ```
  (the `|| '/%'` guard stops `/home/foo` matching `/home/foobar`); normalize trailing slashes on write and read; `workspace_start(...)` create-or-reuse by `(owner, name)`, duplicate `(machine, path)` → error naming the owning workspace; owner-scoped `list_workspaces`/`list_projects`; `merge_projects(from, into)` moves observations/sessions/workflows, deletes loser.
- **Merge caveat**: there is no unique constraint on `(project_id, topic_key)` — the merge must detect duplicate `topic_key` collisions and report them in the result (the topic upsert's `fetchone()` becomes ambiguous otherwise).
- New tests: `tests/test_resolution.py` (exact, child dir, foobar guard, nested→longest, machine isolation, owner isolation), `tests/test_merge_projects.py` (incl. topic_key collision reporting).

## Phase 3 — Strict wiring + destructive cleanup (biggest, riskiest)

- Remove name-only fallback: `get_or_create_project` requires `workspace_id`; delete the `else` branch.
- `tools/memory.py` / `tools/session.py` / `tools/workflow.py`: gain `path`, `user_id`, `machine` params; resolve workspace first; unresolved → `{"type": "not_started"}` with an error message that names the fix (`memodi_workspace_start`); bad/missing key → `{"type": "not_authenticated"}`. Add `workspace_start` and `merge_projects` tool functions; owner-scope `list_*` and `search_global`.
- `server.py`: `ctx: Context` + `path: str` on project-scoped tools; register `memodi_workspace_start`, `memodi_merge_projects` (merge: `dry_run=True` default, reusing the `purge_workspace` pattern); REMOVE `memodi_register_path`/`memodi_link_project`/`memodi_check_workspace` (no second onboarding path); update `CORE_TOOLS`, instructions, counts. Workflow tools keyed by `workflow_id` unchanged (UUID as capability token — accepted looseness).
- `013_cleanup_and_constraints.sql` (**destructive, approved**): delete workspace-less projects + children (workflow_transitions → workflows → observations → sessions → projects), delete owner-less workspaces + their paths, then `SET NOT NULL` on `projects.workspace_id` and `workspaces.owner_user_id`, drop `idx_projects_name_no_workspace`.
- `tests/conftest.py` (new): `registered_workspace` fixture (user + workspace_start + temp path/machine, env-var identity fallback — no ctx mocking). Rewrite affected tests across `test_tools_memory.py`, `test_tools_workspace.py`, `test_tools_session.py`, `test_tools_workflow.py`, `test_vector_search.py`, `test_purge_workspace.py`. Add regression test reproducing the original cross-repo bleed and asserting it is fixed.
- Verify: full suite green in one session — never leave it red between phases. `grep -rn "get_or_create_project" src/` shows only workspace-scoped call sites.
- Carry-overs from the Phase 2 review (do not skip):
  - The tool layer MUST enforce ownership on `memodi_merge_projects` — the repository function deliberately takes no user_id (mechanism vs policy); verify caller owns BOTH projects before invoking.
  - Retire the `machine DEFAULT 'legacy'` sentinel when register_path/link_project are removed: drop the column default, delete remaining machine='legacy' rows, and reject 'legacy' as a client-supplied machine value.
  - The machine-#2 attach flow passes the workspace name EXACTLY as returned by the owner-scoped listing (name drift silently creates a new workspace; consider id-based attach if it bites).

## Phase 4 — Web signup page (same process, one route, no framework)

- `server.py`: `@mcp.custom_route` `GET /signup` (inline HTML form) + `POST /signup` (create user, show `mmd_…` key ONCE, copy button). f-string HTML, Starlette responses already available.
- Minimal anti-abuse gate: `MEMODI_SIGNUP_CODE` env var (invite code field in the form) — the only signup protection, documented as such.
- Note in docs: `custom_route` bypasses MCP auth by design (correct for public signup).
- `tests/test_signup_route.py` via Starlette `TestClient` on `mcp.streamable_http_app()`: form renders, POST creates user + shows key once, wrong invite code rejected, duplicate email friendly error, only hash stored.
- Verify: tests + manual `curl localhost:8787/signup`.

## Phase 5 — Plugin contract, installer, docs

- `session-start.sh` / `post-compaction.sh`: resolve path ONCE per session; unresolved → inform "memodi inactive here, run memodi_workspace_start" (no per-save spam); pass full `${CWD}`.
- `subagent-stop.sh`: send `X-Memodi-Api-Key`/`X-Memodi-Machine` headers, pass `path`; on `not_started` exit 0 silently (opt-in inertness).
- `install.sh`: add both headers to `claude mcp add`; print the signup URL first so the user obtains a key before installing.
- `SKILL.md`: replace register_path+link_project onboarding with the start gate; document parent-folder pattern, machine-#2 select-your-workspace flow (list owner-scoped workspaces → USER picks, never invented), inert behavior, and the `path` param rule. Fix project-derivation inconsistency (lines 82 vs 151). Update tool tables/counts.
- `README.md`/`CLAUDE.md`: per-user auth model, signup, api-key = the app-level access control.
- Update `tests/test_sync_plugin_version.py` / `test_defer_loading.py` expectations; bump `plugin.json` via `scripts/sync_plugin_version.py`.
- Risk: old plugin installs break silently (`not_started`/`not_authenticated`) — ship server + plugin close together.
- Carry-overs from the Phase 3 review (do not skip):
  - `plugin/claude-code/skills/import/SKILL.md` also references the removed tools (register_path/resolve_path/link_project) — update alongside the memory skill.
  - README's `memodi_search_global` description ("todos los workspaces") is now wrong — it is owner-scoped; fix the security-relevant wording.
  - Project naming needs ONE owner: the server derives `basename(path)` when `project` is omitted. The skill must instruct Claude to pass `path` only and never self-derive a project name (mixing explicit git-remote names with omitted-project calls splits one repo's memories across two projects in the same workspace).

## Phase 6 — Infra: Raspberry Pi + Cloudflare Tunnel + backups (replaces Hetzner)

- `db-image.yml` currently builds amd64 only — add `docker/setup-qemu-action` + `platforms: linux/amd64,linux/arm64` (pgvector and AGE compile from source; QEMU builds will be slow).
- `docker/Dockerfile.mcp`: the hardcoded PyTorch CPU index (`download.pytorch.org/whl/cpu`) may lack aarch64 wheels — make it conditional or use PyPI default wheels for arm64 (verify during implementation).
- New `docker/prod/docker-compose.pi.yml`: db + server + cloudflared + backup sidecar. Retire `Caddyfile`, `memodi.service`, Hetzner-era setup scripts.
- SECURITY (from Phase 4 review): the current prod Caddyfile gates the WHOLE domain behind `X-Api-Key`, which today accidentally shields `/signup`. Once Caddy is retired, `/signup` becomes genuinely public — its own invite-code gate + body cap (added in Phase 4) become the only protection. Add `/signup` rate limiting here (per-IP throttle via Cloudflare or app-level); keep `/mcp` reachable only with a valid per-user api-key.
- **Backups are a cutover GATE, not an afterthought**: nightly `pg_dump | gzip` pushed offsite (rclone → Cloudflare R2, already in the account; or Backblaze B2), ~14-day retention. Run one full backup → push → download → restore → row-count drill BEFORE pointing DNS at the Pi. Also: manual `pg_dump` immediately before deploying Phase 3 (migration 013 auto-runs via `ensure_schema()`, no prompt).
- Deploy: recommend a self-hosted GitHub Actions runner on the Pi (reuses the workflow chain, outbound-only, native arm64 builds). Leaner alternative: pull-based deploy (systemd timer: git fetch → compose up → health check) with `deploy.yml` shrunk to CI-only. Decide at implementation; `HETZNER_*` secrets retire either way.
- Update `CLAUDE.md` architecture section (Pi + tunnel replaces Hetzner + Caddy + systemd), `README.md`, `.env.prod.example`.
- Verify: full stack up on the Pi, save round-trip from a laptop through the tunnel, 401 without key, backup drill documented as executed.

---

## Risks (ranked)

1. **Test-suite blast radius**: ~48 of 81 tests must onboard via the new fixture (Phase 3, one focused session, land atomically).
2. **Migration destructiveness**: 012 wipes `workspace_paths`; 013 deletes workspace-less projects and owner-less workspaces. Both auto-run via `ensure_schema()` with no prompt — manual `pg_dump` before deploying is non-negotiable.
3. **Plugin contract break**: server and plugin must ship together; self-describing errors let stale sessions adapt.
4. **arm64 unknowns**: AGE+pgvector under QEMU unverified until Phase 6; embeddings on Pi CPU ≈ 0.5-3s per save plus ~1GB RAM for torch — accepted; mitigations (ONNX/fastembed/smaller model) deferred.
5. **Path normalization**: trailing slashes normalized on write and read; symlinked cwds compared as given — document "register resolved paths".
6. **Public exposure**: after Phase 6 the per-user api-key is the ONLY app-level gate; signup guarded only by invite code; emails unverified. Timing-safe compares, hashed keys.

## End-to-end verification (after Phase 5)

1. Fresh DB → signup via web → get key → configure `claude mcp add` with headers.
2. `memodi_workspace_start` on a parent folder → save from two child repos → memories land in distinct projects of the same workspace.
3. From an unregistered path → `not_started`; without key → `not_authenticated`.
4. Simulate machine #2 (different `X-Memodi-Machine`, different path) → start lists existing workspaces → attach → search finds machine-#1 memories.
5. Name-collision regression test: two workspaces, same project name → no bleed.
6. Full suite: `docker compose up -d db && uv run pytest -v`.
