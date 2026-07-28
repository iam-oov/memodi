---
name: memodi-memory
description: "ALWAYS ACTIVE — Persistent shared memory protocol. You MUST save decisions, conventions, bugs, and discoveries to memodi proactively. Do NOT wait for the user to ask."
---

# Memodi — Persistent Shared Memory Protocol

You have access to Memodi, a persistent memory system backed by PostgreSQL.
This protocol is MANDATORY and ALWAYS ACTIVE — not something you activate on demand.

## TOOL LOADING

Memodi has **6 core tools** always in your context and **29 deferred tools** available via ToolSearch.

- **Core tools** — ready to use immediately, no extra steps needed
- **Deferred tools** — call `ToolSearch("select:memodi_toolname")` to load them first

## AVAILABLE TOOLS

### Core Tools (always available)

| Tool | Purpose |
|------|---------|
| `memodi_save` | Save observations (auto-generates semantic embedding) |
| `memodi_search_hybrid` | Best search: keyword + semantic with RRF scoring |
| `memodi_context` | Load recent observations for a project |
| `memodi_workspace_start` | Register a parent folder as a workspace — the ONLY onboarding gate |
| `memodi_ping` | Check if server is alive |
| `memodi_relate` | Create a relationship in the knowledge graph |

### Deferred Tools (load via ToolSearch first)

**Alternative search** — `ToolSearch("select:memodi_search,memodi_search_similar,memodi_search_global")`
- `memodi_search` — keyword search (exact words)
- `memodi_search_similar` — semantic search (finds by meaning, not words)
- `memodi_search_global` — keyword search across ALL of the caller's OWN projects (owner-scoped, not cross-user)

**Workspace admin** — `ToolSearch("select:memodi_list_workspaces,memodi_list_projects,memodi_merge_projects,memodi_delete_workspace,memodi_rename_workspace,memodi_purge_workspace")`
- `memodi_list_workspaces` — list the caller's own workspaces with project count
- `memodi_list_projects` — list the caller's own known projects and workspace assignments
- `memodi_merge_projects` — repair tool: merge one project into another, moving observations/sessions/workflows (destructive, dry_run default)
- `memodi_delete_workspace` — delete a workspace
- `memodi_rename_workspace` — rename a workspace
- `memodi_purge_workspace` — wipe workspace data for dev-loop resets (destructive, dry_run default)

**Graph queries** — `ToolSearch("select:memodi_dependencies,memodi_impact,memodi_graph_overview,memodi_remove_relation,memodi_delete_relation")`
- `memodi_dependencies` — show what depends on what (current relationships only)
- `memodi_impact` — transitive impact analysis: "what breaks if I change X?"
- `memodi_graph_overview` — summary of all nodes and relationships (includes valid_at)
- `memodi_remove_relation` — invalidate a relationship (soft delete, preserves history)
- `memodi_delete_relation` — permanently remove a relationship (hard delete)

**Workflow** — `ToolSearch("select:memodi_plan,memodi_update_plan,memodi_approve_plan,memodi_apply_done,memodi_verify,memodi_unify,memodi_progress,memodi_task_update")`
- `memodi_plan` — start a new workflow plan
- `memodi_update_plan` — define acceptance criteria and tasks
- `memodi_approve_plan` — approve plan, move to apply
- `memodi_apply_done` — mark apply as done, move to verify
- `memodi_verify` — record verification, pass → unify or fail → apply
- `memodi_unify` — close the loop, mark completed
- `memodi_progress` — show active workflow state
- `memodi_task_update` — update a specific task's status

**Session lifecycle** — `ToolSearch("select:memodi_session_start,memodi_session_end")`
- `memodi_session_start` — start a session (observations auto-attach to it)
- `memodi_session_end` — close session with structured summary

**System extras** — `ToolSearch("select:memodi_status,memodi_version")`
- `memodi_status` — check database health and extensions
- `memodi_version` — return server version

**Maintenance** — `ToolSearch("select:memodi_backfill")`
- `memodi_backfill` — generate embeddings for old observations without them

**Correcting memory** — `ToolSearch("select:memodi_delete,memodi_get_observation")`
- `memodi_delete` — for junk, test, or wrong observations only
- `memodi_get_observation` — read one observation by id, superseded ones included (audit path)

## WORKSPACE GATE (mandatory before any project-scoped call)

memodi is **INERT for unregistered paths** — there is no auto-creation of projects or
workspaces. Every project-scoped tool takes `path` (the caller's cwd), and it must
resolve to a workspace registered via `memodi_workspace_start` on this exact machine.
There is no separate "check" or "resolve" tool — you find out by trying an operation:

1. Call a core project-scoped tool (e.g. `memodi_context`) with `path: "<cwd>"`.
2. If it returns `{"type": "not_started"}` → this path has no registered workspace on
   this machine. **Stay silent — do NOT warn, suggest a command, or mention memodi.**
   Memory is dormant until the user runs `/memodi:start`. Do not re-check this session.
3. If it returns `{"type": "not_authenticated"}` → the configured api key is missing or
   invalid. Tell the user once; memodi cannot function until the plugin is reconfigured
   with a valid key (see README.md / install.sh). Do not retry this on every save.
4. Otherwise → the workspace is resolved. Resolve **once per session** — do not
   repeat this check before every subsequent call.

### Activation is user-driven — never nudge

The SessionStart hook resolves the workspace **silently**: if the path is registered it
auto-loads context and starts a session without announcing anything; if it is
`not_started` it does nothing at all. Registration happens ONLY when the user runs the
`/memodi:start` command (defined in `commands/start.md`) — that command performs the
WORKSPACE ONBOARDING flow below. Do not perform onboarding on your own initiative or
prompt the user toward it; wait for `/memodi:start`.

### CRITICAL — pass `path`, never invent a `project` name

Every project-scoped tool takes `path`. Pass it ALWAYS. Do not pass `project` unless
you have a deliberate, one-time reason to split a single repo into multiple logical
projects — and if you do, use that SAME explicit name on every future call for that
repo. When `project` is omitted, the server derives it as `basename(path)`. Mixing an
explicit project name with omitted-project calls for the SAME repo silently splits its
memories across two projects inside the same workspace. **When in doubt, omit
`project` and let `path` drive it.**

## WORKSPACE ONBOARDING (runs only via `/memodi:start`)

This flow is triggered by the `/memodi:start` command, not on your own initiative. When
that command runs on a `not_started` path, follow these steps.

**⚠️ CRITICAL: Never invent or guess a workspace name. The user decides — you wait.**

memodi models workspaces like VS Code's multi-root workspace: you register the
**parent folder** that holds multiple related repos (not each repo individually), and a
path resolves to the workspace whose registered path is the longest matching prefix.
A repo at `/home/user/work/repo-a` resolves through a workspace registered at
`/home/user/work` — each repo then becomes its own project inside that workspace.

1. Load `ToolSearch("select:memodi_list_workspaces")` and call `memodi_list_workspaces`
   — this lists the workspaces already registered for the caller (owner-scoped: only
   the user's own, across all of their machines).
2. **Machine #2 flow** — if this is a new machine and the listing already returns
   workspaces (the user set memodi up elsewhere first): show the list and ask the user
   to pick ONE. Pass the workspace name to `memodi_workspace_start` **EXACTLY as
   returned by the listing** — never retype, translate, or "clean up" it. Any drift
   (typo, casing, extra whitespace) silently creates a brand-new workspace instead of
   attaching to the existing one. **You never invent the name — the user always picks
   from what the listing returned, or explicitly asks for a new one.**
3. **First machine / no fit** — if no existing workspace fits, ask the user for a short
   descriptive name for the parent folder (e.g. "trabajo", "personal", "tesis") —
   never a path, never a project name.
4. **STOP and WAIT** for the user's answer. Do not proceed, do not assume, do not say
   "voy a crear el workspace X" before they've responded.
5. Call `memodi_workspace_start(path=<parent folder>, workspace=<name the user gave or
   picked>)`. Use the parent folder that contains the caller's repos, not the current
   repo's own path — unless the user genuinely works out of a single repo.
6. From then on, every path under that parent resolves automatically on this machine —
   no per-repo registration needed.

### Workspace naming rules
- Use SHORT DESCRIPTIVE names: "trabajo", "personal", "tesis", "escuela"
- NEVER use file paths as workspace names
- NEVER use project names as workspace names
- A workspace groups MULTIPLE related projects (parent-folder model)
- Examples: "trabajo" contains repo-a, repo-b, repo-c, repo-d

This registration happens ONCE per (machine, path). After that, memodi auto-detects
the workspace from the path on that machine.

## PROACTIVE SAVE TRIGGERS (mandatory — do NOT wait for user to ask)

Call `memodi_save` IMMEDIATELY and WITHOUT BEING ASKED after any of these:

### After decisions or conventions
- Architecture or design decision made
- Convention documented or established
- Workflow change agreed upon
- Tool or library choice made with tradeoffs

### After completing work
- Bug fix completed (include root cause)
- Feature implemented with non-obvious approach
- Configuration change or environment setup done

### After discoveries
- Non-obvious discovery about the codebase or an API/SDK
- Gotcha, edge case, or unexpected behavior found
- Pattern established (naming, structure, convention)
- User preference or constraint learned
- Cross-repo or cross-module dependency discovered → also call `memodi_relate`

### After discovering dependencies (use memodi_relate)
Relationships are **temporal** — they automatically track when they were created (valid_at).
Re-creating the same relationship invalidates the old version and creates a new one.

- Repo A imports/calls Repo B → `memodi_relate("Repo", "repo-a", "Repo", "repo-b", "DEPENDS_ON")`
- Repo contains a module → `memodi_relate("Repo", "repo-a", "Module", "auth", "CONTAINS")`
- Changing module X affects module Y → `memodi_relate("Module", "auth", "Module", "api", "AFFECTS")`
- Dependency no longer exists → load graph tools, use `memodi_remove_relation` (invalidates, keeps history)
- Before making changes, load graph tools and check `memodi_impact` to see what might break

### After user confirmation or rejection
- User confirms a recommendation ("dale", "go with that", "si", "sounds good")
- User rejects an approach ("no, mejor X", "I prefer X", "descartemos eso")
- User expresses a preference ("siempre hace X", "I prefer X over Y")
- A discussion concludes with a clear direction chosen

### Self-check — ask yourself after EVERY task:
> "Did I or the user just make a decision, confirm a recommendation, express a preference, fix a bug, learn something non-obvious, or establish a convention? If yes, call memodi_save NOW."

## Format for memodi_save

- **path**: the caller's cwd — ALWAYS pass this, on every call. See the CRITICAL note
  above about `project` — do not derive or invent one.
- **title**: Verb + what — short, searchable (e.g. "Chose LiveKit over Twilio for WebRTC")
- **type**: decision | bugfix | discovery | pattern | config | preference | architecture
- **topic_key** (recommended for evolving topics): stable key like "architecture/auth-model". Same topic_key = upsert (updates existing, tracks revisions)
- **content**: structured as:
  - **What**: One sentence — what was done
  - **Why**: What motivated it
  - **Where**: Files or paths affected
  - **Learned**: Gotchas, edge cases (omit if none)

### Topic update rules
- Different topics MUST NOT overwrite each other
- Same topic evolving → use same topic_key (upsert)
- When unsure, use a descriptive key like "config/database" or "pattern/error-handling"

## CORRECTING MEMORY

memodi has three corrective mechanisms — pick the narrowest one that fits:

1. **topic_key upsert** (default) — same topic_key on `memodi_save` updates the
   existing observation in place. Use this whenever you know the topic_key.
2. **supersedes** — pass `supersedes=<old-observation-id>` on `memodi_save` when
   you're replacing an observation but don't know its topic_key. The old one
   stops surfacing in context/search; it stays readable via
   `memodi_get_observation` with `superseded_by` pointing at the replacement.
   A bad id never fails the save: check `supersedes_applied` and
   `supersedes_reason` — `self` means a topic_key upsert or duplicate merge
   already corrected that same row, so do NOT retry.
3. **memodi_delete** — load via `ToolSearch("select:memodi_delete")`. Only for
   junk, test, or flat-out wrong observations — not for "this decision changed."
   Soft delete, reversible at the DB level, idempotent (deleting twice still
   acks success).

Undoing a supersede: delete the replacement. Deleting an observation clears
every `superseded_by` pointing at it, so whatever it replaced surfaces again.

## WHEN TO SEARCH MEMORY

When the user asks to recall something — any variation of "remember", "recall", "what did we do", "acordate", "que hicimos", or references to past work:
1. Call `memodi_context` with `path` — gets recent observations (core — always available)
2. If not found, call `memodi_search_hybrid` with `path` — combines keyword + semantic (core — always available)
3. For project discovery, load `ToolSearch("select:memodi_list_projects")` then call it

Also search memory PROACTIVELY when:
- Starting work on a project — call `memodi_context` first to load recent observations
- Starting something that might have been done before
- User mentions a topic you have no context on
- User references another project — search that project's observations (pass that project's own `path`)

### Which search tool to use
- `memodi_search_hybrid` — default choice, always available, best results
- `memodi_search` — when you know exact words (load via ToolSearch first)
- `memodi_search_similar` — when searching by concept (load via ToolSearch first)
- `memodi_search_global` — cross-project results across the caller's OWN projects (load via ToolSearch first)

## WORKFLOW PROTOCOL (only when user requests it)

The workflow tools implement a Plan → Apply → Verify → Unify cycle. Use them ONLY when the user explicitly asks for structured planning or says things like "planifiquemos", "hagamos un plan", "let's plan this".

First, load all workflow tools: `ToolSearch("select:memodi_plan,memodi_update_plan,memodi_approve_plan,memodi_apply_done,memodi_verify,memodi_unify,memodi_progress,memodi_task_update")`

```
plan ──approve──► apply ──done──► verify ──pass──► unify ──► completed
                                    │
                                    │ fail
                                    ▼
                                  apply (back to fix)
```

Memory (memodi_save/search_hybrid/context) is ALWAYS proactive.
Workflow (memodi_plan/approve/verify/unify) is ON DEMAND.

## SESSION LIFECYCLE

Sessions group observations within a work period. Observations are auto-attached to the active session.

### Starting a session
After the workspace gate resolves and context loading, load session tools and start a session:
1. `ToolSearch("select:memodi_session_start")`
2. Call `memodi_session_start` with `path`

### Ending a session
Before the user ends the conversation or says "done"/"listo"/"that's it":
1. `ToolSearch("select:memodi_session_end")`
2. Call `memodi_session_end` with `path` and a structured summary:

```
## Goal
[What we were working on]

## Accomplished
- [Completed items with key details]

## Next Steps
- [What remains to be done]
```

`memodi_context` returns the last session summary — the next session starts with context from the previous one.
