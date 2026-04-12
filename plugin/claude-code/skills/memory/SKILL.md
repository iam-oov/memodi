---
name: memodi-memory
description: "ALWAYS ACTIVE — Persistent shared memory protocol. You MUST save decisions, conventions, bugs, and discoveries to memodi proactively. Do NOT wait for the user to ask."
---

# Memodi — Persistent Shared Memory Protocol

You have access to Memodi, a persistent memory system backed by PostgreSQL.
This protocol is MANDATORY and ALWAYS ACTIVE — not something you activate on demand.

## TOOL LOADING

Memodi has **8 core tools** always in your context and **26 deferred tools** available via ToolSearch.

- **Core tools** — ready to use immediately, no extra steps needed
- **Deferred tools** — call `ToolSearch("select:memodi_toolname")` to load them first

## AVAILABLE TOOLS

### Core Tools (always available)

| Tool | Purpose |
|------|---------|
| `memodi_save` | Save observations (auto-generates semantic embedding) |
| `memodi_search_hybrid` | Best search: keyword + semantic with RRF scoring |
| `memodi_context` | Load recent observations for a project |
| `memodi_check_workspace` | Check if a project has a workspace |
| `memodi_resolve_path` | Resolve a filesystem path to its workspace |
| `memodi_link_project` | Link a project to a workspace |
| `memodi_ping` | Check if server is alive |
| `memodi_relate` | Create a relationship in the knowledge graph |

### Deferred Tools (load via ToolSearch first)

**Alternative search** — `ToolSearch("select:memodi_search,memodi_search_similar,memodi_search_global")`
- `memodi_search` — keyword search (exact words)
- `memodi_search_similar` — semantic search (finds by meaning, not words)
- `memodi_search_global` — keyword search across ALL workspaces

**Workspace admin** — `ToolSearch("select:memodi_list_workspaces,memodi_register_path,memodi_list_projects")`
- `memodi_list_workspaces` — list all workspaces with project count
- `memodi_register_path` — register a filesystem path to a workspace
- `memodi_list_projects` — list all known projects
- `memodi_delete_workspace` — delete a workspace
- `memodi_rename_workspace` — rename a workspace

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

## WORKSPACE AUTO-DETECTION (mandatory at session start)

At the START of every session, before any save or search:

1. Get the current working directory (pwd)
2. Call `memodi_resolve_path` with the full path (core — always available)
3. If `resolved: true` → workspace is known, use it for all operations. Derive project name from the last directory component of pwd.
4. If `resolved: false` → this is a new path, run ONBOARDING below

## WORKSPACE ONBOARDING (only for new/unregistered paths)

1. Load workspace tools: `ToolSearch("select:memodi_list_workspaces,memodi_register_path")`
2. Call `memodi_list_workspaces` to get existing workspaces
3. Show the user the available workspaces with their project count
4. Ask: "Este directorio no esta registrado. ¿A que workspace pertenece?"
5. WAIT for the user's answer — do NOT assume or continue
6. Call `memodi_register_path` with the full pwd path and the workspace name
7. Call `memodi_link_project` with the project name (last dir component) and workspace

### Workspace naming rules
- Use SHORT DESCRIPTIVE names: "trabajo", "personal", "tesis", "escuela"
- NEVER use file paths as workspace names
- NEVER use project names as workspace names
- A workspace groups MULTIPLE related projects
- Examples: "phone-call-memodi" contains repo-a, repo-b, repo-c, repo-d

This registration happens ONCE per path. After that, memodi auto-detects the workspace from the directory.

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

- **project**: derive from current working directory or git remote name
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

## WHEN TO SEARCH MEMORY

When the user asks to recall something — any variation of "remember", "recall", "what did we do", "acordate", "que hicimos", or references to past work:
1. Call `memodi_context` with the project name — gets recent observations (core — always available)
2. If not found, call `memodi_search_hybrid` — combines keyword + semantic (core — always available)
3. For project discovery, load `ToolSearch("select:memodi_list_projects")` then call it

Also search memory PROACTIVELY when:
- Starting work on a project — call `memodi_context` first to load recent observations
- Starting something that might have been done before
- User mentions a topic you have no context on
- User references another project — search that project's observations

### Which search tool to use
- `memodi_search_hybrid` — default choice, always available, best results
- `memodi_search` — when you know exact words (load via ToolSearch first)
- `memodi_search_similar` — when searching by concept (load via ToolSearch first)
- `memodi_search_global` — cross-workspace results (load via ToolSearch first)

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
After workspace detection and context loading, load session tools and start a session:
1. `ToolSearch("select:memodi_session_start")`
2. Call `memodi_session_start` with the project name

### Ending a session
Before the user ends the conversation or says "done"/"listo"/"that's it":
1. `ToolSearch("select:memodi_session_end")`
2. Call `memodi_session_end` with a structured summary:

```
## Goal
[What we were working on]

## Accomplished
- [Completed items with key details]

## Next Steps
- [What remains to be done]
```

`memodi_context` returns the last session summary — the next session starts with context from the previous one.
