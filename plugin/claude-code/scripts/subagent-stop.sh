#!/bin/sh
# Memodi — SubagentStop hook
#
# Captures key learnings from subagent output and saves them to memodi via
# plain HTTP (/hooks/capture) — no MCP client, no python `mcp` dependency
# (that package lives only in the project venv, not system python3; see
# plugin/hook-mcp-dependency-broken). Runs async — does not block Claude.
# Opt-in inert: if the caller's path has no registered workspace
# (not_started), the route is a silent no-op — no spam, no error surfaced
# to the user.
#
# stdin JSON fields:
#   last_assistant_message — the subagent's final reply text
#   cwd — current working directory
#   agent_type — type of subagent (Explore, Plan, etc.)

# --- Server URL (env var or production default) ---
MEMODI_URL="${MEMODI_URL:-https://memodi.valdoh.com}"

[ -z "$MEMODI_API_KEY" ] && exit 0

# --- Parse stdin JSON ---
INPUT=$(cat)
MESSAGE=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('last_assistant_message',''))" 2>/dev/null)
CWD=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null)
AGENT_TYPE=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('agent_type',''))" 2>/dev/null)

# Nothing to capture
[ -z "$MESSAGE" ] && exit 0

CWD="${CWD:-$PWD}"

# --- Extract key sections ---
# Truncated because the server caps the capture body at 64KB: a long
# subagent reply must degrade to a shorter observation, never to a rejected
# request (and never to a multi-megabyte POST).
MAX_CONTENT=32768
EXTRACTED=$(printf '%s' "$MESSAGE" | MAX_CONTENT="$MAX_CONTENT" python3 -c "
import os, re, sys

content = sys.stdin.read()
sections = []

# Match markdown headers with key content
patterns = [
    r'##\s+(?:Key\s+)?Learn(?:ings?)?\s*\n(.*?)(?=\n##\s|\Z)',
    r'##\s+Summ(?:ary|ario)\s*\n(.*?)(?=\n##\s|\Z)',
    r'##\s+Discover(?:ies|y|imientos?)\s*\n(.*?)(?=\n##\s|\Z)',
    r'##\s+Accomplish(?:ed|ments?)\s*\n(.*?)(?=\n##\s|\Z)',
    r'##\s+Completado\s*\n(.*?)(?=\n##\s|\Z)',
    r'##\s+Resumen\s*\n(.*?)(?=\n##\s|\Z)',
]

for pattern in patterns:
    for match in re.finditer(pattern, content, re.DOTALL | re.IGNORECASE):
        text = match.group(1).strip()
        if text and len(text) > 20:  # skip trivially short sections
            sections.append(text)

if sections:
    joined = '\n\n'.join(sections)
    limit = int(os.environ['MAX_CONTENT'])
    if len(joined) > limit:
        joined = joined[:limit] + '\n\n[truncated by the memodi SubagentStop hook]'
    print(joined)
" 2>/dev/null)

# Nothing meaningful extracted
[ -z "$EXTRACTED" ] && exit 0

# --- Build auth headers (per-user api key + machine identity) ---
MACHINE="${MEMODI_MACHINE:-$(hostname 2>/dev/null)}"
AUTH_HEADERS=""
[ -n "$MEMODI_API_KEY" ] && AUTH_HEADERS="-H X-Memodi-Api-Key:${MEMODI_API_KEY}"
[ -n "$MACHINE" ] && AUTH_HEADERS="$AUTH_HEADERS -H X-Memodi-Machine:${MACHINE}"

# --- Check connectivity before attempting save ---
if ! curl -s -o /dev/null --max-time 2 $AUTH_HEADERS "${MEMODI_URL}/mcp" 2>/dev/null; then
  exit 0  # Server not reachable, skip silently
fi

# --- Save via plain HTTP (opt-in inert: not_started exits silently) ---
# No topic_key: it would upsert, so every later capture in the project would
# overwrite the same single row. Each capture is its own observation; the
# server's content-hash dedup already collapses exact repeats.
TITLE="Subagent (${AGENT_TYPE}) findings"
PAYLOAD=$(CWD="$CWD" TITLE="$TITLE" CONTENT="$EXTRACTED" python3 -c "
import json, os
print(json.dumps({
    'path': os.environ['CWD'],
    'title': os.environ['TITLE'],
    'content': os.environ['CONTENT'],
}))
" 2>/dev/null)
curl -s -o /dev/null --max-time 10 -X POST $AUTH_HEADERS \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  "${MEMODI_URL}/hooks/capture" 2>/dev/null

exit 0
