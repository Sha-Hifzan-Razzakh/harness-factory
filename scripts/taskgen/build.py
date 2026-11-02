"""Regenerate the shop and service seeds and the second task batch from these data modules.

    uv run python scripts/taskgen/build.py            # writes into catalog/
    uv run python scripts/taskgen/build.py <catalog>  # or into another catalog (tests use a copy)

The generated files are committed; tests/test_taskgen.py fails if they drift from this source. Change the data
here, rebuild, then run `hfx reference`. Editing a seed changes the hash of every task that uses it.
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def build(catalog: Path) -> None:
    (catalog / "seeds/shop/files").mkdir(parents=True, exist_ok=True)
    for script, arg in [("shop.py", catalog / "seeds/shop/db.sql"), ("files.py", catalog), ("tasks.py", catalog)]:
        subprocess.run([sys.executable, str(HERE / script), str(arg)], check=True, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parents[1] / "catalog")
