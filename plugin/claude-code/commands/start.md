---
description: Activate memodi memory here — register the workspace if needed, load its cross-machine memories, and start a session.
argument-hint: "[workspace-name]"
allowed-tools: Bash(pwd), mcp__memodi__*
---

The user ran `/memodi:start` to activate memodi memory for the current workspace.

- Current working directory: !`pwd`
- Workspace name argument (may be empty): $ARGUMENTS

The **current working directory is the default workspace root** — you register
the folder you are standing in. Its **parent** is that path minus its last
segment (e.g. `/Users/x/Personal/repo` → `/Users/x/Personal`); offer it only
when the user is clearly inside one repo of a group they want to share.

Work through these steps in order. Be terse — this is an activation command, not a conversation.

## 1. Resolve the workspace

Load and call `memodi_list_paths` (`ToolSearch("select:memodi_list_paths")`) — every path this user has registered, on every machine. Then call `memodi_context` with `path` set to the current working directory above. Do NOT pass `project`.

**Resolving is NOT the same as being registered.** A path resolves by longest-prefix from any ANCESTOR registration, so a folder deep under a registered parent answers like it belongs while having no boundary of its own. Read the listing, not just the context result:

- **The cwd appears in the listing** (exact path, this machine) → genuinely registered. Skip step 2. Do NOT ask for a name. Go to step 3.
- **`memodi_context` resolved, but the cwd is NOT in the listing** → an ancestor is covering it. Say which path and workspace are shadowing it, in one line, then ASK whether to register the cwd as its own workspace boundary, and WAIT. Registering it makes this folder its own root (longest prefix wins) without touching the ancestor. If the user declines, go to step 3.
- **`{"type": "not_started"}`** → this path has no workspace on this machine. Go to step 2.
- **`{"type": "not_authenticated"}`** → the api key is missing or invalid. Tell the user in ONE line to re-run `install.sh` with a valid key, then STOP.

## 2. Register the workspace (only if `not_started`)

Registering a folder makes it a workspace **boundary**: that folder is the root, and every repo under it becomes its own project. Register the folder the user is standing in — it is the one they are looking at, and the one they mean.

The exception is standing INSIDE a single repo that belongs to a group meant to share memory (`.../Repos/tiriel-gateway-service` when the whole of `.../Repos` is one workspace). There, offer the parent instead — and ask, never assume.

Registering the same workspace **name** on another machine, or on a second path of this one, is what makes memories shared: many paths, one workspace, one memory.

First, load and call `memodi_list_workspaces` (`ToolSearch("select:memodi_list_workspaces")`) — these are the workspaces the user already owns, across ALL their machines.

Pick the name:
- If the argument gave a name → use it **exactly as given**.
- Else if the listing has existing workspaces → let the user **select**, never retype:
  - **4 or fewer**: use the `AskUserQuestion` tool — one option per workspace (label = exact name), so the user picks instead of typing. "Other" covers creating a new workspace.
  - **5 or more** (AskUserQuestion caps at 4 options): show a numbered list — one workspace per line, plus a final `N+1. Create a new workspace` entry — and WAIT for the user to answer with a number or a new name.
  - Map the selection back to the workspace's **exact stored name** yourself; the user must never have to retype it.
- Else (no argument, no existing workspaces) → ask for a short descriptive name (e.g. `trabajo`, `personal`, `tesis`). Then WAIT.

**Never invent or translate a name.** Case and surrounding whitespace are folded for you (`Tiriel`, `tiriel` and `  TIRIEL ` are one workspace), but nothing else is — `tiriel-automatice` is a different workspace from `tiriel`. Selection by number/option exists so the user never has to retype and drift.

Before registering, check the listing from step 1: if the path you are about to register is already covered by an ancestor, say so and name the workspace that covers it, so the user knows this creates a NEW boundary rather than joining the old one.

Register the **current working directory** as `path`:

`memodi_workspace_start(path=<cwd>, workspace=<name>)`

State plainly what you registered: `workspace "<name>" → <cwd>`. If the cwd is one repo among siblings that should share the workspace, ask whether to register the parent instead — and WAIT — rather than silently registering a folder the user did not name.

## 3. Load cross-machine memories

Call `memodi_context` with `path` (cwd) — this returns recent observations for the **whole workspace** (every project, every machine) plus the last session summary.

Do NOT call `memodi_session_start`. The `SessionStart` hook owns the session lifecycle; from the next session on it opens one automatically for this now-registered path. For the rest of THIS conversation there is no open session, which costs nothing: `/memodi:end` creates and closes one on the spot so the summary is never lost.

Then give the user a **short** recap and stop:
- workspace name → project resolved
- last session goal (if any)
- how many observations were loaded, and 2–3 of the most relevant titles

From here on, memory is active for the session and loads automatically on future sessions in this repo — no need to run this again unless you want to re-pull context.
