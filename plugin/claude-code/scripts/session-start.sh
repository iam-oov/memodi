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
## Memodi Memory — Session Started

Resolve the workspace ONCE this session, before responding to the user:

1. Call memodi_context with path: "${CWD}" — do NOT pass project; never
   self-derive a project name, let memodi derive it from path.
   - {"type": "not_started"} -> this path has no registered workspace on
     this machine. Tell the user ONCE: "memodi inactive here, run
     memodi_workspace_start" — then keep working normally without memory.
     Do NOT re-check or repeat this warning on later saves this session.
   - {"type": "not_authenticated"} -> the configured api key is missing or
     invalid. Tell the user once, then keep working without memory.
   - Otherwise -> the workspace is resolved; proceed to step 2.

2. Read the returned observations and the last session summary for context.

3. Load session tools via ToolSearch("select:memodi_session_start")
   then call memodi_session_start with path: "${CWD}"

PROACTIVE SAVE REMINDER: After every decision, bug fix, discovery, convention, or user confirmation — call memodi_save (path: "${CWD}") immediately. Do NOT wait to be asked. If the workspace was not_started, skip saves silently — do not repeat the warning.

SESSION CLOSE REMINDER: Before the conversation ends, call memodi_session_end with path: "${CWD}" and a structured summary (Goal / Accomplished / Next Steps).
EOF

exit 0
