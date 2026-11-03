"""Oversized tool output (SUB-07, ADR 0015): spill it to a file, show the model its head and tail and a handle.

A tool result longer than SPILL_ABOVE_CHARS is written in full to the run's spill area, in the sandbox's state
folder (next to the database, never in the workspace: the model's file tools can't see it, and it never shows in
the diff the graders read). The model gets the first HEAD_CHARS and the last TAIL_CHARS with a note naming the
handle, and reads the rest in pages with the harness-side `spill__read(handle, page)` tool, which every shape is
offered the same way once the run has spilled something (shapes/common.model_turn). A spill read is a tool call: it
is traced, charged to the budget and counted in Q like any other. The area is capped per run (MAX_TOTAL_CHARS,
MAX_SPILLS); past the cap an output is cut as before, and the note says so.

One SpillStore per run, shared by delegated helpers (same sandbox): handles are numbered in the order outputs
spill, so a replayed run gets the same handles.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from harness_factory.hashing import content_hash
from harness_factory.mcp.client import ToolResult
from harness_factory.substrate.errors import ErrorKind

SPILL_REF = "spill.read"
SPILL_NAME = SPILL_REF.replace(".", "__")

SPILL_ABOVE_CHARS = 16_000      # the old inline cap: anything longer spills
HEAD_CHARS = 6_000
TAIL_CHARS = 2_000
PAGE_CHARS = 8_000
MAX_TOTAL_CHARS = 4_000_000     # every spill of one run together
MAX_SPILLS = 64

DESCRIPTION = ("Read a page of a tool output that was too long to show in full. The note in that output names its "
               "handle and how many pages it has.")
SCHEMA = {"type": "object", "required": ["handle", "page"],
          "properties": {"handle": {"type": "string", "description": "The handle from the note, e.g. spill-1"},
                         "page": {"type": "integer", "minimum": 1, "description": "Page number, from 1"}}}

NOTE = ("\n[... {omitted} characters not shown. The full output ({total} characters, {pages} pages of {page} "
        "characters) is saved as {handle}: call " + SPILL_NAME + " with handle \"{handle}\" and page 1 to {pages} "
        "to read it.]\n")


def tool_spec() -> dict[str, Any]:
    return {"type": "function", "function": {"name": SPILL_NAME, "description": DESCRIPTION, "parameters": SCHEMA}}


class SpillStore:
    def __init__(self, root: Path, *, above: int = SPILL_ABOVE_CHARS, head: int = HEAD_CHARS, tail: int = TAIL_CHARS,
                 page: int = PAGE_CHARS, max_total: int = MAX_TOTAL_CHARS, max_spills: int = MAX_SPILLS) -> None:
        if head + tail >= above or page <= 0:
            raise ValueError("spill: head + tail must be shorter than the spill threshold, and pages non-empty")
        self.root = root
        self.above, self.head, self.tail, self.page = above, head, tail, page
        self.max_total, self.max_spills = max_total, max_spills
        self.sizes: dict[str, int] = {}          # handle -> characters
        self.total = 0

    @property
    def active(self) -> bool:
        """Something has spilled: from now on the model is offered the read tool."""
        return bool(self.sizes)

    def pages(self, handle: str) -> int:
        return max(1, math.ceil(self.sizes[handle] / self.page))

    def shrink(self, text: str, span) -> str:
        """The text the model sees for one tool result: unchanged when short, else head + note + tail."""
        if len(text) <= self.above:
            return text
        span.attributes["truncated_from"] = len(text)
        if len(self.sizes) >= self.max_spills or self.total + len(text) > self.max_total:
            span.attributes["spill_refused"] = "spill area full"
            return text[:self.above] + "\n[truncated: the rest is not saved, this run's spill area is full]"
        handle = f"spill-{len(self.sizes) + 1}"
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / f"{handle}.txt").open("w", encoding="utf-8", newline="") as f:     # byte-exact on Windows
            f.write(text)
        self.sizes[handle] = len(text)
        self.total += len(text)
        span.attributes.update(spill_handle=handle, spill_chars=len(text))
        note = NOTE.format(omitted=len(text) - self.head - self.tail, total=len(text), pages=self.pages(handle),
                           page=self.page, handle=handle)
        return text[:self.head] + note + text[-self.tail:]

    def read(self, raw_args: str | dict, span) -> ToolResult:
        """One page of a spilled output. Bad arguments are the model's error (invalid in Q), like any tool's."""
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            handle, page = str(args["handle"]), int(args["page"])
        except (ValueError, TypeError, KeyError) as e:
            span.error = ErrorKind.TOOL_ARGS.value
            return ToolResult(text=f"Invalid arguments for {SPILL_NAME}: give handle and page ({e})", is_error=True)
        span.attributes.update(args=json.dumps({"handle": handle, "page": page}), side_effect="READ",
                               args_hash=content_hash({"handle": handle, "page": page}, 12))
        if handle not in self.sizes:
            span.error = ErrorKind.TOOL_ARGS.value
            known = ", ".join(self.sizes) or "none"
            return ToolResult(text=f"No spilled output {handle!r}. Known handles: {known}.", is_error=True)
        pages = self.pages(handle)
        if not 1 <= page <= pages:
            span.error = ErrorKind.TOOL_ARGS.value
            return ToolResult(text=f"{handle} has pages 1 to {pages}; page {page} does not exist.", is_error=True)
        with (self.root / f"{handle}.txt").open(encoding="utf-8", newline="") as f:
            text = f.read()
        span.attributes.update(spill_handle=handle, spill_page=page)
        body = text[(page - 1) * self.page:page * self.page]
        return ToolResult(text=f"[{handle} page {page} of {pages}]\n{body}")

    def describe(self) -> dict[str, Any]:
        return {"spills": dict(self.sizes), "spill_chars": self.total}
