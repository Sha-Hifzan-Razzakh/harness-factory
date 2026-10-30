"""Campaign report (markdown) generated from the results store: `hfx report`.

Sections:
  1. Scope            what the numbers cover: runs, models, recipes, tasks, cost baseline
  2. Recipes x models status counts, mean E0 and E, tokens per run
  3. Matrix vs base   each matrix recipe against the recipe it extends, paired by task and model: mean delta E,
                      wins / losses / ties, a bootstrap 95% interval. Pairing removes the task-to-task spread
                      that would otherwise swamp a dimension's effect.
  4. Selector         the pick per task class and model (SEL), with its rule
  5. Failures         every scored run short of SUCCESS: failed checks and violations
  6. Not scored       INCOMPLETE / ERROR runs by cause: infrastructure and setup, never averaged

Everything is computed from stored rows (newest result per RunKey), against one cost baseline.
"""

from __future__ import annotations

import random
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime

from harness_factory.results.cost import CostBaseline, is_clean
from harness_factory.results.store import UNSCORED

STATUSES = ("SUCCESS", "PARTIAL", "FAIL", "INCOMPLETE", "ERROR")


def paired_delta(pairs: list[tuple[float, float]], seed: int = 0) -> dict:
    """Mean of (variant - base) over paired runs, wins/losses/ties, and a bootstrap 95% interval."""
    deltas = [v - b for b, v in pairs]
    n = len(deltas)
    if not n:
        return {"n": 0}
    mean = statistics.fmean(deltas)
    if n > 1:
        rng = random.Random(seed)
        boots = sorted(statistics.fmean(rng.choices(deltas, k=n)) for _ in range(2000))
        ci = (boots[50], boots[1949])
    else:
        ci = (mean, mean)
    eps = 1e-9
    return {"n": n, "mean": mean, "ci": ci, "wins": sum(d > eps for d in deltas),
            "losses": sum(d < -eps for d in deltas), "ties": sum(abs(d) <= eps for d in deltas)}


def _cls(task_class: str) -> str:
    """depth|side_effects|risk, readable and safe inside a markdown table."""
    return task_class.replace("|", " / ")


def _table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def _failed_checks(row: dict) -> list[str]:
    return [c["id"] for g in (row.get("grades") or []) if g.get("grader_id") == "state_diff"
            for c in g.get("checks", []) if not c.get("passed")]


def _run_error(row: dict) -> str:
    for g in row.get("grades") or []:
        if g.get("grader_id") == "run_error":
            return str(g.get("detail", ""))[:120]
    return ""


def build_report(f, rows: list[dict], baseline: CostBaseline, *, title: str = "Campaign report",
                 models: list[str] | None = None, with_selector: bool = True) -> str:
    clean = [r for r in rows if is_clean(r) and (not models or r["canonical_model"] in models)]
    scored = [r for r in clean if r["status"] not in UNSCORED]
    e_of = {r["run_key"]: baseline.adjusted(r) for r in scored}
    lines = [f"# {title}", "", (f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `hfx report` from the results "
                                f"store. Cost baseline `{baseline.id}` ({baseline.basis})."), ""]

    # 1. scope
    cells = sorted({(r["canonical_model"], r["provider"]) for r in clean})
    recipe_hashes = defaultdict(set)
    for r in clean:
        recipe_hashes[r["recipe_id"]].add(r["recipe_hash"])
    lines += ["## 1. Scope", "", _table(["", ""], [
        ["Runs (clean, newest per RunKey)", f"{len(clean)} ({len(scored)} scored)"],
        ["Models", ", ".join(f"`{m}` on {p}" for m, p in cells) or "-"],
        ["Recipes", ", ".join(f"`{r}`" for r in sorted(recipe_hashes)) or "-"],
        ["Tasks", str(len({r['task_version'] for r in clean}))],
        ["Faulted runs (feed R only)", str(sum(1 for r in rows if not is_clean(r)))],
    ]), ""]
    changed = [r for r, hs in recipe_hashes.items() if len(hs) > 1]
    if changed:
        lines += [(f"> Recipes edited during these runs (more than one hash): {', '.join(changed)}. Their rows are "
                   "different experiments; the selector only uses each recipe's current hash."), ""]

    # 2. recipes x models
    groups = defaultdict(list)
    for r in clean:
        groups[(r["recipe_id"], r["canonical_model"])].append(r)
    body = []
    for (rid, m), rs in sorted(groups.items()):
        st = Counter(r["status"] for r in rs)
        sc = [r for r in rs if r["status"] not in UNSCORED]
        e0 = statistics.fmean(r["composite"] for r in sc) if sc else None
        e = statistics.fmean(e_of[r["run_key"]] for r in sc) if sc else None
        tok = statistics.fmean(r["tokens_in"] + r["tokens_out"] for r in sc) if sc else None
        body.append([f"`{rid}`", f"`{m}`", *(str(st.get(s, 0)) for s in STATUSES),
                     "-" if e0 is None else f"{e0:.3f}", "-" if e is None else f"{e:.3f}",
                     "-" if tok is None else f"{tok:,.0f}"])
    lines += ["## 2. Recipes × models", "", _table(["recipe", "model", *STATUSES, "E0", "E", "tokens/run"], body), ""]

    # 3. matrix vs base, paired by (task, model, provider, seed)
    by_key = {(r["recipe_id"], r["task_version"], r["canonical_model"], r["provider"], r["seed"]): r for r in scored}
    body = []
    for rid in sorted(recipe_hashes):
        recipe = f.recipes.get(rid)
        if recipe is None or not recipe.base:
            continue
        for m, p in cells:
            pairs = [(e_of[b["run_key"]], e_of[v["run_key"]]) for (r2, tv, m2, p2, s), v in by_key.items()
                     if r2 == rid and m2 == m and p2 == p and (b := by_key.get((recipe.base, tv, m, p, s)))]
            d = paired_delta(pairs)
            if not d["n"]:
                continue
            verdict = "better" if d["ci"][0] > 0 else "worse" if d["ci"][1] < 0 else "no clear difference"
            body.append([f"`{rid}`", f"`{recipe.base}`", f"`{m}`", str(d["n"]), f"{d['mean']:+.3f}",
                         f"{d['ci'][0]:+.3f} to {d['ci'][1]:+.3f}", f"{d['wins']}/{d['losses']}/{d['ties']}", verdict])
    lines += ["## 3. Matrix recipes vs their base (paired)", ""]
    lines += [_table(["recipe", "base", "model", "pairs", "mean ΔE", "95% CI", "W/L/T", "verdict"], body)
              if body else "No matrix recipe has runs paired with its base yet.", ""]

    # 4. selector
    if with_selector:
        from harness_factory.select.policy import build_policy
        body = []
        for m, p in cells:
            pol = build_policy(f, rows, baseline, m, p)
            for cls, d in pol.decisions.items():
                top = d.ranked[0] if d.ranked else None
                body.append([_cls(cls), f"`{m}`", d.rule, ", ".join(f"`{c}`" for c in d.chosen),
                             "-" if top is None else f"{top.mean_e:.3f} (n={top.n})", str(len(d.insufficient))])
        lines += ["## 4. Selector picks", "", _table(["task class", "model", "rule", "chosen", "E (n)", "below min"],
                                                     body) if body else "-", ""]

    # 5. failures, one line per distinct way of failing (seeds that failed the same way are counted)
    ways = Counter((r["task_version"], r["recipe_id"], r["canonical_model"], r["status"],
                    ", ".join(_failed_checks(r)) or "-",
                    "; ".join(sorted({v.get("kind", "") for v in (r.get("violations") or [])})) or "-")
                   for r in scored if r["status"] != "SUCCESS")
    body = [[f"`{t}`", f"`{rid}`", f"`{m}`", st, checks, viol, str(n)]
            for (t, rid, m, st, checks, viol), n in sorted(ways.items())]
    lines += ["## 5. Scored runs short of SUCCESS", ""]
    lines += [_table(["task", "recipe", "model", "status", "failed checks", "violations", "runs"], body)
              if body else "None.", ""]

    # 6. not scored
    causes = Counter((r["status"], _run_error(r).split(":")[0] or "-") for r in clean if r["status"] in UNSCORED)
    lines += ["## 6. Not scored (excluded from every mean)", ""]
    lines += [_table(["status", "cause", "runs"], [[s, c, str(n)] for (s, c), n in sorted(causes.items())])
              if causes else "None.", ""]
    return "\n".join(lines)
