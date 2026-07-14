#!/bin/sh
# memodi — Plugin installer for Claude Code
#
# Installs the memodi plugin (hooks + skills) and configures
# the MCP server connection to the shared production instance.
#
# Usage:
#   1. Sign up for an api key: https://memodi.valdoh.com/signup
#   2. ./install.sh          # prompts for the key (no echo)
#
# Or via curl:
#   curl -sf https://raw.githubusercontent.com/iam-oov/memodi/main/install.sh | sh
#
# The key is read from the terminal (never from argv), so it does not
# land in your shell history or scrollback. It is persisted to your
# shell rc file automatically so the plugin hooks work in future
# sessions. To run non-interactively, export MEMODI_API_KEY beforehand.

set -e

MEMODI_BASE_URL="https://memodi.valdoh.com"
MEMODI_URL="${MEMODI_BASE_URL}/mcp"
SIGNUP_URL="${MEMODI_BASE_URL}/signup"

MARKER_START="# >>> memodi env >>>"
MARKER_END="# <<< memodi env <<<"

# Pick the shell rc file to persist exports to.
detect_rc() {
  case "$(basename "${SHELL:-/bin/sh}")" in
    zsh)  echo "$HOME/.zshrc" ;;
    bash) if [ -f "$HOME/.bash_profile" ]; then echo "$HOME/.bash_profile"; else echo "$HOME/.bashrc"; fi ;;
    *)    echo "$HOME/.profile" ;;
  esac
}

# Rewrite the memodi-managed block in an rc file (idempotent).
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

echo "memodi needs a per-user api key before it can be installed."
echo "Sign up here if you don't have one yet:"
echo ""
echo "  ${SIGNUP_URL}"
echo ""

# --- Preflight checks ---
if ! command -v claude >/dev/null 2>&1; then
  echo "Error: claude CLI not found. Install Claude Code first."
  exit 1
fi

# --- Obtain the api key (prompt interactively, no echo) ---
if [ -z "$MEMODI_API_KEY" ]; then
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

MEMODI_MACHINE="${MEMODI_MACHINE:-$(hostname 2>/dev/null)}"

CLAUDE_SETTINGS="$HOME/.claude/settings.json"

echo "Installing memodi plugin for Claude Code..."

# --- Add marketplace ---
echo "[1/5] Adding marketplace..."
claude plugin marketplace add iam-oov/memodi 2>/dev/null || true

# --- Install plugin (hooks + skills) ---
echo "[2/5] Installing plugin..."
claude plugin install memodi@memodi 2>/dev/null || true

# --- Configure MCP server connection ---
echo "[3/5] Configuring MCP server..."
claude mcp remove memodi --scope user 2>/dev/null || true
claude mcp add --transport http \
  -H "X-Memodi-Api-Key: ${MEMODI_API_KEY}" \
  -H "X-Memodi-Machine: ${MEMODI_MACHINE}" \
  --scope user \
  memodi "$MEMODI_URL"

# --- Add wildcard permission for all memodi tools ---
echo "[4/5] Adding permissions..."
if [ -f "$CLAUDE_SETTINGS" ]; then
  if ! grep -q '"mcp__memodi__\*"' "$CLAUDE_SETTINGS" 2>/dev/null; then
    python3 -c "
import json, sys
with open('$CLAUDE_SETTINGS') as f:
    cfg = json.load(f)
allow = cfg.setdefault('permissions', {}).setdefault('allow', [])
if 'mcp__memodi__*' not in allow:
    allow.append('mcp__memodi__*')
with open('$CLAUDE_SETTINGS', 'w') as f:
    json.dump(cfg, f, indent=2)
    f.write('\n')
" 2>/dev/null || echo "  Warning: could not add permissions automatically. Add \"mcp__memodi__*\" to ~/.claude/settings.json manually."
  fi
else
  mkdir -p "$HOME/.claude"
  printf '{\n  "permissions": {\n    "allow": [\n      "mcp__memodi__*"\n    ]\n  }\n}\n' > "$CLAUDE_SETTINGS"
fi

# --- Persist env vars for the plugin hooks ---
# The hooks (session start, post-compaction, subagent capture) read
# MEMODI_API_KEY and MEMODI_MACHINE from the shell environment. Without
# them they stay silently inactive in future sessions.
echo "[5/5] Persisting environment to your shell rc..."
RC_FILE="$(detect_rc)"
persist_env "$RC_FILE"

echo ""
echo "Done! Wrote MEMODI_API_KEY and MEMODI_MACHINE to ${RC_FILE}."
echo ""
echo "Next:"
echo "  1. Reload your shell:   source ${RC_FILE}   (or open a new terminal)"
echo "  2. Restart Claude Code."
echo "  3. Register your workspace with"
echo "     memodi_workspace_start(path=<parent folder>, workspace=<name>)."
