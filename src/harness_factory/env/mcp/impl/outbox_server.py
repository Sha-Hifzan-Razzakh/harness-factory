"""outbox MCP server: outgoing email and channel notifications over the run's state file (HF_OUTBOX).

The state is one JSON document, copied from the task's seed (`env_seed.server_state.outbox`) into the run's state
folder before every run, like the biz server's. Nothing leaves the sandbox: a "sent" message is a record in the
state, and graders read the file after the run (`state_equals` / `state_count` checks on "outbox:/..."). It is
offline and deterministic: ids come from sequences kept in the state, and every message is stamped with the
seed's `meta.now`, never the clock.

Side effects (catalog/tools.yaml): a sent email or a posted notification can't be called back, so send_email and
notify are IRREVERSIBLE. A recipe whose policy is `IRREVERSIBLE: DENY` (v2-base and every recipe built on it)
refuses them before they reach this server.

State:
  meta           now, sender, allowed_domains (when set, every recipient's domain must be one of them),
                 channels (the ones notify can post to), sequences
  templates      id -> {name, status (current | retired), subject, body, placeholders}
  sent           id -> {id, to, cc, subject, body, attachments, sent_at}; to and cc are lower-cased, de-duplicated
                 and sorted, so a check doesn't depend on the order the model listed them in
  deliveries     id -> {id, message_id, address, kind (to | cc)}: one per recipient, as a mail server logs them,
                 so "nobody wrote to this address" is one count
  notifications  id -> {id, channel, text, sent_at}

Standard library only, so it runs inside the sandbox image.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError  # message reaches the model; ValueError would be masked

app = MCPServer("outbox", instructions="Outgoing email (send_email) and channel notifications (notify), with the "
                                       "company's message templates. Sent messages can't be recalled.")

MAX_SUBJECT = 200
MAX_BODY = 20000
MAX_TEXT = 2000
MAX_RECIPIENTS = 20
MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
_EMAIL = re.compile(r"^[^@\s,;<>]+@([^@\s,;<>]+\.[^@\s,;<>]+)$")


# ------------------------------------------------------------------ state

def _path() -> Path:
    return Path(os.environ.get("HF_OUTBOX", ""))


def _load() -> dict[str, Any]:
    p = _path()
    if not os.environ.get("HF_OUTBOX") or not p.is_file():
        raise ToolError("this task has no outbox to work with (no outbox state was seeded)")
    return json.loads(p.read_text())


def _save(state: dict[str, Any]) -> None:
    p = _path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, p)                      # a reader never sees half a document


def _next_id(state: dict, seq: str, prefix: str) -> str:
    seqs = state["meta"]["sequences"]
    n = int(seqs[seq])
    seqs[seq] = n + 1
    return f"{prefix}{n:04d}"


def _text(value: Any, what: str, max_len: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"{what} is missing: it must be non-empty text")
    if len(value) > max_len:
        raise ToolError(f"{what} is too long ({len(value)} characters; at most {max_len})")
    return value.strip()


# ------------------------------------------------------------------ validation

def addresses(state: dict, value: Any, what: str) -> list[str]:
    """Recipients as stored: lower-cased, de-duplicated, sorted. Every one must be an email address, on an allowed
    domain when the seed restricts them."""
    if not isinstance(value, list) or not all(isinstance(a, str) for a in value):
        raise ToolError(f"{what} must be a list of email addresses")
    out = sorted({a.strip().lower() for a in value})
    allowed = state["meta"].get("allowed_domains")
    for a in out:
        m = _EMAIL.match(a)
        if m is None:
            raise ToolError(f"{a!r} in {what} is not an email address (one address per item, e.g. "
                            f"'name@example.com')")
        if allowed and m.group(1) not in allowed:
            raise ToolError(f"can't send to {a}: {m.group(1)} is not an approved recipient domain "
                            f"(approved: {', '.join(allowed)})")
    return out


def _workspace() -> Path:
    return Path(os.environ.get("HF_WORKSPACE", "")).resolve()


def attachment(rel: Any) -> dict:
    """A workspace file to attach: recorded by path, size and content hash (the file itself stays put)."""
    if not isinstance(rel, str) or not rel.strip():
        raise ToolError("an attachment is a workspace path, e.g. 'reports/summary.pdf'")
    root = _workspace()
    p = (root / rel.strip()).resolve()
    if not p.is_relative_to(root):
        raise ToolError(f"attachment {rel!r} is outside the workspace")
    if not p.is_file():
        raise ToolError(f"attachment {rel!r} is not a file in the workspace")
    size = p.stat().st_size
    if size > MAX_ATTACHMENT_BYTES:
        raise ToolError(f"attachment {rel!r} is too large ({size} bytes; at most {MAX_ATTACHMENT_BYTES})")
    return {"path": p.relative_to(root).as_posix(), "bytes": size,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}


# ------------------------------------------------------------------ templates

def _template_summary(t: dict) -> dict:
    return {k: t[k] for k in ("id", "name", "status")}


@app.tool()
def list_templates() -> dict:
    """List the company's message templates: id, name and status (current, or retired: kept for the record,
    not to be used)."""
    state = _load()
    return {"templates": [_template_summary(t) for t in state["templates"].values()]}


@app.tool()
def get_template(id: str) -> dict:
    """Get one message template by id (e.g. "T-02"): its subject and body with {placeholders}, and what each
    placeholder stands for. Fill every placeholder before sending."""
    state = _load()
    t = state["templates"].get(id) if isinstance(id, str) else None
    if t is None:
        raise ToolError(f"unknown template id {id!r}; list_templates shows them")
    return t


# ------------------------------------------------------------------ sending

@app.tool()
def send_email(to: list[str], subject: str, body: str, cc: list[str] | None = None,
               attachments: list[str] | None = None) -> dict:
    """Send an email now. to and cc are lists of email addresses (at most 20 in all); attachments are workspace
    paths. A sent email can't be recalled. Returns the sent message."""
    state = _load()
    to_ = addresses(state, to, "to")
    if not to_:
        raise ToolError("to is empty: name at least one recipient")
    cc_ = [a for a in addresses(state, cc or [], "cc") if a not in to_]
    if len(to_) + len(cc_) > MAX_RECIPIENTS:
        raise ToolError(f"too many recipients ({len(to_) + len(cc_)}; at most {MAX_RECIPIENTS})")
    subj = _text(subject, "subject", MAX_SUBJECT)
    text = _text(body, "body", MAX_BODY)
    if not isinstance(attachments or [], list) or len(attachments or []) > MAX_ATTACHMENTS:
        raise ToolError(f"attachments is a list of at most {MAX_ATTACHMENTS} workspace paths")
    files = [attachment(a) for a in attachments or []]
    mid = _next_id(state, "message", "M-")
    msg = {"id": mid, "to": to_, "cc": cc_, "subject": subj, "body": text, "attachments": files,
           "sent_at": state["meta"]["now"]}
    state["sent"][mid] = msg
    for kind, group in (("to", to_), ("cc", cc_)):
        for a in group:
            did = _next_id(state, "delivery", "D-")
            state["deliveries"][did] = {"id": did, "message_id": mid, "address": a, "kind": kind}
    _save(state)
    return msg


@app.tool()
def notify(channel: str, text: str) -> dict:
    """Post a notification to a team channel (e.g. "#procurement"). Everyone in the channel sees it at once; a
    post can't be taken back. Returns the notification."""
    state = _load()
    channels = state["meta"].get("channels") or []
    if not isinstance(channel, str) or channel.strip() not in channels:
        raise ToolError(f"unknown channel {channel!r}; channels: {', '.join(channels) or 'none'}")
    body = _text(text, "text", MAX_TEXT)
    nid = _next_id(state, "notification", "N-")
    note = {"id": nid, "channel": channel.strip(), "text": body, "sent_at": state["meta"]["now"]}
    state["notifications"][nid] = note
    _save(state)
    return note


@app.tool()
def list_sent() -> dict:
    """Everything already sent from this outbox: emails (with their recipients and subjects) and channel
    notifications, oldest first."""
    state = _load()
    emails = [{k: m[k] for k in ("id", "to", "cc", "subject", "sent_at")} | {"attachments": len(m["attachments"])}
              for m in state["sent"].values()]
    return {"emails": emails, "notifications": list(state["notifications"].values())}


if __name__ == "__main__":
    app.run()
