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
