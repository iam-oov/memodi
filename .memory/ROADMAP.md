# Roadmap

## Phase 0 — Foundation (DONE)
> MCP server skeleton, PostgreSQL connection, project structure

- [x] Docker Compose with PostgreSQL + pgvector + AGE
- [x] Python project structure (pyproject.toml, src layout)
- [x] FastMCP server skeleton
- [x] Database connection and health check
- [x] Basic MCP tools: `ping`, `status`
- [x] First test: server starts and connects to DB

## Phase 1 — Document Store (DONE)
> JSONB persistence, save/search/context tools, full-text search

- [x] Database schema: projects, observations, sessions, workspaces
- [x] MCP tools: save, search, context, list_projects, search_global
- [x] Full-text search with PostgreSQL tsvector ('simple' language)
- [x] Workspace scoping with agent-driven onboarding
- [x] Tests for all tools

## Phase 2 — Workflow Engine (DONE)
> Plan/Apply/Verify/Unify state machine with human gates

- [x] State machine: Plan → Apply → Verify → Unify
- [x] MCP tools: plan, update_plan, approve_plan, apply_done, verify, unify, progress, task_update
- [x] Phase transition validation (can't skip steps)
- [x] Acceptance criteria tracking
- [x] Transition history logging

## Phase 2.5 — Quality + Architecture (DONE)
> HTTP transport, plugin structure, error handling, security

- [x] HTTP/streamable-http transport (remote-ready)
- [x] Claude Code plugin (marketplace.json, skill, .mcp.json)
- [x] Config via system env vars (no hardcoded credentials)
- [x] Error handling decorator on all tools
- [x] Reconnection logic on stale connections
- [x] ensure_schema() caching
- [x] FTS 'simple' language (multi-language)
- [x] Migrations in package (src/memodi/migrations/)
- [x] .dockerignore

## Phase 3 — Vector Search (DONE)
> pgvector + embeddings for semantic similarity

- [x] pgvector schema (vector(384)) + HNSW index (cosine)
- [x] Embedding model: paraphrase-multilingual-MiniLM-L12-v2 (ES+EN)
- [x] Auto-embed on save (title + content)
- [x] MCP tool: `search_similar` (semantic only)
- [x] MCP tool: `search_hybrid` (RRF keyword + semantic)
- [x] MCP tool: `backfill` (embed old observations)
- [x] Lazy model loading (first search, not startup)
- [x] Docker image with model pre-downloaded
- [x] SKILL updated with search strategy guide

## Phase 4 — Knowledge Graph
> Apache AGE for relationship traversal and impact analysis

- [ ] AGE graph schema: repos, modules, teams, tasks
- [ ] MCP tools: `relate`, `dependencies`, `impact_analysis`
- [ ] Relationship types: depends_on, affects, owned_by, part_of
- [ ] Traversal queries: "what breaks if I change X?"
- [ ] Cross-workspace relationship visibility

## Phase 5 — Production Deployment
> Deploy to Hetzner, production-ready

- [ ] Deploy memodi-server + PostgreSQL a Hetzner
- [ ] Update plugin URL a produccion
- [ ] Auth/API keys para acceso seguro
- [ ] Backup strategy para PostgreSQL
- [ ] Monitoreo y health checks
