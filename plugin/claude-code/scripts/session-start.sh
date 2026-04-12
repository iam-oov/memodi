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

# --- Check connectivity ---
if ! curl -sf --max-time 2 "${MEMODI_URL}/mcp" > /dev/null 2>&1; then
  cat <<'EOF'
## Memodi — CONNECTION FAILED

The memodi server is not reachable. Memory tools will NOT work this session.

Possible fixes:
- Run `docker compose up -d` in the memodi repo
- Check if port 8787 is in use: `lsof -i :8787`
- Check Docker: `docker ps | grep memodi`

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
   to load recent observations and decisions

PROACTIVE SAVE REMINDER: After every decision, bug fix, discovery, convention, or user confirmation — call memodi_save immediately. Do NOT wait to be asked.
EOF

exit 0
