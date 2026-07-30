#!/bin/sh
# Memodi — SessionEnd hook
#
# Hygiene close only: closes ONLY the session carrying this exact Claude
# Code session id, with a NULL summary, over plain HTTP — no MCP client,
# no python `mcp` dependency (that package lives only in the project venv,
# not system python3; see plugin/hook-mcp-dependency-broken). Fire and
# forget: never blocks Claude Code exiting, always exits 0. Matching by id
# means a wrong/absent id, or an already-closed session, is a silent
# no-op — it can never close a different window's session.
#
# stdin JSON fields:
#   cwd — current working directory
#   session_id — the Claude Code session id (matches client_session_id)
#   reason — clear|resume|logout|prompt_input_exit|bypass_permissions_disabled|other

# --- Parse stdin JSON ---
INPUT=$(cat)
CWD=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null)
SESSION_ID=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null)
REASON=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('reason',''))" 2>/dev/null)

CWD="${CWD:-$PWD}"

# On resume the conversation continues elsewhere and SessionStart will not
# re-fire for it — closing here would end a session still in use.
[ "$REASON" = "resume" ] && exit 0

# --- Server URL (env var or production default) ---
MEMODI_URL="${MEMODI_URL:-https://memodi.valdoh.com}"

# --- Build auth headers (per-user api key + machine identity) ---
MACHINE="${MEMODI_MACHINE:-$(hostname 2>/dev/null)}"
AUTH_HEADERS=""
[ -n "$MEMODI_API_KEY" ] && AUTH_HEADERS="-H X-Memodi-Api-Key:${MEMODI_API_KEY}"
[ -n "$MACHINE" ] && AUTH_HEADERS="$AUTH_HEADERS -H X-Memodi-Machine:${MACHINE}"

# --- Check connectivity before attempting the close ---
if ! curl -s -o /dev/null --max-time 2 $AUTH_HEADERS "${MEMODI_URL}/mcp" 2>/dev/null; then
  exit 0  # Server not reachable, skip silently
fi

# --- Close via plain HTTP (opt-in inert: not_started / no_match are no-ops) ---
PAYLOAD=$(CWD="$CWD" SESSION_ID="$SESSION_ID" python3 -c "
import json, os
print(json.dumps({'path': os.environ['CWD'], 'client_session_id': os.environ['SESSION_ID']}))
" 2>/dev/null)
curl -s -o /dev/null --max-time 5 -X POST $AUTH_HEADERS \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  "${MEMODI_URL}/hooks/session-close" 2>/dev/null

exit 0
