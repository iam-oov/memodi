---
name: memodi-memory
description: "ALWAYS ACTIVE — Persistent shared memory protocol. You MUST save decisions, conventions, bugs, and discoveries to memodi proactively. Do NOT wait for the user to ask."
---

# Memodi — Persistent Shared Memory Protocol

You have access to Memodi, a persistent memory system backed by PostgreSQL.
This protocol is MANDATORY and ALWAYS ACTIVE — not something you activate on demand.

## AVAILABLE TOOLS

### Memory (always use proactively)
- `memodi_save` — save observations (auto-generates semantic embedding)
- `memodi_search` — keyword search (exact words)
- `memodi_search_similar` — semantic search (finds by meaning, not words)
- `memodi_search_hybrid` — best of both: keyword + semantic with RRF scoring
- `memodi_context` — load recent observations for a project
- `memodi_list_projects` — list all known projects
- `memodi_search_global` — keyword search across ALL workspaces
- `memodi_backfill` — generate embeddings for old observations without them

### Knowledge Graph (proactive — create relationships when discovered)
- `memodi_relate` — create a relationship (e.g. repo-a DEPENDS_ON repo-b)
- `memodi_dependencies` — show what depends on what
- `memodi_impact` — transitive impact analysis: "what breaks if I change X?"
- `memodi_graph_overview` — summary of all nodes and relationships
- `memodi_remove_relation` — remove a relationship

### Workspace management
- `memodi_check_workspace` — check if a project has a workspace
- `memodi_link_project` — link a project to a workspace
- `memodi_list_workspaces` — list all workspaces with project count

### Workflow (only when user requests structured work)
- `memodi_plan` — start a new workflow plan
- `memodi_update_plan` — define acceptance criteria and tasks
- `memodi_approve_plan` — approve plan, move to apply
- `memodi_apply_done` — mark apply as done, move to verify
- `memodi_verify` — record verification, pass → unify or fail → apply
- `memodi_unify` — close the loop, mark completed
- `memodi_progress` — show active workflow state
- `memodi_task_update` — update a specific task's status

### System
- `memodi_ping` — check if server is alive
- `memodi_status` — check database health and extensions

## WORKSPACE AUTO-DETECTION (mandatory at session start)

At the START of every session, before any save or search:

1. Get the current working directory (pwd)
2. Call `memodi_resolve_path` with the full path
3. If `resolved: true` → workspace is known, use it for all operations. Derive project name from the last directory component of pwd.
4. If `resolved: false` → this is a new path, run ONBOARDING below

## WORKSPACE ONBOARDING (only for new/unregistered paths)

1. Call `memodi_list_workspaces` to get existing workspaces
2. Show the user the available workspaces with their project count
3. Ask: "Este directorio no esta registrado. ¿A que workspace pertenece?"
4. WAIT for the user's answer — do NOT assume or continue
5. Call `memodi_register_path` with the full pwd path and the workspace name
6. Call `memodi_link_project` with the project name (last dir component) and workspace

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
- Repo A imports/calls Repo B → `memodi_relate("Repo", "repo-a", "Repo", "repo-b", "DEPENDS_ON")`
- Repo contains a module → `memodi_relate("Repo", "repo-a", "Module", "auth", "CONTAINS")`
- Changing module X affects module Y → `memodi_relate("Module", "auth", "Module", "api", "AFFECTS")`
- Before making changes, check `memodi_impact` to see what might break

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
1. Call `memodi_context` with the project name — gets recent observations
2. If not found, call `memodi_search_hybrid` — combines keyword + semantic
3. Use `memodi_list_projects` if unsure which project to search

Also search memory PROACTIVELY when:
- Starting work on a project — call `memodi_context` first to load recent observations
- Starting something that might have been done before
- User mentions a topic you have no context on
- User references another project — search that project's observations

### Which search tool to use
- `memodi_search` — when you know the exact words (e.g. "JWT", "LiveKit")
- `memodi_search_similar` — when searching by concept (e.g. "how do we handle auth?")
- `memodi_search_hybrid` — default choice, best results, combines both
- `memodi_search_global` — when you need cross-workspace results

## WORKFLOW PROTOCOL (only when user requests it)

The workflow tools implement a Plan → Apply → Verify → Unify cycle. Use them ONLY when the user explicitly asks for structured planning or says things like "planifiquemos", "hagamos un plan", "let's plan this".

```
plan ──approve──► apply ──done──► verify ──pass──► unify ──► completed
                                    │
                                    │ fail
                                    ▼
                                  apply (back to fix)
```

Memory (memodi_save/search/context) is ALWAYS proactive.
Workflow (memodi_plan/approve/verify/unify) is ON DEMAND.
