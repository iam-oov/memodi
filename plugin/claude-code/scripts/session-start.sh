#!/bin/sh
# Memodi — SessionStart hook (startup|clear)
#
# 1. Reads cwd from stdin JSON
# 2. Checks if memodi server is reachable
# 3. Injects workspace detection + context loading protocol

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# --- Parse stdin JSON ---
INPUT=$(cat)
CWD=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null)
CWD="${CWD:-$PWD}"

# --- Detect server URL from .mcp.json ---
MEMODI_URL=""
if [ -f "${PLUGIN_ROOT}/.mcp.json" ]; then
  MEMODI_URL=$(python3 -c "
import json
with open('${PLUGIN_ROOT}/.mcp.json') as f:
    cfg = json.load(f)
url = cfg.get('mcpServers',{}).get('memodi',{}).get('url','')
print(url.rsplit('/mcp',1)[0] if url.endswith('/mcp') else url)
" 2>/dev/null)
fi
MEMODI_URL="${MEMODI_URL:-http://localhost:8787}"

# --- Build auth header ---
AUTH_HEADER=""
[ -n "$MEMODI_API_KEY" ] && AUTH_HEADER="-H X-Api-Key:${MEMODI_API_KEY}"

# --- Check connectivity ---
if ! curl -sf --max-time 2 $AUTH_HEADER "${MEMODI_URL}/mcp" > /dev/null 2>&1; then
  cat <<'EOF'
## Memodi — CONNECTION FAILED

The memodi server is not reachable. Memory tools will NOT work this session.

Possible fixes:
- Check that MEMODI_API_KEY is set in your environment
- Verify the server is up: `curl -sf https://62-238-15-94.sslip.io/mcp`
- For local dev: `docker compose up -d` in the memodi repo

You can still work normally, but observations will NOT be persisted.
EOF
  exit 0
fi

# --- Inject session protocol ---
cat <<EOF
## Memodi Memory — Session Started

Workspace detection needed. Execute these steps NOW, before responding to the user:

1. Call memodi_resolve_path with path: "${CWD}"
   - If resolved: true -> workspace is known, proceed to step 2
   - If resolved: false -> follow WORKSPACE ONBOARDING in the memodi skill

2. Call memodi_context with the project name (last component of the path)
   to load recent observations and the last session summary

3. Load session tools via ToolSearch("select:memodi_session_start")
   then call memodi_session_start with the project name

PROACTIVE SAVE REMINDER: After every decision, bug fix, discovery, convention, or user confirmation — call memodi_save immediately. Do NOT wait to be asked.

SESSION CLOSE REMINDER: Before the conversation ends, call memodi_session_end with a structured summary (Goal / Accomplished / Next Steps).
EOF

exit 0
