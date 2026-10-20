"""MCP server: exposes the same tools (schemas, resolvers, confirmation gates) to any MCP client.

    uv run python -m app.mcp_server            # stdio transport

Claude Desktop / Cursor config:
    {"command": "uv", "args": ["--directory", "<repo>/backend", "run", "python", "-m", "app.mcp_server"],
     "env": {"CONVERSE_USER_ID": "1"}}

Gated actions (place_order, clear_cart, large quantities) return a token. Executing it requires the
separate `confirm_action` tool, which is annotated destructive so MCP hosts ask the human first.
"""

import asyncio
import json
import os
import uuid

import mcp.types as t
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from app.agent.confirm import resolve_pending
from app.agent.protocol import ConfirmRequest
from app.agent.tools import TOOLS, ToolContext, tool_specs
from app.db import SessionLocal
from app.domain.services import DomainError
from app.search.engine import search_engine

USER_ID = int(os.environ.get("CONVERSE_USER_ID", "1"))
SESSION_ID = f"mcp-{uuid.uuid4().hex[:8]}"
MEMORY: dict = {}

READ_ONLY = {"search_products", "get_product_details", "view_cart", "find_orders", "navigate"}
GATED = {"place_order", "clear_cart"}

CONFIRM_SCHEMA = {
    "type": "object",
    "properties": {"token": {"type": "string"}, "approve": {"type": "boolean"}},
    "required": ["token", "approve"],
}


def _tools() -> list[t.Tool]:
    out = []
    for spec in tool_specs():
        fn = spec["function"]
        name = fn["name"]
        if name == "navigate":  # no UI to drive over MCP
            continue
        desc = fn["description"]
        if name in GATED:
            desc += " Returns a confirmation token; nothing happens until confirm_action is called with the user's approval."
        out.append(t.Tool(name=name, description=desc, input_schema=fn["parameters"],
                          annotations=t.ToolAnnotations(read_only_hint=name in READ_ONLY, destructive_hint=False,
                                                        idempotent_hint=name in READ_ONLY)))
    out.append(t.Tool(
        name="confirm_action",
        description="Execute or cancel an action that returned a confirmation token (placing an order, emptying the "
                    "cart, large quantities). Only call this after the human has explicitly approved.",
        input_schema=CONFIRM_SCHEMA,
        annotations=t.ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True)))
    return out


def _text(obj, is_error: bool = False) -> t.CallToolResult:
    return t.CallToolResult(content=[t.TextContent(type="text", text=json.dumps(obj, default=str))], is_error=is_error)


async def list_tools(ctx, params) -> t.ListToolsResult:
    return t.ListToolsResult(tools=_tools())


async def call_tool(ctx, params: t.CallToolRequestParams) -> t.CallToolResult:
    args = params.arguments or {}
    async with SessionLocal() as db:
        if not search_engine.docs:
            await search_engine.build(db)
        try:
            if params.name == "confirm_action":
                out = await resolve_pending(db, ConfirmRequest(user_id=USER_ID, session_id=SESSION_ID, **args))
                return _text({"status": out["status"], "message": out["reply"]})
            tool = TOOLS.get(params.name)
            if not tool:
                return _text({"error": f"unknown tool {params.name}"}, True)
            tctx = ToolContext(db=db, user_id=USER_ID, session_id=SESSION_ID, turn_id=uuid.uuid4().hex, memory=MEMORY)
            result = await tool.handler(tctx, tool.args.model_validate(args))
            data = dict(result.data)
            if tokens := [a.payload["token"] for a in tctx.ui_actions if a.type == "confirm"]:
                data["confirmation_token"] = tokens[0]
            return _text(data)
        except DomainError as e:
            return _text({"error": e.message}, True)
        except Exception as e:  # validation errors etc. go back to the model, not the transport
            return _text({"error": str(e)[:500]}, True)


server = Server("converse", version="1.0", instructions="Grocery shopping tools for the Converse app.",
                on_list_tools=list_tools, on_call_tool=call_tool)


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
