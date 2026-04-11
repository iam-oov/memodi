# State

## Current Position

- **Phase**: 2.5 — Quality pass + HTTP transport (COMPLETE)
- **Status**: Ready for Phase 3 (Vector Search) or deployment to Hetzner
- **Last Activity**: 2026-04-10

## Completed

- Phase 0: MCP server skeleton, Docker Compose, PostgreSQL + pgvector + AGE
- Phase 1: Document store (JSONB), FTS (tsvector), save/search/context/list_projects
- Phase 2: Workflow engine (Plan/Apply/Verify/Unify), state machine, transition validation
- Phase 2.5: Quality pass + architecture changes:
  - HTTP/streamable-http transport (remote-ready)
  - Claude Code plugin structure (marketplace.json, skill, .mcp.json)
  - Workspace scoping with agent-driven onboarding
  - Config via system env vars only (no .env files)
  - FTS changed to 'simple' (multi-language)
  - ensure_schema() cached after first run
  - Error handling decorator on all tools
  - Reconnection logic on stale DB connections
  - Workspace warning on save for unlinked projects
  - Migrations moved to package (src/memodi/migrations/)
  - .dockerignore for DB image

## Active Work

- Next: Phase 3 (Vector Search) OR deploy to Hetzner

## Decisions Made

- Language: Python 3.12+
- MCP SDK: FastMCP with streamable-http transport
- Database: PostgreSQL 16 (pgvector + Apache AGE) via Docker Compose
- Transport: HTTP on port 8787 (same for local and remote)
- Plugin: skill (SKILL.md) + MCP (.mcp.json) separated — skill handles behavior, MCP handles tools
- Config: system env vars with MEMODI_ prefix, no .env files
- FTS: 'simple' language for multi-language support
- Workspace: agent-driven onboarding, not env var based
- Credentials: never hardcoded, never committed — system env vars or Docker env
- Docker Compose runs both DB and server — user only needs Docker
- Plugin connects via http://localhost:8787/mcp (future: Hetzner URL)
