"""A stand-in remote MCP server with test tenants, for tests (EXT-07): a small order desk whose tools change state.

Run: python tests/remote_tenant_server.py <port>. Each request names its tenant in the HF-Tenant header; every tenant
has its own state, which the factory sets with `_tenant_reset`, reads with `_tenant_export` and forgets with
`_tenant_drop`. GET /tenants returns how many tenants the server holds (to check that runs clean up).
"""

from __future__ import annotations

import copy
import sys

import uvicorn
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse

TENANTS: dict[str, dict] = {}
app = MCPServer("orders", version="1.0", instructions="The order desk. Look orders up and refund them.")


def tenant(ctx: Context) -> dict:
    tid = (ctx.headers or {}).get("hf-tenant")
    if not tid:
        raise ToolError("this server needs a test tenant (HF-Tenant header)")
    if tid not in TENANTS:
        raise ToolError(f"unknown tenant {tid}")
    return TENANTS[tid]


@app.tool()
def get_order(order_id: str, ctx: Context) -> str:
    """Look up an order: its status and amount."""
    order = tenant(ctx).setdefault("orders", {}).get(order_id)
    if order is None:
        raise ToolError(f"no order {order_id}")
    return f"{order_id}: {order['status']}, {order['amount']} AED"


@app.tool()
def refund_order(order_id: str, ctx: Context) -> str:
    """Refund a paid order."""
    order = tenant(ctx).setdefault("orders", {}).get(order_id)
    if order is None:
        raise ToolError(f"no order {order_id}")
    if order["status"] != "paid":
        raise ToolError(f"{order_id} is {order['status']}, only paid orders can be refunded")
    order["status"] = "refunded"
    return f"{order_id} refunded"


@app.tool()
def _tenant_reset(state: dict, ctx: Context) -> str:
    tid = (ctx.headers or {}).get("hf-tenant")
    if not tid:
        raise ToolError("no HF-Tenant header")
    TENANTS[tid] = copy.deepcopy(state)
    return "ok"


@app.tool()
def _tenant_export(ctx: Context) -> dict:
    return tenant(ctx)


@app.tool()
def _tenant_drop(ctx: Context) -> str:
    TENANTS.pop((ctx.headers or {}).get("hf-tenant", ""), None)
    return "ok"


async def count(request: Request) -> JSONResponse:
    return JSONResponse({"tenants": len(TENANTS)})


if __name__ == "__main__":
    web = app.streamable_http_app()
    web.add_route("/tenants", count)
    uvicorn.run(web, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
