# State

## Current Position

- **Phase**: 5 — Production Deployment (COMPLETE)
- **Status**: All phases complete. memodi is live.
- **Last Activity**: 2026-04-11

## Completed

- Phase 0: MCP server skeleton, Docker Compose, PostgreSQL + pgvector + AGE
- Phase 1: Document store (JSONB), FTS (tsvector), save/search/context/list_projects
- Phase 2: Workflow engine (Plan/Apply/Verify/Unify), state machine, transition validation
- Phase 2.5: Quality pass — HTTP transport, plugin structure, error handling, security
- Phase 3: Vector search — pgvector HNSW, sentence-transformers multilingual, RRF hybrid
- Phase 4: Knowledge graph — Apache AGE, Cypher, dependency tracking, impact analysis
- Phase 5: Production deployment — Raspberry Pi, Cloudflare Tunnel (TLS), per-user API key auth
- Phase 6: Native production infra — uv + systemd (`memodi.service`), PostgreSQL + pgvector + Apache AGE nativos (no containers en produccion; Docker sigue siendo solo para desarrollo local)

## Production

- Server: Raspberry Pi (arm64), todo nativo — uv + systemd (`memodi.service`), sin containers
- URL: https://memodi.valdoh.com/mcp
- PostgreSQL: nativo (PGDG apt repo) con pgvector + Apache AGE compilado desde source — ver docs/pi-setup.md
- Deploy: push-based — GitHub Actions por SSH a traves del Cloudflare Tunnel, `uv sync --reinstall-package memodi` + `systemctl restart memodi`
- Auth: api key por usuario en header X-Memodi-Api-Key (validada en el server)
- HTTPS/TLS: Cloudflare Tunnel (sin abrir puertos, sin Caddy); cloudflared corre como servicio systemd nativo del usuario, fuera del repo
- Backups: deferred

## Decisions Made

- Language: Python 3.12+
- MCP SDK: FastMCP with streamable-http transport
- Database: PostgreSQL 16 nativo (pgvector + Apache AGE)
- Transport: HTTPS via Cloudflare Tunnel → memodi-server:8787
- Auth: api key por usuario en header X-Memodi-Api-Key, validada en el server
- Domain: memodi.valdoh.com (Cloudflare Tunnel, TLS incluido)
- Production: Raspberry Pi, todo nativo (uv + systemd + PostgreSQL nativo) — Docker Compose queda solo para desarrollo local
- Backups: deferred
- Plugin .mcp.json: localhost para dev, .mcp.json por proyecto para produccion
- Embeddings: paraphrase-multilingual-MiniLM-L12-v2 (384 dims, ES+EN)
- Vector index: HNSW with cosine distance
- Graph: Apache AGE with Cypher
