"""Edit one model entry of catalog/providers.yaml in place, as text.

providers.yaml carries hand-written comments that matter (where limits were checked and when, which models were
retired), and a YAML round trip would drop them. So a model is found by its provider block and `canonical:`, and
only the lines of its flow mapping (`- {canonical: ..., ...}`, possibly over several lines) are replaced. Every other
line, comments included, is kept byte for byte.
"""

from __future__ import annotations

import re

import yaml

_PROVIDER = re.compile(r"^\s*-\s+id:\s*['\"]?([^'\"\s#]+)")
_MODEL = re.compile(r"^(\s*)-\s+\{")
_CANON = re.compile(r"canonical:\s*['\"]?([^,'\"}\s]+)")


def _code(line: str) -> str:
    return line.split("#", 1)[0]


def find(text: str, provider: str, canonical: str) -> tuple[int, int, str] | None:
    """(first line, last line, indent) of the model's entry, or None."""
    lines = text.splitlines()
    current = None
    i = 0
    while i < len(lines):
        m = _PROVIDER.match(lines[i])
        if m and not lines[i].lstrip().startswith("- {"):
            current = m.group(1)
        mm = _MODEL.match(lines[i])
        if mm:
            depth, j = 0, i
            while True:
                code = _code(lines[j])
                depth += code.count("{") - code.count("}")
                if depth <= 0 or j == len(lines) - 1:
                    break
                j += 1
            c = _CANON.search(" ".join(_code(x) for x in lines[i:j + 1]))
            if current == provider and c and c.group(1) == canonical:
                return i, j, mm.group(1)
            i = j + 1
            continue
        i += 1
    return None


def provider_end(text: str, provider: str) -> tuple[int, str] | None:
    """(index of the provider's last model line, indent of its model entries), or None if it has no models."""
    lines = text.splitlines()
    current, last, indent = None, None, None
    for i, line in enumerate(lines):
        m = _PROVIDER.match(line)
        if m and not line.lstrip().startswith("- {"):
            current = m.group(1)
            continue
        if current == provider:
            mm = _MODEL.match(line)
            if mm:
                indent = mm.group(1)
            if indent is not None and line.strip() and not line.lstrip().startswith("#") \
                    and len(line) - len(line.lstrip()) >= len(indent):
                last = i
    return (last, indent) if last is not None else None


def providers_in(text: str) -> list[str]:
    return [m.group(1) for line in text.splitlines() if (m := _PROVIDER.match(line))
            and not line.lstrip().startswith("- {")]


def entry_text(text: str, provider: str, canonical: str) -> str | None:
    span = find(text, provider, canonical)
    if span is None:
        return None
    lines = text.splitlines()
    return "\n".join(lines[span[0]:span[1] + 1]) + "\n"


def render(fields: dict, indent: str, comment: str = "") -> str:
    body = yaml.safe_dump(fields, default_flow_style=True, sort_keys=False, width=10**6).strip()
    return f"{indent}- {body}{('   ' + comment) if comment else ''}\n"


def compact(model: dict) -> dict:
    """The model's fields without defaults, so a rendered line stays as short as hand-written ones."""
    out = {"canonical": model["canonical"], "provider_model_id": model["provider_model_id"]}
    for k in ("price_in", "price_out"):
        if model.get(k):
            out[k] = model[k]
    if model.get("free"):
        out["free"] = True
    defaults = {"tool_calling": "native", "structured_output": True, "context_len": 32_768}
    caps = {k: v for k, v in (model.get("caps") or {}).items() if v is not None and v != defaults.get(k)}
    if caps.get("reasoning") is not None:
        caps["reasoning"] = {k: v for k, v in caps["reasoning"].items() if v or k == "mode"}
    if caps:
        out["caps"] = caps
    if model.get("limits"):
        out["limits"] = {k: v for k, v in model["limits"].items() if v is not None}
    if model.get("endpoint"):
        ep_defaults = {"order": [], "quantizations": [], "require_parameters": False, "allow_fallbacks": True}
        ep = {k: v for k, v in model["endpoint"].items() if v != ep_defaults.get(k)}
        out["endpoint"] = ep
    return out


def replace(text: str, provider: str, canonical: str, new_entry: str | None) -> str:
    """Replace (new_entry text), add (canonical not there yet) or remove (new_entry None) one model."""
    lines = text.splitlines(keepends=True)
    span = find(text, provider, canonical)
    if span is not None:
        start, end, _ = span
        return "".join(lines[:start]) + (new_entry or "") + "".join(lines[end + 1:])
    if new_entry is None:
        return text
    where = provider_end(text, provider)
    if where is None:
        raise ValueError(f"provider {provider!r} has no models list to add to")
    last, _ = where
    return "".join(lines[:last + 1]) + new_entry + "".join(lines[last + 1:])


def trailing_comment(entry: str) -> str:
    """The `# ...` after the closing brace of an existing entry (kept when the entry is rewritten)."""
    last = entry.rstrip("\n").splitlines()[-1]
    return "#" + last.split("#", 1)[1].rstrip() if "#" in last and last.rfind("}") < last.find("#") else ""
