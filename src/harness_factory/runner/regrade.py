"""Re-grading (GRD-04): run the current graders over a stored run's end state.

The run is not repeated: its end state (runner/endstate.py) is unpacked into a scratch directory and graded by
the task's current definition with the factory's current pipeline, then scored and given a status by the same
functions run_one uses. The result is a new row in `regrades`; the run's own row is never touched.

Rubric checks are judged again by the factory's current judge (ADR 0014); the judge's usage is stored with the
re-grade, never in the run's cost. A run that was unscored only because its calibrated judge failed is graded as
the scored run it was (its end state keeps `pre_judge`).

A run can't be re-graded when it kept no end state (HF_KEEP_ENDSTATE=0, over HF_ENDSTATE_MAX_MB, or stored before
end states existed) or when its task is no longer in the catalog.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from pydantic import BaseModel

from harness_factory import settings
from harness_factory.env.state import EnvState, StateInspector
from harness_factory.grading.base import GradeContext
from harness_factory.grading.judge import apply_judge_failure, judge_usage
from harness_factory.observability.span import Span
from harness_factory.results.model import ScoreWeights
from harness_factory.results.store import task_hash_of
from harness_factory.runner.endstate import load_endstate
from harness_factory.runner.runner import Factory, _finalize_status, run_scores
from harness_factory.runner.spec import RunKey
from harness_factory.shapes.result import RunResult, RunStatus, Violation


class Scored(BaseModel):
    status: str
    outcome: float
    trajectory: float
    composite: float


class RegradeOutcome(BaseModel):
    run_id: int
    task_version: str
    before: Scored
    after: Scored | None = None
    changed: bool = False
    task_edited: bool = False          # the task's definition changed since the run (graded against the current)
    regrade_id: int | None = None
    skipped: str | None = None         # why it could not be re-graded


def _scored(r: dict) -> Scored:
    return Scored(status=r["status"], outcome=round(r["outcome"], 6), trajectory=round(r["trajectory"], 6),
                  composite=round(r["composite"], 6))


async def regrade_row(f: Factory, row: dict) -> RegradeOutcome:
    """Re-grade one stored run and store the new grade (nothing is stored when it is skipped)."""
    out = RegradeOutcome(run_id=row["id"], task_version=row["task_version"], before=_scored(row))
    bundle = row.get("endstate_path")
    if not bundle or not Path(bundle).is_file():
        out.skipped = "no end state kept" if not bundle else f"end state missing: {bundle}"
        return out
    task_id = row["task_version"].rsplit("@", 1)[0]
    try:
        task = f.tasks.get(task_id)
    except KeyError:
        out.skipped = f"task {task_id} is not in the catalog"
        return out
    out.task_edited = task.key() != row["task_version"] or task.content_hash() != task_hash_of(row)
    settings.ensure_dirs()
    scratch = Path(tempfile.mkdtemp(prefix="regrade-", dir=settings.SANDBOXES_DIR))
    try:
        workspace, db_path, meta = load_endstate(Path(bundle), scratch)
        ended = meta.get("pre_judge") or meta          # before a judge failure made it unscored, if one did
        result = RunResult(run_key=RunKey.from_str(meta["run_key"]), status=RunStatus(ended["status"]),
                           scored=ended["scored"], output=meta["output"], error=ended["error"],
                           violations=[Violation(**v) for v in meta["violations"]])
        before = EnvState(**meta["before"])
        after = StateInspector(workspace, db_path).capture()
        ctx = GradeContext(task=task, result=result, before=before, after=after,
                           diff=StateInspector.diff(before, after), workspace=workspace, db_path=db_path,
                           spans=[Span(**s) for s in meta["spans"]], max_steps=meta["max_steps"],
                           remote=meta.get("remote") or {})
        grades = await f.graders.arun(ctx, f.judge)    # reads the subject before the scratch copy goes
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    apply_judge_failure(result, ctx.judgements)
    status = _finalize_status(result, grades, task.oracle.pass_threshold)
    scores, violations, critical = run_scores(result, grades)
    weights = ScoreWeights.for_profile(meta.get("weight_profile") or row["task_class"].split("|")[1].lower())
    out.after = Scored(status=status.value, outcome=round(scores.outcome, 6),
                       trajectory=round(scores.trajectory, 6),
                       composite=round(weights.composite(scores, critical), 6))
    out.changed = out.after != out.before
    if f.store is not None:
        out.regrade_id = f.store.add_regrade(dict(
            run_id=row["id"], run_key=row["run_key"], task_version=task.key(), task_hash=task.content_hash(),
            status=out.after.status, outcome=out.after.outcome, trajectory=out.after.trajectory,
            composite=out.after.composite, violations=[v.model_dump() for v in violations],
            grades=[g.model_dump() for g in grades], grader_versions={g.grader_id: g.version for g in grades},
            changed=int(out.changed), judge_usage=judge_usage(ctx.judgements)))
    return out


def select_rows(f: Factory, run_ids: list[int] | None = None, task: str | None = None,
                all_runs: bool = False) -> list[dict]:
    """The stored runs to re-grade: by id (any row), or the latest row per RunKey of one task or of all."""
    if f.store is None:
        raise ValueError("re-grading needs a results store")
    if sum(bool(x) for x in (run_ids, task, all_runs)) != 1:
        raise ValueError("choose exactly one of run ids, a task, or all")
    if run_ids:
        rows = []
        for rid in run_ids:
            r = f.store.run_by_id(rid)
            if r is None:
                raise KeyError(f"no stored run with id {rid}")
            rows.append(r)
        return rows
    rows = f.store.rows()
    if task:
        rows = [r for r in rows if r["task_version"] == task or r["task_version"].rsplit("@", 1)[0] == task]
    return rows


async def regrade(f: Factory, run_ids: list[int] | None = None, task: str | None = None,
                  all_runs: bool = False) -> list[RegradeOutcome]:
    """One run at a time: re-grading is cheap except for judge calls, which the quota ledger paces anyway."""
    return [await regrade_row(f, r) for r in select_rows(f, run_ids, task, all_runs)]
