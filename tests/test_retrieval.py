"""Retrieval dimension (KNW-02, ADR 0017): `retrieve.search` over the task's corpus, built from LlamaIndex.

Offline: the embedding and the reranker are deterministic and need no download, so every mode's ranking is a
fixed fact the tests can assert. The tool is served by the harness but goes through ToolExecutor like any MCP tool."""

import json
from pathlib import Path

import pytest

from harness_factory.provision.provisioner import run_key_for, task_tools
from harness_factory.recipes.model import RetrievalConfig, Variant
from harness_factory.recipes.validate import inert_fields, validate_recipe
from harness_factory.retrieval import tool as retrieval
from harness_factory.retrieval.engine import CorpusIndex
from harness_factory.runner.runner import _check_retrieved, run_one
from harness_factory.substrate.gateway import ScriptedBackend
from harness_factory.tasks.reference import load_reference

MODES = [(m, rr) for m in ("bm25", "dense", "hybrid") for rr in (False, True)]
RAG_TASKS = ["rag-01", "rag-02", "rag-03"]

FIXTURE = {
    "lighthouse.md": "# Lighthouse keeping\n\nThe lighthouse lamp is lit at dusk. The keeper winds the clockwork "
                     "every four hours and logs passing ships in the lamp room.",
    "bread.md": "# Sourdough bread\n\nFeed the sourdough starter with flour and water. Bake the loaf in a hot "
                "Dutch oven for forty minutes until the crust is dark.",
    "volcano.md": "# Volcanoes\n\nMagma rises through the crust and erupts as lava. Volcanic ash can ground "
                  "flights for days.",
    "chess.md": "# Chess openings\n\nThe Sicilian defence answers e4 with c5. Castling early keeps the king safe.",
    "bees.md": "# Beekeeping\n\nA hive needs a queen, workers and drones. Beekeepers harvest honey in late summer "
               "and smoke the hive to calm the bees.",
    "notes/tides.txt": "Tide tables\n\nHigh tide arrives about fifty minutes later each day because of the moon.",
    "ignored.json": '{"not": "indexed"}',
}
QUERIES = {"when is the lighthouse lamp lit": "corpus/lighthouse.md",
           "how long to bake sourdough bread": "corpus/bread.md",
           "harvest honey from the hive": "corpus/bees.md",
           "why is high tide later each day": "corpus/notes/tides.txt"}


@pytest.fixture
def corpus(tmp_path) -> Path:
    root = tmp_path / "corpus"
    for rel, text in FIXTURE.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def spans(rec):
    return [json.loads(line) for line in Path(rec.trace_path).read_text().splitlines()]


def transcript(rec):
    return json.loads(Path(rec.transcript_path).read_text())


def search(query):
    return {"tool_calls": [{"name": "retrieve__search", "arguments": {"query": query}}]}


# ------------------------------------------------------------------ the engine: each mode on a fixture corpus

@pytest.mark.parametrize(("mode", "rerank"), MODES)
def test_each_mode_returns_the_expected_passage_first(corpus, mode, rerank):
    index = CorpusIndex(corpus, RetrievalConfig(mode=mode, top_k=2, rerank=rerank))
    assert index.documents == 6                                              # .md and .txt only
    for query, source in QUERIES.items():
        hits = index.search(query)
        assert 1 <= len(hits) <= 2
        assert hits[0].source == source, (mode, rerank, query, [h.source for h in hits])


@pytest.mark.parametrize(("mode", "rerank"), MODES)
def test_passages_carry_exact_offsets_and_are_deterministic(corpus, mode, rerank):
    cfg = RetrievalConfig(mode=mode, top_k=3, rerank=rerank)
    a, b = CorpusIndex(corpus, cfg), CorpusIndex(corpus, cfg)
    assert a.index_id == b.index_id
    for query in QUERIES:
        pa, pb = a.search(query), b.search(query)
        assert [p.as_dict() for p in pa] == [p.as_dict() for p in pb]       # same corpus, same answer
        for p in pa:                                                        # KNW-03: source id and offsets
            assert (corpus.parent / p.source).read_text()[p.start:p.end] == p.text


def test_index_id_follows_the_corpus_and_the_config(corpus):
    base = CorpusIndex(corpus, RetrievalConfig(mode="bm25")).index_id
    assert CorpusIndex(corpus, RetrievalConfig(mode="bm25", top_k=3)).index_id != base
    (corpus / "chess.md").write_text("# Chess\n\nThe French defence answers e4 with e6.")
    assert CorpusIndex(corpus, RetrievalConfig(mode="bm25")).index_id != base


def test_rerank_orders_a_wider_pool_by_query_coverage(corpus):
    """The reranker scores each candidate of a top_k x 3 pool by the share of query words it contains."""
    query = "keeper winds the clockwork in the lamp room"
    ranked = CorpusIndex(corpus, RetrievalConfig(mode="bm25", top_k=3, rerank=True)).search(query)
    assert len(ranked) == 3 and ranked[0].source == "corpus/lighthouse.md"
    scores = [p.score for p in ranked]
    assert scores == sorted(scores, reverse=True) and all(0 <= x <= 1 for x in scores) and scores[0] > scores[-1]


@pytest.mark.parametrize("task_id", RAG_TASKS)
@pytest.mark.parametrize(("mode", "rerank"), MODES)
def test_every_mode_finds_what_the_reference_reads_next(factory, task_id, mode, rerank):
    """On the catalog's corpus (60 documents with distractors), every mode at top_k 5 returns each file the
    reference plan reads after its retrieval call."""
    task = factory.tasks.get(task_id)
    plan = load_reference(task).plan
    index = retrieval.build_index(task, RetrievalConfig(mode=mode, top_k=5, rerank=rerank))
    steps = [i for i, s in enumerate(plan) if s["tool"] == retrieval.RETRIEVE_REF]
    assert steps and all(plan[i].get("optional") for i in steps)
    for i in steps:
        _check_retrieved(task, plan, i, retrieval.render(index.search(plan[i]["args"]["query"])))


# ------------------------------------------------------------------ identity

def test_retrieval_is_in_the_recipe_hash_only_when_set(factory):
    base = factory.recipes["v2-base"]
    assert base.content_hash() == "79abd03818943f65"                       # unchanged: retrieval unset
    bm25, hybrid = factory.recipes["v2-retrieval-bm25"], factory.recipes["v2-retrieval-hybrid"]
    db = base.model_dump()
    for r in (bm25, hybrid):
        d = r.model_dump()
        assert sorted(k for k in db if db[k] != d[k]) == ["base", "id", "retrieval"]
    hashes = {base.content_hash(), bm25.content_hash(), hybrid.content_hash()}
    for change in ({"top_k": 6}, {"rerank": True}, {"mode": "dense"}):
        r = bm25.model_copy(update={"retrieval": bm25.retrieval.model_copy(update=change)})
        hashes.add(r.content_hash())
    assert len(hashes) == 6


def test_env_hash_names_the_retrieval_frameworks_only_for_retrieval_recipes(factory):
    task = factory.tasks.get("rag-01")
    key = lambda r: run_key_for(task, factory.recipes[r], "scripted/replay", "scripted", 0,
                                declared=factory.declared, providers=factory.providers)
    served = factory.providers.served("scripted", "scripted/replay")
    assert key("v2-base").env_hash == factory.declared.env_hash(["fs", "sql"], served.provider_model_id)
    assert key("v2-retrieval-bm25").env_hash != key("v2-base").env_hash
    ident = retrieval.identity(factory.recipes["v2-retrieval-bm25"].retrieval)
    assert set(ident["versions"]) == set(retrieval.PACKAGES) and "missing" not in ident["versions"].values()


def test_the_corpus_is_task_data(factory):
    task = factory.tasks.get("rag-01")
    assert retrieval.corpus_dir(task) is not None
    assert task.env_seed.files == "../../seeds/kb/files"                     # seed hash covers corpus/
    assert retrieval.corpus_dir(factory.tasks.get("fs-01")) is None


# ------------------------------------------------------------------ validation

def test_retrieval_is_implemented_and_validated(factory):
    base = factory.recipes["v2-base"]
    for rid in ("v2-retrieval-bm25", "v2-retrieval-hybrid"):
        assert not validate_recipe(factory.recipes[rid], factory.declared)
        assert inert_fields(factory.recipes[rid]) == []
    rerank_only = base.model_copy(update={"retrieval": RetrievalConfig(mode="none", rerank=True)})
    assert [i.rule for i in validate_recipe(rerank_only, factory.declared)] == ["retrieval_unused"]
    assert not validate_recipe(base.model_copy(update={"retrieval": RetrievalConfig()}), factory.declared)
    v1 = factory.recipes["v1-base"].model_copy(update={"retrieval": RetrievalConfig(mode="bm25")})
    assert "retrieval_unused" in [i.rule for i in validate_recipe(v1, factory.declared)]
    with pytest.raises(ValueError):
        RetrievalConfig(mode="bm25", top_k=0)


def test_a_v3_stage_can_name_the_retrieval_tool(factory):
    v3 = factory.recipes["v3-staged"]
    stages = [st.model_copy(update={"tools": [*st.tools, "retrieve.search"]}) if st.id == "inspect" else st
              for st in v3.workflow.stages]
    wf = v3.workflow.model_copy(update={"stages": stages})
    without = v3.model_copy(update={"workflow": wf})
    assert "stage_tool_resolves" in [i.rule for i in validate_recipe(without, factory.declared)]
    with_r = without.model_copy(update={"retrieval": RetrievalConfig(mode="bm25", top_k=5)})
    assert not validate_recipe(with_r, factory.declared)


# ------------------------------------------------------------------ when it is offered

async def test_offered_only_on_tasks_with_a_corpus(factory):
    r = factory.recipes["v2-retrieval-bm25"]
    plain = await run_one(factory, factory.tasks.get("fs-01"), r, "scripted/replay", "scripted")
    t = transcript(plain)
    assert plain.status.value == "SUCCESS"
    assert retrieval.RETRIEVE_REF not in t["snapshot"]["allowed_tools"] and "retrieval" not in t["snapshot"]
    assert "retrieve__search" not in t["messages"][0]["content"]
    rag = factory.tasks.get("rag-01")
    no_read = rag.model_copy(update={"allowed_tools": ["fs.list_dir"]})      # the grant needs fs.read_file
    assert not retrieval.offered(r, no_read, task_tools(no_read, factory.declared))
    assert retrieval.offered(r, rag, task_tools(rag, factory.declared))


@pytest.mark.parametrize("recipe_id", ["v2-retrieval-bm25", "v2-retrieval-hybrid"])
async def test_a_retrieval_recipe_solves_its_tasks_with_the_reference_plan(factory, recipe_id):
    """scripted/replay calls retrieve.search when offered, then reads what it found."""
    for task_id in RAG_TASKS:
        rec = await run_one(factory, factory.tasks.get(task_id), factory.recipes[recipe_id], "scripted/replay",
                            "scripted")
        assert rec.status.value == "SUCCESS", (task_id, rec.grades)
        names = [s["name"] for s in spans(rec) if s["kind"] == "tool_call"]
        assert names[0] == retrieval.RETRIEVE_REF and names.count(retrieval.RETRIEVE_REF) >= 1
        snap = transcript(rec)["snapshot"]["retrieval"]
        assert snap["documents"] == 60 and snap["mode"] == factory.recipes[recipe_id].retrieval.mode
        assert snap["evidence"][0]["passages"] and set(snap["evidence"][0]["passages"][0]) == \
            {"source", "start", "end", "score"}


async def test_without_retrieval_the_same_plan_skips_the_call(factory):
    rec = await run_one(factory, factory.tasks.get("rag-02"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted")
    assert rec.status.value == "SUCCESS"
    assert retrieval.RETRIEVE_REF not in [s["name"] for s in spans(rec) if s["kind"] == "tool_call"]


@pytest.mark.parametrize("variant", ["v3", "v4"])
async def test_every_shape_gets_the_tool_the_same_way(factory, variant):
    cfg = RetrievalConfig(mode="hybrid", top_k=5)
    if variant == "v3":
        v3 = factory.recipes["v3-staged"]
        stages = [st.model_copy(update={"tools": [*st.tools, "retrieve.search"]}) if st.id == "inspect" else st
                  for st in v3.workflow.stages]
        recipe = v3.model_copy(update={"workflow": v3.workflow.model_copy(update={"stages": stages}),
                                       "retrieval": cfg})
    else:
        recipe = factory.recipes["v4-planner"].model_copy(update={"retrieval": cfg})
    assert recipe.variant in (Variant.V3_WORKFLOW_GRAPH, Variant.V4_PLANNER)
    rec = await run_one(factory, factory.tasks.get("rag-02"), recipe, "scripted/replay", "scripted")
    assert rec.status.value == "SUCCESS"
    assert retrieval.RETRIEVE_REF in [s["name"] for s in spans(rec) if s["kind"] == "tool_call"]


# ------------------------------------------------------------------ accounting: nothing escapes the run

async def test_a_retrieval_call_is_a_traced_charged_read(factory):
    rec = await run_one(factory, factory.tasks.get("rag-01"), factory.recipes["v2-retrieval-bm25"],
                        "scripted/replay", "scripted",
                        backend=ScriptedBackend([search("refund approval limit support agent"),
                                                 {"content": "750 AED (kb-009)"}]))
    calls = [s for s in spans(rec) if s["kind"] == "tool_call"]
    assert [s["name"] for s in calls] == [retrieval.RETRIEVE_REF]
    s = calls[0]
    assert s["status"] == "ok" and s["attributes"]["side_effect"] == "READ" and s["attributes"]["permission"] == "AUTO"
    assert "args_hash" in s["attributes"] and s["attributes"]["result_chars"] > 0
    body = json.loads(transcript(rec)["messages"][3]["content"])             # KNW-04: data, with a note
    assert body["note"] == retrieval.NOTE and all(p["source"].startswith("corpus/") for p in body["passages"])
    assert rec.cost.tool_calls == 1                                         # counted in the run's cost
    assert rec.status.value == "SUCCESS"


async def test_bad_arguments_and_the_budget_apply(factory):
    bad = {"tool_calls": [{"name": "retrieve__search", "arguments": {"query": "  "}}]}
    rec = await run_one(factory, factory.tasks.get("rag-01"), factory.recipes["v2-retrieval-bm25"],
                        "scripted/replay", "scripted", backend=ScriptedBackend([bad, {"content": "750 AED"}]))
    calls = [s for s in spans(rec) if s["kind"] == "tool_call"]
    assert calls[0]["status"] == "error"
    assert next(g for g in rec.grades if g["grader_id"] == "trajectory")["score"] == 0.0      # invalid in Q
    r = factory.recipes["v2-retrieval-bm25"]
    tight = r.model_copy(update={"config": r.config.model_copy(update={"max_tool_calls": 1})})
    rec = await run_one(factory, factory.tasks.get("rag-01"), tight, "scripted/replay", "scripted",
                        backend=ScriptedBackend([search("refund limit"), search("refund policy"),
                                                 {"content": "750"}]))
    assert [s["name"] for s in spans(rec) if s["kind"] == "tool_call"] == [retrieval.RETRIEVE_REF]
    assert rec.status.value != "SUCCESS"                                     # the cap stopped the second call


async def test_faults_reach_the_retrieval_tool(factory):
    rec = await run_one(factory, factory.tasks.get("rag-01"), factory.recipes["v2-retrieval-bm25"],
                        "scripted/retry", "scripted", fault="first-call-fails")
    first = next(s for s in spans(rec) if s["kind"] == "tool_call" and s["name"] == retrieval.RETRIEVE_REF)
    assert first["attributes"].get("fault") == "error"
    assert transcript(rec)["snapshot"]["retrieval"]["evidence"][0]["call"] == 1   # the retry ran it for real
    assert rec.status.value == "SUCCESS"


async def test_a_helper_gets_the_tool_only_when_its_recipe_retrieves(factory):
    cfg = RetrievalConfig(mode="bm25", top_k=5)
    for child_retrieves in (False, True):
        child = factory.recipes["v2-base"]
        if child_retrieves:
            child = child.model_copy(update={"retrieval": cfg})
        d = factory.recipes["v2-delegate"].delegation.model_copy()
        d._child = child
        parent = factory.recipes["v2-delegate"].model_copy(update={"retrieval": cfg, "delegation": d})
        rec = await run_one(factory, factory.tasks.get("rag-01"), parent, "scripted/delegate", "scripted")
        assert rec.status.value == "SUCCESS"
        helper_calls = [s["name"] for s in spans(rec) if s["kind"] == "tool_call"
                        and s["attributes"].get("scope", "").startswith("delegate:")]
        assert (retrieval.RETRIEVE_REF in helper_calls) is child_retrieves, helper_calls
