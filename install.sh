#!/bin/sh
# memodi — Plugin installer for Claude Code
#
# Installs the memodi plugin (hooks + skills) and configures
# the MCP server connection to the shared production instance.
#
# Usage:
#   export MEMODI_API_KEY="your-api-key"
#   ./install.sh
#
# Or via curl:
#   export MEMODI_API_KEY="your-api-key"
#   curl -sf https://raw.githubusercontent.com/iam-oov/memodi/main/install.sh | sh

set -e

# --- Preflight checks ---
if ! command -v claude >/dev/null 2>&1; then
  echo "Error: claude CLI not found. Install Claude Code first."
  exit 1
fi

if [ -z "$MEMODI_API_KEY" ]; then
  echo "Error: MEMODI_API_KEY is not set."
  echo ""
  echo "  export MEMODI_API_KEY=\"your-api-key\""
  echo "  ./install.sh"
  exit 1
fi

MEMODI_URL="https://62-238-15-94.sslip.io/mcp"

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
  -H "X-Api-Key: ${MEMODI_API_KEY}" \
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
echo "Done! Restart Claude Code to activate memodi."
echo ""
echo "Tip: add this to your ~/.zshrc or ~/.bashrc so the key"
echo "persists across sessions:"
echo ""
echo "  export MEMODI_API_KEY=\"${MEMODI_API_KEY}\""
