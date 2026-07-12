#!/usr/bin/env python3
"""Save an observation to memodi via MCP streamable-http.

Usage: mcp-capture.py <url> <path> <title> <content>

Uses the MCP client library to call memodi_save through the proper
protocol (initialize -> tools/call). Designed to be called from hooks.

Opt-in inert: if the caller's path has no registered workspace, memodi_save
returns a self-describing {"type": "not_started"} error. This script exits
silently in that case — no spam, no error surfaced to the user.
"""

import asyncio
import json
import os
import socket
import sys


async def save_capture(url: str, path: str, title: str, content: str) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    mcp_url = url if url.endswith("/mcp") else f"{url}/mcp"

    headers = {}
    api_key = os.environ.get("MEMODI_API_KEY")
    if api_key:
        headers["X-Memodi-Api-Key"] = api_key
    machine = os.environ.get("MEMODI_MACHINE") or socket.gethostname()
    if machine:
        headers["X-Memodi-Machine"] = machine

    async with (
        streamablehttp_client(mcp_url, headers=headers) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        project_label = os.path.basename(path.rstrip("/")) or path
        result = await session.call_tool(
            "memodi_save",
            {
                "path": path,
                "title": title,
                "content": content,
                "type": "discovery",
                "topic_key": f"subagent/{project_label}/capture",
            },
        )
        for block in result.content:
            if not hasattr(block, "text"):
                continue
            text = block.text
            try:
                payload = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                payload = None
            if isinstance(payload, dict) and payload.get("type") in (
                "not_started",
                "not_authenticated",
            ):
                # Opt-in inertness: unregistered path or missing/invalid api
                # key, no spam.
                return
            print(text)


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print(
            f"Usage: {sys.argv[0]} <url> <path> <title> <content>",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        asyncio.run(save_capture(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]))
    except Exception as e:
        # Never fail loudly — this runs as an async hook
        print(json.dumps({"error": str(e)}), file=sys.stderr)
