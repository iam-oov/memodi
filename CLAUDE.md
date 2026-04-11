# memodi — Memoria Distribuida

## What is this?

MCP server (Python) that gives Claude Code persistent, distributed memory across workspaces and projects. Claude is the brain — memodi is the persistence layer.

## Architecture

```
Claude Code (brain) → MCP (stdio) → memodi (Python) → PostgreSQL
```

### Storage layers (PostgreSQL)

| Layer | Extension | Purpose |
|-------|-----------|---------|
| Document Store | JSONB | State, tasks, decisions, metadata |
| Vector Search | pgvector | Semantic similarity (cosine distance) |
| Knowledge Graph | Apache AGE | Relationships: repos, modules, teams |

## Tech Stack

- **Language**: Python 3.12+
- **MCP SDK**: FastMCP
- **Database**: PostgreSQL 16+ (pgvector + Apache AGE)
- **Embeddings**: sentence-transformers (local, Phase 3)
- **Infra**: Docker Compose (local dev)

## Conventions

- Screaming architecture: folder names describe WHAT, not HOW
- No frameworks for DI — composition root pattern
- Tests required for every tool exposed via MCP
- Conventional commits (no AI attribution)

## Rules

- Never expose database internals through MCP tools — Claude sees domain concepts, not SQL
- Every MCP tool must have a clear, single responsibility
- PostgreSQL is the ONLY persistence — no local files for shared state
- Docker Compose for local dev, managed PostgreSQL for production (future)
