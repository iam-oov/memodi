#!/bin/sh
# memodi — Uninstaller for Codex
#
# Usage:
#   curl -sf https://raw.githubusercontent.com/iam-oov/memodi/main/uninstall-codex.sh | sh

set -e

if ! command -v codex >/dev/null 2>&1; then
  echo "Error: codex CLI not found."
  exit 1
fi

echo "Uninstalling memodi from Codex..."
codex plugin remove memodi@memodi 2>/dev/null || true
codex plugin marketplace remove memodi 2>/dev/null || true

echo ""
echo "Done! Start a new Codex thread to apply the change."
echo ""
echo "Note: MEMODI_API_KEY and MEMODI_MACHINE in your shell profile"
echo "were not removed. Delete the memodi marker block manually if"
echo "you no longer use Memodi with another client."
