"""Context accounting (SUB-06, ADR 0015): how big the next prompt is, and how big it may be.

The estimate is offline, deterministic and the same for every model: UTF-8 bytes of the messages and tool schemas,
as compact JSON, divided by CHARS_PER_TOKEN, plus a few tokens per message for the chat format. No tokenizer is
loaded: tiktoken's encodings are bundled with LiteLLM, but they are OpenAI's, not those of the open models the
factory runs, and loading one costs seconds and ties the number to a LiteLLM release. Three bytes per token
over-counts prose and JSON (about four and 3.6 per token with cl100k on this repository's files and tool messages)
and under-counts only dense numbers (down to about two), so it errs towards compacting early. Each model call records
the estimate next to the provider's own count (`est_tokens_in` vs `tokens_in` on the model_call span), so the ratio
can be checked per model from traces.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

CHARS_PER_TOKEN = 3.0
MESSAGE_OVERHEAD = 4            # role and separators per message


def estimate_tokens(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> int:
    """Estimated prompt tokens of one model call. Deterministic: the same messages and tools always give the same
    number, on every platform."""
    size = len(json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          default=str).encode("utf-8"))
    if tools:
        size += len(json.dumps(tools, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                               default=str).encode("utf-8"))
    return math.ceil(size / CHARS_PER_TOKEN) + MESSAGE_OVERHEAD * len(messages)


@dataclass(frozen=True)
class ContextWindow:
    context_len: int            # the model's context length (providers.yaml caps.context_len)
    reply: int                  # the reply budget of the slot (max_output_tokens)

    @property
    def limit(self) -> int:
        """Largest prompt that still leaves room for a full reply."""
        return self.context_len - self.reply

    def trigger(self, at: float) -> int:
        """Prompt size above which a compacting recipe compacts: `at` of the context, and never above the limit."""
        return min(int(self.context_len * at), self.limit)


def window_for(catalog, provider_id: str, canonical_model: str | None, reply: int) -> ContextWindow | None:
    """The context window of the model a slot runs on, or None when the catalog does not serve it."""
    if not canonical_model:
        return None
    try:
        served = catalog.served(provider_id, canonical_model)
    except KeyError:
        return None
    return ContextWindow(context_len=served.caps.context_len, reply=reply)


def scripted_summary(messages: list[dict[str, Any]]) -> str:
    """What the scripted models answer to a compaction request (slot role "compactor"): deterministic, and built
    only from the request, so it never moves a script forward."""
    text = str(messages[-1].get("content") or "") if messages else ""
    calls = [line.split("]", 1)[0].removeprefix("[assistant calls ") for line in text.splitlines()
             if line.startswith("[assistant calls ")]
    return f"Earlier: {len(calls)} tool call(s) ({', '.join(calls) or 'none'}); their results were used as planned."
