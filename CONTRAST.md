m● Fantástico, ya tengo los tres repos analizados en
  profundidad. Dejame hacer la síntesis comparativa
  contra lo que tenemos en memodi.

  ---
  Análisis Comparativo: engram vs graphiti vs paul vs
  memodi

  1. ENGRAM — Qué tiene que nosotros no

  Aspecto: Deferred tool loading
  engram: Solo 6 tools en contexto inicial, el resto se

    cargan via ToolSearch on-demand
  memodi: Los 31 tools se cargan siempre
  Oportunidad: ALTO — estamos quemando tokens al pedo
  en
     cada sesión
  ────────────────────────────────────────
  Aspecto: Shell hooks lifecycle
  engram: session-start, session-stop,
    user-prompt-submit, post-compaction, subagent-stop
  —
     5 hooks que inyectan protocolo y nudges
  memodi: Ningún hook — dependemos 100% del skill
  Oportunidad: ALTO — los hooks son el mecanismo que
    FUERZA la proactividad
  ────────────────────────────────────────
  Aspecto: Activity tracking + nudges
  engram: El server trackea tool calls por sesión, y si

    pasaron 15min sin mem_save, appendea un warning a
    las respuestas de search/context
  memodi: No hay tracking de actividad
  Oportunidad: MEDIO — interesante pero invasivo
  ────────────────────────────────────────
  Aspecto: Passive capture (subagent-stop)
  engram: Cada vez que un subagent termina, su stdout
  se
    manda al server y se extraen learnings de secciones

    ## Key Learnings:
  memodi: No capturamos nada de subagents
  Oportunidad: MEDIO — buena idea para no perder
    contexto
  ────────────────────────────────────────
  Aspecto: Project similarity detection
  engram: Al hacer save con un proyecto nuevo, busca
    nombres similares (Levenshtein) y avisa
  memodi: No hay detección de duplicados de proyecto
  Oportunidad: BAJO — nice to have
  ────────────────────────────────────────
  Aspecto: Deduplicación por content hash
  engram: Hashes de contenido en ventana temporal,
    incrementa duplicate_count en vez de crear nuevo
  row
  memodi: No hay dedup — cada save crea un registro
    nuevo
  Oportunidad: MEDIO — previene spam de observaciones
    idénticas
  ────────────────────────────────────────
  Aspecto: Session tracking
  engram: Tabla sessions con
    start/end/summary/project/directory
  memodi: No tenemos concepto de sesión
  Oportunidad: MEDIO — útil para mem_context más
    inteligente
  ────────────────────────────────────────
  Aspecto: Go binary, zero deps
  engram: Single binary, SQLite embebido, sin Docker
    para el cliente
  memodi: Python + Docker + PostgreSQL
  Oportunidad: N/A — decisiones de stack distintas,
    PostgreSQL nos da más poder

  2. GRAPHITI — Qué tiene que nosotros no

  Aspecto: Temporal triplets
  graphiti: Cada relación tiene valid_at, invalid_at,
    expired_at — podés reconstruir el estado del grafo
    en cualquier momento pasado
  memodi: Nuestras relaciones no tienen dimensión
    temporal
  Oportunidad: ALTO — es lo que separa un grafo útil de

    uno estático
  ────────────────────────────────────────
  Aspecto: Entity resolution con LLM
  graphiti: Pipeline multi-stage: exact match → cosine
    similarity → LLM escalation para dedup de entidades
  memodi: Creamos nodos manualmente, sin dedup
  Oportunidad: ALTO — pero requiere llamadas a LLM,
    cambia el modelo de costos
  ────────────────────────────────────────
  Aspecto: Relationship extraction automática
  graphiti: El LLM extrae entidades y relaciones de
    texto libre
  memodi: Las relaciones se crean manualmente via
    memodi_relate
  Oportunidad: MEDIO — nuestro caso de uso es más
    explícito (repos, módulos), no texto libre
  ────────────────────────────────────────
  Aspecto: Episodic memory como provenance
  graphiti: Cada entidad/relación traza hasta el
    episodio que la creó
  memodi: No hay trazabilidad de origen
  Oportunidad: MEDIO — saber POR QUÉ existe una
  relación
  ────────────────────────────────────────
  Aspecto: Community detection
  graphiti: Label propagation agrupa entidades en
    comunidades con summaries jerárquicos
  memodi: No tenemos clustering
  Oportunidad: BAJO — overkill para nuestro modelo de
    repos/módulos
  ────────────────────────────────────────
  Aspecto: Sagas
  graphiti: Conversaciones largas con summary
    incremental
  memodi: No aplica
  Oportunidad: BAJO — no es nuestro caso de uso
  ────────────────────────────────────────
  Aspecto: Search reranking (RRF, MMR, cross-encoder)
  graphiti: 15 recetas de búsqueda pre-configuradas,
    rerankers combinables
  memodi: Solo RRF básico en hybrid search
  Oportunidad: MEDIO — podríamos mejorar el ranking
  ────────────────────────────────────────
  Aspecto: Multi-backend (Neo4j, FalkorDB, Kuzu,
    Neptune)
  graphiti: Abstracción de driver para 4 backends
  memodi: Solo PostgreSQL + AGE
  Oportunidad: N/A — PostgreSQL como single backend es
    una FORTALEZA, no debilidad
  ────────────────────────────────────────
  Aspecto: Group partitioning
  graphiti: group_id para multi-tenant en una misma DB
  memodi: Nuestro scoping es por workspace/project
  Oportunidad: Ya lo tenemos de otra forma

  3. PAUL — Qué tiene que nosotros no

  Aspecto: Context budget awareness
  paul: Clasifica planes por % de contexto: FRESH
    (>70%), MODERATE, DEEP, CRITICAL
  memodi workflow: No hay awareness de contexto
  Oportunidad: ALTO — los planes deberían respetar el
    context window
  ────────────────────────────────────────
  Aspecto: Scale routing
  paul: Clasifica scope (quick-fix/standard/complex) y
    adapta la estructura del plan
  memodi workflow: Un plan es un plan, sin importar
    tamaño
  Oportunidad: MEDIO — evita overhead para cambios
    chicos
  ────────────────────────────────────────
  Aspecto: AC como Given/When/Then
  paul: Acceptance Criteria en formato BDD,
    verificables, linkeados a tareas
  memodi workflow: AC es un dict libre sin formato
  Oportunidad: MEDIO — estructura mejora
  verificabilidad
  ────────────────────────────────────────
  Aspecto: Task types (auto/checkpoint)
  paul: auto, checkpoint:decision,
    checkpoint:human-verify, checkpoint:human-action
  memodi workflow: Tasks sin tipado — todo es "hacer"
  Oportunidad: MEDIO — distinguir qué necesita humano
  vs
     automático
  ────────────────────────────────────────
  Aspecto: SUMMARY.md con dependency frontmatter
  paul: requires, provides, affects, tech-stack
  memodi workflow: Nuestro unify es solo un summary
    string
  Oportunidad: MEDIO — metadata estructurada en el
    cierre
  ────────────────────────────────────────
  Aspecto: Enterprise audit mode
  paul: Review de seguridad/compliance entre PLAN y
    APPLY
  memodi workflow: No existe
  Oportunidad: BAJO — no es nuestro target hoy
  ────────────────────────────────────────
  Aspecto: Handoff files
  paul: Para resumir después de días sin tocar el
    proyecto
  memodi workflow: Nuestro mem_context ya cubre esto
  Oportunidad: Ya lo tenemos
  ────────────────────────────────────────
  Aspecto: Progress routing → UNA acción
  paul: /progress siempre devuelve exactamente UNA
    siguiente acción
  memodi workflow: Nuestro progress devuelve el estado
    completo
  Oportunidad: BAJO — es más estilo que funcionalidad

  ---
  Priorización: Qué vale la pena tomar

  Tier 1 — Impacto alto, esfuerzo razonable

  1. Deferred tool loading (de engram) — Pasar de 31
  tools siempre en contexto a ~8-10 core + el resto
  deferred. FastMCP soporta esto? Hay que verificar,
  pero el impacto en tokens es SIGNIFICATIVO.
  2. Shell hooks lifecycle (de engram) — session-start,
   post-compaction, user-prompt-submit. El skill solo
  NO alcanza para garantizar proactividad. Los hooks
  son el enforcement mechanism.
  3. Temporal triplets en el grafo (de graphiti) —
  Agregarle valid_at / invalid_at a las relaciones en
  AGE. "repo-a DEPENDS_ON repo-b DESDE 2026-01-15 HASTA
   2026-03-20". Cambia el grafo de estático a temporal.

  Tier 2 — Impacto medio, vale explorar

  4. Deduplicación de observaciones (de engram) —
  Content hash + ventana temporal para no crear
  registros duplicados.
  5. Session tracking (de engram) — Tabla de sesiones
  para que context devuelva cosas más relevantes.
  6. AC estructurados en workflow (de paul) — Formato
  BDD para acceptance criteria, verificables
  automáticamente.
  7. Scale routing en workflow (de paul) — Quick-fix vs
   standard vs complex para no sobre-planificar cambios
   chicos.

  Tier 3 — Interesante pero no ahora

  8. Entity resolution (graphiti) — Requiere LLM,
  cambia el modelo de costos
  9. Passive capture de subagents (engram) — Buena
  idea, depende de hooks
  10. Community detection (graphiti) — Overkill para
  nuestro modelo
  11. Enterprise audit (paul) — No es target


 =================
 
 Tier 2 — Re-análisis

  ┌─────┬────────────────────┬────────┬─────────────────────────────────┐
  │  #  │      Feature       │ Fuente │             Qué es              │
  ├─────┼────────────────────┼────────┼─────────────────────────────────┤
  │     │ Deduplicación de   │        │ Content hash + ventana temporal │
  │ 4   │ observaciones      │ engram │  para no crear registros        │
  │     │                    │        │ duplicados                      │
  ├─────┼────────────────────┼────────┼─────────────────────────────────┤
  │ 5   │ Session tracking   │ engram │ Tabla de sesiones para context  │
  │     │                    │        │ más inteligente                 │
  ├─────┼────────────────────┼────────┼─────────────────────────────────┤
  │ 6   │ AC estructurados   │ paul   │ Formato BDD (Given/When/Then)   │
  │     │ en workflow        │        │ para acceptance criteria        │
  ├─────┼────────────────────┼────────┼─────────────────────────────────┤
  │ 7   │ Scale routing en   │ paul   │ Quick-fix vs standard vs        │
  │     │ workflow           │        │ complex para adaptar el plan    │
  └─────┴────────────────────┴────────┴─────────────────────────────────┘

  Dejame profundizar en lo que REALMENTE implica cada uno para memodi, no
  solo la idea sino el costo real.

  4. Deduplicación de observaciones

  Problema que resuelve: Claude a veces guarda la misma observación
  múltiples veces en la misma sesión (proactividad duplicada). Engram lo
  resuelve hasheando el contenido y comparando dentro de una ventana
  temporal.

  Implicación en memodi:
  - Agregar columna content_hash a la tabla observations (migración SQL)
  - Calcular hash (SHA-256 o similar) de title + content en cada save
  - Antes de insertar, buscar hash duplicado en la misma project/ventana
  temporal (~15 min)
  - Si existe → incrementar duplicate_count en el registro existente, NO
  crear nuevo
  - Si es el mismo topic_key → ya tenemos upsert, esto es para cuando NO hay
   topic_key
  - Esfuerzo: Bajo-medio. Una migración, cambio en
  repository.save_observation, un test.

  5. Session tracking

  Problema que resuelve: memodi_context devuelve las observaciones más
  recientes sin saber CUÁNDO fue la última sesión. Con sessions, podríamos
  agrupar por sesión, mostrar "última sesión: trabajaste en X, Y, Z", y
  hacer que el context recovery sea más inteligente.

  Implicación en memodi:
  - Nueva tabla sessions (id, project_id, started_at, ended_at, summary,
  directory)
  - Hooks ya creados podrían llamar a un endpoint para crear/cerrar sesiones
   (pero requiere endpoint REST fuera de MCP, o usar el propio MCP via curl
  con JSON-RPC)
  - memodi_context podría agrupar observaciones por sesión
  - Nuevo tool memodi_session_summary para guardar resumen al final
  - Esfuerzo: Medio-alto. Migración, nuevo repository, nuevos tools, cambio
  en hooks, cambio en context.
  - Complicación: Los hooks no pueden fácilmente llamar tools MCP via curl
  (streamable-http requiere session init). Habría que agregar un endpoint
  REST simple /api/sessions fuera de MCP, o hacer que los hooks inyecten
  instrucciones para que Claude llame los tools.

  6. AC estructurados en workflow

  Problema que resuelve: Actualmente los acceptance criteria del workflow
  son un list[dict] libre — puede ser cualquier cosa. Paul los fuerza a
  formato BDD (Given/When/Then) con ID, lo que hace que la verificación sea
  sistemática.

  Implicación en memodi:
  - Validar formato de AC en update_plan: cada item necesita id, description
   en formato GWT
  - Linkear tareas a ACs via campo done_criteria en cada task
  - En verify, evaluar cada AC individualmente (PASS/FAIL con evidencia)
  - En unify, generar tabla de resultados de AC
  - Esfuerzo: Medio. Cambios en workflow_repository y tools/workflow. No
  necesita migración (AC ya es JSONB).
  - Valor real: Solo vale si el workflow se usa frecuentemente. Si es
  ocasional, el overhead del formato BDD puede ser más molesto que útil.

  7. Scale routing en workflow

  Problema que resuelve: Un cambio de una línea no necesita el mismo plan
  que un refactor de 8 archivos. Paul clasifica en
  quick-fix/standard/complex y adapta la estructura del plan.

  Implicación en memodi:
  - En memodi_plan, agregar clasificación automática basada en el objetivo
  (o dejar que Claude clasifique)
  - Quick-fix: 1 tarea, 1 AC, sin boundaries
  - Standard: 2-5 tareas, ACs completos
  - Complex: recomendar split en múltiples planes
  - Esfuerzo: Bajo. Es principalmente lógica en tools/workflow sin cambios
  de schema.
  - Valor real: Depende de cuánto se use el workflow. Reduce fricción cuando
   se usa.
