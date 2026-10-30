"""A stand-in remote MCP server for tests: a read-only glossary over streamable HTTP, behind a bearer token.

Run: python tests/remote_kb_server.py <port> <token>. Not an env/mcp/impl server: it never runs in a sandbox,
it plays an existing service the factory's MCP client connects to (EXT-05).
"""

from __future__ import annotations

import sys

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

GLOSSARY = {
    "net revenue": "Paid order value minus refunds.",
    "test account": "A customer whose email ends in @example.test; excluded from reporting.",
}

app = MCPServer("kb", instructions="Company glossary. Look up a term before relying on its meaning.")


@app.tool()
def lookup(term: str) -> str:
    """Look up a business term in the company glossary."""
    if term.lower() not in GLOSSARY:
        raise ToolError(f"no glossary entry for {term!r}; known: {', '.join(sorted(GLOSSARY))}")
    return GLOSSARY[term.lower()]


def main(port: int, token: str) -> None:
    web = app.streamable_http_app()

    class BearerAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.headers.get("authorization") != f"Bearer {token}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    web.add_middleware(BearerAuth)
    uvicorn.run(web, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main(int(sys.argv[1]), sys.argv[2])
