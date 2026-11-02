import pytest

from harness_factory.runner.runner import Factory

# The original smoke set. Tests about aggregation, identity or campaigns use it: what they check doesn't depend
# on which tasks run, and running every task would only make them slower as the catalog grows.
P0_TASKS = ["fs-01", "fs-02", "mix-01", "sql-01", "sql-02"]


@pytest.fixture(autouse=True)
def own_sandboxes_dir(tmp_path, monkeypatch):
    """Each test gets its own sandboxes directory. Tests run in parallel processes, and a test that checks it
    left nothing behind must not see another worker's live sandbox."""
    from harness_factory import settings
    d = tmp_path / "sandboxes"
    d.mkdir()
    monkeypatch.setattr(settings, "SANDBOXES_DIR", d)
    return d


@pytest.fixture
def p0_tasks() -> list[str]:
    return list(P0_TASKS)


@pytest.fixture
def factory(tmp_path):
    return Factory.load(results_url=f"sqlite:///{tmp_path / 'results.db'}", quota_state=tmp_path / "quota.json")
