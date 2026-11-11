"""Build spec cards from the loaded catalog (Factory). Nothing here runs a model, a tool or a sandbox.

Cards:
- model:   a canonical model, every provider that serves it, caps, prices, limits, measured runs
- harness: a recipe, resolved (base merged), what it changes vs its base, prompts, tools, budgets, policies
- task:    instruction, class, seed, allowance, checks, reference plan
- tool:    an MCP server and its declared tools
- fault:   a fault profile's rules
- spec:    one experiment, task x harness x model x provider x seed x fault, with its RunKey, the exact tools
           and prompts the model will see, validation, a cost estimate, earlier results and a READY/BLOCKED verdict
"""

from __future__ import annotations

import statistics
from pathlib import Path

import yaml
from jinja2 import TemplateError

from harness_factory import settings
from harness_factory.cards.model import Card, Section, Table
from harness_factory.hashing import content_hash

_OPTIONAL_DIMENSIONS = ("retrieval", "workflow", "planning", "delegation", "runtime")


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, (list, tuple)):
        return ", ".join(_fmt(x) for x in v) or "-"
    if isinstance(v, dict):
        return ", ".join(f"{k}={_fmt(x)}" for k, x in v.items()) or "-"
    return str(v)


def _rows(f) -> list[dict]:
    return f.store.rows() if f.store is not None else []


def _scored(rows: list[dict]) -> list[dict]:
    from harness_factory.results.store import UNSCORED
    return [r for r in rows if r["status"] not in UNSCORED]


def _perf_table(rows: list[dict], key: str) -> Table | None:
    """Mean E0 and pass rate of scored clean runs, grouped by `key` (a row column)."""
    groups: dict[str, list[dict]] = {}
    for r in _scored(rows):
        if r.get("fault"):
            continue
        groups.setdefault(r[key], []).append(r)
    if not groups:
        return None
    out = []
    for k, rs in sorted(groups.items()):
        ok = sum(r["status"] == "SUCCESS" for r in rs)
        out.append([k, str(len(rs)), f"{statistics.fmean(r['composite'] for r in rs):.3f}", f"{ok}/{len(rs)}",
                    f"{statistics.fmean(r['tokens_in'] + r['tokens_out'] for r in rs):,.0f}"])
    return Table(headers=[key.replace("_", " "), "runs", "mean E0", "SUCCESS", "mean tokens"], rows=out)


# --------------------------------------------------------------------------------------------------- model

def model_card(f, canonical: str) -> Card:
    served = [(p, m) for p in f.providers.providers for m in p.models if m.canonical == canonical]
    if not served:
        known = sorted({m.canonical for p in f.providers.providers for m in p.models})
        raise KeyError(f"no provider serves {canonical!r}; known models: {', '.join(known)}")
    sections = []
    for p, m in served:
        limits = m.limits or p.limits
        sections.append(Section(title=f"Served by {p.id}", fields=[
            ("provider model id", m.provider_model_id),
            ("provider kind", p.kind + (f" ({p.litellm_prefix})" if p.litellm_prefix else "")),
            ("status", p.status),
            ("API key variable", p.auth_env or "-"),
            ("tool calling", m.caps.tool_calling),
            ("structured output", _fmt(m.caps.structured_output)),
            ("context window", f"{m.caps.context_len:,} tokens"),
            ("price in / out", f"${m.price_in:g} / ${m.price_out:g} per M tokens" + (" (free tier)" if m.free else "")),
            ("rate limits", _fmt({k: v for k, v in limits.model_dump().items() if v is not None})),
            ("quota bucket", f.providers.bucket(p.id, canonical)),
            ("data terms", p.data_terms),
        ]))
    rows = [r for r in _rows(f) if r["canonical_model"] == canonical]
    perf = _perf_table(rows, "recipe_id")
    sections.append(Section(title="Measured runs by recipe", table=perf,
                            note=None if perf else "No scored runs of this model in the results store yet."))
    return Card(kind="model", id=canonical, title=f"Model card: {canonical}",
                summary=f"{canonical}, served by {', '.join(p.id for p, _ in served)}",
                hash=content_hash([m.provider_model_id for _, m in served], 12), sections=sections)


# ------------------------------------------------------------------------------------------------- harness

def _recipe_file(recipe_id: str) -> tuple[Path | None, dict]:
    for p in sorted(settings.RECIPES_DIR.rglob("*.yaml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if data.get("id") == recipe_id:
            return p, data
    return None, {}


def _flat(d: dict, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and v:
            out.update(_flat(v, key + "."))
        else:
            out[key] = v
    return out


def recipe_changes(f, recipe_id: str) -> list[tuple[str, str, str]]:
    """(field, base value, this recipe's value) for every resolved field that differs from the base recipe."""
    recipe = f.recipes[recipe_id]
    if not recipe.base or recipe.base not in f.recipes:
        return []
    skip = {"id", "version", "base"}
    mine = _flat(recipe.model_dump(mode="json", exclude_none=True))
    base = _flat(f.recipes[recipe.base].model_dump(mode="json", exclude_none=True))
    keys = sorted(k for k in set(mine) | set(base) if k.split(".")[0] not in skip and mine.get(k) != base.get(k))
    return [(k, _fmt(base.get(k)), _fmt(mine.get(k))) for k in keys]


def _tool_rows(f, refs) -> list[list[str]]:
    rows = []
    for ref in sorted(refs):
        s = f.declared.spec(ref)
        rows.append([ref, s.side_effect.value, s.risk, f"{s.timeout_s:g}s",
                     "granted by " + ", ".join(s.granted_by) if s.granted_by else ("every task" if s.granted_by == [] else "-")])
    return rows


def harness_card(f, recipe_id: str) -> Card:
    from harness_factory.recipes.validate import validate_recipe
    from harness_factory.substrate.prompts import PromptRegistry

    if recipe_id not in f.recipes:
        raise KeyError(f"unknown recipe {recipe_id!r}; known: {', '.join(sorted(f.recipes))}")
    r = f.recipes[recipe_id]
    path, _ = _recipe_file(recipe_id)
    prompts = PromptRegistry()
    shape = r.variant.value
    sections = [Section(title="Identity", fields=[
        ("id / version", f"{r.id} v{r.version}"),
        ("file", str(path.relative_to(settings.ROOT)) if path else "-"),
        ("base", r.base or "- (a base recipe)"),
        ("shape", shape),
        ("runtime", r.runtime or "native"),
        ("tier", r.tier),
        ("recipe hash", r.content_hash()),
    ])]
    changes = recipe_changes(f, recipe_id)
    if r.base:
        sections.append(Section(title=f"Changes vs {r.base}",
                                table=Table(headers=["field", r.base, r.id], rows=[list(c) for c in changes])
                                if changes else None,
                                note=None if changes else "Identical to its base apart from its id."))
    sections.append(Section(title="Model slots", table=Table(
        headers=["slot", "role", "temperature", "max output tokens"],
        rows=[[k, s.role, _fmt(s.temperature), f"{s.max_output_tokens:,}"] for k, s in r.model_slots.items()]),
        note="The model itself is left empty on purpose: it is a campaign axis, chosen per run."))
    layers = [("system", r.prompts.system), ("operator", r.prompts.operator), ("task", r.prompts.task)]
    sections.append(Section(title="Prompts", table=Table(
        headers=["layer", "template", "exists"],
        rows=[[k, f"prompts/{v}.j2", _fmt(prompts.exists(v))] for k, v in layers if v])))
    reachable = [ref for ref in f.declared.all_refs()
                 if ref.split(".")[0] in r.tools.mcp_servers and r.tools.allows(ref)]
    sections.append(Section(title="Tools", fields=[
        ("servers opened", _fmt(r.tools.mcp_servers)),
        ("tool patterns", _fmt(r.tools.tool_ids)),
    ], table=Table(headers=["tool", "side effect", "risk", "timeout", "grant"], rows=_tool_rows(f, reachable)),
        note="A run offers only the tools that are also in the task's allowance (see the spec card)."))
    sections.append(Section(title="Permissions", fields=[(k, v) for k, v in r.policies.permission_mode.items()],
                            note="ASK is treated as DENY until the approval gate exists."))
    cfg = r.config
    sections.append(Section(title="Budgets and runtime config", fields=[
        ("max model calls (steps)", str(cfg.max_steps)), ("max tool calls", str(cfg.max_tool_calls)),
        ("max tokens", f"{cfg.max_tokens:,}"), ("wall time", f"{cfg.wall_time_s}s"),
        ("parallel tool calls", _fmt(cfg.parallel_tool_calls)),
        ("recover malformed replies", _fmt(cfg.recover_malformed)),
        ("compaction", _fmt(cfg.compaction.model_dump() if cfg.compaction else None)),
        ("call timeout", f"{cfg.call_timeout_s}s" if cfg.call_timeout_s else f"default ({settings.CALL_TIMEOUT_S}s)"),
    ]))
    sections.append(Section(title="Skills", fields=[("loading", r.skills.loading),
                                                    ("skills", _fmt(r.skills.skill_ids))]))
    dims = []
    if r.retrieval is not None:
        dims.append(("retrieval", _fmt(r.retrieval.model_dump())))
    if r.workflow is not None:
        dims.append(("workflow stages", " -> ".join(f"{s.id} (max {s.max_steps}, tools {_fmt(s.tools)})"
                                                     for s in r.workflow.stages)))
        dims.append(("retry from", _fmt(r.workflow.retry_from)))
    if r.planning is not None:
        dims.append(("planning", _fmt(r.planning.model_dump(exclude_none=True))))
    if r.delegation is not None:
        dims.append(("delegation", _fmt(r.delegation.model_dump())))
    if dims:
        sections.append(Section(title="Shape-specific settings", fields=dims))
    issues = [f"{i.rule}: {i.detail}" for i in validate_recipe(r, f.declared, providers=f.providers)]
    rows = [x for x in _rows(f) if x["recipe_id"] == recipe_id and x["recipe_hash"] == r.content_hash()]
    perf = _perf_table(rows, "task_class")
    sections.append(Section(title="Measured performance (current hash) by task class", table=perf,
                            note=None if perf else "No scored runs of this exact recipe version yet."))
    used_by = sorted(rid for rid, x in f.recipes.items() if x.base == recipe_id)
    if used_by:
        sections.append(Section(title="Recipes built on this one", text=", ".join(used_by)))
    reaches = [t.id for t in f.tasks.all() if _reaches(f, r, t)]
    sections.append(Section(title="Tasks it can attempt", text=", ".join(reaches) or "-",
                            note=f"{len(reaches)} DEV/TEST tasks: it opens every tool those tasks allow."))
    return Card(kind="harness", id=recipe_id, title=f"Harness card: {recipe_id}",
                summary=f"{shape} recipe" + (f", {r.base} + {len(changes)} change(s)" if r.base else ", a base recipe"),
                hash=r.content_hash(), sections=sections, issues=issues)


def _reaches(f, recipe, task) -> bool:
    from harness_factory.provision.provisioner import reaches
    return reaches(recipe, task, f.declared)


# ---------------------------------------------------------------------------------------------------- task

def _check_target(c) -> str:
    for attr in ("path", "query", "pattern", "cell", "ref"):
        v = getattr(c, attr, None)
        if v:
            return f"{attr}: {str(v)[:70]}"
    return "final answer" if c.kind.startswith("output") else "-"


def task_card(f, task_id: str) -> Card:
    from harness_factory.provision.provisioner import task_class, task_tools
    from harness_factory.tasks.reference import load_reference

    t = f.tasks.get(task_id)
    seed = t.env_seed
    sections = [Section(title="Identity", fields=[
        ("id / version", t.key()), ("domain", t.domain if isinstance(t.domain, str) else t.domain.value),
        ("class (depth | side effect | risk)", task_class(t, f.declared)),
        ("split", t.split.value), ("status", t.status), ("tags", _fmt(t.tags)),
        ("base task", t.base_id or "-"),
        ("perturbation", f"{t.perturbation.type}" if t.perturbation else "-"),
        ("task hash", t.content_hash()),
    ]), Section(title="Instruction (what the model reads)", text=t.instruction)]
    sections.append(Section(title="Environment seed", fields=[
        ("files", seed.files or "-"), ("sqlite", seed.sqlite or "-"), ("git repository", _fmt(seed.git)),
        ("business state", seed.biz_state or "-"), ("web corpus", seed.corpus or "-"),
        ("other JSON states", _fmt(seed.server_state)), ("remote tenants", _fmt(list(seed.mock_api_state)))]))
    tools = task_tools(t, f.declared)
    sections.append(Section(title="Allowed tools", table=Table(
        headers=["tool", "side effect", "risk", "timeout", "grant"], rows=_tool_rows(f, tools)),
        note=f"Declared allowance: {_fmt(t.allowed_tools)}. Granted extras are offered only if the recipe opens them."))
    checks = [[c.id, c.kind if isinstance(c.kind, str) else c.kind.value, f"{c.weight:g}",
               "guard" if c.weight == 0 and c.critical else ("critical" if c.critical else ""),
               _check_target(c)] for c in t.oracle.checks]
    sections.append(Section(title="Oracle checks", table=Table(
        headers=["check", "kind", "weight", "role", "target"], rows=checks),
        note=f"Pass threshold {t.oracle.pass_threshold:g}. A guard earns nothing, but failing it zeroes the run. "
             f"Graders: {_fmt(t.grader_ids)}."))
    try:
        ref = load_reference(t)
        steps = [[str(i + 1), s["tool"], _fmt(s.get("args", {}))[:90] + ("  (optional)" if s.get("optional") else "")]
                 for i, s in enumerate(ref.plan)]
        sections.append(Section(title=f"Reference plan ({len(ref.plan)} calls)",
                                table=Table(headers=["#", "tool", "arguments"], rows=steps),
                                note=f"Reference answer: {ref.final[:160]}"))
    except (FileNotFoundError, ValueError) as e:
        sections.append(Section(title="Reference plan", note=str(e)))
    rows = [r for r in _rows(f) if r["task_version"] == t.key()]
    perf = _perf_table(rows, "recipe_id")
    sections.append(Section(title="Measured runs by recipe", table=perf,
                            note=None if perf else "No scored runs of this task yet."))
    return Card(kind="task", id=t.id, title=f"Task card: {t.id}",
                summary=f"{task_class(t, f.declared)} task, {t.split.value}, {len(t.oracle.checks)} checks",
                hash=t.content_hash(), sections=sections)


# ---------------------------------------------------------------------------------------------- tool, fault

def tool_card(f, server_id: str) -> Card:
    s = f.declared.server(server_id)
    fixture = settings.ROOT / "catalog" / "conformance" / f"{server_id}.yaml"
    fields = [("transport", s.transport), ("module", s.module or "-"), ("url", s.url or "-"),
              ("pinned version", s.version or "-"), ("token variable", s.auth_env or "-"),
              ("test tenant", "yes" if s.tenant else "-"),
              ("conformance fixture", str(fixture.relative_to(settings.ROOT)) if fixture.is_file() else "MISSING")]
    rows = [[t.name, t.side_effect.value, t.risk, f"{t.timeout_s:g}s",
             f"{t.escalation.classifier} -> {t.escalation.side_effect.value}" if t.escalation else "-",
             ", ".join(t.granted_by) if t.granted_by else ("every task" if t.granted_by == [] else "-"),
             "frozen" if t.schema_hash else "NOT FROZEN"] for t in s.tools]
    users = sorted(rid for rid, r in f.recipes.items() if server_id in r.tools.mcp_servers)
    issues = [] if fixture.is_file() else [f"no conformance fixture catalog/conformance/{server_id}.yaml"]
    issues += [f"{t.name}: schema hash not frozen (run `hfx tools freeze`)" for t in s.tools if not t.schema_hash]
    return Card(kind="tool", id=server_id, title=f"Tool card: {server_id}",
                summary=f"{s.transport} MCP server with {len(s.tools)} tools",
                hash=content_hash([t.hashed() for t in s.tools], 12),
                sections=[Section(title="Server", fields=fields),
                          Section(title="Tools", table=Table(headers=["tool", "side effect", "risk", "timeout",
                                                                       "escalation", "granted by", "schema"],
                                                              rows=rows)),
                          Section(title="Recipes that open it", text=", ".join(users) or "-")],
                issues=issues)


def fault_card(f, fault_id: str) -> Card:
    fc = f.fault(fault_id)
    if fc is None:
        raise KeyError("'none' is the clean run: no fault profile")
    rows = [[_fmt(r.tools), r.kind if isinstance(r.kind, str) else r.kind.value, f"{r.rate:g}", _fmt(r.nth),
             _fmt(r.max_per_run), _fmt(r.delay_s)] for r in fc.rules]
    return Card(kind="fault", id=fc.id, title=f"Fault card: {fc.id}", summary=fc.description or "fault profile",
                hash=fc.content_hash(),
                sections=[Section(title="Rules", table=Table(headers=["tools", "kind", "rate", "nth call",
                                                                       "max per run", "delay"], rows=rows),
                                  note="Faults fire only in ToolExecutor; the model is never told a failure was "
                                       "injected; faulted runs feed only R.")])


# ---------------------------------------------------------------------------------------------------- spec

def spec_card(f, task_id: str, recipe_id: str, model: str, provider: str, seed: int = 0,
              fault: str | None = None, prompts: bool = True) -> Card:
    """The final spec card: everything one run would use, computed without running it."""
    from harness_factory.provision.provisioner import (
        allowed_tools,
        missing_task_tools,
        run_key_for,
        runs_task_code,
        task_class,
    )
    from harness_factory.recipes.validate import validate_recipe
    from harness_factory.runner.projection import Estimator
    from harness_factory.substrate.prompts import PromptAssembler
    from harness_factory.substrate.tools import model_name

    issues: list[str] = []
    task = f.tasks.get(task_id)
    if recipe_id not in f.recipes:
        raise KeyError(f"unknown recipe {recipe_id!r}; known: {', '.join(sorted(f.recipes))}")
    recipe = f.recipes[recipe_id]
    try:
        p = f.providers.get(provider)
        served = f.providers.served(provider, model)
    except KeyError as e:
        raise KeyError(str(e).strip("'\"")) from None
    fc = f.fault(fault)

    key = run_key_for(task, recipe, model, provider, seed, declared=f.declared, providers=f.providers, fault=fc)
    sections = [Section(title="Experiment", fields=[
        ("task", f"{task.key()} ({task_class(task, f.declared)}, {task.split.value})"),
        ("harness", f"{recipe.id} ({recipe.variant.value}{', runtime ' + recipe.runtime if recipe.runtime else ''})"),
        ("model", f"{model} via {provider} (provider id {served.provider_model_id})"),
        ("seed", str(seed)), ("fault profile", fault or "none (clean run)"),
        ("sandbox", settings.SANDBOX)])]
    sections.append(Section(title="Run identity (RunKey)", fields=[
        ("run key (short)", key.short()), ("task hash", key.task_hash), ("recipe hash", key.recipe_hash),
        ("environment hash", key.env_hash), ("fault hash", key.fault_config_hash)],
        note="Change any of these and it is a different experiment; --resume skips a run only on an equal key."))

    offered = sorted(allowed_tools(recipe, task, f.declared))
    rows = []
    for ref in offered:
        s = f.declared.spec(ref)
        perm = recipe.policies.permission_mode.get(s.side_effect.value, "DENY")
        rows.append([model_name(ref), s.side_effect.value, s.risk, perm,
                     f"{s.escalation.classifier} -> {s.escalation.side_effect.value}" if s.escalation else "-"])
        if perm != "AUTO":
            issues.append(f"{ref} is offered but its side effect {s.side_effect.value} is {perm} under this recipe")
    extras = []
    if recipe.delegation is not None:
        extras.append(f"delegate__run (helper {recipe.delegation.recipe}, max {recipe.delegation.max_calls} calls)")
    if recipe.retrieval is not None and recipe.retrieval.mode != "none":
        extras.append(f"retrieve__search ({recipe.retrieval.mode}, top {recipe.retrieval.top_k}) if the seed has corpus/")
    extras.append("spill__read, only after a tool returns more than 16,000 characters")
    sections.append(Section(title="Tools the model will see", table=Table(
        headers=["model-facing name", "side effect", "risk", "permission", "escalation"], rows=rows),
        note="Harness tools: " + "; ".join(extras)))
    missing = missing_task_tools(recipe, task, f.declared)
    if missing:
        issues.append(f"the recipe can't call {', '.join(missing)}, which the task allows: campaigns skip this pair "
                      f"(use a recipe that opens {', '.join(sorted({m.split('.')[0] for m in missing}))})")

    cfg = recipe.config
    slot = recipe.model_slots.get("executor")
    sections.append(Section(title="Budgets", fields=[
        ("max model calls", str(cfg.max_steps)), ("max tool calls", str(cfg.max_tool_calls)),
        ("max tokens", f"{cfg.max_tokens:,}"), ("wall time", f"{cfg.wall_time_s}s"),
        ("max output tokens per call", f"{slot.max_output_tokens:,}" if slot else "-"),
        ("model context window", f"{served.caps.context_len:,}"),
        ("temperature", _fmt(slot.temperature) if slot else "-")]))

    for i in validate_recipe(recipe, f.declared, providers=f.providers, provider_id=provider):
        issues.append(f"recipe {i.rule}: {i.detail}")
    if served.caps.tool_calling == "none" and recipe.variant.value != "V1_SINGLE_CALL":
        issues.append(f"{model} can't call tools; only a V1 recipe fits it")
    if p.kind != "scripted" and runs_task_code(recipe, task) and settings.SANDBOX == "local" \
            and not settings.allow_local_code():
        issues.append("a real model would run workspace code in a local sandbox: set HF_SANDBOX=docker "
                      "(or HF_ALLOW_LOCAL_CODE=1)")
    if p.kind != "scripted" and p.auth_env and not __import__("os").environ.get(p.auth_env):
        issues.append(f"{p.auth_env} is not set (put it in .env)")
    if p.status != "active":
        issues.append(f"provider {provider} is {p.status}")
    if fc is not None and task.bound_faults() is not None:
        issues.append(f"{task.id} binds its own faults; a campaign fault profile is refused on it")
    unknown = f.graders.unknown(task)
    if unknown:
        issues.append(f"unknown grader(s) {', '.join(unknown)}")

    if prompts:
        try:
            msgs = PromptAssembler().assemble(recipe.with_model(model), task, [model_name(r) for r in offered])
            for m in msgs:
                sections.append(Section(title=f"Prompt sent: {m['role']}", text=m["content"]))
            if recipe.workflow is not None:
                for stage, text in PromptAssembler().stage_prompts(recipe, task, [model_name(r) for r in offered]).items():
                    sections.append(Section(title=f"Prompt sent: stage {stage}", text=text))
        except (TemplateError, KeyError, ValueError) as e:   # a template that doesn't render blocks the run
            issues.append(f"prompts don't render: {e}")

    rows_all = _rows(f)
    est = Estimator(rows_all, {x.id for x in f.providers.providers if x.kind == "scripted"}, f.recipes).estimate(
        model, provider, recipe, task.key())
    usd = est.usd if est.usd is not None else (est.tokens * 0.7 * served.price_in + est.tokens * 0.3 * served.price_out) / 1e6
    sections.append(Section(title="Cost estimate", fields=[
        ("model calls", f"{est.calls:g}"), ("tokens", f"{est.tokens:,.0f}"),
        ("list price", f"${usd:.4f}" + ("" if est.usd is not None else " (from tokens at list price)")),
        ("estimated from", est.source)],
        note="From earlier runs, most specific first; 'default' means no history for this model and recipe."))

    same = [r for r in rows_all if r["run_key"] == key.as_str()]
    if same:
        last = same[-1]
        sections.append(Section(title="Already run", fields=[
            ("runs with this exact key", str(len(same))), ("latest status", last["status"]),
            ("latest E0", f"{last['composite']:.3f}"), ("when", str(last["created_at"])),
            ("transcript", last.get("transcript_path") or "-")],
            note="Running again adds a new row; the newest scored row per key wins."))
    verdict = "BLOCKED" if any(_blocking(i) for i in issues) else "READY"
    return Card(kind="spec", id=key.short(), title=f"Spec card: {task.id} x {recipe.id} x {model}",
                summary=f"{task.key()} with {recipe.id} on {model}@{provider}, seed {seed}, "
                        f"{'fault ' + fault if fault else 'clean'}",
                hash=key.short(), sections=sections, issues=issues, verdict=verdict)


def _blocking(issue: str) -> bool:
    """Issues that stop the run (or make it meaningless); the others are warnings."""
    warn = (" is offered but its side effect",)
    return not any(w in issue for w in warn)


def attach_row(card: Card, row: dict) -> Card:
    """The final spec card from a stored run (the UI runs through campaigns, so it has the row, not the record)."""
    failed = [c["id"] for g in (row.get("grades") or []) for c in g.get("checks", []) if not c.get("passed")]
    card.result = {"status": row["status"], "E0": round(row["composite"], 4), "outcome": round(row["outcome"], 4),
                   "trajectory": round(row["trajectory"], 4), "model_calls": row["model_calls"],
                   "tool_calls": row["tool_calls"], "tokens": row["tokens_in"] + row["tokens_out"],
                   "usd": round(row["usd"], 6), "wall_s": round(row["wall_s"], 2),
                   "violations": [v.get("kind") for v in (row.get("violations") or [])], "failed_checks": failed,
                   "answer": (row.get("output") or "")[:500], "trace": row.get("trace_path"),
                   "transcript": row.get("transcript_path"), "run_id": row.get("id")}
    fields = [(k.replace("_", " "), _fmt(v)) for k, v in card.result.items() if v not in (None, "", [])]
    card.sections.insert(0, Section(title="Result", fields=fields))
    return card


def latest_row(f, card: Card) -> dict | None:
    """The newest stored run whose short RunKey is this spec card's id."""
    rows = [r for r in _rows(f) if r.get("run_short") == card.id]
    return max(rows, key=lambda r: r["id"]) if rows else None


def attach_result(card: Card, rec) -> Card:
    """Turn a spec card into the final spec card: the run's outcome next to its spec."""
    s = rec.scores
    card.result = {"status": rec.status.value, "E0": round(rec.composite, 4),
                   "outcome": round(s.outcome, 4) if s.outcome is not None else None,
                   "trajectory": round(s.trajectory, 4) if s.trajectory is not None else None,
                   "model_calls": rec.cost.model_calls, "tool_calls": rec.cost.tool_calls,
                   "tokens": rec.cost.tokens_in + rec.cost.tokens_out, "usd": round(rec.cost.usd, 6),
                   "wall_s": round(rec.cost.wall_s, 2), "violations": [v.kind for v in rec.violations],
                   "failed_checks": [c["id"] for g in rec.grades for c in g.get("checks", []) if not c.get("passed")],
                   "answer": (rec.output or "")[:500], "trace": rec.trace_path, "transcript": rec.transcript_path}
    fields = [(k.replace("_", " "), _fmt(v)) for k, v in card.result.items() if v not in (None, "", [])]
    card.sections.insert(0, Section(title="Result", fields=fields))
    return card
