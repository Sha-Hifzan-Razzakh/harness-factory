"""Batch 4 (author A): regenerate the b4a-* seeds and tasks b4-01 ... b4-08.

    uv run python scripts/taskgen/b4a_build.py            # writes into catalog/
    uv run python scripts/taskgen/b4a_build.py <catalog>  # or into another folder (tests compare the two)

The generated files are committed; tests/test_task_traps_b4a.py fails if they drift from this source.
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def build(catalog: Path) -> None:
    for script in ("b4a_ledger.py", "b4a_files.py", "b4a_office.py", "b4a_tasks.py"):
        subprocess.run([sys.executable, str(HERE / script), str(catalog)], check=True, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parents[1] / "catalog")
