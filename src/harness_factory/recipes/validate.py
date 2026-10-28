"""RecipeValidator (B2): a recipe that fails any rule is never provisioned."""

from __future__ import annotations

from fnmatch import fnmatch

from pydantic import BaseModel

from harness_factory.env.mcp.servers import RISK_TO_TIER, TIER_ORDER, ToolCatalogFile
from harness_factory.providers.catalog import ProviderCatalog
from harness_factory.recipes.model import HarnessRecipe, Variant
from harness_factory.substrate.prompts import PromptRegistry


class ValidationIssue(BaseModel):
    rule: str
    detail: str


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


def validate_recipe(recipe: HarnessRecipe, declared: ToolCatalogFile, prompts: PromptRegistry | None = None,
                    providers: ProviderCatalog | None = None, provider_id: str | None = None) -> list[ValidationIssue]:
    import harness_factory.shapes.builtin  # noqa: F401  (registers built-in shapes)
    from harness_factory.shapes.base import ShapeRegistry

    issues: list[ValidationIssue] = []
    prompts = prompts or PromptRegistry()

    if recipe.variant not in ShapeRegistry.variants():
        issues.append(ValidationIssue(rule="shape_registered", detail=f"{recipe.variant.value} has no shape plugin"))

    wf = recipe.workflow
    if recipe.variant == Variant.V3_WORKFLOW_GRAPH and wf is None:
        issues.append(ValidationIssue(rule="workflow_required", detail="V3_WORKFLOW_GRAPH needs a workflow"))
    if recipe.variant != Variant.V3_WORKFLOW_GRAPH and wf is not None:
        issues.append(ValidationIssue(rule="workflow_unused", detail=f"{recipe.variant.value} ignores workflow"))
    if wf is not None:
        reach = reachable_tools(recipe, declared)
        for st in wf.stages:
            if not prompts.exists(st.prompt):
                issues.append(ValidationIssue(rule="prompt_exists", detail=f"stage {st.id}: prompt {st.prompt!r}"))
            for pat in st.tools:
                if not any(fnmatch(r, pat) for r in reach):
                    issues.append(ValidationIssue(rule="stage_tool_resolves",
                                                  detail=f"stage {st.id}: {pat!r} matches no reachable tool"))

    for ref in (recipe.prompts.system, recipe.prompts.task, recipe.prompts.operator):
        if ref and not prompts.exists(ref):
            issues.append(ValidationIssue(rule="prompt_exists", detail=f"prompt {ref!r} not found"))

    unhashed = [r for r in declared.unhashed_tools() if r.split(".")[0] in recipe.tools.mcp_servers]
    if unhashed:
        issues.append(ValidationIssue(rule="tool_schema_hashed",
                                      detail=f"no schema_hash in tools.yaml for {', '.join(unhashed)}; "
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
        tier = RISK_TO_TIER[declared.spec(ref).risk]
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
