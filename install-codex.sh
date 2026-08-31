#!/bin/sh
# memodi — Plugin installer for Codex
#
# Installs the Codex plugin (MCP connection + memory skill) from the
# iam-oov/memodi marketplace and persists the credentials it references.
#
# Usage:
#   curl -sf https://raw.githubusercontent.com/iam-oov/memodi/main/install-codex.sh | sh
#
# Set MEMODI_API_KEY beforehand for a non-interactive/headless install.

set -e

MEMODI_BASE_URL="https://memodi.valdoh.com"
LOGIN_URL="${MEMODI_BASE_URL}/login"
LOGIN_HELPER_URL="https://raw.githubusercontent.com/iam-oov/memodi/main/plugin/claude-code/scripts/login_listener.py"

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

echo "[1/4] Logging in..."

if ! command -v codex >/dev/null 2>&1; then
  echo "Error: codex CLI not found. Install Codex first."
  exit 1
fi

if [ -n "$MEMODI_API_KEY" ]; then
  PROBE_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
    -X POST \
    -H "X-Memodi-Api-Key: ${MEMODI_API_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"path": "/", "query": "login probe"}' \
    "${MEMODI_BASE_URL}/hooks/prompt-search" 2>/dev/null) || PROBE_CODE=""
  if [ "$PROBE_CODE" = "401" ]; then
    echo "      MEMODI_API_KEY is no longer valid — starting a fresh login."
    MEMODI_API_KEY=""
  else
    echo "      Using MEMODI_API_KEY from your environment (no login needed)."
  fi
fi

if [ -z "$MEMODI_API_KEY" ] && command -v python3 >/dev/null 2>&1; then
  LOGIN_HELPER=$(mktemp "${TMPDIR:-/tmp}/memodi-login.XXXXXX")
  trap 'rm -f "$LOGIN_HELPER"' EXIT HUP INT TERM
  if curl -sf "$LOGIN_HELPER_URL" -o "$LOGIN_HELPER"; then
    LOGIN_OUT=$(python3 "$LOGIN_HELPER" "$LOGIN_URL") || LOGIN_OUT=""
    MEMODI_API_KEY="${LOGIN_OUT%% *}"
    MEMODI_EMAIL="${LOGIN_OUT#* }"
  fi
fi

if [ -z "$MEMODI_API_KEY" ]; then
  echo "memodi needs a per-user api key before it can be installed."
  echo "Log in with Google here if you don't have one yet:"
  echo ""
  echo "  ${LOGIN_URL}"
  echo ""
  if [ -r /dev/tty ]; then
    printf "Paste your memodi api key (mmd_...): " > /dev/tty
    stty -echo < /dev/tty 2>/dev/null || true
    read MEMODI_API_KEY < /dev/tty
    stty echo < /dev/tty 2>/dev/null || true
    printf "\n" > /dev/tty
  fi
fi

if [ -z "$MEMODI_API_KEY" ]; then
  echo "Error: no api key provided."
  echo "Run again and paste your key, or export MEMODI_API_KEY beforehand."
  exit 1
fi

if [ -n "$MEMODI_EMAIL" ]; then
  echo "Logged in as $MEMODI_EMAIL"
fi

MEMODI_MACHINE="${MEMODI_MACHINE:-$(hostname 2>/dev/null)}"
export MEMODI_API_KEY MEMODI_MACHINE

echo "[2/4] Persisting credentials for Codex..."
RC_FILE="$(detect_rc)"
persist_env "$RC_FILE"

echo "[3/4] Adding or refreshing the Memodi marketplace..."
if ! codex plugin marketplace add iam-oov/memodi --ref main; then
  codex plugin marketplace upgrade memodi
fi

echo "[4/4] Installing or updating the Memodi plugin..."
codex plugin add memodi@memodi

echo ""
echo "Done! Memodi is installed for Codex."
echo "Wrote MEMODI_API_KEY and MEMODI_MACHINE to ${RC_FILE}."
echo ""
echo "Next:"
echo "  1. Reload your shell:  source ${RC_FILE}   (or open a new terminal)"
echo "  2. Start a new Codex thread in the folder you want to remember."
echo '  3. Invoke $memodi and ask it to activate this workspace.'
