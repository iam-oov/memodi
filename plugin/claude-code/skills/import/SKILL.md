---
name: memodi-import
description: "ON DEMAND — Bulk-import knowledge from .md files into memodi as structured observations. Trigger ONLY when the user explicitly asks to import, migrate, or ingest a file or directory into memodi (e.g. 'importá /path', 'migrá este repo a memodi', 'ingestá esta carpeta', 'import this folder into memodi')."
---

# Memodi — Import Protocol

This skill is **ON DEMAND**. Run it only when the user explicitly asks to import `.md` files into memodi. Never activate on your own — the `memodi-memory` skill handles routine saves.

## TRIGGER

Activate ONLY when the user explicitly says something like:
- "importá `/path`", "migrá este repo a memodi", "ingestá esta carpeta"
- "import this folder into memodi", "migrate this file to memodi", "ingest this path"
- Passes a filesystem path or `.md` file with clear intent to load it in bulk

Do NOT trigger for regular save flows — those belong to `memodi-memory`.

## PURPOSE

One-shot migration of human-written `.md` knowledge (DECISIONS, STATE, ROADMAP, CONTRACTS, README, CLAUDE, ARCHITECTURE, MILESTONES) into memodi as structured observations. Semantic extraction is done by you, the LLM. **No parser, no regex** — you read each file and decide what to save.

## INPUT RESOLUTION

The user provides either:
- A **file** → process that single file
- A **directory** → recurse and find all `.md` files matching the allowlist below

## FILE ALLOWLIST (default)

Process these patterns by default:

```
.memory/**/*.md
README.md
CLAUDE.md
ARCHITECTURE.md
STATE.md
DECISIONS.md
ROADMAP.md
CONTRACTS.md
MILESTONES.md
```

**Always excluded** (even under broad paths):

```
node_modules/  .git/  dist/  build/  vendor/
target/  .venv/  __pycache__/  .next/  coverage/
```

If the user explicitly asks to include `docs/**/*.md` or passes a file outside the allowlist, honor it. Confirm once.

## WORKFLOW (4 phases)

### Phase 1 — Discovery and plan

1. Load required deferred tools:
   `ToolSearch("select:memodi_search_similar,memodi_list_projects,memodi_list_workspaces,memodi_register_path")`
2. Call `memodi_ping`. If it fails, stop and report — don't proceed.
3. Resolve target project + workspace via `memodi_resolve_path` (core tool). If the path is unregistered, run the workspace onboarding from `memodi-memory` first.
4. `Glob` the input. Apply allowlist + exclusions.
5. For each file, collect: path, size, mtime, first 3 headers (to estimate observation count).

### Phase 2 — Plan confirmation (MANDATORY STOP)

Present the plan to the user in this shape:

```
Importing to project: <project>  (workspace: <workspace>)

Files found (N):
- .memory/DECISIONS.md      12 KB  last modified 2025-10-14
- .memory/STATE.md           8 KB  last modified 2025-11-02
- README.md                  4 KB  last modified 2025-10-01
- ...

Estimated observations: ~M  (rough count from header scan)

Mode: preview-per-file — you'll see extractions from the first file before saving,
then I'll continue for the rest unless you stop me.

Proceed?
```

**STOP. Wait for the user's confirmation.** Do not touch memodi (beyond ping/resolve) until they say go.

### Phase 3 — Process files (one at a time, knowledge-dense first)

Process order: `DECISIONS.md` → `CONTRACTS.md` → `ARCHITECTURE.md` → `STATE.md` → `ROADMAP.md` → `CLAUDE.md` → `README.md` → `MILESTONES.md` → rest.

For each file:

1. **Read** with the `Read` tool.
2. **Extract** observations semantically (see extraction guide below).
3. **For each candidate observation**, build the full payload: `type`, `title`, `topic_key`, `occurred_at`, `content` (What/Why/Where/Learned).
4. **Dedup**: call `memodi_search_similar` with the observation title. If top result has similarity > 0.85 AND same `topic_key` → skip, note it for the final report.
5. **Show extraction summary** for this file:

   ```
   File: .memory/DECISIONS.md
   Extracted 12 observations:
     - [decision] 2024-08-15: Chose LiveKit over Twilio → topic: decisions/webrtc-livekit
     - [decision] 2024-09-02: SAM for speaker isolation → topic: decisions/speaker-isolation
     - ... (10 more)
   Skipped 2 by dedup.
   Saving now.
   ```

6. **Save** each observation with `memodi_save`.
7. **First file only**: after saving, ask once: *"¿Sigo igual con el resto o querés revisar cada archivo?"* Cache the answer for the rest of the session.

### Phase 4 — Final report

```
Import complete.

✔ 142 observations saved
⊘  17 skipped (dedup, similarity > 0.85)
⚠   3 files with warnings:
    - CLAUDE.md: no dated content, used file mtime as occurred_at
    - README.md: mixed content, extracted 4 architecture + 2 config
    - docs/notes.md: generic .md, review recommended

Suggested next steps:
- memodi_context <project>           — verify
- memodi_search_hybrid "<tema>"      — test a query
```

## EXTRACTION GUIDE BY FILE TYPE

### DECISIONS.md (MVP — highest value)

- **Granularity**: one observation per `## YYYY-MM-DD — title` entry
- `type`: `decision`
- `occurred_at`: date parsed from the header (ISO 8601, `T00:00:00Z`)
- `topic_key`: `decisions/<slug-of-title>`
- `content`: preserve the structured fields (Context, Decision, Consequences, Supersedes, Files, Affected) into What/Why/Where/Learned
- **If `Supersedes:` is present**: after saving, also call `memodi_relate("Decision", "<current>", "Decision", "<superseded>", "SUPERSEDES")`

### CONTRACTS.md

- **Granularity**: one observation per contract/queue
- `type`: `architecture`
- `occurred_at`: file mtime
- `topic_key`: `contracts/<queue-name>`
- **Also** create graph edges: for each `### queue_name` block with `Publisher:` and `Consumer:` fields, call `memodi_relate` with `PUBLISHES_TO` / `CONSUMES_FROM`

### ARCHITECTURE.md

- **Granularity**: by major section
- `type`: `architecture`
- `occurred_at`: file mtime
- `topic_key`: `architecture/<section-slug>`

### STATE.md (rolling — current state only)

- **Granularity**: the current "Current Status" section as ONE observation
- `type`: `architecture`
- `occurred_at`: file mtime
- `topic_key`: `state/current`
- "Recent Changes" subsection: one observation per dated entry, `type=discovery`, `occurred_at` from the entry date

### ROADMAP.md (rolling)

- **Granularity**: committed milestones only (ignore past/done ones)
- `type`: `decision` for committed items, `pattern` for long-term intent
- `occurred_at`: file mtime
- `topic_key`: `roadmap/<milestone-slug>`

### CLAUDE.md

- **Granularity**: by rule or convention (usually bullet or section)
- `type`: `pattern` for conventions, `preference` for user preferences, `config` for tool setup
- `occurred_at`: file mtime
- `topic_key`: `conventions/<rule-slug>`

### README.md

- **Granularity**: by section (Setup, Stack, Architecture, Usage)
- `type`: `architecture` for design; `config` for setup
- `occurred_at`: file mtime
- `topic_key`: `readme/<section-slug>`

### MILESTONES.md

- **Granularity**: one observation per milestone
- `type`: `decision`
- `occurred_at`: milestone date if present, else file mtime
- `topic_key`: `milestones/<milestone-slug>`

### Generic `.md` (fallback, only if user explicitly included it)

- Best-effort semantic extraction, one observation per major section
- `type`: `discovery`
- `occurred_at`: file mtime
- Flag in the final report as "generic extraction — review recommended"

## occurred_at RESOLUTION (in order)

1. Date in the entry header (`## 2024-10-15 — Title`) → use it (`T00:00:00Z`)
2. Date in the filename (`2024-10-15-note.md`) → use it
3. File mtime from the filesystem → use it
4. Nothing available → omit (memodi defaults to now; flag in report)

Always ISO 8601: `2024-10-15T00:00:00Z`.

## DEDUPLICATION

Before every `memodi_save`:
1. Call `memodi_search_similar` with the observation title
2. If top result has similarity > 0.85 **AND** same `topic_key` → skip (idempotent re-run)
3. If similarity > 0.85 but different `topic_key` → save, note the overlap in the final report

This makes the import **safe to re-run**. If the user interrupts mid-flow, running again picks up where it left off without duplicates.

## SAFETY RULES (MANDATORY)

- **Never** start Phase 3 without explicit user confirmation in Phase 2
- **Always** dedup before `memodi_save`
- **Never** skip the final report — the user needs to see the outcome
- **Stop and ask** if more than 20% of a file's content is unclear or contradictory
- Before starting, tell the user: *"If you interrupt, re-running is safe — dedup skips what was already saved."*

## POST-IMPORT

Suggest (don't auto-run):
- `memodi_context <project>` — verify recent observations
- `memodi_search_hybrid "<tema>"` — test a query
- Review the warnings list in the final report
