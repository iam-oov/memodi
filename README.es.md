# memodi

[![CI](https://github.com/iam-oov/memodi/actions/workflows/ci.yml/badge.svg)](https://github.com/iam-oov/memodi/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/iam-oov/memodi)](https://github.com/iam-oov/memodi/releases)
[![Python](https://img.shields.io/badge/python-3.12+-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/github/license/iam-oov/memodi)](LICENSE)

[English](README.md) | Español

**Memoria Distribuida** — servidor MCP que le da a Claude Code memoria persistente entre workspaces, proyectos y maquinas. Guarda decisiones, bugs y descubrimientos de forma proactiva y los recupera por keyword, semantica o grafo — sin llamadas extra a LLMs.

Una sola instancia de PostgreSQL hace todo: document store (JSONB), busqueda semantica (pgvector) y grafo de conocimiento (Apache AGE).

## Features

- **Memoria proactiva** — el agente guarda observaciones sin que se lo pidas; las instrucciones viajan con el skill del plugin
- **Busqueda hibrida** — keyword + semantica combinadas con RRF, ademas de busqueda global entre todos tus proyectos
- **Grafo de conocimiento** — dependencias entre repos y analisis de impacto transitivo ("que se rompe si cambio X?")
- **Auto-linking** — escribir `[[topic-key]]` en una observacion crea la relacion `LINKS_TO` en el grafo
- **Multi-maquina** — una key por usuario; registrar el mismo workspace en dos maquinas comparte las memorias
- **Contexto automatico** — hooks de sesion cargan la memoria al abrir el repo e inyectan punteros relevantes en cada prompt
- **Inerte por defecto** — un path no registrado devuelve `not_started`; nunca se auto-crean proyectos ni workspaces

## Quick start

Necesitas [Claude Code](https://docs.anthropic.com/en/docs/claude-code) y una API key de memodi (una por usuario). Registrate en `https://memodi.valdoh.com/signup` (o la URL de tu instancia) y copia la key `mmd_...` apenas la veas — se muestra una sola vez.

### Instalar

```bash
export MEMODI_API_KEY="mmd_..."
curl -sf https://raw.githubusercontent.com/iam-oov/memodi/main/install.sh | sh
```

<details>
<summary>Instalacion manual</summary>

```bash
# 1. API key en el shell profile (~/.zshrc o ~/.bashrc)
export MEMODI_API_KEY="mmd_..."

# 2. Marketplace + plugin (hooks de sesion + skills)
claude plugin marketplace add iam-oov/memodi
claude plugin install memodi@memodi

# 3. Conexion al server
claude mcp add --transport http \
  -H "X-Memodi-Api-Key: $MEMODI_API_KEY" \
  -H "X-Memodi-Machine: $(hostname)" \
  --scope user \
  memodi https://memodi.valdoh.com/mcp
```

Agregar `"mcp__memodi__*"` a `permissions.allow` en `~/.claude/settings.json` evita aprobar tool por tool.

</details>

Reinicia Claude Code y corre `/memodi:start`: registra el workspace en esta maquina (o engancha uno existente de otra — mismo nombre = memorias compartidas) y carga su memoria. Una vez por (maquina, carpeta); despues la memoria se carga sola y en silencio al abrir el repo.

`/memodi:end` cierra la sesion con un resumen estructurado (Goal / Accomplished / Next Steps). Un hook `SessionEnd` corre igual en cada salida como red de contencion — nunca pisa un resumen real.

### Actualizar

El instalador es idempotente — volver a correrlo trae la ultima version del plugin:

```bash
curl -sf https://raw.githubusercontent.com/iam-oov/memodi/main/install.sh | sh
```

O directo:

```bash
claude plugin marketplace update memodi
claude plugin update memodi@memodi
```

### Desinstalar

```bash
curl -sf https://raw.githubusercontent.com/iam-oov/memodi/main/uninstall.sh | sh
```

## Arquitectura

```
Claude Code ──HTTPS──► Cloudflare Tunnel ──► memodi-server (uv + systemd) ──► PostgreSQL
                       memodi.valdoh.com      Raspberry Pi                     pgvector + AGE
```

Claude decide que vale la pena recordar; memodi persiste y consulta.

| Capa | Extension | Para que |
|------|-----------|----------|
| Document store | JSONB | Estado, tareas, decisiones, metadata |
| Busqueda full-text | tsvector | Keywords multi-idioma |
| Busqueda semantica | pgvector (HNSW, 384d) | "ya resolvimos algo parecido?" |
| Grafo de conocimiento | Apache AGE (Cypher) | Dependencias, impacto |

## Autenticacion

Cuentas reales por usuario, no una key compartida:

- Alta en `/signup` (unica ruta sin key); la api key `mmd_...` se muestra UNA sola vez — el server guarda solo su hash
- `X-Memodi-Api-Key` identifica al usuario y es el unico control de acceso frente a `/mcp` y `/hooks/*`
- `X-Memodi-Machine` identifica la maquina; los paths se registran por (usuario, maquina, path) — la misma carpeta puede resolver a workspaces distintos en maquinas distintas
- `path` (el cwd del caller) es parametro explicito en cada tool de proyecto
- Path no registrado → `{"type": "not_started"}`; key ausente o invalida → `{"type": "not_authenticated"}`

## Tools MCP (37)

Todas las tools de proyecto reciben `path` (el cwd del caller) y lo resuelven contra un workspace registrado.

### Memoria
| Tool | Descripcion |
|------|-------------|
| `memodi_save` | Guardar observacion (auto-genera embedding) |
| `memodi_search` | Busqueda por keywords |
| `memodi_search_similar` | Busqueda semantica |
| `memodi_search_hybrid` | Keyword + semantica con RRF |
| `memodi_context` | Contexto reciente de un proyecto |
| `memodi_search_global` | Buscar en todos tus proyectos (scoped al usuario) |
| `memodi_backfill` | Embeddings para observaciones viejas |
| `memodi_backfill_links` | Reconciliar LINKS_TO previos al auto-linking (idempotente) |
| `memodi_find_consolidation_clusters` | Detectar clusters de observaciones listas para consolidar (solo lectura) |
| `memodi_list_projects` | Proyectos conocidos y su workspace |
| `memodi_delete` | Soft-delete de una observacion |
| `memodi_get_observation` | Leer observacion por id, incluidas superseded |

### Grafo de conocimiento
| Tool | Descripcion |
|------|-------------|
| `memodi_relate` | Crear relacion (ej: repo-a DEPENDS_ON repo-b) |
| `memodi_dependencies` | Que depende de que; con `path` incluye LINKS_TO del workspace |
| `memodi_impact` | Impacto transitivo; con `path` recorre tambien LINKS_TO |
| `memodi_graph_overview` | Resumen de nodos y relaciones |
| `memodi_remove_relation` | Invalidar relacion (soft delete) |
| `memodi_delete_relation` | Eliminar relacion (hard delete) |

### Workspaces
| Tool | Descripcion |
|------|-------------|
| `memodi_workspace_start` | Registrar carpeta como workspace (lo dispara `/memodi:start`) |
| `memodi_list_workspaces` | Listar workspaces |
| `memodi_merge_projects` | Fusionar proyectos duplicados (dry_run por defecto) |
| `memodi_delete_workspace` | Eliminar workspace |
| `memodi_rename_workspace` | Renombrar workspace |
| `memodi_purge_workspace` | Vaciar workspace (destructivo, dry_run por defecto) |

### Workflow
| Tool | Descripcion |
|------|-------------|
| `memodi_plan` | Crear plan |
| `memodi_update_plan` | Definir criterios y tareas |
| `memodi_approve_plan` | Aprobar plan, pasar a apply |
| `memodi_apply_done` | Marcar apply hecho |
| `memodi_verify` | Verificar resultado |
| `memodi_unify` | Cerrar el loop |
| `memodi_progress` | Estado del workflow activo |
| `memodi_task_update` | Actualizar una tarea |

### Sesiones y sistema
| Tool | Descripcion |
|------|-------------|
| `memodi_session_start` | Iniciar sesion (las observaciones se auto-adjuntan) |
| `memodi_session_end` | Cerrar sesion con resumen estructurado (obligatorio) |
| `memodi_ping` | Server vivo |
| `memodi_status` | Salud del server y extensiones de PostgreSQL |
| `memodi_version` | Version en produccion |

## Modelo del grafo

```
Repo ──DEPENDS_ON──► Repo
Repo ──CONTAINS────► Module
Module ──AFFECTS───► Module
Topic ──LINKS_TO───► Topic
```

`LINKS_TO` se auto-crea al escribir `[[topic-key]]` en el contenido de un `memodi_save` con `topic_key` propio. `Topic` es el unico nodo scoped por workspace (identidad = name + workspace_id); `Repo` y `Module` son globales y se crean solo via `memodi_relate`.

Limitaciones de Apache AGE:

- Sin union de tipos en paths variables (`[:A|B*1..5]`)
- Sin parametros Cypher — los valores se interpolan
- Cada conexion necesita `LOAD 'age'` + `SET search_path`

## Desarrollo local

```bash
docker compose pull db        # imagen pre-buildeada (pgvector + AGE); sin pull, compila desde source
docker compose up -d
export MEMODI_DB_USER=memodi MEMODI_DB_PASSWORD=memodi_dev
uv sync
uv run pytest -v
uv run ruff check src/ tests/
```

PR a `main` → `ci.yml` corre lint + tests (437) → si se mergea, `deploy.yml` deploya solo.

## Produccion

Corre nativo en una Raspberry Pi (PostgreSQL + pgvector + AGE, uv + systemd) detras de un Cloudflare Tunnel, con deploy push-based via GitHub Actions. Setup completo y operaciones dia 2: [`docs/pi-setup.md`](docs/pi-setup.md).

## Licencia

MIT
