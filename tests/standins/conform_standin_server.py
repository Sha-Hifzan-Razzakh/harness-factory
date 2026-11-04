"""A stand-in MCP server for the conformance suite's own tests (tests/test_mcp_conformance.py).

Run with `python -m conform_standin_server` (tests/standins on PYTHONPATH). It is a well-behaved server unless the
workspace has a file BROKEN naming one conformance rule; then it breaks exactly that rule. Built on the low-level
server so every part of the protocol (names, schemas, errors) can be got wrong on purpose.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import anyio
from mcp import types
from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server

MODE = (Path(os.environ.get("HF_WORKSPACE", "/nonexistent")) / "BROKEN")
MODE = MODE.read_text().strip() if MODE.is_file() else ""
STATE = {"wedged": False}

ECHO_IN = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
ECHO_OUT = {"type": "object", "properties": {"result": {"type": "string"}}, "required": ["result"]}
STAMP_IN = {"type": "object", "properties": {"n": {"type": "integer", "default": 1}}}


def _error(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=True)


def _ok(text: str, structured=None) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], structured_content=structured)


async def list_tools(ctx, params) -> types.ListToolsResult:
    echo_in = {**ECHO_IN, "properties": {"text": {"type": "strin"}}} if MODE == "input_schema" else ECHO_IN
    tools = [types.Tool(name="echo", description="Echo the text back.", input_schema=echo_in, output_schema=ECHO_OUT),
             types.Tool(name="stamp", description="Stamp a number.", input_schema=STAMP_IN)]
    if MODE == "list_tools":
        tools.append(types.Tool(name="undeclared", description="Not in tools.yaml.", input_schema=STAMP_IN))
    if MODE == "tool_names":
        tools.append(types.Tool(name="bad name!", description="Not a valid tool name.", input_schema=STAMP_IN))
    return types.ListToolsResult(tools=tools)


async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
    args = params.arguments or {}
    if STATE["wedged"]:
        return _error("the server is wedged since its last refusal")
    if params.name == "echo":
        if MODE != "invalid_args" and not isinstance(args.get("text"), str):
            return _error("text is required and must be a string")
        text = str(args.get("text"))
        if "forbidden" in text:
            if MODE == "usable_after_error":
                STATE["wedged"] = True
            return _error("Error executing tool echo" if MODE == "refusal" else "echo refuses the word 'forbidden'")
        if MODE == "call":
            return _error("internal failure")
        return _ok(text, {"result": text})
    if params.name == "stamp":
        n = args.get("n", 1)
        if MODE != "invalid_args" and (isinstance(n, bool) or not isinstance(n, int)):
            return _error(f"n must be an integer, got {n!r}")
        return _ok(f"stamp {n}")
    if MODE == "unknown_tool":
        return _ok("ok")
    return _error(f"Unknown tool: {params.name}")


async def main() -> None:
    server = Server("someone-else" if MODE == "initialize" else "standin", on_list_tools=list_tools,
                    on_call_tool=call_tool)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    if MODE == "shutdown":        # a non-daemon thread keeps the process alive after stdin closes
        threading.Thread(target=time.sleep, args=(30,)).start()
    anyio.run(main)
