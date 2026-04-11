# State

## Current Position

- **Phase**: 4 — Knowledge Graph (COMPLETE)
- **Status**: Ready for Phase 5 (Production deployment)
- **Last Activity**: 2026-04-11

## Completed

- Phase 0: MCP server skeleton, Docker Compose, PostgreSQL + pgvector + AGE
- Phase 1: Document store (JSONB), FTS (tsvector), save/search/context/list_projects
- Phase 2: Workflow engine (Plan/Apply/Verify/Unify), state machine, transition validation
- Phase 2.5: Quality pass — HTTP transport, plugin structure, error handling, security
- Phase 3: Vector search — pgvector HNSW, sentence-transformers multilingual, RRF hybrid
- Phase 4: Knowledge graph — Apache AGE, Cypher, dependency tracking, impact analysis

## Active Work

- Next: Phase 5 (Production deployment to Hetzner or similar)

## Decisions Made

- Language: Python 3.12+
- MCP SDK: FastMCP with streamable-http transport
- Database: PostgreSQL 16 (pgvector + Apache AGE) via Docker Compose
- Transport: HTTP on port 8787 (same for local and remote)
- Plugin: skill (SKILL.md) + MCP (.mcp.json) separated
- Config: system env vars with MEMODI_ prefix
- FTS: 'simple' language for multi-language
- Workspace: agent-driven onboarding
- Credentials: never hardcoded — system env vars or Docker env
- Embeddings: paraphrase-multilingual-MiniLM-L12-v2 (384 dims, ES+EN)
- Vector index: HNSW with cosine distance
- Hybrid search: RRF (Reciprocal Rank Fusion) with k=60
- Embedding generation: lazy loading, at save time
- Docker image: 5.88GB with PyTorch (optimize to ~1.5GB with ONNX in Phase 5)
- Graph: Apache AGE with Cypher, graph name 'memodi'
- Graph creation in Python (not SQL migration) due to LOAD 'age' requirement
- AGE limitation: no union types in variable-length paths (|)
- AGE limitation: no parameterized Cypher queries
- Workspace/Team NOT in graph — already in relational tables
- Edge upsert via DELETE+CREATE (AGE MERGE for edges is limited)
