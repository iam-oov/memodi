# TODO

## Bugs

- [x] `memodi_save` returns `_warning: "Project has no workspace"` even when the project IS linked — fixed in 4db365c
- [ ] Claude Code silently drops some MCP tools — `memodi_resolve_path`, `memodi_register_path`, `memodi_session_start`, `memodi_session_end` are registered in server.py but invisible to the agent. Not a memodi bug — server reports them correctly. Workaround: hooks call these indirectly

## High Impact

- [ ] Rewrite MCP tool descriptions — current ones say WHAT but not WHEN/WHY. Agents don't connect user intent to the right tool. Add "When to use", context triggers, and examples to each description. Discovered via dogfooding: agent didn't suggest `memodi_plan` for a testing checklist because the description ("Start a new workflow plan") has no contextual signals.

## Improvements

- [ ] Test `install.sh` on a fresh machine / teammate's environment
- [ ] Custom domain for Hetzner server (replace sslip.io)
- [ ] Monitor Claude Code for plugin HTTP MCP fix — could simplify install back to plugin-only
- [x] `memodi_save` type validation: add `session` as a valid type — fixed
- [ ] Plugin scripts (`session-start.sh`, `post-compaction.sh`, `subagent-stop.sh`) still reference localhost:8787 as fallback — should fallback to production URL instead
