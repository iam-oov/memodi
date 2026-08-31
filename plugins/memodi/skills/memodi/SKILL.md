---
name: memodi
description: Use Memodi persistent memory in Codex. Activate workspaces, load prior project context, recall earlier decisions, and save durable discoveries during coding work. Use when starting work in a Memodi-enabled project or when the user asks about project history, prior decisions, or Memodi setup.
---

# Memodi

Memodi is the persistence layer; Codex decides what is worth loading or saving.

## Project orientation

- Use the caller's current working directory as `path`. Never invent or normalize it to a different machine's path.
- On the first substantive task in a project, call `memodi_context` before inspecting git history, TODO files, or repository documentation. Read relevant observation pointers with `memodi_get_observation`.
- If context returns `not_started`, keep Memodi inert for this path. Do not register anything unless the user explicitly asks to activate or configure Memodi.
- If a tool returns `not_authenticated`, tell the user to rerun `install-codex.sh` with a valid account and stop making Memodi calls for that turn.

## Activation

Treat `$memodi`, “activate Memodi”, and “start Memodi for this workspace” as explicit activation requests.

1. Resolve the exact directory the user wants as the workspace boundary. The parent holding sibling repositories is usually the useful boundary; a deeper registration shadows its parent.
2. Call `memodi_list_workspaces` so an existing workspace can be reused across machines.
3. If the boundary or workspace name is not clear, explain the consequence briefly and ask for it. Registration is persistent and must not be guessed.
4. Call `memodi_workspace_start` with the confirmed path and workspace name, then call `memodi_context` for the newly active project.

## Recall and saving

- Prefer `memodi_search_hybrid` for ordinary recall. Use exact, semantic, or global search only when the question calls for it.
- Save durable decisions, architecture, bug causes, non-obvious fixes, configuration changes, and concrete next steps with `memodi_save` as work progresses.
- Do not save conversational filler, guesses, secrets, credentials, or facts already obvious from the repository.
- Use a stable `topic_key` when an observation is expected to evolve. Read the old observation before superseding an unknown id.
- Use `affects` for decisions spanning multiple projects in the same workspace.
- For completed multi-step work, close the session with `memodi_session_end` and a concise Goal / Accomplished / Next Steps summary.

Memodi writes do not authorize unrelated repository changes, external messages, deployments, or destructive actions.
