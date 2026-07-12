#!/bin/sh
# memodi — Plugin installer for Claude Code
#
# Installs the memodi plugin (hooks + skills) and configures
# the MCP server connection to the shared production instance.
#
# Usage:
#   1. Sign up for an api key: https://memodi.valdoh.com/signup
#   2. export MEMODI_API_KEY="mmd_..."
#   3. ./install.sh
#
# Or via curl:
#   export MEMODI_API_KEY="mmd_..."
#   curl -sf https://raw.githubusercontent.com/iam-oov/memodi/main/install.sh | sh

set -e

MEMODI_BASE_URL="https://memodi.valdoh.com"
MEMODI_URL="${MEMODI_BASE_URL}/mcp"
SIGNUP_URL="${MEMODI_BASE_URL}/signup"

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

if [ -z "$MEMODI_API_KEY" ]; then
  echo "Error: MEMODI_API_KEY is not set."
  echo ""
  echo "  export MEMODI_API_KEY=\"mmd_...\"   # from ${SIGNUP_URL}"
  echo "  ./install.sh"
  exit 1
fi

MEMODI_MACHINE="${MEMODI_MACHINE:-$(hostname 2>/dev/null)}"

CLAUDE_SETTINGS="$HOME/.claude/settings.json"

echo "Installing memodi plugin for Claude Code..."

# --- Add marketplace ---
echo "[1/4] Adding marketplace..."
claude plugin marketplace add iam-oov/memodi 2>/dev/null || true

# --- Install plugin (hooks + skills) ---
echo "[2/4] Installing plugin..."
claude plugin install memodi@memodi 2>/dev/null || true

# --- Configure MCP server connection ---
echo "[3/4] Configuring MCP server..."
claude mcp remove memodi --scope user 2>/dev/null || true
claude mcp add --transport http \
  -H "X-Memodi-Api-Key: ${MEMODI_API_KEY}" \
  -H "X-Memodi-Machine: ${MEMODI_MACHINE}" \
  --scope user \
  memodi "$MEMODI_URL"

# --- Add wildcard permission for all memodi tools ---
echo "[4/4] Adding permissions..."
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

echo ""
echo "Done! Restart Claude Code, then register your workspace with"
echo "memodi_workspace_start(path=<parent folder>, workspace=<name>)."
echo ""
echo "REQUIRED: the plugin hooks (session start, post-compaction, and"
echo "subagent capture) read MEMODI_API_KEY and MEMODI_MACHINE from your"
echo "shell environment. Add these to your ~/.zshrc or ~/.bashrc — without"
echo "them the hooks and subagent capture stay silently INACTIVE in future"
echo "sessions:"
echo ""
echo "  export MEMODI_API_KEY=\"${MEMODI_API_KEY}\""
echo "  export MEMODI_MACHINE=\"${MEMODI_MACHINE}\""
