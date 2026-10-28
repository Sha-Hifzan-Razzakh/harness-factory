import json
from pathlib import Path

import pytest

from harness_factory.runner.runner import Factory, run_one

TASK_IDS = [t.id for t in Factory.load(store=False).tasks.all()]


@pytest.mark.parametrize("task_id", TASK_IDS)
async def test_replay_succeeds_and_is_recorded(factory, task_id):
    rec = await run_one(factory, factory.tasks.get(task_id), factory.recipes["v2-base"], "scripted/replay", "scripted")
    assert rec.status.value == "SUCCESS", rec.grades
    assert rec.composite == pytest.approx(1.0)
    kinds = {json.loads(line)["kind"] for line in Path(rec.trace_path).read_text().splitlines()}
    assert {"run", "model_call", "tool_call", "grade"} <= kinds
    rows = factory.store.recent()
    assert rows[0]["run_key"] == rec.run_key.as_str()


async def test_lazy_model_fails_and_noisy_scores_lower(factory):
    task, recipe = factory.tasks.get("fs-02"), factory.recipes["v2-base"]
    lazy = await run_one(factory, task, recipe, "scripted/lazy", "scripted")
    noisy = await run_one(factory, task, recipe, "scripted/noisy", "scripted")
    assert lazy.status.value == "FAIL" and lazy.composite == 0
    assert noisy.status.value == "SUCCESS" and 0 < noisy.composite < 1


async def test_ungrounded_answer_is_not_success(factory):
    rec = await run_one(factory, factory.tasks.get("sql-01"), factory.recipes["v2-base"], "scripted/lazy", "scripted")
    assert rec.status.value == "PARTIAL"


async def test_transcript_and_previews_are_persisted(factory, tmp_path):
    from harness_factory.results.store import ResultsStore
    factory.store = ResultsStore(f"sqlite:///{tmp_path / 'r.db'}")
    rec = await run_one(factory, factory.tasks.get("mix-01"), factory.recipes["v2-base"], "scripted/replay",
                        "scripted")
    t = json.loads(Path(rec.transcript_path).read_text())
    roles = [m["role"] for m in t["messages"]]
    assert roles[:2] == ["system", "user"] and "tool" in roles
    assert any("Al Noor Trading" in (m.get("content") or "") for m in t["messages"] if m["role"] == "tool")
    assert t["output"] == rec.output and t["status"] == "SUCCESS"
    spans = [json.loads(line) for line in Path(rec.trace_path).read_text().splitlines()]
    write = next(s for s in spans if s["name"] == "fs.write_file")
    assert '"reports/dubai.csv"' in write["attributes"]["args"] and write["attributes"]["result_preview"]
    row = factory.store.rows()[0]
    assert row["output"] == rec.output and row["transcript_path"] == rec.transcript_path


def test_old_results_store_is_migrated_in_place(tmp_path):
    import sqlalchemy as sa

    from harness_factory.results.store import ResultsStore
    url = f"sqlite:///{tmp_path / 'old.db'}"
    eng = sa.create_engine(url)
    with eng.begin() as c:                                       # a store created before output/transcript_path
        c.execute(sa.text("CREATE TABLE runs (id INTEGER PRIMARY KEY, run_key VARCHAR NOT NULL)"))
        c.execute(sa.text("INSERT INTO runs (run_key) VALUES ('old')"))
    ResultsStore(url)
    cols = {c["name"] for c in sa.inspect(eng).get_columns("runs")}
    assert {"output", "transcript_path"} <= cols
    with eng.connect() as c:
        assert c.execute(sa.text("SELECT run_key FROM runs")).scalar() == "old"     # data kept
