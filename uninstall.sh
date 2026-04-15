#!/bin/sh
# memodi — Uninstaller for Claude Code
#
# Removes the MCP server, plugin, and marketplace.
#
# Usage:
#   curl -sf https://raw.githubusercontent.com/iam-oov/memodi/main/uninstall.sh | sh

set -e

if ! command -v claude >/dev/null 2>&1; then
  echo "Error: claude CLI not found."
  exit 1
fi

echo "Uninstalling memodi from Claude Code..."

# --- Remove MCP server ---
echo "[1/3] Removing MCP server..."
claude mcp remove memodi --scope user 2>/dev/null || true

# --- Uninstall plugin ---
echo "[2/3] Uninstalling plugin..."
claude plugin uninstall memodi@memodi --scope user 2>/dev/null || true

# --- Remove marketplace ---
echo "[3/3] Removing marketplace..."
claude plugin marketplace remove memodi 2>/dev/null || true

echo ""
echo "Done! Restart Claude Code to apply changes."
echo ""
echo "Note: MEMODI_API_KEY in your shell profile (~/.zshrc or ~/.bashrc)"
echo "was not removed. Delete it manually if you no longer need it."
