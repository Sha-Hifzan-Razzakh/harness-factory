"""RecipeValidator (B2): a recipe that fails any rule is never provisioned."""

from __future__ import annotations

from fnmatch import fnmatch

from pydantic import BaseModel

from harness_factory.env.mcp.servers import RISK_TO_TIER, TIER_ORDER, ToolCatalogFile
from harness_factory.providers.catalog import ProviderCatalog
from harness_factory.recipes.model import (
    STRUCTURED_PLAN_PROMPTS,
    HarnessRecipe,
    MemoryProfile,
    ModelSlot,
    RuntimeConfig,
    ToolSlice,
    Variant,
)
from harness_factory.substrate.prompts import PromptRegistry


class ValidationIssue(BaseModel):
    rule: str
    detail: str


def inert_fields(recipe: HarnessRecipe) -> list[str]:
    """Recipe fields set away from their default that nothing in the factory acts on yet. A recipe that sets one
    would be measured as if it had that feature while running without it, so it is refused until the field is
    implemented (ADR 0002). Remove a line here in the PR that implements the field."""
    d_cfg, d_tools = RuntimeConfig(), ToolSlice(tool_ids=[], mcp_servers=[])
    out = []
    if recipe.memory != MemoryProfile():
        out.append("memory")
    if recipe.config.compaction_at != d_cfg.compaction_at:
        out.append("config.compaction_at")
    for f in ("deferred", "defer_above_tokens", "binding"):
        if getattr(recipe.tools, f) != getattr(d_tools, f):
            out.append(f"tools.{f}")
    if recipe.policies.approval_rules:
        out.append("policies.approval_rules")
    if not recipe.headless:
        out.append("headless")
    if recipe.grader_hooks:
        out.append("grader_hooks")
    return out


def reachable_tools(recipe: HarnessRecipe, declared: ToolCatalogFile) -> list[str]:
    """Tools the recipe allows on its servers, minus those its policy denies outright, plus the retrieval tool when
    the recipe retrieves (offered only on tasks with a corpus, ADR 0017)."""
    from harness_factory.retrieval import tool as retrieval
    out = []
    if retrieval.active(recipe) and recipe.policies.permission_mode.get(retrieval.RETRIEVE_SPEC.side_effect.value) != "DENY":
        out.append(retrieval.RETRIEVE_REF)
    for sid in recipe.tools.mcp_servers:
        try:
            server = declared.server(sid)
        except KeyError:
            continue
        for t in server.tools:
            ref = t.ref(sid)
            if recipe.tools.allows(ref) and recipe.policies.permission_mode.get(t.side_effect.value) != "DENY":
                out.append(ref)
    return out


def _skill_issues(recipe: HarnessRecipe, declared: ToolCatalogFile) -> list[ValidationIssue]:
    from harness_factory.skills.library import load_skills
    sk, out = recipe.skills, []
    if sk.loading == "none" and sk.skill_ids:
        out.append(ValidationIssue(rule="skills_loading", detail="skill_ids set but loading is none"))
    if sk.loading != "none" and not sk.skill_ids:
        out.append(ValidationIssue(rule="skills_loading", detail=f"loading {sk.loading} with no skill_ids"))
    unknown = sorted(set(sk.skill_ids) - set(load_skills()))
    if unknown:
        out.append(ValidationIssue(rule="skill_exists", detail=f"no SKILL.md for {', '.join(unknown)}"))
    reachable = reachable_tools(recipe, declared)
    if sk.loading == "on_demand" and "skills.load" not in reachable:
        out.append(ValidationIssue(rule="skills_tool_reachable",
                                   detail="loading on_demand needs the skills server and skills.load in the slice"))
    # Skill files are installed for the skills server only when loading is on_demand (provisioner.install_skills).
    # Any other loading leaves the server empty: every call answers "no skill named ...; available: none".
    served = sorted(ref for ref in reachable if ref.split(".", 1)[0] == "skills")
    if sk.loading != "on_demand" and served:
        out.append(ValidationIssue(rule="skills_server_unserved",
                                   detail=f"loading {sk.loading} installs no skills for the skills server, so "
                                          f"{', '.join(served)} can only answer 'available: none'; use loading "
                                          f"on_demand, or drop the skills server from the recipe"))
    return out


def _retrieval_issues(recipe: HarnessRecipe, declared: ToolCatalogFile) -> list[ValidationIssue]:
    """ADR 0017. A task without a corpus simply doesn't get the tool, so nothing here depends on tasks."""
    from harness_factory.retrieval import tool as retrieval
    r = recipe.retrieval
    if r is None:
        return []
    if r.mode == "none":
        return [ValidationIssue(rule="retrieval_unused", detail="retrieval.rerank is set but mode is none")] \
            if r.rerank else []
    out = []
    if not retrieval.installed():
        out.append(ValidationIssue(rule="retrieval_installed",
                                   detail=f"retrieval mode {r.mode} needs the retrieval extra (uv sync --extra retrieval)"))
    if retrieval.RETRIEVE_SERVER in {s.id for s in declared.servers}:
        out.append(ValidationIssue(rule="retrieval_namespace",
                                   detail=f"tools.yaml declares a server {retrieval.RETRIEVE_SERVER!r}, the retrieval "
                                          f"tool's namespace"))
    if recipe.policies.permission_mode.get(retrieval.RETRIEVE_SPEC.side_effect.value) == "DENY":
        out.append(ValidationIssue(rule="retrieval_unused", detail="retrieval is set but the policy denies READ tools"))
    if recipe.variant == Variant.V1_SINGLE_CALL:
        out.append(ValidationIssue(rule="retrieval_unused", detail="V1_SINGLE_CALL ignores the retrieval tool"))
    return out


def _delegation_issues(recipe: HarnessRecipe) -> list[ValidationIssue]:
    """EXT-09/11: the child must exist, and open no server its parent doesn't (it runs on the parent's sessions)."""
    d = recipe.delegation
    if d is None:
        return []
    if d.child is None:
        return [ValidationIssue(rule="delegate_resolves", detail=f"delegation.recipe {d.recipe!r} is not a known recipe")]
    extra = sorted(set(d.child.tools.mcp_servers) - set(recipe.tools.mcp_servers))
    if extra:
        return [ValidationIssue(rule="delegate_servers",
                                detail=f"{d.child.id} opens {', '.join(extra)}, which {recipe.id} does not")]
    return []


def _context_issues(recipe: HarnessRecipe, prompts: PromptRegistry, providers: ProviderCatalog | None,
                    provider_id: str | None) -> list[ValidationIssue]:
    """RCP-05 (context part, ADR 0015). The compaction prompt exists and its slot is unpinned; with a provider
    catalog, every slot's reply budget leaves room for a prompt in the model's context_len. At provisioning that is
    the run's model; in `hfx validate` (no model yet) every model the catalog serves."""
    out: list[ValidationIssue] = []
    spec = recipe.config.compaction
    if spec is not None and not prompts.exists(spec.prompt):
        out.append(ValidationIssue(rule="prompt_exists", detail=f"compaction: prompt {spec.prompt!r}"))
    slot = recipe.model_slots.get("compactor")
    if slot is not None and slot.canonical_model is not None:
        out.append(ValidationIssue(rule="compactor_model_unpinned",
                                   detail="the compactor slot uses the run's model; leave canonical_model empty"))
    if providers is None:
        return out
    if provider_id:
        served = []
        for s in recipe.model_slots.values():
            if s.canonical_model:
                try:
                    served.append(providers.served(provider_id, s.canonical_model))
                except KeyError:
                    pass                                   # reported as model_served
    else:
        served = [m for p in providers.providers if p.status == "active" for m in p.models]
    for m in served:
        out += _fit_issues(recipe, m, recipe.model_slots.values())
        if provider_id:                       # the run's own models: their known caps are checked too (A3, #122)
            run_model = (recipe.model_slots.get("executor") or ModelSlot()).canonical_model
            out += _caps_issues([s for s in recipe.model_slots.values()
                                 if (s.canonical_model or run_model) == m.canonical], m)
    return sorted({(i.rule, i.detail): i for i in out}.values(), key=lambda i: (i.rule, i.detail))


def _fit_issues(recipe: HarnessRecipe, m, slots) -> list[ValidationIssue]:
    """The reply budget leaves room for a prompt in the model's context, and compaction can make room."""
    out: list[ValidationIssue] = []
    spec, exec_slot = recipe.config.compaction, recipe.model_slots.get("executor")
    for s in slots:
        if s.max_output_tokens >= m.caps.context_len:
            out.append(ValidationIssue(rule="context_fits_reply",
                                       detail=f"{s.role} max_output_tokens {s.max_output_tokens} leaves no room "
                                              f"for a prompt in {m.canonical}'s context_len {m.caps.context_len}"))
    if spec is not None and exec_slot is not None and spec.keep_last_turns * exec_slot.max_output_tokens \
            >= m.caps.context_len - exec_slot.max_output_tokens:
        out.append(ValidationIssue(rule="compaction_fits_context",
                                   detail=f"keep_last_turns {spec.keep_last_turns} replies of up to "
                                          f"{exec_slot.max_output_tokens} tokens fill {m.canonical}'s "
                                          f"context_len {m.caps.context_len}: compacting could not make room"))
    return out


def _caps_issues(slots, m) -> list[ValidationIssue]:
    """What a model's known caps rule out (#121): a slot asking for more output than the model returns. Unknown caps
    pass (`pair_notes` says what wasn't checked)."""
    out: list[ValidationIssue] = []
    cap = m.caps.max_output_tokens
    for s in slots:
        if cap is not None and s.max_output_tokens > cap:
            out.append(ValidationIssue(rule="model_max_output",
                                       detail=f"{s.role} asks for up to {s.max_output_tokens} output tokens; "
                                              f"{m.canonical} returns at most {cap}"))
    return out


def _pair_slots(recipe: HarnessRecipe, providers: ProviderCatalog, provider_id: str, canonical: str):
    """(served model, the slots it serves) for one run of `recipe` on `canonical`: empty slots use the run's model,
    pinned ones their own (when this provider serves it; otherwise `model_served` reports it at provisioning)."""
    groups: dict[str, list] = {}
    for s in recipe.model_slots.values():
        groups.setdefault(s.canonical_model or canonical, []).append(s)
    if not recipe.model_slots:
        groups[canonical] = []
    out = []
    for mid, slots in groups.items():
        try:
            out.append((providers.served(provider_id, mid), slots))
        except KeyError:
            pass
    return out


def pair_issues(recipe: HarnessRecipe, providers: ProviderCatalog, provider_id: str,
                canonical: str) -> list[ValidationIssue]:
    """A3 (#122): can this recipe run on this model at all? The reply fits the model's context (and compaction can
    make room), and no slot asks for more output than the model returns. Campaign planning leaves a failing pair out
    and says why; provisioning refuses it (`validate_recipe` with a provider); `hfx validate --models` reports it.
    The recipe is never adjusted to fit."""
    out: list[ValidationIssue] = []
    for m, slots in _pair_slots(recipe, providers, provider_id, canonical):
        out += _fit_issues(recipe, m, slots) + _caps_issues(slots, m)
    return sorted({(i.rule, i.detail): i for i in out}.values(), key=lambda i: (i.rule, i.detail))


def pair_notes(recipe: HarnessRecipe, providers: ProviderCatalog, provider_id: str, canonical: str) -> list[str]:
    """What the pair check couldn't check, because the catalog doesn't know it (#121's fields left unset), and
    what it can't size yet: a model that always reasons spends part of the reply budget on reasoning (C5)."""
    out: list[str] = []
    for m, slots in _pair_slots(recipe, providers, provider_id, canonical):
        if slots and m.caps.max_output_tokens is None:
            out.append(f"{m.canonical}: maximum output unknown (caps.max_output_tokens), so max_output_tokens "
                       f"is not checked against it")
        if m.caps.reasoning is not None and m.caps.reasoning.mode == "always":
            out.append(f"{m.canonical} always reasons: its reasoning tokens come out of the reply budget")
    return out


def _structured_issues(recipe: HarnessRecipe, prompts: PromptRegistry) -> list[ValidationIssue]:
    """Structured plans (SUB-08): max_parallel and plan_retries mean something only with JSON plans, and concurrent
    steps only in V5 (V4's steps share one executor conversation)."""
    pl, out = recipe.planning, []
    if not pl.structured:
        for f in ("max_parallel", "plan_retries"):
            if getattr(pl, f) is not None:
                out.append(ValidationIssue(rule="structured_plan_required",
                                           detail=f"planning.{f} needs planning.structured: true"))
        return out
    if pl.max_parallel is not None and recipe.variant != Variant.V5_ORCHESTRATOR:
        out.append(ValidationIssue(rule="max_parallel_v5_only",
                                   detail="only V5 runs steps concurrently (V4's steps share one conversation)"))
    for ref in STRUCTURED_PLAN_PROMPTS:
        if not prompts.exists(ref):
            out.append(ValidationIssue(rule="prompt_exists", detail=f"planning (structured): prompt {ref!r}"))
    return out


def _runtime_issues(recipe: HarnessRecipe, provisioning: bool = False) -> list[ValidationIssue]:
    """A framework runner (ADR 0016) is that framework's own ready-made tool-calling agent: the V2 tool loop. Control
    flow beyond it (V1's single call, V3 stages, a V4 planner, V5 delegation) and harness-side additions would be the
    factory's code running inside the framework, not the framework, so they are refused rather than measured under
    the framework's name."""
    out = []
    child = recipe.delegation.child if recipe.delegation is not None else None
    if child is not None and child.runner is not None:
        out.append(ValidationIssue(rule="runtime_harness_tools",
                                   detail=f"delegate helper {child.id} has runtime {child.runner}, but helpers run "
                                          f"on the native runtime"))
    if recipe.runner is None:
        return out
    from harness_factory.runners.identity import RUNNER_EXTRAS, missing_packages
    if recipe.variant != Variant.V2_TOOL_LOOP:
        out.append(ValidationIssue(rule="runtime_shape",
                                   detail=f"runtime {recipe.runner} runs the framework's own tool-calling agent, "
                                          f"which is the V2 tool loop; {recipe.variant.value} is native only"))
    if recipe.delegation is not None:
        out.append(ValidationIssue(rule="runtime_harness_tools",
                                   detail=f"runtime {recipe.runner}: the delegate tool is run by the native "
                                          f"runtime; a framework runner gets only the MCP tools"))
    if recipe.config.recover_malformed:
        out.append(ValidationIssue(rule="runtime_harness_tools",
                                   detail=f"runtime {recipe.runner}: config.recover_malformed re-asks the model in "
                                          f"the native loop; a framework runner keeps its own error handling"))
    missing = missing_packages(recipe.runner) if provisioning else []
    if missing:                 # the environment's problem, not the recipe's: checked when a run is provisioned
        out.append(ValidationIssue(rule="runner_installed",
                                   detail=f"runtime {recipe.runner} needs {', '.join(missing)} (install the "
                                          f"`{RUNNER_EXTRAS[recipe.runner]}` extra)"))
    return out


def validate_recipe(recipe: HarnessRecipe, declared: ToolCatalogFile, prompts: PromptRegistry | None = None,
                    providers: ProviderCatalog | None = None, provider_id: str | None = None) -> list[ValidationIssue]:
    import harness_factory.shapes.builtin  # noqa: F401  (registers built-in shapes)
    from harness_factory.shapes.base import ShapeRegistry

    issues: list[ValidationIssue] = []
    prompts = prompts or PromptRegistry()

    for f in inert_fields(recipe):
        issues.append(ValidationIssue(rule="field_implemented", detail=f"{f} is declared but not implemented yet"))
    for role, slot in recipe.model_slots.items():
        if slot.seed is not None:   # a campaign axis (RunKey): left out of the recipe hash, so it can't live here
            issues.append(ValidationIssue(rule="seed_is_campaign_axis",
                                          detail=f"model slot {role!r} sets seed {slot.seed}: seeds are chosen per "
                                                 f"run (`--seeds`), never in a recipe"))
    issues += _skill_issues(recipe, declared)
    issues += _delegation_issues(recipe)
    issues += _context_issues(recipe, prompts, providers, provider_id)
    issues += _retrieval_issues(recipe, declared)
    issues += _runtime_issues(recipe, provisioning=bool(providers and provider_id))

    if recipe.variant not in ShapeRegistry.variants():
        issues.append(ValidationIssue(rule="shape_registered", detail=f"{recipe.variant.value} has no shape plugin"))

    wf = recipe.workflow
    if recipe.variant == Variant.V3_WORKFLOW_GRAPH and wf is None:
        issues.append(ValidationIssue(rule="workflow_required", detail="V3_WORKFLOW_GRAPH needs a workflow"))
    if recipe.variant != Variant.V3_WORKFLOW_GRAPH and wf is not None:
        issues.append(ValidationIssue(rule="workflow_unused", detail=f"{recipe.variant.value} ignores workflow"))
    if wf is not None:
        worst = sum(st.max_steps for st in wf.stages)
        if wf.retry_from is not None:
            worst += wf.max_retries * sum(st.max_steps for st in wf.stages[wf.index(wf.retry_from):])
        if worst > recipe.config.max_steps:
            issues.append(ValidationIssue(rule="workflow_fits_budget",
                                          detail=f"stages can take {worst} model calls (retries included) but "
                                                 f"max_steps is {recipe.config.max_steps}"))
        reach = reachable_tools(recipe, declared)
        for st in wf.stages:
            if not prompts.exists(st.prompt):
                issues.append(ValidationIssue(rule="prompt_exists", detail=f"stage {st.id}: prompt {st.prompt!r}"))
            for pat in st.tools:
                if not any(fnmatch(r, pat) for r in reach):
                    issues.append(ValidationIssue(rule="stage_tool_resolves",
                                                  detail=f"stage {st.id}: {pat!r} matches no reachable tool"))

    pl = recipe.planning
    planned = (Variant.V4_PLANNER, Variant.V5_ORCHESTRATOR)
    if recipe.variant in planned and pl is None:
        issues.append(ValidationIssue(rule="planning_required", detail=f"{recipe.variant.value} needs `planning`"))
    if recipe.variant not in planned and pl is not None:
        issues.append(ValidationIssue(rule="planning_unused", detail=f"{recipe.variant.value} ignores planning"))
    if recipe.variant == Variant.V5_ORCHESTRATOR:
        if pl is not None and not pl.worker_prompt:
            issues.append(ValidationIssue(rule="worker_prompt_required",
                                          detail="V5_ORCHESTRATOR needs planning.worker_prompt"))
        if recipe.delegation is None:
            issues.append(ValidationIssue(rule="workers_required",
                                          detail="V5_ORCHESTRATOR needs `delegation` (the worker recipe)"))
        elif pl is not None and recipe.delegation.max_calls < pl.max_plan_steps * (1 + pl.max_replans):
            issues.append(ValidationIssue(rule="workers_fit_plan",
                                          detail=f"plans can have {pl.max_plan_steps * (1 + pl.max_replans)} steps "
                                                 f"(replans included) but delegation.max_calls is "
                                                 f"{recipe.delegation.max_calls}"))
    elif pl is not None and pl.worker_prompt:
        issues.append(ValidationIssue(rule="worker_prompt_unused", detail="only V5 uses planning.worker_prompt"))
    if pl is not None:
        if recipe.variant == Variant.V4_PLANNER and pl.worst_case_calls() > recipe.config.max_steps:
            issues.append(ValidationIssue(rule="planning_fits_budget",
                                          detail=f"plans and steps can take {pl.worst_case_calls()} model calls "
                                                 f"(replans included) but max_steps is {recipe.config.max_steps}"))
        for ref in (pl.planner_prompt, pl.step_prompt, pl.replan_prompt, *([pl.worker_prompt] if pl.worker_prompt else [])):
            if not prompts.exists(ref):
                issues.append(ValidationIssue(rule="prompt_exists", detail=f"planning: prompt {ref!r}"))
        issues += _structured_issues(recipe, prompts)
        planner = recipe.model_slots.get("planner")
        if planner is not None and planner.canonical_model is not None:
            # The model is a campaign axis and only the executor's is in the RunKey: a pinned planner model would
            # be a different experiment under the same identity.
            issues.append(ValidationIssue(rule="planner_model_unpinned",
                                          detail="the planner slot uses the run's model; leave canonical_model empty"))

    for ref in (recipe.prompts.system, recipe.prompts.task, recipe.prompts.operator):
        if ref and not prompts.exists(ref):
            issues.append(ValidationIssue(rule="prompt_exists", detail=f"prompt {ref!r} not found"))

    unhashed = [r for r in declared.unhashed_tools() if r.split(".")[0] in recipe.tools.mcp_servers]
    if unhashed:
        issues.append(ValidationIssue(rule="tool_schema_hashed",
                                      detail=f"no schema_hash or description_hash in tools.yaml for "
                                             f"{', '.join(unhashed)}; "
                                             f"run `hfx tools freeze`"))

    known = {s.id for s in declared.servers}
    for sid in recipe.tools.mcp_servers:
        if sid not in known:
            issues.append(ValidationIssue(rule="server_declared", detail=f"MCP server {sid!r} not in tools.yaml"))
    refs = [t.ref(s.id) for s in declared.servers if s.id in recipe.tools.mcp_servers for t in s.tools]
    for pat in recipe.tools.tool_ids:
        if not any(fnmatch(r, pat) for r in refs):
            issues.append(ValidationIssue(rule="tool_resolves", detail=f"pattern {pat!r} matches no declared tool"))

    if recipe.variant != Variant.V1_SINGLE_CALL and not reachable_tools(recipe, declared):
        issues.append(ValidationIssue(rule="has_tools", detail="tool-using shape with no reachable tools"))

    needed = "T1"
    for ref in reachable_tools(recipe, declared):
        if ref.split(".")[0] not in known:            # a harness-side tool (retrieval: READ, R0)
            continue
        spec = declared.spec(ref)
        risks = [spec.risk]
        esc = spec.escalation                       # calls the policy lets through with a stronger side effect
        if esc is not None and recipe.policies.permission_mode.get(esc.side_effect.value) != "DENY":
            risks.append(esc.risk)
        for risk in risks:
            tier = RISK_TO_TIER[risk]
            if TIER_ORDER.index(tier) > TIER_ORDER.index(needed):
                needed = tier
    if TIER_ORDER.index(recipe.tier) < TIER_ORDER.index(needed):
        issues.append(ValidationIssue(rule="tier_covers_risk",
                                      detail=f"tier {recipe.tier} is below {needed} required by reachable tools"))

    if providers and provider_id:
        for slot in recipe.model_slots.values():
            if not slot.canonical_model:
                continue
            try:
                served = providers.served(provider_id, slot.canonical_model)
            except KeyError as e:
                issues.append(ValidationIssue(rule="model_served", detail=str(e)))
                continue
            if recipe.variant != Variant.V1_SINGLE_CALL and served.caps.tool_calling == "none":
                issues.append(ValidationIssue(rule="model_tool_calling",
                                              detail=f"{slot.canonical_model} on {provider_id} has no tool calling"))
    return issues
