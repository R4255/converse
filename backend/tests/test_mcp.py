"""End-to-end MCP test: spawn the server over stdio and drive it like an MCP host would."""

import json
import sys
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

pytestmark = pytest.mark.asyncio(loop_scope="session")
ROOT = Path(__file__).resolve().parent.parent


async def test_mcp_lists_and_calls_tools():
    params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server"], cwd=str(ROOT),
                                   env={"CONVERSE_USER_ID": "2", "FROZEN_TODAY": "2026-10-19", "PATH": "/usr/bin:/bin"})
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = {t.name: t for t in (await session.list_tools()).tools}
        assert {"search_products", "add_to_cart", "place_order", "confirm_action"} <= tools.keys()
        assert tools["confirm_action"].annotations.destructive_hint is True
        assert tools["search_products"].annotations.read_only_hint is True

        res = await session.call_tool("find_orders", {"when": "last month"})
        assert not res.is_error and "September 2026" in json.loads(res.content[0].text)["date_range"]

        res = await session.call_tool("clear_cart", {})
        body = json.loads(res.content[0].text)
        assert body.get("confirmation_token") or body.get("status") == "cart already empty"
