#!/bin/sh
# Memodi — SubagentStop hook
#
# Captures key learnings from subagent output and saves them to memodi.
# Runs async — does not block Claude.
#
# stdin JSON fields:
#   last_assistant_message — the subagent's final reply text
#   cwd — current working directory
#   agent_type — type of subagent (Explore, Plan, etc.)

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# --- Server URL (env var or production default) ---
MEMODI_URL="${MEMODI_URL:-https://62-238-15-94.sslip.io}"

# --- Parse stdin JSON ---
INPUT=$(cat)
MESSAGE=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('last_assistant_message',''))" 2>/dev/null)
CWD=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null)
AGENT_TYPE=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('agent_type',''))" 2>/dev/null)

# Nothing to capture
[ -z "$MESSAGE" ] && exit 0

CWD="${CWD:-$PWD}"
PROJECT=$(basename "$CWD")

# --- Extract key sections ---
EXTRACTED=$(printf '%s' "$MESSAGE" | python3 -c "
import re, sys

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
    print('\n\n'.join(sections))
" 2>/dev/null)

# Nothing meaningful extracted
[ -z "$EXTRACTED" ] && exit 0


# --- Build auth header ---
AUTH_HEADER=""
[ -n "$MEMODI_API_KEY" ] && AUTH_HEADER="-H X-Api-Key:${MEMODI_API_KEY}"

# --- Check connectivity before attempting save ---
if ! curl -sf --max-time 2 $AUTH_HEADER "${MEMODI_URL}/mcp" > /dev/null 2>&1; then
  exit 0  # Server not reachable, skip silently
fi

# --- Save via MCP protocol ---
TITLE="Subagent (${AGENT_TYPE}) findings"
python3 "${PLUGIN_ROOT}/scripts/mcp-capture.py" \
  "$MEMODI_URL" "$PROJECT" "$TITLE" "$EXTRACTED" 2>/dev/null

exit 0
