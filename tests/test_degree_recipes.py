"""DegreeAudit in the lab, HF-2: the degree recipes, their stage prompts and the OpenRouter shortlist.

The degree tasks come with HF-3, so the runs here use generic tasks the recipes can reach: they show the recipes
provision, open the audit server and move through their stages offline. The audit itself is measured on the degree
tasks.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from harness_factory import settings
from harness_factory.env.mcp.servers import SideEffect, load_tool_catalog
from harness_factory.providers.catalog import load_provider_catalog
from harness_factory.provision.provisioner import allowed_tools, reaches
from harness_factory.recipes.model import Variant, load_recipes
from harness_factory.recipes.validate import validate_recipe
from harness_factory.runner.runner import run_one
from harness_factory.substrate.prompts import PromptAssembler
from harness_factory.substrate.tools import model_name
from harness_factory.tasks.registry import TaskRegistry

DEGREE = ["v2-audit", "v2-audit-rag", "v3-audit"]
GRAPH_A = ["validate", "evaluate", "cite", "report", "verify"]
STAGE_PROMPTS = ["stage/audit_validate", "stage/audit_evaluate", "stage/audit_cite", "stage/audit_report"]
TOOL_TOKEN = re.compile(r"\b[a-z][a-z0-9_]*?__[a-z][a-z0-9_]*\b")     # a model-facing name (promptfoo checks.js)
MESSAGE_CHARS = 2500                                                   # promptfoo DEFAULT_BUDGETS["message_chars"]
needs_core = pytest.mark.skipif(importlib.util.find_spec("degree_audit_core") is None,
                                reason="the audit server needs degree_audit_core (extra `docs`)")


@pytest.fixture(scope="module")
def recipes():
    return load_recipes(settings.RECIPES_DIR)


@pytest.fixture(scope="module")
def declared():
    return load_tool_catalog(settings.TOOLS_FILE)


def _spans(rec, kind):
    return [s for s in map(json.loads, Path(rec.trace_path).read_text().splitlines()) if s["kind"] == kind]


# ---------------------------------------------------------------------------------------------------- recipes

def test_degree_recipes_validate(recipes, declared):
    for rid in DEGREE:
        assert not validate_recipe(recipes[rid], declared), rid


def test_degree_recipes_open_the_audit_server_and_never_write_the_sis(recipes):
    for rid in DEGREE:
        r = recipes[rid]
        assert "audit" in r.tools.mcp_servers
        assert all(r.tools.allows(f"audit.{t}") for t in ("validate_record", "evaluate", "explain"))
        assert not r.tools.allows("sql.execute") and not r.tools.allows("fs.delete_file"), rid
        assert r.model_slots["executor"].max_output_tokens == 8192     # the audit file is written in one reply


def test_v2_audit_is_the_v2_loop(recipes):
    assert recipes["v2-audit"].variant == Variant.V2_TOOL_LOOP and recipes["v2-audit"].base == "v2-base"
    assert recipes["v2-audit"].workflow is None and recipes["v2-audit"].retrieval is None


def test_v2_audit_rag_changes_only_retrieval(recipes):
    rag, base = recipes["v2-audit-rag"], recipes["v2-audit"]
    keep = {"id", "version", "base", "retrieval"}
    assert rag.model_dump(exclude=keep) == base.model_dump(exclude=keep)
    assert rag.retrieval.mode == "hybrid" and rag.retrieval.top_k == 5 and rag.retrieval.rerank


def test_v3_audit_follows_graph_a(recipes, declared):
    r = recipes["v3-audit"]
    stages = r.workflow.stages
    assert [s.id for s in stages] == GRAPH_A
    assert [s.prompt for s in stages[:4]] == STAGE_PROMPTS and stages[4].prompt == "stage/verify"
    assert r.workflow.retry_from == "report" and r.workflow.max_retries == 1
    refs = [ref for ref in declared.all_refs() if r.tools.allows(ref) and ref.split(".")[0] in r.tools.mcp_servers]
    writers = {s.id for s in stages
               for ref in refs if s.allows(ref) and declared.spec(ref).side_effect != SideEffect.READ}
    assert writers == {"report"}                                       # only report writes
    retry = sum(s.max_steps for s in stages[r.workflow.index("report"):])
    assert sum(s.max_steps for s in stages) + retry <= r.config.max_steps


def test_degree_recipes_reach_no_task_that_needs_sql_writes(recipes, declared):
    tasks = TaskRegistry()
    task = tasks.get("mix-03")                                          # allows sql.*, including sql.execute
    assert all(not reaches(recipes[rid], task, declared) for rid in DEGREE)
    assert all(reaches(recipes[rid], tasks.get("sql-01"), declared) for rid in DEGREE)


# ---------------------------------------------------------------------------------------------------- prompts

@pytest.mark.parametrize("task_id", ["fs-01", "sql-01"])
def test_stage_prompts_list_only_their_tools_and_stay_short(recipes, declared, task_id):
    r, task = recipes["v3-audit"], TaskRegistry().get(task_id)
    names = sorted(model_name(ref) for ref in allowed_tools(r, task, declared))
    rendered = PromptAssembler().stage_prompts(r, task, names)
    for st in r.workflow.stages:
        text = rendered[st.id]
        offered = [n for n in names if st.allows(n.replace("__", ".", 1))]
        line = f"Tools in this stage: {', '.join(offered)}." if offered else "No tools in this stage."
        assert line in text, st.id
        assert set(TOOL_TOKEN.findall(text)) <= set(offered), st.id    # never names a tool it doesn't offer
        assert len(text) <= MESSAGE_CHARS and "{{" not in text and "{%" not in text, st.id


def test_evaluate_prompt_knows_the_blocking_marker():
    from harness_factory.substrate.prompts import PromptRegistry
    reg = PromptRegistry()
    validate = (reg.root / "stage/audit_validate.j2").read_text()
    evaluate = (reg.root / "stage/audit_evaluate.j2").read_text()
    assert '"BLOCKING:"' in validate and '"BLOCKING:"' in evaluate   # the validate -> report short cut (Graph A)


# ---------------------------------------------------------------------------------------------------- offline runs

@needs_core
async def test_v3_audit_replays_through_its_stages(factory):
    rec = await run_one(factory, factory.tasks.get("fs-01"), factory.recipes["v3-audit"], "scripted/replay",
                        "scripted")
    assert rec.status.value == "SUCCESS", rec.grades
    assert [s["name"] for s in _spans(rec, "handoff")] == [f"stage:{s}" for s in GRAPH_A]
    assert not rec.violations


@needs_core
@pytest.mark.parametrize("recipe_id", ["v2-audit", "v2-audit-rag"])
async def test_v2_degree_recipes_replay(factory, recipe_id):
    rec = await run_one(factory, factory.tasks.get("sql-01"), factory.recipes[recipe_id], "scripted/replay",
                        "scripted")
    assert rec.status.value == "SUCCESS", rec.grades


# ---------------------------------------------------------------------------------------------------- providers

SHORTLIST = {
    "deepseek-v4.1-flash": "deepseek/deepseek-v4.1-flash",
    "glm-5.3-flash": "z-ai/glm-5.3-flash",
    "qwen3.6-35b-a3b": "qwen/qwen3.6-35b-a3b",
    "claude-haiku-5.5": "anthropic/claude-haiku-5.5",
    "gpt-oss-120b": "openai/gpt-oss-120b",
    "nemotron-3-ultra:free": "nvidia/nemotron-3-ultra-550b-a55b:free",
}


def test_openrouter_serves_the_degree_shortlist():
    cat = load_provider_catalog(settings.PROVIDERS_FILE)
    for canonical, model_id in SHORTLIST.items():
        m = cat.served("openrouter", canonical)
        assert m.provider_model_id == model_id
        assert m.free == model_id.endswith(":free")
        assert (m.price_in == 0 and m.price_out == 0) if m.free else m.price_out > 0


def test_openrouter_limits_free_and_paid_models_apart():
    """Paid variants: no platform cap, only the factory's 20 rpm pacing, one account bucket. The free variant:
    20 rpm and 1,000 a day (credits >= $10), in its own bucket (openrouter.ai/docs/api/reference/limits)."""
    cat = load_provider_catalog(settings.PROVIDERS_FILE)
    paid = cat.limits(cat.bucket("openrouter", "deepseek-v4.1-flash"))
    assert cat.bucket("openrouter", "deepseek-v4.1-flash") == "openrouter"
    assert paid.rpm == 20 and paid.rpd is None
    free_bucket = cat.bucket("openrouter", "nemotron-3-ultra:free")
    assert free_bucket != "openrouter"
    free = cat.limits(free_bucket)
    assert free.rpm == 20 and free.rpd == 1000
    assert sum(m.free for m in cat.get("openrouter").models) == 1    # a second free model needs a shared limit
