"""RecipeValidator (B2): a recipe that fails any rule is never provisioned."""

from __future__ import annotations

from fnmatch import fnmatch

from pydantic import BaseModel

from harness_factory.env.mcp.servers import RISK_TO_TIER, TIER_ORDER, ToolCatalogFile
from harness_factory.providers.catalog import ProviderCatalog
from harness_factory.recipes.model import (
    HarnessRecipe,
    MemoryProfile,
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
    if recipe.retrieval is not None and recipe.retrieval.mode != "none":
        out.append("retrieval")
    if recipe.config.compaction_at != d_cfg.compaction_at:
        out.append("config.compaction_at")
    if recipe.config.parallel_tool_calls != d_cfg.parallel_tool_calls:
        out.append("config.parallel_tool_calls")
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
    """Tools the recipe allows on its servers, minus those its policy denies outright."""
    out = []
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
    if sk.loading == "on_demand" and "skills.load" not in reachable_tools(recipe, declared):
        out.append(ValidationIssue(rule="skills_tool_reachable",
                                   detail="loading on_demand needs the skills server and skills.load in the slice"))
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
