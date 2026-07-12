#!/bin/sh
# Memodi — Post-compaction hook (compact)
#
# After context compaction, re-injects memory recovery protocol.
# Claude has lost all prior context — this is the lifeline.

# --- Parse stdin JSON ---
INPUT=$(cat)
CWD=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null)
CWD="${CWD:-$PWD}"

# --- Server URL (env var or production default) ---
MEMODI_URL="${MEMODI_URL:-https://62-238-15-94.sslip.io}"

# --- Build auth headers (per-user api key + machine identity) ---
MACHINE="${MEMODI_MACHINE:-$(hostname 2>/dev/null)}"
AUTH_HEADERS=""
[ -n "$MEMODI_API_KEY" ] && AUTH_HEADERS="-H X-Memodi-Api-Key:${MEMODI_API_KEY}"
[ -n "$MACHINE" ] && AUTH_HEADERS="$AUTH_HEADERS -H X-Memodi-Machine:${MACHINE}"

# --- Check connectivity ---
if ! curl -s -o /dev/null --max-time 1 $AUTH_HEADERS "${MEMODI_URL}/mcp" 2>/dev/null; then
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

1. Call memodi_context with path: "${CWD}" to re-establish workspace context
   — do NOT pass project; never self-derive a project name, let memodi
   derive it from path.
   - {"type": "not_started"} -> tell the user ONCE: "memodi inactive here,
     run memodi_workspace_start", then continue without memory. Do NOT
     repeat this warning on later saves this session.
   - {"type": "not_authenticated"} -> tell the user once that the api key
     is missing or invalid, then continue without memory.

2. Read the returned observations carefully — they contain what was being worked on

3. Only THEN continue with the user's task

PROACTIVE SAVE REMINDER: After every decision, bug fix, discovery, convention, or user confirmation — call memodi_save (path: "${CWD}") immediately. Do NOT wait to be asked.
EOF

exit 0
