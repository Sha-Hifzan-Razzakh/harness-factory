"""ResultsStore on SQLite and Postgres: Alembic migrations, adoption of pre-Alembic stores, and the store API.

Every test that takes `results_url` runs twice: on a SQLite file under tmp_path, and on a fresh database in a
Postgres server. The server is HF_TEST_POSTGRES_URL when set (any database URL on a server where the user may
CREATE DATABASE, e.g. the compose `postgres` service), otherwise a throwaway testcontainers Postgres, one per
xdist worker. Without either (no Docker, no psycopg) the Postgres variants skip, except when
HF_REQUIRE_POSTGRES=1 (CI), where they fail, so the Postgres path can never silently stop being tested.
"""

from __future__ import annotations

import os
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

from harness_factory.results import store as store_mod
from harness_factory.results.cost import CostBaseline
from harness_factory.results.model import EffectivenessRecord, FamilyScores
from harness_factory.results.store import (
    ResultsStore,
    alembic_config,
    current_revision,
    head_revision,
    metadata,
    record_row,
    runs,
)
from harness_factory.runner.ledger import CostLedger
from harness_factory.runner.spec import RunKey
from harness_factory.shapes.result import RunStatus, Violation

PG_IMAGE = "postgres:16-alpine"


def _unavailable(reason: str):
    if os.environ.get("HF_REQUIRE_POSTGRES") == "1":
        pytest.fail(f"HF_REQUIRE_POSTGRES=1 but {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="session")
def postgres_server():
    """Admin URL of a Postgres server, as a SQLAlchemy URL. One per worker process."""
    url = os.environ.get("HF_TEST_POSTGRES_URL")
    if url:
        yield sa.make_url(url)
        return
    try:
        import psycopg  # noqa: F401
        from testcontainers.community.postgres import PostgresContainer
    except ImportError as e:
        _unavailable(f"Postgres tests need the postgres and dev extras ({e.name} missing)")
    try:
        container = PostgresContainer(PG_IMAGE, driver="psycopg")   # connects to the Docker daemon already
        container.start()
    except Exception as e:  # noqa: BLE001  (no Docker daemon, image pull refused, ...: skip, or fail in CI)
        _unavailable(f"cannot start a Postgres container ({type(e).__name__}: {e})")
    try:
        yield sa.make_url(container.get_connection_url())
    finally:
        container.stop()


@pytest.fixture
def postgres_url(postgres_server):
    """A fresh, empty database on the server, dropped afterwards: tests never share state."""
    name = f"hf_test_{uuid.uuid4().hex[:12]}"
    admin = sa.create_engine(postgres_server, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as c:
            c.execute(sa.text(f'CREATE DATABASE "{name}"'))
        yield postgres_server.set(database=name).render_as_string(hide_password=False)
        with admin.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        admin.dispose()


@pytest.fixture(params=["sqlite", "postgres"])
def results_url(request, tmp_path):
    if request.param == "sqlite":
        return f"sqlite:///{tmp_path / 'results.db'}"
    return request.getfixturevalue("postgres_url")


def _record(task: str = "fs-01@1", seed: int = 0, outcome: float = 1.0) -> EffectivenessRecord:
    key = RunKey(task_version=task, task_hash="th", recipe_hash="rh", canonical_model="m", provider="scripted",
                 seed=seed, env_hash="env")
    return EffectivenessRecord(
        run_key=key, task_class="KNOWN_PATH|REVERSIBLE|R1", recipe_id="v2-base", status=RunStatus.SUCCESS,
        scores=FamilyScores(outcome=outcome, trajectory=1.0), composite=outcome,
        cost=CostLedger(tokens_in=100, tokens_out=20, model_calls=2, tool_calls=3, wall_s=0.5),
        violations=[Violation(kind="tool_not_allowed", detail="x")], grades=[{"check": "c1", "passed": True}],
        output="done", fault=None)


def _diffs(engine: sa.Engine) -> list:
    """What autogenerate would change to make the database match `metadata`: empty = the same schema."""
    with engine.connect() as c:
        return compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), metadata)


@pytest.fixture
def opened():
    """Stores and engines a test opens, disposed at the end so the Postgres database can be dropped."""
    engines: list[sa.Engine] = []
    yield engines
    for e in engines:
        e.dispose()


def _open(url: str, opened: list) -> ResultsStore:
    s = ResultsStore(url)
    opened.append(s.engine)
    return s


def _raw(url: str, opened: list) -> sa.Engine:
    e = sa.create_engine(url)
    opened.append(e)
    return e


# ----------------------------------------------------------------- migrations

def test_migrations_have_one_head():
    """Two PRs that each add a revision on the same parent make two heads; the second must rebase onto the
    first (README: Changing the results schema)."""
    assert head_revision()


def test_new_store_is_at_head_with_the_code_schema(results_url, opened):
    s = _open(results_url, opened)
    assert current_revision(s.engine) == head_revision()
    assert _diffs(s.engine) == []                         # store.metadata and the migrations agree


def test_store_made_before_alembic_upgrades_cleanly(results_url, opened):
    """Today's main created stores with metadata.create_all (plus additive columns): no alembic_version. Such a
    store is adopted by the baseline revision and upgraded to head, with its rows and baselines intact."""
    raw = _raw(results_url, opened)
    metadata.create_all(raw)                              # the pre-Alembic code path
    baseline = CostBaseline.from_rows([record_row(_record())], note="frozen")
    with raw.begin() as c:
        c.execute(sa.insert(runs).values(**record_row(_record(seed=0))))
        c.execute(sa.insert(runs).values(**record_row(_record(seed=1, outcome=0.5))))
        c.execute(sa.insert(store_mod.cost_baselines).values(
            id=baseline.id, basis=baseline.basis, bases=baseline.bases, medians=baseline.medians,
            counts=baseline.counts, note=baseline.note, created_at=baseline.created_at))
    assert current_revision(raw) is None

    s = _open(results_url, opened)
    assert current_revision(s.engine) == head_revision()
    assert _diffs(s.engine) == []
    rows = s.rows()
    assert [(r["seed"], r["outcome"]) for r in rows] == [(0, 1.0), (1, 0.5)]
    assert rows[0]["violations"][0]["kind"] == "tool_not_allowed" and rows[0]["output"] == "done"
    assert [b.id for b in s.list_baselines()] == [baseline.id]
    s.add(_record(seed=2))                                # and it is writable after the upgrade
    assert len(s.rows()) == 3


def test_very_old_store_gains_the_later_columns(results_url, opened):
    """A store from before output / transcript_path / fault / bases: the baseline adds them, as _migrate did."""
    raw = _raw(results_url, opened)
    old = sa.MetaData()
    sa.Table("runs", old, *[c._copy() for c in runs.columns
                            if c.name not in {"output", "transcript_path", "fault"}])
    sa.Table("cost_baselines", old, *[c._copy() for c in store_mod.cost_baselines.columns if c.name != "bases"])
    old.create_all(raw)
    row = record_row(_record())
    with raw.begin() as c:
        c.execute(sa.insert(old.tables["runs"]).values(
            **{k: v for k, v in row.items() if k not in {"output", "transcript_path", "fault"}}))
        c.execute(sa.text("INSERT INTO cost_baselines (id, basis, medians, counts, note, created_at) "
                          "VALUES ('old000000000', 'tokens', '{}', '{}', '', '2026-10-01 00:00:00')"))

    s = _open(results_url, opened)
    assert current_revision(s.engine) == head_revision()
    assert _diffs(s.engine) == []
    (r,) = s.rows()
    assert r["run_key"] == row["run_key"] and r["output"] is None and r["fault"] is None
    assert s.list_baselines() == [] and s.legacy_baselines() == ["old000000000"]


def test_reopening_a_current_store_runs_no_migration(results_url, opened, monkeypatch):
    s = _open(results_url, opened)
    s.add(_record())

    def boom(*a, **k):
        raise AssertionError("a store at head must not run Alembic again")
    monkeypatch.setattr(store_mod.command, "upgrade", boom)
    again = _open(results_url, opened)
    assert len(again.rows()) == 1


def test_store_from_newer_code_is_refused(results_url, opened):
    s = _open(results_url, opened)
    with s.engine.begin() as c:
        c.execute(sa.text("UPDATE alembic_version SET version_num = 'ffffffffffff'"))
    with pytest.raises(RuntimeError, match="does not know"):
        ResultsStore(results_url)


def test_every_revision_downgrades_and_upgrades_again(results_url, opened):
    s = _open(results_url, opened)
    cfg = alembic_config()
    with s.engine.begin() as c:
        cfg.attributes["connection"] = c
        command.downgrade(cfg, "base")
    assert set(sa.inspect(s.engine).get_table_names()) <= {"alembic_version"}
    with s.engine.begin() as c:
        cfg.attributes["connection"] = c
        command.upgrade(cfg, "head")
    assert current_revision(s.engine) == head_revision() and _diffs(s.engine) == []


# ----------------------------------------------------------------- the store API, on both databases

def test_runs_round_trip_and_latest_wins(results_url, opened):
    s = _open(results_url, opened)
    s.add(_record(seed=0, outcome=0.0))
    s.add(_record(seed=1))
    s.add(_record(seed=0, outcome=1.0))                   # rerun of seed 0 replaces it
    assert len(s.rows(latest=False)) == 3
    latest = s.rows()
    assert sorted((r["seed"], r["outcome"]) for r in latest) == [(0, 1.0), (1, 1.0)]
    assert s.recent(1)[0]["outcome"] == 1.0 and s.recent(1)[0]["seed"] == 0
    assert len(s.scored_keys()) == 2
    assert latest[0]["grades"] == [{"check": "c1", "passed": True}]


def test_baselines_round_trip(results_url, opened):
    s = _open(results_url, opened)
    s.add(_record())
    frozen = s.save_baseline(s.live_baseline())
    assert s.save_baseline(s.live_baseline()) == frozen                 # idempotent: content-addressed id
    got = s.get_baseline(frozen)
    assert got is not None and got.medians == s.live_baseline().medians
    assert [b.id for b in s.list_baselines()] == [frozen] and s.legacy_baselines() == []
    _, cells = s.aggregate(got)
    assert len(cells) == 1 and cells[0].e.n == 1
