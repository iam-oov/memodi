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

echo "Installing memodi plugin for Claude Code..."

# --- Add marketplace ---
echo "[1/3] Adding marketplace..."
claude plugin marketplace add iam-oov/memodi 2>/dev/null || true

# --- Install plugin (hooks + skills) ---
echo "[2/3] Installing plugin..."
claude plugin install memodi@memodi 2>/dev/null || true

# --- Configure MCP server connection ---
echo "[3/3] Configuring MCP server..."
claude mcp remove memodi --scope user 2>/dev/null || true
claude mcp add --transport http \
  -H "X-Api-Key: ${MEMODI_API_KEY}" \
  --scope user \
  memodi "$MEMODI_URL"

echo ""
echo "Done! Restart Claude Code to activate memodi."
echo ""
echo "Tip: add this to your ~/.zshrc or ~/.bashrc so the key"
echo "persists across sessions:"
echo ""
echo "  export MEMODI_API_KEY=\"${MEMODI_API_KEY}\""
