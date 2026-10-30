"""A stand-in agent behind a remote MCP server, for tests (EXT-10): its tools answer as an agent would, and report
the usage of the model calls it made under `_meta["harness-factory/usage"]`.

Run: python tests/remote_agent_server.py <port>. Like remote_kb_server.py it never runs in a sandbox; it plays
another team's agent that the factory's MCP client connects to. No model is called: the answers and usage are canned.
"""

from __future__ import annotations

import sys

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, TextContent

ANSWERS = {
    "net revenue": "Net revenue is paid order value minus refunds; test accounts (@example.test) are excluded.",
}
USAGE = {"tokens_in": 1200, "tokens_out": 80, "model": "stand-in/analyst-1"}

app = MCPServer("analyst", instructions="An analyst agent. Ask it a question about the business in plain words.")


def answer(question: str) -> str:
    if not question.strip():
        raise ToolError("ask a question in plain words")               # refused before any model call
    for term, text in ANSWERS.items():
        if term in question.lower():
            return text
    return "I could not find an answer to that."


@app.tool()
def ask(question: str) -> CallToolResult:
    """Ask the analyst agent a question. It researches and answers in a sentence or two."""
    return CallToolResult(content=[TextContent(type="text", text=answer(question))],
                          _meta={"harness-factory/usage": USAGE})


@app.tool()
def ask_unmetered(question: str) -> str:
    """The same agent, behind an endpoint that does not report what it used."""
    return answer(question)


if __name__ == "__main__":
    uvicorn.run(app.streamable_http_app(), host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
