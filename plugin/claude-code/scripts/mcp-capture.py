#!/usr/bin/env python3
"""Save an observation to memodi via MCP streamable-http.

Usage: mcp-capture.py <url> <project> <title> <content>

Uses the MCP client library to call memodi_save through the proper
protocol (initialize → tools/call). Designed to be called from hooks.
"""

import asyncio
import json
import os
import sys


async def save_capture(url: str, project: str, title: str, content: str) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    mcp_url = url if url.endswith("/mcp") else f"{url}/mcp"

    headers = {}
    api_key = os.environ.get("MEMODI_API_KEY")
    if api_key:
        headers["X-Api-Key"] = api_key

    async with (
        streamablehttp_client(mcp_url, headers=headers) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(
            "memodi_save",
            {
                "project": project,
                "title": title,
                "content": content,
                "type": "discovery",
                "topic_key": f"subagent/{project}/capture",
            },
        )
        # Print result for debugging (captured by async hook)
        for block in result.content:
            if hasattr(block, "text"):
                print(block.text)


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print(
            f"Usage: {sys.argv[0]} <url> <project> <title> <content>",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        asyncio.run(save_capture(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]))
    except Exception as e:
        # Never fail loudly — this runs as an async hook
        print(json.dumps({"error": str(e)}), file=sys.stderr)
