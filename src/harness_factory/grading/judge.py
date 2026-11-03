"""Rubric judge (GRD-03, ADR 0014): a judge model scores open-ended deliverables against a check's rubric.

Flow, per run: GradingPipeline.arun() asks RubricJudge.judge_checks() for a RubricVerdict per rubric check, then
the synchronous graders read the verdicts (StateDiffGrader turns them into check results). The judge

  * calls its model through ModelGateway only: quota slot, timeout, retries and error classification are the
    gateway's; this module catches only the HarnessError it raises
  * has its own Tracer, so its model calls are never in the run's spans: they never enter the run's CostLedger,
    its budget or C. Their usage is stored with the verdict (and summed per run as judge_usage)
  * sees the rubric's criteria and the subject, nothing else: no expected answers, no other checks, no threshold
  * frames the subject as data between two marker lines that carry a code derived from the subject itself (the
    first 16 hex digits of its SHA-256), so the subject cannot close its own block and add instructions after it

Identity. judge_hash covers what decides how the judge scores: provider and model (canonical and provider id),
the two prompt templates' sources, sampling, the subject cap and JUDGE_PROTOCOL (framing + reply parser). The
rubric has its own rubric_hash (it is also in the task hash). Both are stored with every verdict.

Trust. A judge is calibrated when its current judge_hash has a calibration record (grading/calibration.py) on at
least MIN_ITEMS hand-scored items with Cohen's kappa >= MIN_KAPPA; the latest such record decides. An
uncalibrated judge's checks are graded and reported, but carry weight 0 (grading/state._weigh_rubric_checks).

Failure. A judge that cannot grade (provider down, quota, setup, an unparseable reply) never gives the agent a 0:
  * uncalibrated judge: the check is marked ungraded; it carried weight 0 anyway, so the run is scored as usual
  * calibrated judge: the outcome cannot be computed, so the run is unscored: INCOMPLETE (ERROR for a setup
    problem), excluded from cell means and rerun by --resume (apply_judge_failure)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from harness_factory import settings
from harness_factory.grading.verdict import CriterionScore, JudgeUsage, RubricVerdict
from harness_factory.hashing import content_hash
from harness_factory.observability.span import SpanKind
from harness_factory.observability.tracer import Tracer
from harness_factory.providers.catalog import ProviderCatalog
from harness_factory.providers.quota import QuotaLedger
from harness_factory.recipes.model import ModelSlot
from harness_factory.shapes.result import RunResult, RunStatus
from harness_factory.substrate.errors import ErrorKind, HarnessError
from harness_factory.substrate.gateway import Backend, Completion, ModelGateway, approx_tokens
from harness_factory.substrate.prompts import PromptRegistry
from harness_factory.tasks.model import Rubric, WeightedCheck

JUDGE_PROTOCOL = "1"        # the subject framing and the reply parser; changing either changes every judge hash
MIN_ITEMS = 100             # a calibration needs at least this many hand-scored items...
MIN_KAPPA = 0.7             # ...and at least this agreement with the humans on pass/fail
SETUP_KINDS = frozenset({ErrorKind.CONFIG, ErrorKind.PROVISION})


class JudgeConfig(BaseModel):
    """catalog/judge/judge.yaml. HF_JUDGE_PROVIDER / HF_JUDGE_MODEL override provider and model."""
    provider: str
    model: str                                  # canonical model, served by `provider` in providers.yaml
    system_prompt: str = "judge/system"
    user_prompt: str = "judge/rubric"
    temperature: float = 0.0
    max_output_tokens: int = Field(default=1024, gt=0)
    max_subject_chars: int = Field(default=12_000, gt=0)
    timeout_s: float | None = None              # per call; None = HF_CALL_TIMEOUT_S. Not part of the judge hash
    retries: int = Field(default=2, ge=0)       # transient provider errors. Not part of the judge hash


def judge_file() -> Path:
    return settings.CATALOG / "judge" / "judge.yaml"


def load_judge_config(path: Path | None = None, environ: dict[str, str] | None = None) -> JudgeConfig | None:
    """The configured judge, or None when there is no judge file (rubric checks are then ungraded, weight 0)."""
    path = path or judge_file()
    env = os.environ if environ is None else environ
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text()) or {}
    if env.get("HF_JUDGE_PROVIDER"):
        data["provider"] = env["HF_JUDGE_PROVIDER"]
    if env.get("HF_JUDGE_MODEL"):
        data["model"] = env["HF_JUDGE_MODEL"]
    return JudgeConfig.model_validate(data)


def rubric_hash(rubric: Rubric) -> str:
    return content_hash(rubric.model_dump(mode="json"))


def subject_nonce(subject: str) -> str:
    """The code on the subject's marker lines: from the subject itself, so the same subject always renders the
    same prompt, and a subject cannot contain its own closing marker (that would need a SHA-256 fixed point)."""
    return hashlib.sha256(subject.encode("utf-8", "surrogatepass")).hexdigest()[:16]


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def parse_reply(text: str, rubric: Rubric) -> list[CriterionScore]:
    """The judge's reply: one JSON object (a ``` fence around it is tolerated) with a score in [0, 1] for every
    criterion id, each exactly once. Anything else is the judge's malformed output (MODEL_PARSE)."""
    m = _JSON_BLOCK.search(text or "")
    if m is None:
        raise HarnessError(ErrorKind.MODEL_PARSE, f"judge reply has no JSON object: {str(text)[:200]!r}")
    try:
        doc = json.loads(m.group(0))
    except ValueError as e:
        raise HarnessError(ErrorKind.MODEL_PARSE, f"judge reply is not JSON: {e}") from e
    items = doc.get("criteria") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        raise HarnessError(ErrorKind.MODEL_PARSE, "judge reply has no 'criteria' list")
    want = [c.id for c in rubric.criteria]
    got: dict[str, CriterionScore] = {}
    for it in items:
        if not isinstance(it, dict) or it.get("id") not in want or it["id"] in got:
            raise HarnessError(ErrorKind.MODEL_PARSE, f"judge reply: unexpected or repeated entry {it!r:.120}")
        s = it.get("score")
        if isinstance(s, bool) or not isinstance(s, (int, float)) or not 0 <= s <= 1:
            raise HarnessError(ErrorKind.MODEL_PARSE, f"judge reply: score for {it['id']} must be in [0, 1], got {s!r}")
        got[it["id"]] = CriterionScore(id=it["id"], score=float(s), reason=str(it.get("reason") or "")[:300])
    missing = [i for i in want if i not in got]
    if missing:
        raise HarnessError(ErrorKind.MODEL_PARSE, f"judge reply misses criteria {missing}")
    return [got[i] for i in want]


def weighted_score(rubric: Rubric, scores: list[CriterionScore]) -> float:
    by_id = {s.id: s.score for s in scores}
    return round(sum(c.weight * by_id[c.id] for c in rubric.criteria), 6)


# ---- the scripted judge (offline, deterministic) ---------------------------------------------------------------

_MARK = re.compile(r"^<<<DELIVERABLE ([0-9a-f]{16})$", re.MULTILINE)
_CRITERION = re.compile(r"^- \[([A-Za-z0-9_.-]+)\] (.+)$", re.MULTILINE)
_QUOTED = re.compile(r'"([^"]+)"')
_WORD = re.compile(r"[a-z0-9@._-]{5,}")
_STOP = frozenset({"about", "after", "before", "being", "every", "their", "there", "these", "those", "which", "while",
                   "would", "should", "could", "other", "where", "under", "names", "states", "says"})


def read_judge_prompt(messages: list[dict]) -> tuple[list[tuple[str, str]], str]:
    """(criteria, subject) as a model reads them: criteria only from the text before the first marker line,
    the subject only from between the markers carrying that code."""
    text = next((m.get("content") or "" for m in reversed(messages) if m.get("role") == "user"), "")
    m = _MARK.search(text)
    if m is None:
        raise HarnessError(ErrorKind.MODEL_PARSE, "scripted judge: no deliverable markers in the prompt")
    head, nonce = text[:m.start()], m.group(1)
    end = text.find(f"\nDELIVERABLE {nonce}>>>", m.end())
    if end < 0:
        raise HarnessError(ErrorKind.MODEL_PARSE, "scripted judge: the deliverable block is not closed")
    return _CRITERION.findall(head), text[m.end() + 1:end]


def scripted_score(criterion: str, subject: str) -> float:
    """Phrases in double quotes must all appear in the subject (case-insensitive): 1 all, 0.5 some, 0 none.
    Without quotes: the share of the criterion's longer words found in the subject (>= 0.6 -> 1, >= 0.3 -> 0.5)."""
    body = subject.casefold()
    phrases = [p.casefold() for p in _QUOTED.findall(criterion)]
    if phrases:
        hits = sum(p in body for p in phrases)
        return 1.0 if hits == len(phrases) else 0.5 if hits else 0.0
    words = [w for w in _WORD.findall(criterion.casefold()) if w not in _STOP]
    if not words:
        return 1.0 if body.strip() else 0.0
    share = sum(w in body for w in words) / len(words)
    return 1.0 if share >= 0.6 else 0.5 if share >= 0.3 else 0.0


class ScriptedJudgeBackend:
    """A deterministic stand-in for a judge model (offline runs, CI). It reads the rendered prompt the way a
    model would (scripted_score). It is never calibrated: it exists to exercise the path, not to grade."""

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []          # the messages of every call, for tests

    async def complete(self, messages, tools, slot, provider, served) -> Completion:
        self.calls.append(messages)
        criteria, subject = read_judge_prompt(messages)
        reply = {"criteria": [{"id": cid, "score": scripted_score(text, subject), "reason": "scripted"}
                              for cid, text in criteria]}
        msg = {"role": "assistant", "content": json.dumps(reply)}
        return Completion(message=msg, tokens_in=approx_tokens(messages), tokens_out=approx_tokens(msg))


# ---- the judge -------------------------------------------------------------------------------------------------

def status_from_records(records: list[Any]) -> tuple[bool, Any]:
    """(calibrated, the deciding record). Records on fewer than MIN_ITEMS items are informational; of the rest
    the latest decides, so a newer, worse measurement withdraws the trust an older one gave."""
    full = sorted((r for r in records if r.n >= MIN_ITEMS), key=lambda r: r.created_at)
    if not full:
        return False, None
    last = full[-1]
    return last.kappa is not None and last.kappa >= MIN_KAPPA, last


def _usage(tracer: Tracer) -> JudgeUsage:
    u = JudgeUsage()
    for s in tracer.spans:
        if s.kind != SpanKind.model_call:
            continue
        if s.status == "error":
            u.failed_calls += 1
            continue
        u.model_calls += 1
        u.tokens_in += s.tokens_in
        u.tokens_out += s.tokens_out
        u.usd = round(u.usd + float(s.attributes.get("usd", 0.0)), 9)
    return u


def read_subject(ctx: Any, c: WeightedCheck) -> tuple[str, str | None]:
    """(subject, why it fails without a judge call). A missing, unreadable or empty deliverable is the agent's
    doing: the check fails like any other check whose file is missing."""
    if c.path is None:
        out = ctx.result.output if isinstance(ctx.result.output, str) else str(ctx.result.output or "")
        return out, None if out.strip() else "empty final answer"
    ws = Path(ctx.workspace).resolve()
    p = (ws / c.path).resolve()
    if not p.is_relative_to(ws) or not p.is_file():
        return "", f"missing file {c.path}"
    text = p.read_text(errors="replace")
    return text, None if text.strip() else f"empty file {c.path}"


class RubricJudge:
    def __init__(self, config: JudgeConfig, providers: ProviderCatalog, ledger: QuotaLedger,
                 calibrations: Callable[[str], list[Any]] | None = None, prompts: PromptRegistry | None = None,
                 backend: Backend | None = None) -> None:
        """calibrations: judge hash -> its calibration records (ResultsStore.calibrations); None = no records,
        so the judge is uncalibrated. backend: the model backend (tests); default LiteLLM, or the scripted judge
        for a scripted provider."""
        self.config = config
        self.providers = providers
        self.ledger = ledger
        self.calibrations = calibrations
        self.prompts = prompts or PromptRegistry()
        provider = providers.get(config.provider)
        self.served = providers.served(config.provider, config.model)      # KeyError: not in providers.yaml
        self.backend = backend or (ScriptedJudgeBackend() if provider.kind == "scripted" else None)
        self.hash = self._hash()

    @classmethod
    def from_catalog(cls, providers: ProviderCatalog, ledger: QuotaLedger,
                     calibrations: Callable[[str], list[Any]] | None = None) -> RubricJudge | None:
        cfg = load_judge_config()
        if cfg is None:
            return None
        try:
            return cls(cfg, providers, ledger, calibrations)
        except (KeyError, FileNotFoundError) as e:       # loud: a mistyped judge must not silently weigh 0
            raise ValueError(f"rubric judge ({judge_file()}, HF_JUDGE_PROVIDER / HF_JUDGE_MODEL): {e}") from e

    def _source(self, ref: str) -> str:
        path = self.prompts.root / f"{ref}.j2"
        if not path.is_file():
            raise FileNotFoundError(f"judge prompt {ref} not found under {self.prompts.root}")
        return path.read_text()

    def _hash(self) -> str:
        c = self.config
        return content_hash({
            "protocol": JUDGE_PROTOCOL, "provider": c.provider, "model": c.model,
            "provider_model_id": self.served.provider_model_id, "temperature": c.temperature,
            "max_output_tokens": c.max_output_tokens, "max_subject_chars": c.max_subject_chars,
            "prompts": {"system": self._source(c.system_prompt), "user": self._source(c.user_prompt)},
        })

    def calibration(self) -> tuple[bool, Any]:
        return status_from_records(self.calibrations(self.hash) if self.calibrations else [])

    def messages(self, rubric: Rubric, subject: str) -> list[dict[str, str]]:
        """The judge's whole input: its instructions, the criteria and the framed subject. Nothing else."""
        cap = self.config.max_subject_chars
        shown = subject[:cap]
        system = self.prompts.render(self.config.system_prompt)
        user = self.prompts.render(self.config.user_prompt, rubric=rubric, subject=shown, nonce=subject_nonce(shown),
                                   truncated=len(subject) > cap, max_chars=cap)
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    async def score(self, rubric: Rubric, subject: str, *, check_id: str, label: str,
                    calibrated: bool) -> RubricVerdict:
        """One judge call on one subject."""
        base = dict(check_id=check_id, judge_hash=self.hash, rubric_hash=rubric_hash(rubric), calibrated=calibrated,
                    subject_chars=len(subject), truncated=len(subject) > self.config.max_subject_chars)
        tracer = Tracer(f"judge|{label}")
        gw = ModelGateway(self.providers, self.ledger, tracer, self.config.provider, backend=self.backend,
                          retries=self.config.retries, timeout_s=self.config.timeout_s or settings.CALL_TIMEOUT_S)
        slot = ModelSlot(role="judge", canonical_model=self.config.model, temperature=self.config.temperature,
                         max_output_tokens=self.config.max_output_tokens)
        try:
            comp = await gw.complete(slot, self.messages(rubric, subject), [])
            scores = parse_reply(str(comp.message.get("content") or ""), rubric)
        except HarnessError as e:          # already classified by the gateway (or the parser): the judge failed
            return RubricVerdict(**base, available=False, error=f"{e.kind.value}: {e.message[:300]}",
                                 error_kind=e.kind.value, usage=_usage(tracer))
        total = weighted_score(rubric, scores)
        return RubricVerdict(**base, available=True, score=total, passed=total >= rubric.threshold - 1e-9,
                             criteria=scores, usage=_usage(tracer))

    async def judge_checks(self, ctx: Any) -> dict[str, RubricVerdict]:
        """A verdict for every rubric check of ctx.task (GradingPipeline.arun)."""
        calibrated, _ = self.calibration()
        out: dict[str, RubricVerdict] = {}
        for c in ctx.task.oracle.rubric_checks():
            base = dict(check_id=c.id, judge_hash=self.hash, rubric_hash=rubric_hash(c.rubric), calibrated=calibrated)
            if not ctx.result.scored:
                out[c.id] = RubricVerdict(**base, available=False, error="run not scored: nothing to judge")
                continue
            subject, fails = read_subject(ctx, c)
            if fails is not None:
                out[c.id] = RubricVerdict(**base, available=True, score=0.0, passed=False, error=fails)
                continue
            out[c.id] = await self.score(c.rubric, subject, check_id=c.id, label=ctx.result.run_key.short(),
                                         calibrated=calibrated)
        return out


# ---- what a judge failure does to the run ----------------------------------------------------------------------

def judge_failure(judgements: dict[str, RubricVerdict]) -> RubricVerdict | None:
    """The first verdict the outcome needed but the judge could not give (calibrated and unavailable)."""
    return next((v for v in judgements.values() if v.calibrated and not v.available), None)


def apply_judge_failure(result: RunResult, judgements: dict[str, RubricVerdict]) -> dict[str, Any] | None:
    """A scored run whose calibrated judge failed becomes unscored: INCOMPLETE (provider, quota, unusable reply)
    or ERROR (judge setup). Returns how the run stood before, for its end state, so `hfx regrade` can judge it
    again; None when nothing changed."""
    v = judge_failure(judgements) if result.scored else None
    if v is None:
        return None
    before = {"status": result.status.value, "scored": True, "error": result.error}
    setup = v.error_kind in {k.value for k in SETUP_KINDS}
    result.status, result.scored = (RunStatus.ERROR if setup else RunStatus.INCOMPLETE), False
    result.error = f"{'setup' if setup else 'infra'}: judge for check {v.check_id}: {v.error}"
    return before


def judge_usage(judgements: dict[str, RubricVerdict], wall_s: float | None = None) -> dict[str, Any] | None:
    """The run's judge usage, stored apart from its cost (runs.judge_usage); None when nothing was judged."""
    if not judgements:
        return None
    total = JudgeUsage()
    for v in judgements.values():
        total = total + v.usage
    out = {**total.model_dump(), "judge_hash": next(iter(judgements.values())).judge_hash}
    if wall_s is not None:
        out["wall_s"] = round(wall_s, 3)
    return out
