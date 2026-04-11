# memodi

**Memoria Distribuida** — Memoria persistente y distribuida para agentes de IA.

## Que es memodi?

Un servidor MCP que le da a Claude Code (y a cualquier agente compatible con MCP) memoria persistente distribuida entre workspaces y proyectos. Pensalo como `git pull` para contexto — cambias de proyecto y retomas exactamente donde quedaste.

## Por que?

Los agentes de IA olvidan todo entre sesiones. Las soluciones existentes son:
- **Solo locales** (SQLite) — no se pueden compartir entre equipos
- **Demasiado pesadas** (infra completa de knowledge graph) — overkill para equipos chicos
- **Sin relaciones** — no pueden responder "que se rompe si cambio esto?"

memodi combina tres capacidades en una sola instancia de PostgreSQL:
- **Document store** (JSONB) — tareas, estado, decisiones, metadata
- **Busqueda semantica** (pgvector) — "ya resolvimos algo parecido?"
- **Grafo de conocimiento** (Apache AGE) — dependencias entre repos, relaciones entre modulos, analisis de impacto

## Arquitectura

```
Claude Code ──HTTP──► memodi-server (Docker) ──► PostgreSQL
  (cerebro)          (puerto 8787)               (JSONB + pgvector + AGE)
```

Claude decide que vale la pena recordar. memodi persiste y consulta. Sin llamadas extra a LLMs — Claude ya esta ahi.

El agente usa memodi de forma PROACTIVA — guarda decisiones, bugs y descubrimientos automaticamente sin que el usuario lo pida. Las instrucciones viajan con el skill del plugin.

## Quick Start

### 1. Configurar variables de entorno

Agregar a `~/.zshrc` o `~/.bashrc`:

```bash
export MEMODI_DB_HOST=localhost
export MEMODI_DB_USER=memodi
export MEMODI_DB_PASSWORD=memodi_dev
export MEMODI_DB_NAME=memodi
```

### 2. Levantar los servicios

```bash
docker compose up -d
```

Esto levanta:
- **memodi-db** — PostgreSQL 16 con pgvector y Apache AGE
- **memodi-server** — servidor MCP HTTP en puerto 8787

### 3. Instalar el plugin en Claude Code

En `~/.claude/settings.json`:

```json
{
  "enabledPlugins": {
    "memodi@memodi": true
  },
  "extraKnownMarketplaces": {
    "memodi": {
      "source": {
        "source": "github",
        "repo": "iam-oov/memodi"
      }
    }
  }
}
```

### 4. Listo

Abrí Claude Code en cualquier proyecto. El agente va a:
1. Detectar que es un proyecto nuevo
2. Preguntarte a que workspace queres linkarlo
3. Empezar a guardar decisiones automaticamente

## Tools MCP disponibles

### Memoria (proactivo — el agente los usa sin que le pidas)
| Tool | Descripcion |
|------|-------------|
| `memodi_save` | Guardar observacion (auto-genera embedding semantico) |
| `memodi_search` | Buscar por keywords exactos |
| `memodi_search_similar` | Buscar por significado (semantica) |
| `memodi_search_hybrid` | Mejor de ambos: keyword + semantica con RRF |
| `memodi_context` | Cargar contexto reciente de un proyecto |
| `memodi_search_global` | Buscar keywords en TODOS los workspaces |
| `memodi_backfill` | Generar embeddings para observaciones viejas |
| `memodi_list_projects` | Listar proyectos conocidos |

### Grafo de conocimiento (proactivo — el agente crea relaciones al descubrirlas)
| Tool | Descripcion |
|------|-------------|
| `memodi_relate` | Crear relacion (ej: repo-a DEPENDS_ON repo-b) |
| `memodi_dependencies` | Que depende de que |
| `memodi_impact` | Analisis de impacto transitivo: "que se rompe si cambio X?" |
| `memodi_graph_overview` | Resumen de todos los nodos y relaciones |
| `memodi_remove_relation` | Eliminar una relacion |

### Workspaces (el agente pregunta al usuario)
| Tool | Descripcion |
|------|-------------|
| `memodi_check_workspace` | Verificar si un proyecto tiene workspace |
| `memodi_link_project` | Linkar proyecto a un workspace |
| `memodi_list_workspaces` | Listar workspaces disponibles |

### Workflow (solo cuando el usuario pide planificacion)
| Tool | Descripcion |
|------|-------------|
| `memodi_plan` | Crear plan de trabajo |
| `memodi_update_plan` | Definir criterios y tareas |
| `memodi_approve_plan` | Aprobar plan, pasar a apply |
| `memodi_apply_done` | Marcar apply como hecho |
| `memodi_verify` | Verificar resultado |
| `memodi_unify` | Cerrar el loop |
| `memodi_progress` | Ver estado del workflow activo |
| `memodi_task_update` | Actualizar estado de una tarea |

## Modelo del grafo

```
Repo ──DEPENDS_ON──► Repo
Repo ──CONTAINS────► Module
Module ──AFFECTS───► Module
```

| Nodo | Propiedades | Ejemplo |
|------|-------------|---------|
| Repo | name, language, description | repo-a, Python |
| Module | name, description | auth, database |

| Relacion | De → A | Ejemplo |
|----------|--------|---------|
| DEPENDS_ON | Repo → Repo | repo-c depende de repo-a |
| CONTAINS | Repo → Module | repo-a contiene auth |
| AFFECTS | Module → Module | auth afecta a api |

### Limitaciones conocidas de Apache AGE

- **Sin union de tipos en paths variables**: `[:DEPENDS_ON|AFFECTS*1..5]` no funciona. AGE no soporta el operador `|` en variable-length patterns. El impact analysis usa un solo tipo de relacion por query.
- **Sin parametros Cypher**: AGE no soporta `$1`, `$2` en Cypher. Los valores se interpolan directamente en el query string.
- **LOAD requerido por conexion**: Cada conexion necesita `LOAD 'age'` y `SET search_path` antes de cualquier operacion de grafo.
- **agtype**: AGE devuelve un tipo custom `agtype` que necesita casteo a JSON/text para Python.

## Desarrollo

```bash
# Instalar dependencias
uv sync

# Correr tests (necesita env vars y DB corriendo)
uv run pytest -v

# Lint
uv run ruff check src/ tests/
```

## Licencia

MIT
