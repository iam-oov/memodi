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
- Phase 5: Production deployment — Hetzner CX23, Caddy HTTPS, API key auth, backups

## Production

- Server: Hetzner CX23 (2 vCPU, 4GB RAM, Ubuntu 24)
- URL: https://62-238-15-94.sslip.io/mcp
- PostgreSQL: nativo en server (SSD directo)
- memodi-server + Caddy: Docker containers
- Auth: X-Api-Key header, validado en Caddy
- HTTPS: Let's Encrypt via Caddy (auto-renewal)
- Backups: pg_dump cron diario, 7 dias retencion

## Decisions Made

- Language: Python 3.12+
- MCP SDK: FastMCP with streamable-http transport
- Database: PostgreSQL 16 nativo (pgvector + Apache AGE)
- Transport: HTTPS via Caddy → memodi-server:8787
- Auth: API key en header X-Api-Key, validado en Caddy
- Domain: sslip.io (temporal, reemplazar por dominio propio)
- Production: PostgreSQL nativo, solo server + Caddy en Docker
- Backups: pg_dump diario con 7 dias de retencion
- Plugin .mcp.json: localhost para dev, .mcp.json por proyecto para produccion
- Embeddings: paraphrase-multilingual-MiniLM-L12-v2 (384 dims, ES+EN)
- Vector index: HNSW with cosine distance
- Graph: Apache AGE with Cypher
