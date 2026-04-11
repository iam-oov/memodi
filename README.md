# memodi

**Memoria Distribuida** — Persistent, distributed memory for AI coding agents.

## What is memodi?

An MCP server that gives Claude Code (and any MCP-compatible agent) persistent memory distributed across workspaces and projects. Think of it as `git pull` for context — switch projects and pick up exactly where things left off.

## Why?

AI agents forget everything between sessions. Existing solutions are either:
- **Local-only** (SQLite) — can't share across teams
- **Too heavy** (full knowledge graph infra) — overkill for small teams
- **No relationships** — can't answer "what breaks if I change this?"

memodi combines three capabilities in one PostgreSQL instance:
- **Document store** (JSONB) — tasks, state, decisions, metadata
- **Semantic search** (pgvector) — "have we solved something similar?"
- **Knowledge graph** (Apache AGE) — repo dependencies, module relationships, impact analysis

## Quick Start

```bash
# Start the database
docker compose up -d

# Install memodi
pip install -e .

# Connect from Claude Code via .mcp.json
```

## Architecture

```
Claude Code ──MCP──► memodi (Python) ──► PostgreSQL
  (brain)           (persistence)       (JSONB + pgvector + AGE)
```

Claude decides what's worth remembering. memodi persists and retrieves. No extra LLM calls — Claude is already there.

## License

TBD
