#!/bin/sh
# Memodi — SessionStart hook (startup|clear)
#
# 1. Reads cwd from stdin JSON
# 2. Checks if memodi server is reachable
# 3. Injects a workspace-resolution + context-loading protocol,
#    resolved ONCE per session — no per-save re-checks

# --- Parse stdin JSON ---
INPUT=$(cat)
CWD=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null)
CWD="${CWD:-$PWD}"

# --- Server URL (env var or production default) ---
MEMODI_URL="${MEMODI_URL:-https://memodi.valdoh.com}"

# --- Build auth headers (per-user api key + machine identity) ---
MACHINE="${MEMODI_MACHINE:-$(hostname 2>/dev/null)}"
AUTH_HEADERS=""
[ -n "$MEMODI_API_KEY" ] && AUTH_HEADERS="-H X-Memodi-Api-Key:${MEMODI_API_KEY}"
[ -n "$MACHINE" ] && AUTH_HEADERS="$AUTH_HEADERS -H X-Memodi-Machine:${MACHINE}"

# --- Check connectivity ---
if ! curl -s -o /dev/null --max-time 2 $AUTH_HEADERS "${MEMODI_URL}/mcp" 2>/dev/null; then
  cat <<'EOF'
## Memodi — CONNECTION FAILED

The memodi server is not reachable. Memory tools will NOT work this session.

Possible fixes:
- Check that MEMODI_API_KEY is set in your environment
- Verify the server is up: `curl -s -o /dev/null -w '%{http_code}\n' https://memodi.valdoh.com/mcp` (any HTTP code means reachable)
- For local dev: `docker compose up -d` in the memodi repo

You can still work normally, but observations will NOT be persisted.
EOF
  exit 0
fi

# --- Inject session protocol ---
cat <<EOF
## Memodi Memory — Session Start (resolve silently)

Resolve the workspace ONCE this session, before responding. Do this
SILENTLY — no status line, no announcement, no mention of memodi.

1. Call memodi_context with path: "${CWD}" — do NOT pass project; let
   memodi derive it from path.

   - Resolved (returns observations / last_session): the workspace is
     registered here. Read the last session summary and the returned
     observations for context, then load session tools via
     ToolSearch("select:memodi_session_start") and call
     memodi_session_start with path: "${CWD}". Carry the context into
     your work — do NOT print or narrate any of this.

   - {"type": "not_started"}: this path is NOT registered on this machine.
     Do NOTHING and say NOTHING. Do not warn, do not suggest a command, do
     not mention memodi at all. Memory stays dormant until the user runs
     /memodi:start. Never re-check this during the session.

   - {"type": "not_authenticated"}: the api key is missing or invalid.
     State this in ONE short line, then continue without memory. Do not
     repeat it on later calls.

PROACTIVE SAVE (only if the workspace resolved): after any decision, bug
fix, discovery, convention, or user confirmation, call memodi_save
(path: "${CWD}") immediately — no announcement needed. If not_started,
skip saves silently.

SESSION CLOSE (only if the workspace resolved): before the conversation
ends, call memodi_session_end with path: "${CWD}" and a structured summary
(Goal / Accomplished / Next Steps).
EOF

exit 0
