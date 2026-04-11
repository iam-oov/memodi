# State

## Current Position

- **Phase**: 2 — Workflow Engine (COMPLETE)
- **Status**: Ready for Phase 3
- **Last Activity**: 2026-04-10

## Completed

- Phase 0: MCP server skeleton, Docker Compose, PostgreSQL + pgvector + AGE
- Phase 1: Document store (JSONB), FTS (tsvector), save/search/context/list_projects tools
- Phase 2: Workflow engine (Plan/Apply/Verify/Unify), 8 MCP tools, transition validation

## Active Work

- Next: Phase 3 — Vector Search (pgvector + embeddings for semantic similarity)

## Decisions Made

- Language: Python 3.12+
- MCP SDK: FastMCP
- Database: PostgreSQL 16 (pgvector + Apache AGE) via Docker Compose
- Storage strategy: single PostgreSQL instance with three layers (JSONB, pgvector, AGE)
- No cloud required for dev — Docker Compose local
- Embeddings: sentence-transformers local (Phase 3, not now)
- Architecture: screaming architecture, composition root, no DI framework
- Temporary state in .memory/ until memodi manages its own persistence
- FTS uses plainto_tsquery (plain text, no special syntax needed)
- Upsert by topic_key for evolving decisions (revision_count tracked)
- Migrations tracked in _migrations table, auto-applied on ensure_schema()
- No team management — project auto-detected, context loaded on demand
- Workflow transitions validated: plan→apply→verify→unify→completed (verify can loop back to apply)
- Task statuses: pending, in_progress, done, blocked
- Acceptance criteria stored as JSONB array of {criterion, met}
