---
description: Activate memodi memory here — register the workspace if needed, load its cross-machine memories, and start a session.
argument-hint: "[workspace-name]"
allowed-tools: Bash(pwd), mcp__memodi__*
---

The user ran `/memodi:start` to activate memodi memory for the current workspace.

- Current working directory: !`pwd`
- Workspace name argument (may be empty): $ARGUMENTS

The **parent folder** (default workspace root) is the current working
directory minus its last path segment — derive it from the value above,
no shell needed (e.g. `/Users/x/Personal/repo` → `/Users/x/Personal`).

Work through these steps in order. Be terse — this is an activation command, not a conversation.

## 1. Resolve the workspace

Call `memodi_context` with `path` set to the current working directory above. Do NOT pass `project`.

- **Resolved** (returns `observations` / `last_session`) → the workspace is ALREADY registered on this machine. Skip step 2 entirely. Do NOT ask for a name. Go to step 3.
- **`{"type": "not_started"}`** → this path has no workspace on this machine. Go to step 2.
- **`{"type": "not_authenticated"}`** → the api key is missing or invalid. Tell the user in ONE line to re-run `install.sh` with a valid key, then STOP.

## 2. Register the workspace (only if `not_started`)

memodi groups related repos under a **parent folder** (like VS Code multi-root): you register the parent that holds your repos, and each repo under it becomes its own project. Registering the same workspace **name** on another machine is what makes memories shared cross-machine.

First, load and call `memodi_list_workspaces` (`ToolSearch("select:memodi_list_workspaces")`) — these are the workspaces the user already owns, across ALL their machines.

Pick the name:
- If the argument gave a name → use it **exactly as given**.
- Else if the listing has existing workspaces → let the user **select**, never retype:
  - **4 or fewer**: use the `AskUserQuestion` tool — one option per workspace (label = exact name), so the user picks instead of typing. "Other" covers creating a new workspace.
  - **5 or more** (AskUserQuestion caps at 4 options): show a numbered list — one workspace per line, plus a final `N+1. Create a new workspace` entry — and WAIT for the user to answer with a number or a new name.
  - Map the selection back to the workspace's **exact stored name** yourself; the user must never have to retype it.
- Else (no argument, no existing workspaces) → ask for a short descriptive name (e.g. `trabajo`, `personal`, `tesis`). Then WAIT.

**Never invent, translate, or "clean up" a name.** To attach to an existing workspace the name must match byte-for-byte — any drift silently creates a separate one. Selection by number/option exists precisely to make that impossible.

Register with the **parent folder** derived above as `path`, so sibling repos share the workspace:

`memodi_workspace_start(path=<parent folder>, workspace=<name>)`

State plainly what you registered: `workspace "<name>" → <parent folder> (this repo and its siblings share it)`. If the user actually works out of this single repo only, register the cwd instead — but default to the parent.

## 3. Load cross-machine memories and start the session

1. Call `memodi_context` with `path` (cwd) — this returns recent observations for the **whole workspace** (every project, every machine) plus the last session summary.
2. Load and call `memodi_session_start` (`ToolSearch("select:memodi_session_start")`) with `path` (cwd).

Then give the user a **short** recap and stop:
- workspace name → project resolved
- last session goal (if any)
- how many observations were loaded, and 2–3 of the most relevant titles

From here on, memory is active for the session and loads automatically on future sessions in this repo — no need to run this again unless you want to re-pull context.
