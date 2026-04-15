#!/bin/sh
# Memodi — Auth headers helper for MCP HTTP connection
#
# Called by Claude Code to get authentication headers.
# Reads MEMODI_API_KEY from environment.

if [ -z "$MEMODI_API_KEY" ]; then
  echo "{}"
  exit 0
fi

echo "{\"X-Api-Key\": \"$MEMODI_API_KEY\"}"
