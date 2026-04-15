# TODO

## Bugs

- [ ] `memodi_save` returns `_warning: "Project has no workspace"` even when the project IS linked — the save response doesn't check the project-workspace link
- [ ] `memodi_resolve_path` tool not exposed via MCP (listed as core in server.py but not available as deferred tool)
- [ ] `memodi_session_start` and `memodi_session_end` not available as tools — hooks reference them but ToolSearch can't find them

## High Impact

- [ ] Rewrite MCP tool descriptions — current ones say WHAT but not WHEN/WHY. Agents don't connect user intent to the right tool. Add "When to use", context triggers, and examples to each description. Discovered via dogfooding: agent didn't suggest `memodi_plan` for a testing checklist because the description ("Start a new workflow plan") has no contextual signals.

## Improvements

- [ ] Test `install.sh` on a fresh machine / teammate's environment
- [ ] Custom domain for Hetzner server (replace sslip.io)
- [ ] Monitor Claude Code for plugin HTTP MCP fix — could simplify install back to plugin-only
- [ ] `memodi_save` type validation: add `session` as a valid type (currently rejects it)
- [ ] Plugin scripts (`session-start.sh`, `post-compaction.sh`, `subagent-stop.sh`) still reference localhost:8787 as fallback — should fallback to production URL instead
