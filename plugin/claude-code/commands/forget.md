---
description: Un-register a folder from memodi on this machine — memories are kept, only the address is dropped.
argument-hint: "[path]"
allowed-tools: Bash(pwd), mcp__memodi__*
---

The user ran `/memodi:forget` to drop a workspace registration on this machine.

- Current working directory: !`pwd`
- Path argument (may be empty): $ARGUMENTS

Work through these steps in order. Be terse — this is a repair command, not a conversation.

## 1. Show what is registered here

Load and call `memodi_list_paths` (`ToolSearch("select:memodi_list_paths")`). Filter to THIS machine — a registration on another machine cannot be dropped from here.

The target is the path argument if given, else the current working directory.

- **Target is in the listing** → this is the row to drop. Go to step 2.
- **Target is NOT in the listing** → there is nothing to forget: the path only *resolves*, through an ancestor's registration. Say which ancestor path and workspace cover it, in one line, and STOP. Do not forget the ancestor — that would un-register every other folder under it. If what the user actually wants is a separate workspace for this folder, tell them to run `/memodi:start` here instead.
- **The listing is empty for this machine** → say memodi is not registered on this machine and STOP.

## 2. Say what changes, then WAIT

State, in at most three lines:

- the exact path and workspace about to be dropped
- **what the path will resolve to afterwards** — check the listing for the longest remaining ancestor of it. If one exists, name it and its workspace: the folder does NOT go dormant, it falls back to that. If none exists, say the folder becomes `not_started` and memory goes silent there.
- that memories are NOT deleted: the workspace, its projects and every observation stay exactly as they are. Only the address goes. Registering the path again re-attaches to whatever workspace is named then.

Then ASK for confirmation and WAIT. Do not proceed on an unanswered question.

## 3. Forget it

Load `memodi_workspace_forget` (`ToolSearch("select:memodi_workspace_forget")`) and call it with the target `path`.

- `{"forgotten": true, ...}` → done.
- `{"forgotten": false, ...}` → the row was already gone; say so in one line.

## 4. Confirm

One or two lines: what was dropped, and what the folder resolves to now (from step 2). If it fell back to an ancestor and that is not what the user wanted, point them at `/memodi:start` to give this folder its own workspace.

If the current session was running against the workspace just dropped, mention that this session keeps its already-loaded context; the change takes effect on the next session.
