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


@pytest.fixture(autouse=True)
def own_var_files(tmp_path, monkeypatch):
    """Nothing a test runs writes into the repo's var/: traces, the quota ledger and the results store default
    to paths under tmp_path. (settings.VAR itself stays: the sandbox image build lock there is shared on purpose,
    so parallel workers don't build the same image twice.)"""
    from harness_factory import settings
    var = tmp_path / "var"
    var.mkdir()
    monkeypatch.setattr(settings, "TRACES_DIR", var / "traces")
    monkeypatch.setattr(settings, "QUOTA_STATE", var / "quota_ledger.json")
    monkeypatch.setattr(settings, "RESULTS_URL", f"sqlite:///{var / 'results.db'}")
    return var


@pytest.fixture
def p0_tasks() -> list[str]:
    return list(P0_TASKS)


@pytest.fixture
def factory(tmp_path):
    return Factory.load(results_url=f"sqlite:///{tmp_path / 'results.db'}", quota_state=tmp_path / "quota.json")
