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

## Phase 4 — Knowledge Graph (DONE)
> Apache AGE for relationship traversal and impact analysis

- [x] AGE connection helper (LOAD, search_path, agtype parsing)
- [x] Graph schema: Repo, Module nodes + DEPENDS_ON, CONTAINS, AFFECTS edges
- [x] MCP tools: relate, dependencies, impact, graph_overview, remove_relation
- [x] Transitive impact analysis (variable-length Cypher paths)
- [x] Edge upsert (DELETE+CREATE pattern)
- [x] SKILL updated with proactive relationship creation
- [x] Documented AGE limitations (no union in paths, no params, LOAD per conn)

## Phase 5 — Production Deployment (DONE)
> Deploy to Hetzner, production-ready

- [x] Hetzner CX23 (Ubuntu 24, 2 vCPU, 4GB RAM)
- [x] PostgreSQL 16 nativo con pgvector + AGE (SSD directo)
- [x] memodi-server + Caddy en Docker (stateless)
- [x] HTTPS via Caddy + Let's Encrypt (auto-renewal)
- [x] API key auth en Caddy (X-Api-Key header)
- [x] Backup script (pg_dump diario, 7 dias retencion)
- [x] Restore script documentado
- [x] Setup script para Ubuntu 24
- [x] Domain temporal: 62-238-15-94.sslip.io

## Future — Nice to have

- [ ] Dominio propio (reemplazar sslip.io)
- [ ] Optimizar imagen Docker (ONNX en vez de PyTorch: 5.88GB → ~1.5GB)
- [ ] CI/CD automatico (GitHub Actions → deploy)
- [ ] Monitoreo (health check endpoint + alertas)
- [ ] Rate limiting en Caddy
- [ ] Multi-user con API keys por usuario
