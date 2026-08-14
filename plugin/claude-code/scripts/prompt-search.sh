#!/bin/sh
# Memodi — UserPromptSubmit hook
#
# On every user prompt, runs a keyword (tsvector) search across the whole
# workspace and injects compact pointers to prior related observations —
# id/type/title/topic_key/project only, never content. Fires on every
# prompt; a short or unmatchable prompt yields no rows server-side, so
# nothing is injected. Never blocks the turn: any failure (server down,
# not_started, timeout, malformed response) exits 0 with no output, so the
# prompt proceeds unchanged.
#
# stdin JSON fields:
#   prompt — the user's prompt text
#   cwd — current working directory

MEMODI_URL="${MEMODI_URL:-https://memodi.valdoh.com}"

[ -z "$MEMODI_API_KEY" ] && exit 0

# --- Parse stdin JSON ---
INPUT=$(cat)
PROMPT=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('prompt',''))" 2>/dev/null)
CWD=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null)

# Nothing to search
[ -z "$PROMPT" ] && exit 0

CWD="${CWD:-$PWD}"

# --- Truncate the prompt before it ever leaves this machine ---
MAX_QUERY=2000
QUERY=$(printf '%s' "$PROMPT" | MAX_QUERY="$MAX_QUERY" python3 -c "
import os, sys
text = sys.stdin.read()
print(text[: int(os.environ['MAX_QUERY'])])
" 2>/dev/null)

# --- Build auth headers (per-user api key + machine identity) ---
MACHINE="${MEMODI_MACHINE:-$(hostname 2>/dev/null)}"
AUTH_HEADERS=""
[ -n "$MEMODI_API_KEY" ] && AUTH_HEADERS="-H X-Memodi-Api-Key:${MEMODI_API_KEY}"
[ -n "$MACHINE" ] && AUTH_HEADERS="$AUTH_HEADERS -H X-Memodi-Machine:${MACHINE}"

# --- Check connectivity before searching ---
if ! curl -s -o /dev/null --max-time 2 $AUTH_HEADERS "${MEMODI_URL}/mcp" 2>/dev/null; then
  exit 0  # Server not reachable, skip silently
fi

# --- Search via plain HTTP ---
PAYLOAD=$(CWD="$CWD" QUERY="$QUERY" python3 -c "
import json, os
print(json.dumps({
    'path': os.environ['CWD'],
    'query': os.environ['QUERY'],
}))
" 2>/dev/null)
RESPONSE=$(curl -s --max-time 5 -X POST $AUTH_HEADERS \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  "${MEMODI_URL}/hooks/prompt-search" 2>/dev/null)

# --- Render pointers, or print nothing on empty/error/unparseable response ---
printf '%s' "$RESPONSE" | python3 -c "
import json, sys

try:
    rows = json.load(sys.stdin)
except Exception:
    sys.exit(0)

if not isinstance(rows, list) or not rows:
    sys.exit(0)

print('## Related memory (memodi — keyword match)')
print(
    'These prior observations may bear on the request. '
    'Call memodi_get_observation(id) before relying on any.'
)
for row in rows:
    topic_key = row.get('topic_key')
    if topic_key:
        tail = 'topic: ' + str(topic_key) + ' · project: ' + str(row.get('project'))
    else:
        tail = 'project: ' + str(row.get('project'))
    print(
        '- [' + str(row.get('type')) + '] ' + str(row.get('title'))
        + ' — ' + tail + ' (id: ' + str(row.get('id')) + ')'
    )
" 2>/dev/null

exit 0
