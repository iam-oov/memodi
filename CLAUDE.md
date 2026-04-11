# memodi — Memoria Distribuida

## What is this?

MCP server (Python) that gives Claude Code persistent, distributed memory across workspaces and projects. Claude is the brain — memodi is the persistence layer.

## Architecture

```
Claude Code ──HTTP──► memodi-server (Docker, port 8787) ──► PostgreSQL
```

### Storage layers (PostgreSQL)

| Layer | Extension | Purpose | Status |
|-------|-----------|---------|--------|
| Document Store | JSONB | State, tasks, decisions, metadata | Done |
| Full-text Search | tsvector (simple) | Keyword search multi-language | Done |
| Workflow Engine | JSONB | Plan/Apply/Verify/Unify cycle | Done |
| Workspace Scoping | FK relations | Multi-workspace isolation | Done |
| Vector Search | pgvector | Semantic similarity (cosine, HNSW, 384d) | Done |
| Knowledge Graph | Apache AGE | Relationships: repos, modules, teams | Phase 4 |

## Tech Stack

- **Language**: Python 3.12+
- **MCP SDK**: FastMCP (streamable-http transport)
- **Database**: PostgreSQL 16+ (pgvector + Apache AGE)
- **Embeddings**: paraphrase-multilingual-MiniLM-L12-v2 (384d, ES+EN)
- **Infra**: Docker Compose (DB + server)
- **Config**: System env vars with MEMODI_ prefix

## Plugin Structure

```
plugin/claude-code/
├── .claude-plugin/plugin.json  — plugin metadata
├── .mcp.json                   — connects to http://localhost:8787/mcp
└── skills/memory/SKILL.md      — proactive memory instructions
```

The skill tells Claude WHEN and WHY to use memory. The MCP server handles HOW.

## Conventions

- Screaming architecture: folder names describe WHAT, not HOW
- No frameworks for DI — composition root pattern
- Tests required for every tool exposed via MCP
- Conventional commits (no AI attribution)
- Migrations in src/memodi/migrations/ (package-relative)

## Rules

- Never expose database internals through MCP tools — Claude sees domain concepts, not SQL
- Every MCP tool must have a clear, single responsibility
- PostgreSQL is the ONLY persistence — no local files for shared state
- Credentials come from system env vars only — never hardcode, never commit
- Docker Compose for local dev, Hetzner for production (future)
