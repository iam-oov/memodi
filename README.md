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

## Quick Start

```bash
# Levantar la base de datos
docker compose up -d

# Instalar memodi
pip install -e .

# Conectar desde Claude Code via .mcp.json
```

## Arquitectura

```
Claude Code ──MCP──► memodi (Python) ──► PostgreSQL
  (cerebro)         (persistencia)       (JSONB + pgvector + AGE)
```

Claude decide que vale la pena recordar. memodi persiste y consulta. Sin llamadas extra a LLMs — Claude ya esta ahi.

## Licencia

TBD
