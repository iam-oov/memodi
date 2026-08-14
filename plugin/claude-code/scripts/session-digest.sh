#!/bin/sh
# Memodi — SessionStart digest hook (startup|clear)
#
# Fetches a preformatted recap of the workspace's recent activity from
# /hooks/digest and shows it to the USER via systemMessage. This is the
# visible counterpart to session-start.sh, whose output is context for the
# model only. Silent on every failure — no api key, unreachable server,
# unregistered path, or an empty digest must never add noise to startup.

INPUT=$(cat)
CWD=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null)
CWD="${CWD:-$PWD}"

[ -z "$MEMODI_API_KEY" ] && exit 0

MEMODI_URL="${MEMODI_URL:-https://memodi.valdoh.com}"
MACHINE="${MEMODI_MACHINE:-$(hostname 2>/dev/null)}"

AUTH_HEADERS="-H X-Memodi-Api-Key:${MEMODI_API_KEY}"
[ -n "$MACHINE" ] && AUTH_HEADERS="$AUTH_HEADERS -H X-Memodi-Machine:${MACHINE}"

PAYLOAD=$(CWD="$CWD" python3 -c "
import json, os
print(json.dumps({'path': os.environ['CWD']}))
" 2>/dev/null)
[ -z "$PAYLOAD" ] && exit 0

RESPONSE=$(curl -s --max-time 5 -X POST $AUTH_HEADERS \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  "${MEMODI_URL}/hooks/digest" 2>/dev/null)
[ -z "$RESPONSE" ] && exit 0

printf '%s' "$RESPONSE" | python3 -c "
import json, sys
try:
    digest = json.load(sys.stdin).get('digest', '')
except Exception:
    digest = ''
if isinstance(digest, str) and digest.strip():
    print(json.dumps({'systemMessage': digest}))
" 2>/dev/null

exit 0
