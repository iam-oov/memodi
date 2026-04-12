"""Tests for deferred tool loading.

Verifies that core tools have no defer_loading flag and deferred tools do.
These tests exercise the MCP server layer only — no database required.
"""

import os

# server.py import chain loads config which requires DB env vars.
# Set dummies before import since we never touch the database here.
os.environ.setdefault("MEMODI_DB_USER", "test")
os.environ.setdefault("MEMODI_DB_PASSWORD", "test")

import pytest

from memodi.server import CORE_TOOLS, _list_tools_with_deferred


@pytest.mark.asyncio
async def test_deferred_tools_have_flag():
    tools = await _list_tools_with_deferred()
    deferred = [t for t in tools if t.name not in CORE_TOOLS]
    assert len(deferred) > 0, "There should be deferred tools"
    for tool in deferred:
        dump = tool.model_dump(by_alias=True, exclude_none=True)
        assert dump.get("defer_loading") is True, (
            f"{tool.name} should have defer_loading=True"
        )


@pytest.mark.asyncio
async def test_core_tools_have_no_flag():
    tools = await _list_tools_with_deferred()
    core = [t for t in tools if t.name in CORE_TOOLS]
    assert len(core) == len(CORE_TOOLS)
    for tool in core:
        dump = tool.model_dump(by_alias=True, exclude_none=True)
        assert "defer_loading" not in dump, (
            f"{tool.name} should NOT have defer_loading"
        )


@pytest.mark.asyncio
async def test_all_core_tools_are_registered():
    tools = await _list_tools_with_deferred()
    names = {t.name for t in tools}
    for core_tool in CORE_TOOLS:
        assert core_tool in names, f"Core tool {core_tool} not registered"


@pytest.mark.asyncio
async def test_total_tool_count():
    tools = await _list_tools_with_deferred()
    assert len(tools) == 31, (
        f"Expected 31 tools (8 core + 23 deferred), got {len(tools)}"
    )
