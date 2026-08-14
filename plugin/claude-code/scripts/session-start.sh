#!/bin/sh
# Memodi — SessionStart hook (startup|clear)
#
# 1. Reads cwd + session_id from stdin JSON
# 2. Checks if memodi server is reachable
# 3. Injects a context-loading protocol, resolved ONCE per session
# 4. Opens the memodi session over plain HTTP (/hooks/session-start),
#    tagged with this Claude Code session id — deterministic, does not
#    depend on the model remembering to call memodi_session_start
#
# The protocol is emitted BEFORE the POST on purpose: it is the part the
# session cannot work without, and a slow or failing POST must never cost
# it. The POST is fire-and-forget, so its outcome is never asserted.

# --- Parse stdin JSON ---
INPUT=$(cat)
CWD=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null)
SESSION_ID=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null)
CWD="${CWD:-$PWD}"

# --- Server URL (env var or production default) ---
MEMODI_URL="${MEMODI_URL:-https://memodi.valdoh.com}"

# --- Build auth headers (per-user api key + machine identity) ---
MACHINE="${MEMODI_MACHINE:-$(hostname 2>/dev/null)}"
AUTH_HEADERS=""
[ -n "$MEMODI_API_KEY" ] && AUTH_HEADERS="-H X-Memodi-Api-Key:${MEMODI_API_KEY}"
[ -n "$MACHINE" ] && AUTH_HEADERS="$AUTH_HEADERS -H X-Memodi-Machine:${MACHINE}"

if [ -z "$MEMODI_API_KEY" ]; then
  cat <<'EOF'
## Memodi — not logged in

Tell the user in ONE short line: run /memodi:login (restart afterwards).
Then continue and do not mention memodi again.
EOF
  exit 0
fi

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

# --- Session-close arguments ---
# Claude Code does not always put session_id on stdin. Name client_session_id
# ONLY when there is a real one: telling the model to pass "" would make it
# pass the untagged identity, which is not this window's session.
if [ -n "$SESSION_ID" ]; then
  CLOSE_ARGS="path: \"${CWD}\", client_session_id: \"${SESSION_ID}\", and a
structured summary (Goal / Accomplished / Next Steps). Passing
client_session_id targets THIS window's own session — concurrent windows in
the same folder each have their own, and leaving it out risks closing
another window's."
else
  CLOSE_ARGS="path: \"${CWD}\" and a structured summary (Goal / Accomplished
/ Next Steps)."
fi

# --- Inject context-loading protocol ---
cat <<EOF
## Memodi Memory — Session Start (resolve silently)

This hook manages the memodi session for this workspace — do NOT call
memodi_session_start yourself. Resolve context ONCE this session, before
responding. Do this SILENTLY — no status line, no announcement, no mention
of memodi.

1. Call memodi_context with path: "${CWD}" — do NOT pass project; let
   memodi derive it from path.

   - Resolved (returns observations / last_session): the workspace is
     registered here. Read the last session summary and the returned
     observations for context. Carry the context into your work — do NOT
     print or narrate any of this.

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
ends, call memodi_session_end with ${CLOSE_ARGS} A SessionEnd hook also
runs on exit as a hygiene net, but it can NEVER write a summary — calling
memodi_session_end yourself (or the user running /memodi:end) is the only
way the next session gets a real recap instead of just a truthfully closed
row.
EOF

# --- Open the session over plain HTTP (opt-in inert: not_started/not_authenticated are no-ops) ---
PAYLOAD=$(CWD="$CWD" SESSION_ID="$SESSION_ID" python3 -c "
import json, os
payload = {'path': os.environ['CWD']}
if os.environ.get('SESSION_ID'):
    payload['client_session_id'] = os.environ['SESSION_ID']
print(json.dumps(payload))
" 2>/dev/null)
curl -s -o /dev/null --max-time 5 -X POST $AUTH_HEADERS \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  "${MEMODI_URL}/hooks/session-start" 2>/dev/null

exit 0
