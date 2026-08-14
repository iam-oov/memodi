---
description: Re-login to memodi without restarting from scratch — opens a browser, no key to paste.
allowed-tools: Bash
---

The user ran `/memodi:login` to obtain a fresh memodi api key.

- Login script: !`echo "${CLAUDE_PLUGIN_ROOT}/scripts/login.sh"`

Work through these steps in order. Be terse — this is an activation command, not a conversation.

## 1. Warn before opening the browser

Tell the user in ONE line that a browser tab is about to open to log in.

## 2. Run the script

Run the resolved script path above via the Bash tool with an explicit `timeout` of `300000` (5 minutes — the listener itself waits up to 180s for the OAuth round-trip, and the Bash tool's default 120s timeout would kill it mid-flow).

- NEVER ask the user for the api key.
- NEVER print, echo, or repeat the key yourself, anywhere.
- NEVER read the shell rc file or `~/.claude.json` to "verify" the result — the script's own output IS the confirmation.

## 3. Relay the result

- **Exit 0** → relay the script's stdout verbatim (it is already exactly: `Logged in as <email>`, a line naming what got updated, and `Restart Claude Code`). Do not add anything else.
- **Exit non-zero** → relay the script's message verbatim (it already names `install.sh` as the fallback). Do not retry automatically.
- **Bash tool itself reports the command unreachable or errors unexpectedly** → tell the user to open `https://memodi.valdoh.com/login` directly in a browser instead.

## 4. Confirm

End with a reminder to restart Claude Code.
