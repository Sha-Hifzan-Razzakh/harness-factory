"""Tool-call emulation (SUB-02, FWK-01): models without native tool calling still run the tool-using shapes.

A served model with `caps.tool_calling: emulated` (catalog/providers.yaml) is never sent `tools`. ModelGateway wraps
its backend in ToolEmulation, which, for that model only:

  1. rewrites the conversation as plain chat: assistant tool calls become the JSON the model is asked to write,
     tool results become user messages ("[Tool result ...]"), so a provider without tool roles accepts it;
  2. lists the offered tools in the system message and lets instructor prepare the request for its reply model
     (instructor's MD_JSON mode: the JSON schema in the system message, a JSON code block asked for);
  3. makes the one call through the wrapped backend, inside ModelGateway.complete (quota slot, timeout, token and
     cost accounting, and provider errors classified only in classify_provider_error: nothing here catches them);
  4. parses and validates the reply with instructor (process_response) into native-format tool calls, so the
     shapes, the tool executor and the graders see exactly what a native model would have produced.

A reply instructor cannot validate (bad JSON, a tool that wasn't offered, the wrong shape) is rejected as
MODEL_PARSE, the same as a provider rejecting a malformed native tool call (Groq's tool_use_failed): the gateway
records it as a failed model call, the trajectory grader counts it as an invalid call in Q, and recovering from it
(`recover_malformed`) costs a step like any other recovery. instructor's own retries are never used: a re-ask is a
model call the harness pays for, never a free one inside a library. A reply with no JSON object at all ("{" nowhere)
is the model answering in prose: like native content without tool calls, it is a final answer.

Native models are untouched: the wrapper passes their calls straight through, and nothing here enters their hashes.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, create_model, field_validator

from harness_factory.substrate.errors import ErrorKind, HarnessError

EMULATED = "emulated"
MODE = "markdown_json_mode"            # instructor.Mode.MD_JSON: no response_format, so it suits any chat model

TOOL_PROMPT = (
    "You cannot call tools directly. To use a tool, reply with a JSON object whose \"tool_calls\" list names the "
    "tools to run, in order, with their arguments. The results come back to you as messages that start with "
    "\"[Tool result\". When you are finished, reply with an empty \"tool_calls\" list and your final answer in "
    "\"content\".\n\nTools you can call:\n{tools}"
)
REPLY_PROMPT = "Reply with the JSON object now: the tool calls to make next, or none and your final answer."
TOOL_RESULT = "[Tool result {id} {name}]\n{content}"
_RESULT_HEAD = "[Tool result "


def fingerprint() -> str:
    """What an emulated model sees beyond the tool specs. It joins the provider model id in RunKey.env_hash of
    emulated models only, so changing the emulation is a new experiment; native models' hashes never include it."""
    from harness_factory.hashing import content_hash
    return content_hash({"mode": MODE, "tool_prompt": TOOL_PROMPT, "reply_prompt": REPLY_PROMPT,
                         "tool_result": TOOL_RESULT}, 8)


def is_emulated(served: Any) -> bool:
    return getattr(getattr(served, "caps", None), "tool_calling", None) == EMULATED


# ------------------------------------------------------------------ conversation <-> plain chat

def _arguments(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError:
            return raw
    return raw


def reply_json(message: dict[str, Any]) -> str:
    """A native assistant message as the JSON an emulated model writes for it."""
    calls = [{"name": c["function"]["name"], "arguments": _arguments(c["function"].get("arguments"))}
             for c in message.get("tool_calls") or []]
    return json.dumps({"tool_calls": calls, "content": message.get("content")})


def to_plain_chat(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The conversation without tool roles. Always new dicts: the run's own messages are never modified."""
    names: dict[str, str] = {}
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for c in m["tool_calls"]:
                names[c["id"]] = c["function"]["name"]
            out.append({"role": "assistant", "content": reply_json(m)})
        elif m.get("role") == "tool":
            cid = m.get("tool_call_id", "")
            out.append({"role": "user", "content": TOOL_RESULT.format(id=cid, name=names.get(cid, "tool"),
                                                                      content=m.get("content") or "")})
        else:
            out.append(dict(m))
    return out


def describe_tools(tools: list[dict[str, Any]]) -> str:
    lines = []
    for t in tools:
        f = t["function"]
        lines.append(f"- {f['name']}: {f.get('description') or ''}".rstrip()
                     + f"\n  arguments (JSON schema): {json.dumps(f.get('parameters') or {}, sort_keys=True)}")
    return "\n".join(lines)


def with_tool_prompt(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tool list into the system message, and a closing request for the reply (instructor adds its own lines)."""
    prompt = TOOL_PROMPT.format(tools=describe_tools(tools))
    out = list(messages)
    if out and out[0].get("role") == "system":
        out[0] = {**out[0], "content": f"{out[0].get('content') or ''}\n\n{prompt}"}
    else:
        out.insert(0, {"role": "system", "content": prompt})
    out.append({"role": "user", "content": REPLY_PROMPT})
    return out


def unframed(text: str) -> str:
    """instructor merges consecutive user messages, so the closing request can end the previous user message."""
    cut = text.rfind(f"\n\n{REPLY_PROMPT}")
    return text[:cut] if cut >= 0 else text


def tool_result(message: dict[str, Any]) -> str | None:
    """The text of a tool result, native (role tool) or emulated ("[Tool result ...]"); None for anything else."""
    if message.get("role") == "tool":
        return message.get("content") or ""
    text = message.get("content") if message.get("role") == "user" else None
    if isinstance(text, str) and text.startswith(_RESULT_HEAD):
        return unframed(text.partition("\n")[2])
    return None


def _scaffold(message: dict[str, Any]) -> bool:
    return message.get("role") == "user" and str(message.get("content") or "").startswith(REPLY_PROMPT)


def latest_tool_result(messages: list[dict[str, Any]]) -> str | None:
    """The tool result the conversation ends with (past the emulation's closing request), or None."""
    rest = [m for m in messages if not _scaffold(m)]
    return tool_result(rest[-1]) if rest else None


def latest_user_text(messages: list[dict[str, Any]]) -> str:
    """The last real user message: not a tool result, not the emulation's closing request."""
    return next((unframed(m.get("content") or "") for m in reversed(messages)
                 if m.get("role") == "user" and tool_result(m) is None and not _scaffold(m)), "")


# ------------------------------------------------------------------ the reply model instructor validates

def _json_arguments(cls, v: Any) -> Any:
    """Native arguments are a JSON string; accept that form too."""
    return json.loads(v or "{}") if isinstance(v, str) else v


def reply_model(names: tuple[str, ...]) -> type[BaseModel]:
    """{"tool_calls": [{"name": <an offered tool>, "arguments": {...}}], "content": "..."}. Arguments are checked by
    the tool executor (TOOL_ARGS), as they are for native models; an unknown tool name is the model's malformed
    output, as a provider rejects it natively. Built per call (no module-level cache: runs share the process)."""
    call = create_model("ToolCall", name=(Literal[names], ...),  # type: ignore[valid-type]
                        arguments=(dict[str, Any], Field(default_factory=dict)),
                        __validators__={"json_arguments": field_validator("arguments", mode="before")(_json_arguments)})
    return create_model("ToolReply", tool_calls=(list[call], Field(default_factory=list)),  # type: ignore[valid-type]
                        content=(str | None, None))


def _instructor():
    try:
        from instructor import Mode
        from instructor.core.exceptions import InstructorError
        from instructor.processing.response import handle_response_model, process_response
    except ImportError as e:
        raise HarnessError(ErrorKind.PROVISION, "tool-call emulation needs instructor: uv sync --extra emulation") \
            from e
    return Mode(MODE), InstructorError, handle_response_model, process_response


def _chat_completion(text: str) -> Any:
    from openai.types.chat import ChatCompletion
    return ChatCompletion.model_validate({
        "id": "emulated", "object": "chat.completion", "created": 0, "model": "emulated",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}]})


class ToolEmulation:
    """Backend wrapper (see the module docstring). Lives in one ModelGateway, so its call counter is per run."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.replies = 0

    @classmethod
    def wrap(cls, backend: Any, provider: Any) -> Any:
        """The backend itself when the provider serves no emulated model: native providers see no change."""
        return cls(backend) if any(is_emulated(m) for m in provider.models) else backend

    @property
    def accepts_response_format(self) -> bool:
        """Transparent to JSON mode: ModelGateway asks the outermost backend, so the wrapper answers for the one
        inside. Without this, wrapping a provider for one emulated model would turn JSON mode off for its native
        models too."""
        return getattr(self.inner, "accepts_response_format", False)

    async def complete(self, messages, tools, slot, provider, served, **kw):
        """kw: call options from ModelGateway (response_format) for the backend inside, passed on unchanged where
        the provider answers freely. On the emulated tool path the reply format is the emulation's (instructor's
        JSON block), so they are not sent there; ModelGateway sends JSON mode only for tool-less planner calls."""
        from harness_factory.substrate.gateway import Completion

        if not is_emulated(served):
            return await self.inner.complete(messages, tools, slot, provider, served, **kw)
        chat = to_plain_chat(messages)
        if not tools:                                   # nothing to call (V1 without tools, the planner slot)
            return await self.inner.complete(chat, [], slot, provider, served, **kw)
        mode, instructor_error, handle_response_model, process_response = _instructor()
        model = reply_model(tuple(t["function"]["name"] for t in tools))
        _, request = handle_response_model(model, mode=mode, messages=with_tool_prompt(chat, tools))
        # The provider is never sent tools. A scripted stand-in reads them as structured specs instead of parsing
        # the prompt (what a real model reads), so it can follow its plan.
        offered = tools if getattr(self.inner, "reads_tools_from_prompt", False) else []
        comp = await self.inner.complete(request["messages"], offered, slot, provider, served)
        text = comp.message.get("content") or ""
        self.replies += 1
        try:
            reply = process_response(_chat_completion(text), response_model=model, stream=False, mode=mode)
        except (ValidationError, instructor_error, ValueError) as e:
            if "{" not in text:                         # prose, no JSON attempted: a final answer
                return comp.model_copy(update={"message": {"role": "assistant", "content": text}})
            detail = e.errors()[0]["msg"] if isinstance(e, ValidationError) and e.errors() else str(e)
            raise HarnessError(ErrorKind.MODEL_PARSE, f"emulated tool call failed validation: {detail[:200]}; "
                                                      f"reply: {text[:200]}") from e
        msg: dict[str, Any] = {"role": "assistant", "content": reply.content}
        if reply.tool_calls:
            msg["tool_calls"] = [{"id": f"call_emu_{self.replies}_{k}", "type": "function",
                                  "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                                 for k, c in enumerate(reply.tool_calls)]
        return Completion(message=msg, tokens_in=comp.tokens_in, tokens_out=comp.tokens_out, usd=comp.usd,
                          finish_reason=comp.finish_reason, served_by=comp.served_by)


class TextReplies:
    """Scripted stand-in for a model without native tool calling (offline runs and tests): wraps a scripted backend
    that answers with native tool calls and writes each reply as text instead, the JSON code block the emulation
    asks for. Without tools offered it answers as the wrapped backend does."""

    reads_tools_from_prompt = True

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    @property
    def accepts_response_format(self) -> bool:
        return getattr(self.inner, "accepts_response_format", False)

    async def complete(self, messages, tools, slot, provider, served, **kw):
        from harness_factory.substrate.gateway import approx_tokens

        comp = await self.inner.complete(messages, tools, slot, provider, served, **kw)
        if not tools:
            return comp
        text = f"```json\n{reply_json(comp.message)}\n```"
        return comp.model_copy(update={"message": {"role": "assistant", "content": text},
                                       "tokens_out": approx_tokens(text)})
