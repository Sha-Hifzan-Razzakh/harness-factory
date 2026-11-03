"""biz MCP server: a mock business back office (CRM, invoicing, ticketing) over the run's state file (HF_BIZ).

The state is one JSON document, copied from the task's seed (`env_seed.biz_state`) into the run's state folder
before every run, so each run starts from the seed and no run sees another's changes. Graders read the same file
after the run (`biz_equals` / `biz_count` checks). It is offline and deterministic: new ids come from sequences
kept in the state, and "today" is the seed's `meta.today`, never the clock.

Side effects (catalog/tools.yaml), by what the mock lets you undo:
- a credit note can be voided (inv_void_credit_note), so issuing one is REVERSIBLE; voiding is undone by issuing
  the same credit again;
- a closed ticket reopens when someone comments on it (as in common help desks), so closing is REVERSIBLE;
- a recorded payment can't be removed (payments are reconciled against the bank), so inv_mark_paid is
  IRREVERSIBLE.

Standard library only, so it runs inside the sandbox image.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError  # message reaches the model; ValueError would be masked

app = MCPServer("biz", instructions="Business systems: CRM contacts (crm_*), invoices and credit notes (inv_*), "
                                    "and support tickets (tkt_*). Amounts are in the currency the records show.")

DEFAULT_LIMIT = 20
MAX_LIMIT = 50
MAX_TEXT = 2000
INVOICE_STATUSES = ("open", "paid", "credited")
TICKET_STATUSES = ("open", "pending", "closed")
TIERS = ("standard", "gold", "platinum")
EDITABLE_CONTACT_FIELDS = ("name", "company", "email", "phone", "city", "tier")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ------------------------------------------------------------------ state

def _path() -> Path:
    return Path(os.environ.get("HF_BIZ", ""))


def _load() -> dict[str, Any]:
    p = _path()
    if not os.environ.get("HF_BIZ") or not p.is_file():
        raise ToolError("this task has no business systems to work with (no business state was seeded)")
    return json.loads(p.read_text())


def _save(state: dict[str, Any]) -> None:
    p = _path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, p)                      # a reader never sees half a document


def _get(state: dict, collection: str, rid: str, what: str) -> dict:
    rec = state.get(collection, {}).get(rid) if isinstance(rid, str) else None
    if rec is None:
        raise ToolError(f"unknown {what} id {rid!r}")
    return rec


def _next_id(state: dict, seq: str, prefix: str, width: int) -> str:
    seqs = state["meta"]["sequences"]
    n = int(seqs[seq])
    seqs[seq] = n + 1
    return f"{prefix}{n:0{width}d}"


def _today(state: dict) -> str:
    return state["meta"]["today"]


def _page(items: list[dict], limit: int, offset: int, key: str) -> dict:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ToolError(f"limit must be a whole number from 1 to {MAX_LIMIT}")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ToolError("offset must be a whole number, 0 or more")
    page = items[offset:offset + limit]
    nxt = offset + limit if offset + limit < len(items) else None
    return {"total": len(items), "offset": offset, key: page, "next_offset": nxt}


def _text(value: Any, what: str, max_len: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"{what} must be non-empty text")
    if len(value) > max_len:
        raise ToolError(f"{what} is too long ({len(value)} characters; at most {max_len})")
    return value.strip()


def _iso(value: Any, what: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise ToolError(f"{what} must be a date as YYYY-MM-DD, got {value!r}") from None


# ------------------------------------------------------------------ money (integer cents inside, numbers outside)

def _cents(x: float) -> int:
    return round(x * 100)


def _money(cents: int) -> float | int:
    v = cents / 100
    return int(v) if v == int(v) else v


def _fmt(cents: int) -> str:
    return f"{cents / 100:,.2f}"


def _amount(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError(f"amount must be a number, got {value!r}")
    cents = round(value * 100)
    if abs(value * 100 - cents) > 1e-6:
        raise ToolError(f"amount {value} has more than 2 decimal places")
    if cents <= 0:
        raise ToolError("amount must be more than 0")
    return cents


def recompute(state: dict, invoice_id: str) -> None:
    """Keep an invoice's derived fields true: credited (issued credit notes), balance_due and status."""
    inv = state["invoices"][invoice_id]
    credited = sum(_cents(cn["amount"]) for cn in state.get("credit_notes", {}).values()
                   if cn["invoice_id"] == invoice_id and cn["status"] == "issued")
    total, paid = _cents(inv["total"]), _cents(inv["paid"])
    balance = max(total - credited - paid, 0)
    inv["credited"] = _money(credited)
    inv["balance_due"] = _money(balance)
    inv["status"] = "paid" if inv.get("paid_on") else ("credited" if balance == 0 else "open")


def _overdue(state: dict, inv: dict) -> bool:
    return inv["status"] == "open" and inv["due_on"] < _today(state)


# ------------------------------------------------------------------ CRM

def _contact_summary(c: dict) -> dict:
    return {k: c[k] for k in ("id", "name", "company", "email", "city", "tier")}


@app.tool()
def crm_search_contacts(query: str, limit: int = 10, offset: int = 0) -> dict:
    """Search CRM contacts by name, company or email (case-insensitive substring, at least 2 characters).
    Returns {total, offset, contacts, next_offset}; page through with offset (limit at most 50)."""
    state = _load()
    q = (query or "").strip().lower()
    if len(q) < 2:
        raise ToolError("query must be at least 2 characters")
    hits = [_contact_summary(c) for c in state["contacts"].values()
            if any(q in str(c.get(f, "")).lower() for f in ("name", "company", "email"))]
    return _page(hits, limit, offset, "contacts")


@app.tool()
def crm_get_contact(id: str) -> dict:
    """Get one CRM contact by id (e.g. "C-1004"), with every field."""
    return _get(_load(), "contacts", id, "contact")


@app.tool()
def crm_update_contact(id: str, fields: dict[str, Any]) -> dict:
    """Update a CRM contact. fields maps field names to new values; editable: name, company, email, phone,
    city, tier (standard, gold or platinum). Returns the updated contact."""
    state = _load()
    c = _get(state, "contacts", id, "contact")
    if not isinstance(fields, dict) or not fields:
        raise ToolError("fields must be an object with at least one field to change")
    for k, v in fields.items():
        if k not in EDITABLE_CONTACT_FIELDS:
            raise ToolError(f"{k!r} can't be changed; editable fields: {', '.join(EDITABLE_CONTACT_FIELDS)}")
        if not isinstance(v, str) or not v.strip():
            raise ToolError(f"{k} must be non-empty text")
        if k == "email" and not _EMAIL.match(v.strip()):
            raise ToolError(f"invalid email address {v!r}")
        if k == "tier" and v not in TIERS:
            raise ToolError(f"tier must be one of {', '.join(TIERS)}")
    for k, v in fields.items():
        c[k] = v.strip()
    _save(state)
    return c


# ------------------------------------------------------------------ invoicing

def _invoice_summary(state: dict, inv: dict) -> dict:
    return {**{k: inv[k] for k in ("id", "customer_id", "po", "issued_on", "due_on", "total", "balance_due",
                                   "status")}, "overdue": _overdue(state, inv)}


@app.tool()
def inv_list_invoices(status: str | None = None, customer_id: str | None = None, limit: int = DEFAULT_LIMIT,
                      offset: int = 0) -> dict:
    """List invoices, optionally only one status (open, paid or credited) and/or one customer id. Returns
    {as_of, total, offset, invoices, next_offset}; each invoice shows its balance due and whether it is overdue
    as of today. Page through with offset (limit at most 50)."""
    state = _load()
    if status is not None and status not in INVOICE_STATUSES:
        raise ToolError(f"unknown invoice status {status!r}; use one of {', '.join(INVOICE_STATUSES)}")
    if customer_id is not None:
        _get(state, "contacts", customer_id, "customer")
    rows = [_invoice_summary(state, i) for i in state["invoices"].values()
            if (status is None or i["status"] == status) and (customer_id is None or i["customer_id"] == customer_id)]
    return {"as_of": _today(state), **_page(rows, limit, offset, "invoices")}


@app.tool()
def inv_get_invoice(id: str) -> dict:
    """Get one invoice by id (e.g. "INV-1042"): its lines, totals, payment, balance due, and the credit notes
    issued against it."""
    state = _load()
    inv = _get(state, "invoices", id, "invoice")
    notes = [cn for cn in state["credit_notes"].values() if cn["invoice_id"] == id]
    return {**inv, "overdue": _overdue(state, inv), "as_of": _today(state), "credit_notes": notes}


@app.tool()
def inv_create_credit_note(invoice_id: str, amount: float, reason: str) -> dict:
    """Issue a credit note against an invoice, reducing what the customer owes (or, on a paid invoice, leaving
    them in credit). amount is in the invoice's currency, at most 2 decimals, and at most what is left to credit
    on the invoice. A credit note can be voided later. Returns the credit note."""
    state = _load()
    inv = _get(state, "invoices", invoice_id, "invoice")
    cents = _amount(amount)
    why = _text(reason, "reason", 500)
    left = _cents(inv["total"]) - _cents(inv["credited"])
    if cents > left:
        raise ToolError(f"a credit of {_fmt(cents)} is more than is left to credit on {invoice_id} (total "
                        f"{_fmt(_cents(inv['total']))}, already credited {_fmt(_cents(inv['credited']))}, left "
                        f"{_fmt(left)})")
    cid = _next_id(state, "credit_note", "CN-", 4)
    note = {"id": cid, "invoice_id": invoice_id, "amount": _money(cents), "reason": why, "issued_on": _today(state),
            "status": "issued"}
    state["credit_notes"][cid] = note
    recompute(state, invoice_id)
    _save(state)
    return note


@app.tool()
def inv_void_credit_note(id: str, reason: str) -> dict:
    """Void a credit note issued by mistake; the invoice owes its amount again. Returns the voided note."""
    state = _load()
    note = _get(state, "credit_notes", id, "credit note")
    why = _text(reason, "reason", 500)
    if note["status"] == "void":
        raise ToolError(f"{id} is already void")
    note["status"], note["void_reason"] = "void", why
    recompute(state, note["invoice_id"])
    _save(state)
    return note


@app.tool()
def inv_mark_paid(id: str, paid_on: str) -> dict:
    """Record that an invoice's balance due was paid in full on paid_on (YYYY-MM-DD). A recorded payment can't
    be removed. Returns the invoice."""
    state = _load()
    inv = _get(state, "invoices", id, "invoice")
    day = _iso(paid_on, "paid_on")
    if inv["status"] == "paid":
        raise ToolError(f"{id} is already paid (on {inv['paid_on']})")
    if _cents(inv["balance_due"]) == 0:
        raise ToolError(f"{id} has nothing left to pay: it is fully credited")
    if day < inv["issued_on"]:
        raise ToolError(f"paid_on {day} is before {id} was issued ({inv['issued_on']})")
    if day > _today(state):
        raise ToolError(f"paid_on {day} is in the future (today is {_today(state)})")
    inv["paid"] = _money(_cents(inv["paid"]) + _cents(inv["balance_due"]))
    inv["paid_on"] = day
    recompute(state, id)
    _save(state)
    return inv


# ------------------------------------------------------------------ ticketing

@app.tool()
def tkt_list(status: str | None = None, limit: int = DEFAULT_LIMIT, offset: int = 0) -> dict:
    """List support tickets, optionally only one status (open, pending or closed). Returns {total, offset,
    tickets, next_offset}; page through with offset (limit at most 50)."""
    state = _load()
    if status is not None and status not in TICKET_STATUSES:
        raise ToolError(f"unknown ticket status {status!r}; use one of {', '.join(TICKET_STATUSES)}")
    rows = [{k: t[k] for k in ("id", "subject", "customer_id", "status", "priority", "opened_on")}
            for t in state["tickets"].values() if status is None or t["status"] == status]
    return _page(rows, limit, offset, "tickets")


@app.tool()
def tkt_get(id: str) -> dict:
    """Get one support ticket by id (e.g. "T-2041"): the customer's message, its comments and its resolution."""
    return _get(_load(), "tickets", id, "ticket")


@app.tool()
def tkt_comment(id: str, text: str) -> dict:
    """Add an internal comment to a ticket (the customer is not notified). Commenting on a closed ticket reopens
    it. Returns the ticket."""
    state = _load()
    t = _get(state, "tickets", id, "ticket")
    body = _text(text, "text")
    t["comments"].append({"id": _next_id(state, "comment", "TC-", 4), "text": body, "at": _today(state)})
    if t["status"] == "closed":
        t["status"], t["resolution"] = "open", None
    _save(state)
    return t


@app.tool()
def tkt_close(id: str, resolution: str) -> dict:
    """Close a ticket with a resolution saying what was done. Returns the ticket."""
    state = _load()
    t = _get(state, "tickets", id, "ticket")
    why = _text(resolution, "resolution", 500)
    if t["status"] == "closed":
        raise ToolError(f"{id} is already closed")
    t["status"], t["resolution"] = "closed", why
    _save(state)
    return t


if __name__ == "__main__":
    app.run()
