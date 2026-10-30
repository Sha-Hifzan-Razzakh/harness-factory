import pytest

from harness_factory.runner.runner import Factory


@pytest.fixture
def factory(tmp_path):
    return Factory.load(results_url=f"sqlite:///{tmp_path / 'results.db'}", quota_state=tmp_path / "quota.json")
