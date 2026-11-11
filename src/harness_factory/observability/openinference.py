"""OpenInference attributes for the OpenTelemetry copy of a span, so Phoenix can show what happened in a run: the run's
prompt, final reply and metadata on the root, model calls as a chat, tool calls as JSON panes (Langfuse reads the
same `input.value` / `output.value`).

Everything here fills `Span.otel`, a field that is never persisted: the JSONL trace, the ledger, the results store,
grading and every hash read `Span.attributes` and never see it. Observability only (HF_TRACE_MESSAGES,
HF_TRACE_MAX_CHARS, HF_TRACE_MCP_SPANS in settings.py).

No headers, provider config or environment values go into these attributes: model-call spans get the messages the
model saw and the reply it gave, and the invocation parameters of its recipe slot (temperature, max tokens,
response format), nothing else.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from openinference.semconv.trace import MessageAttributes as M
from openinference.semconv.trace import SpanAttributes as OI
from openinference.semconv.trace import ToolAttributes as TA
from openinference.semconv.trace import ToolCallAttributes as TC

JSON = "application/json"
TEXT = "text/plain"


def cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}… [{len(text) - limit} more characters]"


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


def message_attributes(prefix: str, i: int, msg: dict[str, Any], limit: int) -> dict[str, Any]:
    """One chat message as `<prefix>.<i>.message.*`: role, content, the tool calls an assistant asked for, and the
    tool_call_id a tool message answers."""
    base = f"{prefix}.{i}."
    out: dict[str, Any] = {base + M.MESSAGE_ROLE: str(msg.get("role") or "")}
    content = msg.get("content")
    if content not in (None, ""):
        out[base + M.MESSAGE_CONTENT] = cap(_text(content), limit)
    if msg.get("name"):
        out[base + M.MESSAGE_NAME] = str(msg["name"])
    if msg.get("tool_call_id"):
        out[base + M.MESSAGE_TOOL_CALL_ID] = str(msg["tool_call_id"])
    for j, tc in enumerate(msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        tb = f"{base}{M.MESSAGE_TOOL_CALLS}.{j}."
        if tc.get("id"):
            out[tb + TC.TOOL_CALL_ID] = str(tc["id"])
        out[tb + TC.TOOL_CALL_FUNCTION_NAME] = str(fn.get("name") or "")
        out[tb + TC.TOOL_CALL_FUNCTION_ARGUMENTS_JSON] = cap(_text(fn.get("arguments") or "{}"), limit)
    return out


def _fingerprint(msg: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(msg, sort_keys=True, default=str).encode()).hexdigest()[:16]


class MessageLog:
    """Per run (one per Tracer, never shared): what each conversation already sent, for HF_TRACE_MESSAGES=delta.

    A conversation is keyed by its first message (the system prompt, or the first user message): a V5 worker, a
    delegate or a V3 stage with its own history gets its own entry. When a call's messages start with what that
    conversation sent last time, only the rest is sent; otherwise (compaction rewrote the history) all of it."""

    def __init__(self) -> None:
        self._sent: dict[str, list[str]] = {}

    def pending(self, messages: list[dict[str, Any]], mode: str) -> tuple[int, list[dict[str, Any]]]:
        """(offset, messages to send): the first `offset` messages went out with an earlier successful call."""
        if mode == "full" or not messages:
            return 0, list(messages)
        prints = [_fingerprint(m) for m in messages]
        before = self._sent.get(prints[0], [])
        offset = len(before) if before and prints[:len(before)] == before else 0
        return offset, list(messages[offset:])

    def commit(self, messages: list[dict[str, Any]]) -> None:
        """These messages went out with a successful call (a failed attempt's retry sends them again)."""
        if messages:
            prints = [_fingerprint(m) for m in messages]
            self._sent[prints[0]] = prints


def llm_input(messages: list[dict[str, Any]], tools: list[dict[str, Any]], params: dict[str, Any], *,
              provider: str, offset: int, limit: int) -> dict[str, Any]:
    """A model call's request: the messages sent (from `offset` on), the tools offered, the invocation parameters."""
    out: dict[str, Any] = {}
    for i, m in enumerate(messages):
        out.update(message_attributes(OI.LLM_INPUT_MESSAGES, i, m, limit))
    for k, t in enumerate(tools or []):
        out[f"{OI.LLM_TOOLS}.{k}.{TA.TOOL_JSON_SCHEMA}"] = cap(json.dumps(t, default=str), limit)
    out[OI.LLM_INVOCATION_PARAMETERS] = json.dumps(params, default=str)
    out[OI.LLM_PROVIDER] = provider
    out[OI.INPUT_VALUE] = cap(json.dumps({"messages_from": offset, "messages": messages}, default=str), limit * 4)
    out[OI.INPUT_MIME_TYPE] = JSON
    if offset:
        out["hf.input_messages_offset"] = offset      # the first `offset` messages went with an earlier call
    return out


def llm_output(message: dict[str, Any], tokens_in: int, tokens_out: int, limit: int) -> dict[str, Any]:
    out = message_attributes(OI.LLM_OUTPUT_MESSAGES, 0, {**message, "role": message.get("role") or "assistant"}, limit)
    out[OI.LLM_TOKEN_COUNT_TOTAL] = tokens_in + tokens_out
    out[OI.OUTPUT_VALUE] = cap(json.dumps(message, default=str), limit * 2)
    out[OI.OUTPUT_MIME_TYPE] = JSON
    return out


def tool_io(args: Any, result: str | None, limit: int) -> dict[str, Any]:
    """A tool call's arguments (JSON) and its result (JSON when it parses, text otherwise)."""
    out: dict[str, Any] = {}
    if args is not None:
        text = args if isinstance(args, str) else json.dumps(args, default=str)
        out[OI.INPUT_VALUE] = cap(text, limit)
        out[OI.INPUT_MIME_TYPE] = JSON
        out[OI.TOOL_PARAMETERS] = cap(text, limit)
    if result is not None:
        try:
            json.loads(result)
            mime = JSON
        except ValueError:
            mime = TEXT
        out[OI.OUTPUT_VALUE] = cap(result, limit)
        out[OI.OUTPUT_MIME_TYPE] = mime if len(result) <= limit else TEXT    # a cut JSON text no longer parses
    return out


def run_root(*, prompt: str, output: str, metadata: dict[str, Any], agent: str, session: str | None,
             limit: int) -> dict[str, Any]:
    out: dict[str, Any] = {OI.INPUT_VALUE: cap(prompt, limit), OI.INPUT_MIME_TYPE: TEXT,
                           OI.OUTPUT_VALUE: cap(output, limit), OI.OUTPUT_MIME_TYPE: TEXT,
                           OI.METADATA: json.dumps(metadata, default=str), OI.AGENT_NAME: agent}
    if session:
        out[OI.SESSION_ID] = session
    return out


def turn_io(request: dict[str, Any], summary: str, limit: int) -> dict[str, Any]:
    """A turn span (#117): what the turn was (its number, how many calls), and what came of it (the model's text,
    the tools it asked for, or the stop)."""
    return {OI.INPUT_VALUE: json.dumps(request), OI.INPUT_MIME_TYPE: JSON,
            OI.OUTPUT_VALUE: cap(summary, limit), OI.OUTPUT_MIME_TYPE: TEXT}
