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
Internet ──HTTPS──► Caddy (auth + TLS) ──► memodi-server (uv + systemd) ──► PostgreSQL
                    puerto 443              puerto 8787                       nativo en SSD
```

Claude decide que vale la pena recordar. memodi persiste y consulta. Sin llamadas extra a LLMs — Claude ya esta ahi.

El agente usa memodi de forma PROACTIVA — guarda decisiones, bugs y descubrimientos automaticamente sin que el usuario lo pida. Las instrucciones viajan con el skill del plugin.

## Quick Start

### 1. Instalar el plugin en Claude Code

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

### 2. Conectar a produccion

Crear `.mcp.json` en la raiz del proyecto:

```json
{
  "mcpServers": {
    "memodi": {
      "type": "http",
      "url": "https://tu-server/mcp",
      "headers": {
        "X-Api-Key": "TU_API_KEY"
      }
    }
  }
}
```

### 3. Listo

Abri Claude Code en cualquier proyecto. El agente va a:
1. Detectar que es un proyecto nuevo
2. Listar workspaces existentes y preguntarte a cual linkarlo
3. Empezar a guardar decisiones automaticamente

## Tools MCP (31 tools)

### Sistema
| Tool | Descripcion |
|------|-------------|
| `memodi_ping` | Verificar que el server esta vivo |
| `memodi_status` | Salud del server y extensiones de PostgreSQL |
| `memodi_version` | Version del server en produccion |

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

### Workspaces
| Tool | Descripcion |
|------|-------------|
| `memodi_check_workspace` | Verificar si un proyecto tiene workspace |
| `memodi_link_project` | Linkar proyecto a un workspace |
| `memodi_list_workspaces` | Listar workspaces disponibles |
| `memodi_delete_workspace` | Eliminar un workspace |
| `memodi_rename_workspace` | Renombrar un workspace |

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

- **Sin union de tipos en paths variables**: `[:DEPENDS_ON|AFFECTS*1..5]` no funciona en variable-length patterns
- **Sin parametros Cypher**: los valores se interpolan directamente en el query string
- **LOAD requerido por conexion**: cada conexion necesita `LOAD 'age'` y `SET search_path`

## Produccion

### Stack
- **Server**: Hetzner CX23 (2 vCPU, 4GB RAM, Ubuntu 24)
- **PostgreSQL 16**: nativo en SSD con pgvector + Apache AGE
- **memodi-server**: Python via uv + systemd (puerto 8787)
- **Caddy**: Docker, HTTPS automatico + API key auth
- **Imagen DB**: pre-buildeada en GHCR (`ghcr.io/iam-oov/memodi-db:latest`) con pgvector + Apache AGE

### Pipeline CI/CD

4 workflows de GitHub Actions, cada uno con una sola responsabilidad:

| Workflow | Trigger | Que hace |
|----------|---------|----------|
| `ci.yml` | PR a main, push a main | Lint + tests (38 tests, 0 skippeados) |
| `deploy.yml` | `ci.yml` pasa en main | SSH a Hetzner + `systemctl restart` + health check |
| `release.yml` | Tag `v*` | Changelog desde el tag anterior + GitHub Release |
| `db-image.yml` | Cambios en `Dockerfile.db` | Build + push a GHCR |

El deploy falla con `exit 1` si `systemctl is-active memodi` no responde despues del restart, y vuelca los ultimos 30 logs del servicio. Se acabaron los deploys "verdes" con un server muerto.

### Crear un release

```bash
# Bump version en pyproject.toml, commit, tag, push
git tag v0.4.0
git push origin v0.4.0
```

El workflow genera el changelog automaticamente desde el tag anterior y crea el GitHub Release.

### Deploy manual (si es necesario)

```bash
ssh memodi@tu-server
cd memodi && git pull && ~/.local/bin/uv sync
sudo systemctl restart memodi
```

### Backups

```bash
# Diario automatico via cron
0 3 * * * source /home/memodi/memodi/docker/prod/.env && /home/memodi/memodi/docker/prod/backup.sh

# Restore
./docker/prod/restore.sh /data/memodi/backups/memodi_20260411.sql.gz
```

## Desarrollo local

```bash
# Pull la imagen pre-buildeada de DB (pgvector + AGE incluidos)
docker compose pull db

# Levantar PostgreSQL local
docker compose up -d

# Configurar env vars
export MEMODI_DB_USER=memodi MEMODI_DB_PASSWORD=memodi_dev

# Instalar dependencias
uv sync

# Correr tests (incluye los 12 tests de graph que requieren Apache AGE)
uv run pytest -v

# Lint
uv run ruff check src/ tests/
```

Si no pulleas la imagen, docker compose la buildea desde `docker/Dockerfile.db` — compila pgvector y AGE desde source (lento pero funciona offline).

## Contribuir

1. Abri un PR apuntando a `main`
2. `ci.yml` corre lint + tests automaticamente — vas a ver el check en el PR
3. Si CI pasa y el PR se mergea, `deploy.yml` arranca solo

Las conexiones a PostgreSQL tienen `idle_in_transaction_session_timeout=30s` — transacciones colgadas se matan solas. Si corres tests y los abortas a la mitad, no vas a dejar locks bloqueando la DB.

## Licencia

MIT
