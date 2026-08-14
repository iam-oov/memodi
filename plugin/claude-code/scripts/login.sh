#!/bin/sh

set -e

MEMODI_BASE_URL="${MEMODI_BASE_URL:-https://memodi.valdoh.com}"
MEMODI_URL="${MEMODI_BASE_URL}/mcp"
LOGIN_URL="${MEMODI_BASE_URL}/login"

MARKER_START="# >>> memodi env >>>"
MARKER_END="# <<< memodi env <<<"

detect_rc() {
  case "$(basename "${SHELL:-/bin/sh}")" in
    zsh)  echo "$HOME/.zshrc" ;;
    bash) if [ -f "$HOME/.bash_profile" ]; then echo "$HOME/.bash_profile"; else echo "$HOME/.bashrc"; fi ;;
    *)    echo "$HOME/.profile" ;;
  esac
}

persist_env() {
  rc="$1"
  tmp="${rc}.memodi.tmp"
  touch "$rc"
  awk -v s="$MARKER_START" -v e="$MARKER_END" '
    $0==s {skip=1}
    skip==0 {print}
    $0==e {skip=0}
  ' "$rc" > "$tmp"
  {
    printf '%s\n' "$MARKER_START"
    printf 'export MEMODI_API_KEY="%s"\n' "$MEMODI_API_KEY"
    printf 'export MEMODI_MACHINE="%s"\n' "$MEMODI_MACHINE"
    printf '%s\n' "$MARKER_END"
  } >> "$tmp"
  mv "$tmp" "$rc"
}

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 not found. Install it to use /memodi:login."
  exit 1
fi

if ! command -v claude >/dev/null 2>&1; then
  echo "Error: claude CLI not found. Install Claude Code first."
  exit 1
fi

LOGIN_OUT=$(python3 "$(dirname "$0")/login_listener.py" "$LOGIN_URL") || LOGIN_OUT=""

if [ -z "$LOGIN_OUT" ]; then
  echo "Login timed out or failed. Run install.sh and paste your key manually."
  exit 1
fi

MEMODI_API_KEY="${LOGIN_OUT%% *}"
MEMODI_EMAIL="${LOGIN_OUT#* }"
MEMODI_MACHINE="${MEMODI_MACHINE:-$(hostname 2>/dev/null)}"

RC_FILE="$(detect_rc)"
persist_env "$RC_FILE"

claude mcp remove memodi --scope user 2>/dev/null || true
claude mcp add --transport http \
  -H "X-Memodi-Api-Key: ${MEMODI_API_KEY}" \
  -H "X-Memodi-Machine: ${MEMODI_MACHINE}" \
  --scope user \
  memodi "$MEMODI_URL"

echo "Logged in as ${MEMODI_EMAIL}"
echo "Updated ${RC_FILE} and the memodi MCP server entry."
echo "Restart Claude Code"
