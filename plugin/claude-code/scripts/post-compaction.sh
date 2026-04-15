#!/bin/sh
# Memodi — Post-compaction hook (compact)
#
# After context compaction, re-injects memory recovery protocol.
# Claude has lost all prior context — this is the lifeline.

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# --- Parse stdin JSON ---
INPUT=$(cat)
CWD=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null)
CWD="${CWD:-$PWD}"
PROJECT=$(basename "$CWD")

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
if ! curl -sf --max-time 1 $AUTH_HEADER "${MEMODI_URL}/mcp" > /dev/null 2>&1; then
  cat <<'EOF'
## Memodi — POST-COMPACTION (server unreachable)

Context was compacted and the memodi server is NOT reachable.
Memory recovery is not possible. Continue working but observations will NOT be persisted.
Check that MEMODI_API_KEY is set, or try: `docker compose up -d` for local dev.
EOF
  exit 0
fi

# --- Inject recovery protocol ---
cat <<EOF
## Memodi — POST-COMPACTION RECOVERY

Context was compacted. You have lost prior conversation context.
Follow these steps IMMEDIATELY and IN ORDER:

1. Call memodi_resolve_path with path: "${CWD}"
   to re-establish workspace context

2. Call memodi_context with project: "${PROJECT}"
   to recover recent observations, decisions, and session history

3. Read the returned observations carefully — they contain what was being worked on

4. Only THEN continue with the user's task

PROACTIVE SAVE REMINDER: After every decision, bug fix, discovery, convention, or user confirmation — call memodi_save immediately. Do NOT wait to be asked.
EOF

exit 0
