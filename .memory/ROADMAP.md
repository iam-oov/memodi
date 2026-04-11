# Roadmap

## Phase 0 — Foundation
> MCP server skeleton, PostgreSQL connection, project structure

- [ ] Docker Compose with PostgreSQL + pgvector + AGE
- [ ] Python project structure (pyproject.toml, src layout)
- [ ] FastMCP server skeleton
- [ ] Database connection and health check
- [ ] Basic MCP tools: `ping`, `status`
- [ ] First test: server starts and connects to DB

## Phase 1 — Document Store
> JSONB persistence, save/search/context tools, full-text search

- [ ] Database schema: projects, teams, observations, sessions
- [ ] MCP tools: `save`, `search`, `context`, `context`, `list_projects`
- [ ] Full-text search with PostgreSQL tsvector
- [ ] Multi-team isolation (team_id on all queries)
- [ ] Tests for all tools

## Phase 2 — Workflow Engine
> Plan/Apply/Verify/Unify state machine with human gates

- [ ] State machine: Plan → Apply → Verify → Unify
- [ ] MCP tools: `plan`, `apply`, `verify`, `unify`, `progress`
- [ ] Phase transition validation (can't skip steps)
- [ ] Acceptance criteria tracking (BDD-style)
- [ ] Phase history and deviation logging

## Phase 3 — Vector Search
> pgvector + embeddings for semantic similarity

- [ ] pgvector schema and indexes
- [ ] Embedding generation (sentence-transformers)
- [ ] MCP tool: `search_similar`
- [ ] Auto-embed on save (decisions, code deltas)
- [ ] Cosine similarity queries

## Phase 4 — Knowledge Graph
> Apache AGE for relationship traversal and impact analysis

- [ ] AGE graph schema: repos, modules, teams, tasks
- [ ] MCP tools: `relate`, `dependencies`, `impact_analysis`
- [ ] Relationship types: depends_on, affects, owned_by, part_of
- [ ] Traversal queries: "what breaks if I change X?"
- [ ] Cross-team relationship visibility
