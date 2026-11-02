"""The second task batch is generated: the committed seeds and tasks must match their source."""

import importlib.util
import shutil
from pathlib import Path

from harness_factory import settings

ROOT = Path(__file__).parents[1]


def test_generated_tasks_and_seeds_match_their_source(tmp_path):
    spec = importlib.util.spec_from_file_location("taskgen_build", ROOT / "scripts/taskgen/build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "catalog"
    mod.build(out)
    generated = sorted(p.relative_to(out) for p in out.rglob("*") if p.is_file())
    assert len(generated) > 40
    drift = [str(p) for p in generated if (out / p).read_bytes() != (settings.CATALOG / p).read_bytes()]
    assert not drift, f"edited by hand or not rebuilt: {drift} (run scripts/taskgen/build.py)"
    shutil.rmtree(out)
