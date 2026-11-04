import tomllib
from pathlib import Path

import ledger


def test_package_version_matches_pyproject():
    pyproject = Path(ledger.__file__).resolve().parents[1] / "pyproject.toml"
    assert tomllib.loads(pyproject.read_text())["project"]["version"] == ledger.__version__
