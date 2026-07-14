# TODO

## Bugs

- [x] `memodi_save` returns `_warning: "Project has no workspace"` even when the project IS linked — fixed in 4db365c
- [ ] Claude Code silently drops some MCP tools — `memodi_session_start`, `memodi_session_end` are registered in server.py but invisible to the agent (`memodi_resolve_path`/`memodi_register_path` were also affected before their removal in the workspace-scoped-resolution change). Not a memodi bug — server reports them correctly. Workaround: hooks call these indirectly

## High Impact

- [x] Rewrite MCP tool descriptions — all 34 tools now include WHEN/WHY context triggers, not just WHAT they do

## Improvements

- [ ] Test `install.sh` on a fresh machine / teammate's environment
- [x] Custom domain — `memodi.valdoh.com` via Cloudflare Tunnel on the Raspberry Pi (Hetzner + sslip.io retired)
- [ ] Monitor Claude Code for plugin HTTP MCP fix — could simplify install back to plugin-only
- [x] `memodi_save` type validation: add `session` as a valid type — fixed
- [x] Plugin scripts now default to production URL instead of localhost:8787 — fixed

<!-- -   El plan está en fase plan en memodi. Cuando quieras arrancarlo, hacemos
   memodi_approve_plan y empezamos con las tareas de diseño. La próxima
  sesión podés decir "qué hay pendiente del migration tool" y memodi te
  lo trae. -->

1. [HECHO] Deploy automático: push a main → CI → deploy a la Pi por Cloudflare Access (service token) — verificado en verde (run 29301850329, 2026-07-14)
2. [HECHO] Rate-limit en /signup — Cloudflare WAF rule (uri.path eq "/signup", 5 req/10s por IP, Block). Free tier: enforcement laxo/laggy, no clampea picos cortos pero sí carga sostenida (brute-force). Health check del deploy no afectado (loopback).
3. [HECHO a796803] Adelgazar la respuesta de memodi_save — allowlist de serialización, ack de 8 campos de dominio (sin vector ni search_vector)
4. Plugin en la Mac: correr `./install.sh` (o el curl one-liner). El installer pide la api key de forma interactiva sin eco (lee de /dev/tty → no queda en el shell history ni en el scrollback) y persiste `MEMODI_API_KEY` + `MEMODI_MACHINE` en el rc del shell (bloque idempotente con marcadores `# >>> memodi env >>>`) para que los hooks funcionen en sesiones futuras. Apunta a memodi.valdoh.com; MEMODI_MACHINE defaultea al hostname de la Mac → workspace scopeado aparte del de Linux. Probar: memodi_workspace_start + un save + verificar aislamiento por máquina.
