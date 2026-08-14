# memodi

[![CI](https://github.com/iam-oov/memodi/actions/workflows/ci.yml/badge.svg)](https://github.com/iam-oov/memodi/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/iam-oov/memodi)](https://github.com/iam-oov/memodi/releases)
[![Python](https://img.shields.io/badge/python-3.12+-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/github/license/iam-oov/memodi)](LICENSE)

English | [Español](README.es.md)

**Memoria Distribuida** — MCP server that gives Claude Code persistent memory across workspaces, projects, and machines. It saves decisions, bugs, and discoveries proactively and recalls them by keyword, semantics, or graph — with no extra LLM calls.

A single PostgreSQL instance does it all: document store (JSONB), semantic search (pgvector), and knowledge graph (Apache AGE).

## Features

- **Proactive memory** — the agent saves observations without being asked; the instructions ship with the plugin skill
- **Hybrid search** — keyword + semantic combined with RRF, plus global search across all your projects
- **Knowledge graph** — cross-repo dependencies and transitive impact analysis ("what breaks if I change X?")
- **Auto-linking** — writing `[[topic-key]]` in an observation creates the `LINKS_TO` relation in the graph
- **Multi-machine** — one key per user; registering the same workspace on two machines shares memories between them
- **Automatic context** — session hooks load memory when you open the repo and inject relevant pointers on every prompt
- **Inert by default** — an unregistered path returns `not_started`; projects and workspaces are never auto-created

## Quick start

You need [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and a memodi API key (one per user). `install.sh` gets you one automatically — it opens a browser to log in with Google and hands the key straight to the installer, nothing to copy or paste.

### Install

```bash
curl -sf https://raw.githubusercontent.com/iam-oov/memodi/main/install.sh | sh
```

### What install.sh does

One run drives login, plugin install, MCP wiring, and permissions — no manual steps:

```
$ curl -sf https://raw.githubusercontent.com/iam-oov/memodi/main/install.sh | sh
[1/6] Logging in...
Open this URL to log in:
https://memodi.valdoh.com/login?port=54231&nonce=Kx9pQ2z8mN...

Logged in as someone@example.com
Installing memodi plugin for Claude Code...
[2/6] Adding marketplace...
[3/6] Installing plugin...
[4/6] Configuring MCP server...
[5/6] Adding permissions...
[6/6] Persisting environment to your shell rc...

Done! Wrote MEMODI_API_KEY and MEMODI_MACHINE to ~/.zshrc.

Next:
  1. Reload your shell:   source ~/.zshrc   (or open a new terminal)
  2. Restart Claude Code, then run:  /memodi:start
```

A browser tab opens on that URL automatically. The key itself never appears in the terminal, your shell history, or a paste prompt — it travels from the browser redirect straight to a short-lived listener on `127.0.0.1`.

That listener binds to loopback on the machine running the installer, so the printed URL only completes the login when your browser runs on that same machine. Over SSH or on a headless box, the listener times out after 180s and the installer falls back to the paste prompt — or export `MEMODI_API_KEY` beforehand and skip login entirely.

What it touches on your machine:

- Your shell rc file (`~/.zshrc`, `~/.bash_profile`, or `~/.profile`) — a marker-delimited block with `MEMODI_API_KEY` and `MEMODI_MACHINE`
- `~/.claude.json` — the `memodi` MCP server entry
- `~/.claude/settings.json` — the `"mcp__memodi__*"` permission
- The `iam-oov/memodi` marketplace and plugin registration

Fallbacks:

| Condition | Result |
|---|---|
| `MEMODI_API_KEY` already exported | Login is skipped entirely |
| No `python3` on the machine | Falls back to the paste prompt |
| No local browser (SSH, headless) | The listener times out, then the paste prompt takes over |
| Listener times out (180s) | Falls back to the paste prompt |

The loopback hand-off is a real HTTP redirect, so the one-time-use URL can land in your local browser history. `/memodi:logout` revokes the key server-side if that's a concern.

<details>
<summary>Manual install</summary>

```bash
# 1. API key in your shell profile (~/.zshrc or ~/.bashrc)
export MEMODI_API_KEY="mmd_..."

# 2. Marketplace + plugin (session hooks + skills)
claude plugin marketplace add iam-oov/memodi
claude plugin install memodi@memodi

# 3. Server connection
claude mcp add --transport http \
  -H "X-Memodi-Api-Key: $MEMODI_API_KEY" \
  -H "X-Memodi-Machine: $(hostname)" \
  --scope user \
  memodi https://memodi.valdoh.com/mcp
```

Adding `"mcp__memodi__*"` to `permissions.allow` in `~/.claude/settings.json` avoids approving tool by tool.

</details>

Restart Claude Code and run `/memodi:start`: it registers the workspace on this machine (or attaches to an existing one from another machine — same name = shared memories) and loads its memory. Once per (machine, folder); after that, memory loads silently every time you open the repo.

`/memodi:end` closes the session with a structured summary (Goal / Accomplished / Next Steps). A `SessionEnd` hook also runs on every exit as a safety net — it never overwrites a real summary.

`/memodi:logout` revokes this machine's api key and cleans up the local config — use it to switch to a different account on this machine, or to test the login flow from scratch.

`/memodi:login` logs back in without restarting from scratch — same browser hand-off as `install.sh`, no need to re-run the whole installer. It needs a browser on the same machine as Claude Code; over SSH it has no paste fallback and will fail, so use `install.sh` in a terminal there instead.

### Upgrade

The installer is idempotent — running it again pulls the latest plugin version:

```bash
curl -sf https://raw.githubusercontent.com/iam-oov/memodi/main/install.sh | sh
```

Or directly:

```bash
claude plugin marketplace update memodi
claude plugin update memodi@memodi
```

### Uninstall

```bash
curl -sf https://raw.githubusercontent.com/iam-oov/memodi/main/uninstall.sh | sh
```

## Architecture

```
Claude Code ──HTTPS──► Cloudflare Tunnel ──► memodi-server (uv + systemd) ──► PostgreSQL
                       memodi.valdoh.com      Raspberry Pi                     pgvector + AGE
```

Claude decides what is worth remembering; memodi persists and retrieves.

| Layer | Extension | Purpose |
|-------|-----------|---------|
| Document store | JSONB | State, tasks, decisions, metadata |
| Full-text search | tsvector | Multi-language keywords |
| Semantic search | pgvector (HNSW, 384d) | "have we solved something like this?" |
| Knowledge graph | Apache AGE (Cypher) | Dependencies, impact |

## Authentication

Real per-user accounts, not a shared key:

- Log in with Google at `/login` (the only route without a key); the `mmd_...` api key is shown ONCE — the server stores only its hash. Each login mints an additional key, so logging in from a second machine never invalidates the first
- `X-Memodi-Api-Key` identifies the user and is the only access control in front of `/mcp` and `/hooks/*`
- `X-Memodi-Machine` identifies the machine; paths are registered per (user, machine, path) — the same folder can resolve to different workspaces on different machines
- `path` (the caller's cwd) is an explicit parameter on every project-scoped tool
- Unregistered path → `{"type": "not_started"}`; missing or invalid key → `{"type": "not_authenticated"}`
- Switching accounts on the same machine needs no new code: a different user's key resolves to its own memories, never the previous user's. Run `/memodi:logout` to revoke this machine's key before logging in as someone else

## MCP Tools (37)

Every project-scoped tool takes `path` (the caller's cwd) and resolves it against a registered workspace.

### Memory
| Tool | Description |
|------|-------------|
| `memodi_save` | Save an observation (auto-generates embedding); `affects` attributes one observation to several projects |
| `memodi_search` | Keyword search |
| `memodi_search_similar` | Semantic search |
| `memodi_search_hybrid` | Keyword + semantic with RRF |
| `memodi_context` | Recent context for a project |
| `memodi_search_global` | Search across all your projects (user-scoped) |
| `memodi_backfill` | Embeddings for old observations |
| `memodi_backfill_links` | Reconcile LINKS_TO from before auto-linking (idempotent) |
| `memodi_find_consolidation_clusters` | Detect clusters of observations ready to consolidate (read-only) |
| `memodi_list_projects` | Known projects and their workspace |
| `memodi_delete` | Soft-delete an observation |
| `memodi_get_observation` | Read an observation by id, including superseded ones |

### Knowledge graph
| Tool | Description |
|------|-------------|
| `memodi_relate` | Create a relation (e.g. repo-a DEPENDS_ON repo-b) |
| `memodi_dependencies` | What depends on what; with `path` includes the workspace's LINKS_TO |
| `memodi_impact` | Transitive impact; with `path` also traverses LINKS_TO |
| `memodi_graph_overview` | Summary of nodes and relations |
| `memodi_remove_relation` | Invalidate a relation (soft delete) |
| `memodi_delete_relation` | Remove a relation (hard delete) |

### Workspaces
| Tool | Description |
|------|-------------|
| `memodi_workspace_start` | Register a folder as a workspace (triggered by `/memodi:start`) |
| `memodi_list_workspaces` | List workspaces |
| `memodi_merge_projects` | Merge duplicate projects (dry_run by default) |
| `memodi_delete_workspace` | Delete a workspace |
| `memodi_rename_workspace` | Rename a workspace |
| `memodi_purge_workspace` | Empty a workspace (destructive, dry_run by default) |

### Workflow
| Tool | Description |
|------|-------------|
| `memodi_plan` | Create a plan |
| `memodi_update_plan` | Define criteria and tasks |
| `memodi_approve_plan` | Approve the plan, move to apply |
| `memodi_apply_done` | Mark apply as done |
| `memodi_verify` | Verify the result |
| `memodi_unify` | Close the loop |
| `memodi_progress` | Active workflow status |
| `memodi_task_update` | Update a task |

### Sessions and system
| Tool | Description |
|------|-------------|
| `memodi_session_start` | Start a session (observations auto-attach) |
| `memodi_session_end` | Close a session with a structured summary (required) |
| `memodi_ping` | Server liveness |
| `memodi_status` | Server health and PostgreSQL extensions |
| `memodi_version` | Version running in production |

## Graph model

```
Repo ──DEPENDS_ON──► Repo
Repo ──CONTAINS────► Module
Module ──AFFECTS───► Module
Topic ──LINKS_TO───► Topic
```

`LINKS_TO` is auto-created by writing `[[topic-key]]` in the content of a `memodi_save` that has its own `topic_key`. `Topic` is the only workspace-scoped node (identity = name + workspace_id); `Repo` and `Module` are global and created only via `memodi_relate`.

Apache AGE limitations:

- No type unions in variable-length paths (`[:A|B*1..5]`)
- No Cypher parameters — values are interpolated
- Every connection needs `LOAD 'age'` + `SET search_path`

## Local development

```bash
docker compose pull db        # pre-built image (pgvector + AGE); without pull, it compiles from source
docker compose up -d
export MEMODI_DB_USER=memodi MEMODI_DB_PASSWORD=memodi_dev
uv sync
uv run pytest -v
uv run ruff check src/ tests/
```

PR to `main` → `ci.yml` runs lint + tests (525) → on merge, `deploy.yml` deploys automatically.

## Production

Runs natively on a Raspberry Pi (PostgreSQL + pgvector + AGE, uv + systemd) behind a Cloudflare Tunnel, with push-based deploys via GitHub Actions. Full setup and day-2 operations: [`docs/pi-setup.md`](docs/pi-setup.md).

## License

MIT
