"""ExperimentTracker (B9): mirrors each run to MLflow when MLFLOW_TRACKING_URI is set. Optional by design:
the results store is the source of truth; MLflow is the browsing and comparison surface."""

from __future__ import annotations

from harness_factory import settings
from harness_factory.recipes.model import HarnessRecipe
from harness_factory.results.model import EffectivenessRecord


class ExperimentTracker:
    def __init__(self, experiment: str = "harness-factory") -> None:
        self.enabled = False
        self.experiment = experiment
        self.disabled_reason: str | None = None      # set when MLflow was asked for but can't be used
        if not settings.MLFLOW_TRACKING_URI:
            return
        try:
            import mlflow
        except ImportError:
            self.disabled_reason = "MLFLOW_TRACKING_URI is set but mlflow is not installed (uv sync --extra mlops)"
            return
        try:
            mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)
            mlflow.set_experiment(experiment)
        except Exception as e:  # noqa: BLE001  unreachable server, bad URI: runs still go to the results store
            self.disabled_reason = f"cannot use MLflow at {settings.MLFLOW_TRACKING_URI}: {str(e)[:200]}"
            return
        self._mlflow = mlflow
        self.enabled = True

    def log(self, rec: EffectivenessRecord, recipe: HarnessRecipe) -> None:
        if not self.enabled:
            return
        mlflow = self._mlflow
        with mlflow.start_run(run_name=f"{rec.run_key.task_version}:{recipe.id}:{rec.run_key.canonical_model}"):
            mlflow.log_params({**recipe.to_mlflow_params(), "model": rec.run_key.canonical_model,
                               "provider": rec.run_key.provider, "seed": rec.run_key.seed,
                               "task": rec.run_key.task_version, "task_class": rec.task_class})
            mlflow.log_metrics({"outcome": rec.scores.outcome, "trajectory": rec.scores.trajectory,
                                "composite": rec.composite, "tokens_in": rec.cost.tokens_in,
                                "tokens_out": rec.cost.tokens_out, "tool_calls": rec.cost.tool_calls,
                                "usd": rec.cost.usd, "wall_s": rec.cost.wall_s})
            mlflow.set_tags({"status": rec.status.value, "run_key": rec.run_key.as_str()})
            if rec.trace_path:
                mlflow.log_artifact(rec.trace_path, artifact_path="trace")
            if rec.transcript_path:
                mlflow.log_artifact(rec.transcript_path, artifact_path="trace")
