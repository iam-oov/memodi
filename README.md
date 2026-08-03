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
Internet ──HTTPS──► Cloudflare Tunnel ──► memodi-server (uv + systemd) ──► PostgreSQL (nativo)
                    memodi.valdoh.com      puerto 8787, Raspberry Pi
```

Claude decide que vale la pena recordar. memodi persiste y consulta. Sin llamadas extra a LLMs — Claude ya esta ahi.

El agente usa memodi de forma PROACTIVA — guarda decisiones, bugs y descubrimientos automaticamente sin que el usuario lo pida. Las instrucciones viajan con el skill del plugin.

## Modelo de autenticacion

memodi usa cuentas reales por usuario, no una key compartida entre todos:

- Alta en `/signup` (ruta publica del server, sin auth de MCP por diseno — es el unico punto de entrada sin key)
- La api key (`mmd_...`) se muestra UNA SOLA VEZ al registrarte; el server solo guarda su hash, nunca puede volver a mostrartela
- `X-Memodi-Api-Key` identifica al usuario y es el UNICO control de acceso a nivel app frente a `/mcp` — no hay otra capa delante
- `X-Memodi-Machine` identifica la maquina; los paths se registran por `(usuario, maquina, path)`, asi la misma carpeta puede resolver a workspaces distintos en maquinas distintas
- `path` (el cwd del caller) es un parametro explicito en cada llamada a una tool de proyecto — memodi es INERTE para paths no registrados: devuelve `{"type": "not_started"}`, sin auto-creacion de proyectos ni workspaces
- Key ausente o invalida -> `{"type": "not_authenticated"}`

## Quick Start

Necesitas: [Claude Code](https://docs.anthropic.com/en/docs/claude-code) instalado y una API key propia de memodi (una por usuario, no se comparte).

### 0. Conseguir tu API key

Registrate en la pagina de signup del server (reemplaza la URL por la de tu instancia):

```
https://memodi.valdoh.com/signup
```

Copia la api key (`mmd_...`) apenas la veas — se muestra una sola vez.

### Instalacion rapida

```bash
export MEMODI_API_KEY="mmd_..."
curl -sf https://raw.githubusercontent.com/iam-oov/memodi/main/install.sh | sh
```

> Si preferis no ejecutar scripts remotos directamente (comprensible), segui la instalacion manual.

### Instalacion manual

**1. Configurar la API key** — agregalo a tu shell profile (`~/.zshrc` o `~/.bashrc`):

```bash
export MEMODI_API_KEY="mmd_..."
```

**2. Agregar el marketplace de memodi:**

```bash
claude plugin marketplace add iam-oov/memodi
```

**3. Instalar el plugin** (hooks de sesion + skills de memoria e import):

```bash
claude plugin install memodi@memodi
```

**4. Configurar la conexion al server** (dos headers: tu identidad de usuario y la de esta maquina):

```bash
claude mcp add --transport http \
  -H "X-Memodi-Api-Key: $MEMODI_API_KEY" \
  -H "X-Memodi-Machine: $(hostname)" \
  --scope user \
  memodi https://memodi.valdoh.com/mcp
```

**5. Permitir todas las tools de memodi** (evita aprobar una por una):

Agregar `"mcp__memodi__*"` al array `permissions.allow` en `~/.claude/settings.json`.

**6. Reiniciar Claude Code y activar la memoria:**

```
/memodi:start
```

### Activar y verificar

Abri Claude Code en tu proyecto y corre `/memodi:start`. Ese comando:
1. Chequea si el path ya esta registrado en esta maquina (`memodi_context`)
2. Si no lo esta, registra el workspace — te deja elegir un nombre nuevo o **enganchar uno que ya tengas en otra maquina** (registrar el mismo nombre en dos maquinas comparte las memorias entre ambas)
3. Carga las memorias de todo el workspace y abre una sesion

Una sola vez por (maquina, carpeta). Despues de eso la memoria se carga **sola y en silencio** cada vez que abris ese repo — no hace falta volver a correr `/memodi:start` salvo que quieras re-traer el contexto a mano. En un path no registrado memodi se queda inerte y callado hasta que corras el comando.

Para cerrar una sesion de forma explicita corre `/memodi:end`: arma un resumen estructurado (Goal / Accomplished / Next Steps) y lo guarda con `memodi_session_end`, para que la proxima sesion arranque con ese contexto. Un hook `SessionEnd` corre igual en cada salida como red de contencion, por HTTP plano (no MCP), pero solo cierra la sesion que coincide exactamente con el session id de Claude Code y siempre con resumen NULL — nunca reemplaza el resumen real.

### Desinstalar

```bash
curl -sf https://raw.githubusercontent.com/iam-oov/memodi/main/uninstall.sh | sh
```

O manualmente:

```bash
claude mcp remove memodi --scope user
claude plugin uninstall memodi@memodi --scope user
claude plugin marketplace remove memodi
```

## Tools MCP (36 tools)

Todas las tools de proyecto (memoria, workflow, sesiones) reciben `path` (el cwd del
caller) y lo resuelven contra un workspace registrado — ver Modelo de autenticacion.

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
| `memodi_search_global` | Buscar keywords en TODOS tus propios proyectos (scoped al usuario, no cruza cuentas) |
| `memodi_backfill` | Generar embeddings para observaciones viejas |
| `memodi_backfill_links` | Reconciliar LINKS_TO para observaciones guardadas antes del auto-linking `[[topic-key]]` (idempotente) |
| `memodi_list_projects` | Listar tus proyectos conocidos y su workspace |
| `memodi_delete` | Soft-delete de una observacion junk/test/incorrecta (reversible a nivel DB) |
| `memodi_get_observation` | Leer una observacion por id, incluidas las superseded (path de auditoria) |

### Grafo de conocimiento (proactivo — el agente crea relaciones al descubrirlas)
| Tool | Descripcion |
|------|-------------|
| `memodi_relate` | Crear relacion (ej: repo-a DEPENDS_ON repo-b) |
| `memodi_dependencies` | Que depende de que. Con `path` (opcional) tambien devuelve `links_to`/`linked_from`, los LINKS_TO auto-creados desde `[[topic-key]]` en el contenido, scoped a ese workspace |
| `memodi_impact` | Analisis de impacto transitivo: "que se rompe si cambio X?". Con `path` (opcional) tambien recorre LINKS_TO ademas de DEPENDS_ON |
| `memodi_graph_overview` | Resumen de todos los nodos y relaciones |
| `memodi_remove_relation` | Invalidar una relacion (soft delete, conserva historial) |
| `memodi_delete_relation` | Eliminar una relacion permanentemente (hard delete) |

### Workspaces (inerte para paths no registrados — sin auto-creacion)
| Tool | Descripcion |
|------|-------------|
| `memodi_workspace_start` | Registrar una carpeta padre como workspace en esta maquina — el UNICO gate de onboarding (normalmente lo dispara `/memodi:start`, no se llama a mano) |
| `memodi_list_workspaces` | Listar tus workspaces con su cantidad de proyectos |
| `memodi_merge_projects` | Fusionar un proyecto en otro (repara duplicados, dry_run por defecto) |
| `memodi_delete_workspace` | Eliminar un workspace |
| `memodi_rename_workspace` | Renombrar un workspace |
| `memodi_purge_workspace` | Vaciar datos de un workspace para reimportar (destructivo, dry_run por defecto) |

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

### Sesiones
| Tool | Descripcion |
|------|-------------|
| `memodi_session_start` | Iniciar una sesion (las observaciones se auto-adjuntan) |
| `memodi_session_end` | Cerrar sesion con un resumen estructurado (obligatorio) |

## Modelo del grafo

```
Repo ──DEPENDS_ON──► Repo
Repo ──CONTAINS────► Module
Module ──AFFECTS───► Module
Topic ──LINKS_TO───► Topic
```

| Nodo | Propiedades | Ejemplo |
|------|-------------|---------|
| Repo | name, language, description | repo-a, Python |
| Module | name, description | auth, database |
| Topic | name, workspace_id | architecture/auth-model |

| Relacion | De → A | Ejemplo |
|----------|--------|---------|
| DEPENDS_ON | Repo → Repo | repo-c depende de repo-a |
| CONTAINS | Repo → Module | repo-a contiene auth |
| AFFECTS | Module → Module | auth afecta a api |
| LINKS_TO | Topic → Topic | auto-creado al escribir `[[topic-key]]` en el contenido de un `memodi_save` con `topic_key` propio |

`Topic` es el unico nodo scoped por workspace (identidad = name + workspace_id): dos
workspaces distintos pueden tener cada uno su propio `architecture/auth-model` sin
pisarse. El resto de los nodos (`Repo`, `Module`) siguen siendo globales, creados solo
via `memodi_relate`.

### Limitaciones conocidas de Apache AGE

- **Sin union de tipos en paths variables**: `[:DEPENDS_ON|AFFECTS*1..5]` no funciona en variable-length patterns
- **Sin parametros Cypher**: los valores se interpolan directamente en el query string
- **LOAD requerido por conexion**: cada conexion necesita `LOAD 'age'` y `SET search_path`

## Produccion

### Stack
- **Server**: Raspberry Pi (arm64), todo nativo — sin containers en produccion
- **PostgreSQL 16**: nativo (PGDG apt repo) con pgvector + Apache AGE compilado desde source — ver `docs/pi-setup.md`
- **memodi-server**: Python via uv + systemd (`memodi.service`, puerto 8787). Por defecto bindea a todas las interfaces (`0.0.0.0`); en producción `MEMODI_HOST=127.0.0.1` lo restringe a loopback para que solo el túnel local llegue, nunca la LAN
- **Cloudflare Tunnel**: expone `https://memodi.valdoh.com` con TLS incluido, sin abrir puertos en el router; corre como servicio systemd nativo del usuario (fuera de este repo); el ingress se configura en el dashboard de Zero Trust
- **Deploy**: push-based — GitHub Actions entra por SSH a traves del mismo tunnel (`pi.valdoh.com`), autenticando con un service token de Cloudflare Access

### Primer arranque (una vez, en el Pi)

El deploy automatico llega al Pi *a traves del tunnel*, asi que no puede
bootstrapearlo. Antes del primer deploy segui `docs/pi-setup.md` completo:
PostgreSQL + pgvector + Apache AGE nativos, `memodi.service`, sudoers, env
file. No hay imagen de Docker que pullear ni compose que levantar —
`db-image.yml` ya no es un prerequisito de produccion (solo alimenta el
desarrollo local).

Verificar antes del primer deploy:
- cloudflared corriendo como servicio nativo del usuario (independiente de cualquier deploy)
- `MEMODI_SIGNUP_CODE` no esta vacio en `docker/prod/.env` (con signup deshabilitado,
  `GET /signup` devuelve 503 y el health check del deploy falla)
- `memodi.service` habilitado y corriendo (`systemctl status memodi`)
- `https://memodi.valdoh.com/signup` responde 200

### Proteccion de /signup, /mcp y /hooks/*

`/signup` es publico por diseno (es el unico punto de entrada sin key). Su proteccion:

- Invite code (`MEMODI_SIGNUP_CODE`) + limite de body a nivel app
- Una regla de **rate limiting en Cloudflare** sobre `memodi.valdoh.com/signup` — configurala en el dashboard (sugerido: 5 requests por minuto por IP). No hay throttling a nivel app.
- `/mcp` requiere una api key valida por usuario (`X-Memodi-Api-Key`); sin key o con key invalida responde `not_authenticated`

Ademas de `/mcp` hay **tres rutas HTTP planas** para los hooks del plugin, con el mismo
contrato de auth (`X-Memodi-Api-Key` + `X-Memodi-Machine`) y sin ninguna otra puerta
delante: `POST /hooks/session-start`, `POST /hooks/session-close` y `POST /hooks/capture`.
Quien configure el WAF tiene que saber que existen:

- Cada ruta valida y acota sus campos, y limita el body a nivel app (4KB las de sesion, 64KB `capture`)
- Igual que `/signup`, no hay throttling a nivel app — una regla de **rate limiting en Cloudflare** sobre `/hooks/*` queda como accion de operador

### Pipeline CI/CD

4 workflows de GitHub Actions, cada uno con una sola responsabilidad:

| Workflow | Trigger | Que hace |
|----------|---------|----------|
| `ci.yml` | PR a main, push a main | Lint + tests (155 tests, 0 skippeados) |
| `deploy.yml` | `ci.yml` pasa en main | SSH al Pi a traves del Cloudflare Tunnel + `uv sync` + `systemctl restart memodi` + health check |
| `release.yml` | Tag `v*` | Changelog desde el tag anterior + GitHub Release |
| `db-image.yml` | Cambios en `Dockerfile.db` | Build + push a GHCR (imagen usada solo en desarrollo local) |

El deploy falla con `exit 1` si `/signup` no devuelve 200 despues del restart, o si la version instalada no coincide con `__about__.py`, y vuelca los ultimos 30 logs del servicio (`journalctl -u memodi`). Ojo: un GET a `/mcp` devuelve 406 con el server SANO (streamable-http exige headers MCP) — nunca lo uses como health check.

### Crear un release

```bash
# Bump version en pyproject.toml, commit, tag, push
git tag v0.4.0
git push origin v0.4.0
```

El workflow genera el changelog automaticamente desde el tag anterior y crea el GitHub Release.

### Deploy manual (si es necesario)

```bash
ssh -o "ProxyCommand=cloudflared access ssh --hostname %h" usuario@pi.valdoh.com
cd ~/memodi && git fetch origin main && git reset --hard origin/main
uv sync --reinstall-package memodi
sudo systemctl restart memodi
```

### Backups

Backups: deferred — el Pi arranca con DB fresca; la estrategia de backup offsite queda para un cambio futuro.

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
