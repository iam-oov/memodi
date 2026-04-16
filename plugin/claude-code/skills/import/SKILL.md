---
name: memodi-import
description: "ON DEMAND — Bulk-import knowledge from .md files into memodi as structured observations. Trigger ONLY when the user explicitly asks to import, migrate, or ingest a file or directory into memodi (e.g. 'importá /path', 'migrá este repo a memodi', 'ingestá esta carpeta', 'import this folder into memodi')."
---

# Memodi — Import Protocol

This skill is **ON DEMAND**. Run it only when the user explicitly asks to import `.md` files into memodi. Never activate on your own — the `memodi-memory` skill handles routine saves.

## CORE PRINCIPLE — minimal friction

The user gave you a clear intent ("import this"). Do NOT interrupt the flow with multiple confirmations. The rule: **one pre-flight briefing, then silent execute, then a final report**. Every extra prompt is friction that breaks flow on bulk imports of 50+ files.

## TRIGGER

Activate ONLY when the user explicitly says something like:
- "importá `/path`", "migrá este repo a memodi", "ingestá esta carpeta"
- "import this folder into memodi", "migrate this file to memodi", "ingest this path"
- Passes a filesystem path or `.md` file with clear intent to load it in bulk

If the user adds phrases like "sin preguntar", "no preguntes", "sin confirmación", "go ahead", skip the briefing confirmation in Phase 2 as well (onboarding still requires confirmation — it's a permanent side-effect).

Do NOT trigger for regular save flows — those belong to `memodi-memory`.

## PURPOSE

One-shot migration of human-written `.md` knowledge (DECISIONS, STATE, ROADMAP, CONTRACTS, README, CLAUDE, ARCHITECTURE, MILESTONES) into memodi as structured observations. Semantic extraction is done by you, the LLM. **No parser, no regex** — you read each file and decide what to save.

## INPUT RESOLUTION

- A **file** → process that single file
- A **directory** → recurse and find all `.md` files matching the allowlist below

## FILE ALLOWLIST (default)

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

If the user explicitly asks to include a non-allowlist file, honor it silently.

## WORKFLOW (6 phases)

### Phase 1 — Discovery + workspace onboarding (silent unless onboarding needed)

1. Load required deferred tools (once per session):
   `ToolSearch("select:memodi_list_workspaces,memodi_register_path")`
2. Call `memodi_ping`. If it fails, stop and report.
3. Call `memodi_resolve_path` with the target repo path.
   - If `resolved: true` → continue silently to Phase 2.
   - If `resolved: false` → **MANDATORY onboarding** (workspace creation is a permanent side-effect; needs user consent):
     - Call `memodi_list_workspaces` to show existing options.
     - Ask the user: *"Este path no está registrado. ¿A qué workspace lo linkeo? (opciones: ..., o nombre nuevo)"*
     - WAIT for answer.
     - Call `memodi_link_project` + `memodi_register_path` with the user's choice.
4. `Glob` the input. Apply allowlist + exclusions.
5. For each file, collect: path, size, first 3 headers (to estimate observation count).

### Phase 2 — One-line pre-flight briefing

Present a **single terse summary** and ask ONE question:

```
Import plan: N file(s) from <path>, ~M observations estimated.
Target: project "<project>" in workspace "<workspace>". Proceed?
```

If the user already said "sin preguntar"/"go ahead", skip this briefing entirely.

Otherwise STOP and wait for a yes. No further confirmations after this.

### Phase 3 — Silent execute + progress

Process files in knowledge-dense order: `DECISIONS.md` → `CONTRACTS.md` → `ARCHITECTURE.md` → `STATE.md` → `ROADMAP.md` → `CLAUDE.md` → `README.md` → `MILESTONES.md` → rest.

For each file, silently:

1. `Read` the file.
2. Extract observations semantically (see extraction guide below).
3. For each observation, build the full payload: `type`, `title`, `topic_key`, `occurred_at`, `content` (What/Why/Where/Learned).
4. Call `memodi_save`. **Do NOT call `memodi_search_similar` beforehand** — `memodi_save` already deduplicates internally via `content_hash` and upserts by `topic_key`. Trust the server. Track the response: if `duplicate_count > 0` → count as skipped in the final report.
5. For `Supersedes:` fields → call `memodi_relate("Decision", <current>, "Decision", <superseded>, "SUPERSEDES")` after the save.
6. For `Publisher:` / `Consumer:` in CONTRACTS.md → call `memodi_relate` for queue edges after the save.

**Progress line** — emit ONE compact line per file (not per observation):

```
✔ DECISIONS.md — 20 saved, 0 duplicates
✔ STATE.md — 1 saved
⚠ CLAUDE.md — 4 saved, 1 warning (unclear section)
```

Do NOT emit preview lists, per-observation details, or extraction summaries. Those go in the final report if relevant.

### Phase 4 — Cross-file synthesis (directory imports only)

> Runs automatically after Phase 3 completes. Skip entirely for single-file imports.

**Trigger**: only when the import source was a **directory** (Phase 1 detected multiple files).

1. Call `memodi_context <project>` to read back the observations just saved in Phase 3. Do not re-read source files.
2. Scan silently for cross-cutting knowledge:
   - A decision that **constrains or explains** a choice in another file
   - A rule or convention that **multiple files reference independently**
   - An architectural pattern that **emerges only when files are read together**
   - A **contradiction or tension** between documents worth surfacing
3. Generate 3–5 synthesis observations that capture knowledge **no single source file contains on its own**:
   - `type`: `architecture` or `pattern`
   - `topic_key`: `synthesis/<theme-slug>`
   - `content`: standalone insight — readable without the source files
   - Save with `memodi_save`
4. For detected relationships between any two observations, call `memodi_relate` with `SUPERSEDES`, `CAUSED_BY`, or `ENABLES`.
5. Progress: `✔ Synthesis: N meta-observations + M graph edges created` (or `skipped` if no cross-file patterns detected)

### Phase 5 — Relationship inference

Run after Phase 4 (directory) or after Phase 3 (single file). Skip if fewer than 2 observations were saved.

Read back all observations saved in this session. For each pair, reason over titles and content — NO regex, NO parsing. Look for:

- **SUPERSEDES**: "retired X", "replaced X", "migrated from X to Y"
- **CAUSED_BY**: observation A states that B motivated it
- **ENABLES**: "this allows X", "unblocks X", downstream dependencies
- **DEPENDS_ON**: "requires X", "builds on X"

Create an edge only when the relationship is **CLEAR and stated** (or strongly implied) in the text. Do not hallucinate from vague thematic similarity.

```
memodi_relate("Decision", "<topic_key_A>", "Decision", "<topic_key_B>", "<EDGE_TYPE>")
```

Progress: `✔ Relationships: N edges created (M SUPERSEDES, P ENABLES, ...)` — or `0 edges` if none found.

### Phase 6 — Final report

ONE report at the end, compact:

```
Import complete ✔

Project: <project> (workspace: <workspace>)
Files processed: N
✔  142 saved
⊘   17 duplicates (memodi_save internal dedup)
🔗   5 synthesis observations + 7 graph edges
⚠    3 warnings:
     - CLAUDE.md: no dated content, used file mtime as occurred_at
     - notes.md: generic .md, content extracted but review recommended
     - 2026-03 entry in DECISIONS.md: month-only date, resolved to 2026-03-01

Next: memodi_context <project> | memodi_search_hybrid "<topic>"
```

## EXTRACTION GUIDE BY FILE TYPE

### Extraction modes

Two modes govern how you split a file into observations:

**Structured** — file has clear, discrete entries with their own dates or identifiers.
Read entry-by-entry. One observation per entry. Preserve dates from headers.
Use when: DECISIONS.md, MILESTONES.md.

**Synthesized** — file has no clear entry boundaries, or entries are too small to be useful alone.
Read the WHOLE file first, then extract 3–7 thematic observations that capture the file's knowledge
as a human expert would summarize it for a colleague. Group by concept, not by heading.
Use when: README.md, CLAUDE.md, STATE.md, ARCHITECTURE.md, CONTRACTS.md, ROADMAP.md, generic .md.

Rule of thumb: if you find yourself producing more than 8 observations from a single file,
or any observation is under 3 meaningful sentences, switch to synthesized mode.

### DECISIONS.md (MVP — highest value)

- **Mode**: structured
- **Granularity**: one observation per `## YYYY-MM-DD — title` entry
- `type`: `decision`
- `occurred_at`: date parsed from the header (ISO 8601, `T00:00:00Z`)
- `topic_key`: `decisions/<slug-of-title>` (cap slug at ~40 chars)
- `content`: map Context → Why, Decision → What, Files/Affected → Where, Validation/Trade-offs/Follow-up/Lesson → Learned. Preserve key technical details verbatim.
- **If `Supersedes:` field present**: call `memodi_relate(..., "SUPERSEDES")` after `memodi_save`

### CONTRACTS.md

- **Mode**: synthesized — one obs per contract/queue is already a concept-level grouping
- **Granularity**: one observation per contract/queue
- `type`: `architecture`
- `occurred_at`: file mtime
- `topic_key`: `contracts/<queue-name>`
- **Also** create graph edges: for each `### queue_name` block with `Publisher:` and `Consumer:`, call `memodi_relate` with `PUBLISHES_TO` / `CONSUMES_FROM`

### ARCHITECTURE.md

- **Mode**: synthesized
- **Granularity**: by major section
- `type`: `architecture`
- `occurred_at`: file mtime
- `topic_key`: `architecture/<section-slug>`

### STATE.md (rolling — current state only)

- **Mode**: synthesized
- **Granularity**: the current "Current Status" section as ONE observation
- `type`: `architecture`
- `occurred_at`: file mtime
- `topic_key`: `state/current`
- "Recent Changes" subsection: one observation per dated entry, `type=discovery`, `occurred_at` from the entry date

### ROADMAP.md (rolling)

- **Mode**: synthesized
- **Granularity**: committed milestones only (ignore past/done ones)
- `type`: `decision` for committed items, `pattern` for long-term intent
- `occurred_at`: file mtime
- `topic_key`: `roadmap/<milestone-slug>`

### CLAUDE.md

- **Mode**: synthesized — never one obs per bullet; group rules by theme (auth, style, workflow, etc.)
- **Granularity**: by rule or convention (usually bullet or section)
- `type`: `pattern` for conventions, `preference` for user preferences, `config` for tool setup
- `occurred_at`: file mtime
- `topic_key`: `conventions/<rule-slug>`

### README.md

- **Mode**: synthesized — never one obs per heading; group by knowledge concept
- **Granularity**: by section (Setup, Stack, Architecture, Usage)
- `type`: `architecture` for design; `config` for setup
- `occurred_at`: file mtime
- `topic_key`: `readme/<section-slug>`

### MILESTONES.md

- **Mode**: structured
- **Granularity**: one observation per milestone
- `type`: `decision`
- `occurred_at`: milestone date if present, else file mtime
- `topic_key`: `milestones/<milestone-slug>`

### Generic `.md` (fallback)

- **Mode**: synthesized
- Best-effort semantic extraction, one observation per major section
- `type`: `discovery`
- `occurred_at`: file mtime
- Flag in the final report as "generic extraction — review recommended"

## occurred_at RESOLUTION (in order)

1. Date in the entry header (`## 2024-10-15 — Title`) → `2024-10-15T00:00:00Z`
2. Partial date (`## 2026-03 — Title`, month only) → first of month; flag in report
3. Date in the filename (`2024-10-15-note.md`) → use it
4. YAML frontmatter field — check for `date:`, `updated:`, or `last_modified:` at top of file; use first match found
5. File mtime from the filesystem → use it
6. Last git commit that touched the file (requires git repo) — run via Bash: `git log --follow -1 --format="%aI" -- <file_path>`; prefer over mtime when available (mtime changes on clone/checkout; commit date reflects real authorship)
7. Per-entry git blame (requires git repo, structured mode only) — when a structured entry has no header date, run `git blame -L <first_line>,<first_line> --porcelain <file_path>` and extract `author-time`; expensive, use only when steps 1–6 produced nothing for that entry
8. Nothing available → omit (memodi defaults to `now()`); flag in report

**Note on steps 6–7**: call both via the Bash tool. Step 6 runs once per file; step 7 per entry when header date is absent. Both require a git repo — skip silently if not in one.

## DEDUPLICATION — trust the server

`memodi_save` handles dedup internally:
- **`content_hash` check**: identical content → `duplicate_count` increments, nothing new is created
- **`topic_key` upsert**: same `topic_key` with different content → upserts (new revision), preserves history

Therefore the skill must NOT call `memodi_search_similar` pre-save. It's redundant, slow, and adds friction for zero dedup gain. Just `memodi_save` everything and read the `duplicate_count` in the response.

## SAFETY RULES (MANDATORY)

- **Workspace onboarding is the only mandatory confirmation** — it's a permanent side-effect
- After onboarding confirmed (or path already registered) and the Phase 2 briefing accepted, proceed silently through Phase 3
- **Never** skip the final report — the user needs to see the outcome
- If `memodi_ping` fails → stop and report, do not proceed
- If more than 30% of a file's content is unclear or contradictory → note in warnings but keep processing (don't stop the whole import for one bad file)
- If the user interrupts, re-running is safe — `content_hash` dedup skips what was already saved

## POST-IMPORT

Suggest (don't auto-run):
- `memodi_context <project>` — verify recent observations
- `memodi_search_hybrid "<tema>"` — test a query
- Review the warnings list in the final report
