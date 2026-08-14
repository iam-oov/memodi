---
description: Revoke this machine's memodi api key server-side and clean up the local config, so memories stop loading here.
allowed-tools: Bash, mcp__memodi__*
---

The user ran `/memodi:logout` to log out of memodi on this machine.

Work through these steps in order. Be terse — this is a closing command, not a conversation.

## 1. Close the active session first (if any)

Call `memodi_context` with `path` set to the current working directory. Do NOT pass `project`.

- **Resolved** (returns `observations` / `last_session`) → a workspace is registered here. Write a brief Goal / Accomplished summary from the actual conversation, load `memodi_session_end` (`ToolSearch("select:memodi_session_end")`), and call it with `path` and that summary. If the SessionStart protocol gave you a `client_session_id` earlier, pass it here too. Do this now: after the key is revoked in step 2, the SessionEnd hook can no longer close this session (it will get a 401).
- **`{"type": "not_started"}`** or **`{"type": "not_authenticated"}`** → no session to close. Continue to step 2.

## 2. Revoke the key

Load `memodi_logout` (`ToolSearch("select:memodi_logout")`) and call it with no arguments.

- Authenticated → it returns `{"revoked": true, "email": "<email>"}`. Note the email for step 4.
- **`{"type": "not_authenticated"}`** → the key was already dead. Say so in one line, then CONTINUE to step 3 anyway — local cleanup still needs to happen.

## 3. Local cleanup

Detect the shell rc file the same way `install.sh`'s `detect_rc` does, based on `$SHELL`: `zsh` → `~/.zshrc`; `bash` → `~/.bash_profile` if it exists, else `~/.bashrc`; anything else → `~/.profile`.

Strip the memodi-managed block from that file with the same awk pattern `install.sh`'s `persist_env` uses:

```bash
rc="<detected rc file>"
awk '
  /^# >>> memodi env >>>$/ {skip=1}
  skip==0 {print}
  /^# <<< memodi env <<<$/ {skip=0}
' "$rc" > "${rc}.memodi.tmp" && mv "${rc}.memodi.tmp" "$rc"
```

Then remove the MCP server registration:

```bash
claude mcp remove memodi --scope user 2>/dev/null || true
```

## 4. Confirm

Tell the user in a few short lines:

- Logged out (name the email from step 2, if you got one).
- Restart Claude Code for this to take effect.
- To log in again — same account or a different one — run `/memodi:login`.
- Memories are per-account: a different account starts with ITS memories (or none), never the previous one's.
