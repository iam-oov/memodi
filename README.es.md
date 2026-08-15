# memodi

[![CI](https://github.com/iam-oov/memodi/actions/workflows/ci.yml/badge.svg)](https://github.com/iam-oov/memodi/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/iam-oov/memodi)](https://github.com/iam-oov/memodi/releases)
[![Python](https://img.shields.io/badge/python-3.12+-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/github/license/iam-oov/memodi)](LICENSE)

[English](README.md) | Español

**Memoria Distribuida** - servidor MCP que le da a Claude Code memoria persistente entre workspaces, proyectos y maquinas. Guarda decisiones, bugs y descubrimientos de forma proactiva y los recupera por keyword, semantica o grafo - sin llamadas extra a LLMs.

Una sola instancia de PostgreSQL hace todo: document store (JSONB), busqueda semantica (pgvector) y grafo de conocimiento (Apache AGE).

## Donde brilla memodi

Tu producto no es un repo - son varios. El API, el worker, el servicio de billing. Y las decisiones que los conectan viven en tu cabeza, en PRs viejos, en hilos de chat... hasta que dejan de vivir ahi.

- **Una sola memoria para todo el producto.** Apunta memodi a la carpeta que contiene tus repos - una sola vez. Desde entonces todos comparten la misma memoria: la decision que tomaste en `api/` esta ahi cuando trabajas en `billing/`, sin escarbar en ningun lado.
- **Retoma exactamente donde quedaste.** Cierra una sesion y la siguiente - mañana, o en tu otra maquina - abre con tus pendientes ya en pantalla. El "donde me quede?" del lunes llega respondido.
- **Recuerda como tu preguntas.** Por palabra exacta, por idea ("no resolvimos ya algo parecido?"), o por conexion ("que se rompe si toco esto?"). Y guarda mientras trabajas - decisiones, bugs, descubrimientos - para que nunca tengas que acordarte de recordar.

Sin comando para guardar, sin comando para buscar. Solo trabajas; preguntar es suficiente.

Esa memoria compartida es un **workspace** - ten tantos como necesites (trabajo, personal, tesis), cada uno aislado del resto. `/memodi:start` crea uno - o te une al que ya usas en otra maquina.

## Correr `/memodi:start`

La carpeta que registras decide cuanto comparten tus repos - es la unica decision que vale la pena pensar bien. `/memodi:start` sugiere por defecto la **carpeta padre** del repo actual, y una sola corrida por (maquina, carpeta) alcanza.

```text
trabajo/         ← /memodi:start aqui: un workspace, una memoria
├── api/
├── billing/
└── worker/
```

Buenos usos:

- ✅ **La carpeta padre, cuando los repos hermanos pertenecen al mismo producto.** Todos los repos debajo comparten el workspace sin mas configuracion, cada uno como su propio proyecto con el nombre de su carpeta - incluso los que clones despues.
- ✅ **El mismo nombre de workspace en tu segunda maquina.** Mismo nombre = mismo workspace: tu desktop y tu laptop leen y escriben las mismas memorias.
- ✅ **La carpeta del repo, cuando es un repo suelto.** Sin hermanos no hay nada que compartir - un workspace de un solo repo esta bien.

Malos usos:

- ❌ **`/memodi:start` adentro de cada repo del mismo producto.** Cada corrida crea su propio workspace aislado: la decision guardada en `api/` simplemente no existe cuando preguntas desde `billing/`.
- ❌ **Un nombre de workspace distinto en la segunda maquina.** Un nombre nuevo crea un workspace nuevo y vacio - no el que guarda tus memorias.
- ❌ **Una subcarpeta de un workspace ya registrado** - `trabajo/billing/` cuando `trabajo/` ya esta registrado. No falla: crea en silencio un workspace anidado que tapa al padre en ese subarbol.

## Features

- **Memoria proactiva** - el agente guarda observaciones sin que se lo pidas; las instrucciones viajan con el skill del plugin
- **Busqueda hibrida** - keyword + semantica combinadas con RRF, ademas de busqueda global entre todos tus proyectos
- **Grafo de conocimiento** - dependencias entre repos y analisis de impacto transitivo ("que se rompe si cambio X?")
- **Auto-linking** - escribir `[[topic-key]]` en una observacion crea la relacion `LINKS_TO` en el grafo
- **Multi-maquina** - una key por usuario; registrar el mismo workspace en dos maquinas comparte las memorias
- **Contexto automatico** - hooks de sesion cargan la memoria al abrir el repo e inyectan punteros relevantes en cada prompt
- **Digest de sesion** - abrir una sesion imprime tus pendientes de la sesion anterior, directo en la terminal
- **Inerte por defecto** - un path no registrado devuelve `not_started`; nunca se auto-crean proyectos ni workspaces

## Quick start

Necesitas [Claude Code](https://docs.anthropic.com/en/docs/claude-code) y una API key de memodi (una por usuario). `install.sh` la obtiene automaticamente - abre un navegador para iniciar sesion con Google y le pasa la key directo al instalador, sin copiar ni pegar nada.

### Instalar

```bash
curl -sf https://raw.githubusercontent.com/iam-oov/memodi/main/install.sh | sh
```

### Que hace install.sh

Una sola corrida encadena login, instalacion del plugin, conexion MCP y permisos - sin pasos manuales:

```
$ curl -sf https://raw.githubusercontent.com/iam-oov/memodi/main/install.sh | sh
[1/6] Logging in...
Open this URL to log in:
https://memodi.valdoh.com/login?port=54231&nonce=Kx9pQ2z8mN...

Logged in as someone@example.com
Installing memodi plugin for Claude Code...
[2/6] Adding marketplace...
[3/6] Installing plugin...
[4/6] Configuring MCP server...
[5/6] Adding permissions...
[6/6] Persisting environment to your shell rc...

Done! Wrote MEMODI_API_KEY and MEMODI_MACHINE to ~/.zshrc.

Next:
  1. Reload your shell:   source ~/.zshrc   (or open a new terminal)
  2. Restart Claude Code, then run:  /memodi:start
```

Una pestaña del navegador se abre sola con esa URL. La key nunca aparece en la terminal, en el historial del shell, ni en un prompt para pegarla - viaja directo desde el redirect del navegador hasta un listener efimero en `127.0.0.1`.

Ese listener escucha en loopback de la maquina que corre el instalador, asi que la URL impresa solo completa el login si tu navegador corre en esa misma maquina. Por SSH o en una maquina sin interfaz grafica el listener llega al timeout a los 180s y el instalador cae al prompt para pegar la key - o exporta `MEMODI_API_KEY` de antemano y saltea el login por completo.

Que toca en tu maquina:

- El rc file de tu shell (`~/.zshrc`, `~/.bash_profile`, o `~/.profile`) - un bloque delimitado por marcadores con `MEMODI_API_KEY` y `MEMODI_MACHINE`
- `~/.claude.json` - la entrada del server MCP `memodi`
- `~/.claude/settings.json` - el permiso `"mcp__memodi__*"`
- El registro del marketplace y del plugin `iam-oov/memodi`

Casos de respaldo:

| Condicion                                          | Resultado                                                       |
| -------------------------------------------------- | --------------------------------------------------------------- |
| `MEMODI_API_KEY` ya exportada                      | El login se salta por completo                                  |
| No hay `python3` en la maquina                     | Cae al prompt para pegar la key                                 |
| No hay navegador local (SSH, sin interfaz grafica) | El listener llega al timeout y toma el prompt para pegar la key |
| El listener llega al timeout (180s)                | Cae al prompt para pegar la key                                 |

El hand-off por loopback es un redirect HTTP real, asi que la URL de un solo uso puede quedar en el historial de tu navegador local. `/memodi:logout` revoca la key del lado del server si eso te preocupa.

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

Reinicia Claude Code y corre `/memodi:start`: registra el workspace en esta maquina (o engancha uno existente de otra - mismo nombre = memorias compartidas) y carga su memoria. Una vez por (maquina, carpeta); despues la memoria se carga sola y en silencio al abrir el repo.

`/memodi:end` cierra la sesion con un resumen estructurado (Goal / Accomplished / Next Steps). Un hook `SessionEnd` corre igual en cada salida como red de contencion - nunca pisa un resumen real.

`/memodi:logout` revoca la api key de esta maquina y limpia la config local - usalo para cambiar de cuenta en esta maquina, o para probar el flujo de login desde cero.

`/memodi:login` vuelve a iniciar sesion sin arrancar de cero - el mismo hand-off por navegador que `install.sh`, sin necesidad de correr todo el instalador de nuevo. Necesita un navegador en la misma maquina que Claude Code; por SSH no tiene respaldo para pegar la key y falla, asi que ahi conviene correr `install.sh` en una terminal.

### Actualizar

El instalador es idempotente - volver a correrlo trae la ultima version del plugin:

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
                       memodi.valdoh.com      home server (x86)                pgvector + AGE
```

Claude decide que vale la pena recordar; memodi persiste y consulta.

| Capa                  | Extension             | Para que                             |
| --------------------- | --------------------- | ------------------------------------ |
| Document store        | JSONB                 | Estado, tareas, decisiones, metadata |
| Busqueda full-text    | tsvector              | Keywords multi-idioma                |
| Busqueda semantica    | pgvector (HNSW, 384d) | "ya resolvimos algo parecido?"       |
| Grafo de conocimiento | Apache AGE (Cypher)   | Dependencias, impacto                |

## Autenticacion

Cuentas reales por usuario, no una key compartida:

- Inicio de sesion con Google en `/login` (unica ruta sin key); la api key `mmd_...` se muestra UNA sola vez - el server guarda solo su hash. Cada login genera una key adicional, asi que iniciar sesion desde una segunda maquina nunca invalida la primera
- `X-Memodi-Api-Key` identifica al usuario y es el unico control de acceso frente a `/mcp` y `/hooks/*`
- `X-Memodi-Machine` identifica la maquina; los paths se registran por (usuario, maquina, path) - la misma carpeta puede resolver a workspaces distintos en maquinas distintas
- `path` (el cwd del caller) es parametro explicito en cada tool de proyecto
- Path no registrado → `{"type": "not_started"}`; key ausente o invalida → `{"type": "not_authenticated"}`
- Cambiar de cuenta en la misma maquina no necesita codigo nuevo: la key de otro usuario resuelve a sus propias memorias, nunca a las del usuario anterior. Corre `/memodi:logout` para revocar la key de esta maquina antes de iniciar sesion con otra cuenta

## Tools MCP (38)

Todas las tools de proyecto reciben `path` (el cwd del caller) y lo resuelven contra un workspace registrado.

### Memoria

| Tool                                 | Descripcion                                                                                   |
| ------------------------------------ | --------------------------------------------------------------------------------------------- |
| `memodi_save`                        | Guardar observacion (auto-genera embedding)                                                   |
| `memodi_search`                      | Busqueda por keywords                                                                         |
| `memodi_search_similar`              | Busqueda semantica                                                                            |
| `memodi_search_hybrid`               | Keyword + semantica con RRF                                                                   |
| `memodi_context`                     | Contexto reciente del workspace completo: ultimo resumen de sesion + punteros a observaciones |
| `memodi_search_global`               | Buscar en todos tus proyectos (scoped al usuario)                                             |
| `memodi_backfill`                    | Embeddings para observaciones viejas                                                          |
| `memodi_backfill_links`              | Reconciliar LINKS_TO previos al auto-linking (idempotente)                                    |
| `memodi_find_consolidation_clusters` | Detectar clusters de observaciones listas para consolidar (solo lectura)                      |
| `memodi_list_projects`               | Proyectos conocidos y su workspace                                                            |
| `memodi_delete`                      | Soft-delete de una observacion                                                                |
| `memodi_get_observation`             | Leer observacion por id, incluidas superseded                                                 |

### Grafo de conocimiento

| Tool                     | Descripcion                                                   |
| ------------------------ | ------------------------------------------------------------- |
| `memodi_relate`          | Crear relacion (ej: repo-a DEPENDS_ON repo-b)                 |
| `memodi_dependencies`    | Que depende de que; con `path` incluye LINKS_TO del workspace |
| `memodi_impact`          | Impacto transitivo; con `path` recorre tambien LINKS_TO       |
| `memodi_graph_overview`  | Resumen de nodos y relaciones                                 |
| `memodi_remove_relation` | Invalidar relacion (soft delete)                              |
| `memodi_delete_relation` | Eliminar relacion (hard delete)                               |

### Workspaces

| Tool                      | Descripcion                                                   |
| ------------------------- | ------------------------------------------------------------- |
| `memodi_workspace_start`  | Registrar carpeta como workspace (lo dispara `/memodi:start`) |
| `memodi_list_workspaces`  | Listar workspaces                                             |
| `memodi_merge_projects`   | Fusionar proyectos duplicados (dry_run por defecto)           |
| `memodi_delete_workspace` | Eliminar workspace                                            |
| `memodi_rename_workspace` | Renombrar workspace                                           |
| `memodi_purge_workspace`  | Vaciar workspace (destructivo, dry_run por defecto)           |

### Workflow

| Tool                  | Descripcion                 |
| --------------------- | --------------------------- |
| `memodi_plan`         | Crear plan                  |
| `memodi_update_plan`  | Definir criterios y tareas  |
| `memodi_approve_plan` | Aprobar plan, pasar a apply |
| `memodi_apply_done`   | Marcar apply hecho          |
| `memodi_verify`       | Verificar resultado         |
| `memodi_unify`        | Cerrar el loop              |
| `memodi_progress`     | Estado del workflow activo  |
| `memodi_task_update`  | Actualizar una tarea        |

### Sesiones y sistema

| Tool                   | Descripcion                                                            |
| ---------------------- | ---------------------------------------------------------------------- |
| `memodi_session_start` | Iniciar sesion (las observaciones se auto-adjuntan)                    |
| `memodi_session_end`   | Cerrar sesion con resumen estructurado (obligatorio)                   |
| `memodi_logout`        | Revocar la api key del caller en el server (respalda `/memodi:logout`) |
| `memodi_ping`          | Server vivo                                                            |
| `memodi_status`        | Salud del server y extensiones de PostgreSQL                           |
| `memodi_version`       | Version en produccion                                                  |

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
- Sin parametros Cypher - los valores se interpolan
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

PR a `main` → `ci.yml` corre lint + la suite completa de tests → si se mergea, `deploy.yml` deploya solo.

## Produccion

Corre nativo en un home server x86 siempre encendido (PostgreSQL + pgvector + AGE, uv + systemd) detras de un Cloudflare Tunnel, con deploy push-based via GitHub Actions. Setup y operaciones dia 2: [`docs/pi-setup.md`](docs/pi-setup.md) - escrito para el host original (Raspberry Pi), los mismos pasos aplican.

## Licencia

MIT
