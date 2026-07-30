---
description: Close the memodi session for this workspace — save a structured summary so the next session picks up from here.
allowed-tools: Bash(pwd), mcp__memodi__*
---

The user ran `/memodi:end` to close out the current memodi session.

- Current working directory: !`pwd`

Work through these steps in order. Be terse — this is a closing command, not a conversation.

## 1. Check the workspace is active

Call `memodi_context` with `path` set to the current working directory above. Do NOT pass `project`.

- **Resolved** (returns `observations` / `last_session`) → the workspace is registered here. Continue to step 2.
- **`{"type": "not_started"}`** → this path has no workspace registered on this machine. Say NOTHING about memodi — there is no session to close.
- **`{"type": "not_authenticated"}`** → the api key is missing or invalid. Tell the user in ONE line to re-run `install.sh` with a valid key, then STOP.

## 2. Build the structured summary

From the actual conversation in this session — not a placeholder — write:

```
## Goal
[What we were working on this session]

## Accomplished
- [Completed items with key details]

## Next Steps
- [What remains to be done]

## Relevant Files
- path/to/file — [what changed or what it does]
```

Omit a section only if it is genuinely empty (e.g. no remaining steps).

## 3. Close the session

Load `memodi_session_end` (`ToolSearch("select:memodi_session_end")`) and call it with `path` (the cwd above) and the `summary` built in step 2.

## 4. Confirm

Reply with ONE line confirming the session closed. Do not restate the whole summary back to the user.
