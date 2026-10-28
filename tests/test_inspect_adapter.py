"""Inspect AI adapter: factory runs as an Inspect eval, with the factory's grades and transcripts."""

import pytest

inspect_ai = pytest.importorskip("inspect_ai")

from inspect_ai import eval as inspect_eval

from harness_factory.integrations.inspect_task import harness_factory, to_inspect_messages


def test_transcript_conversion_keeps_tool_calls():
    msgs = to_inspect_messages([
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do it"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "fs__read_file", "arguments": '{"path": "a"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "body"},
        {"role": "assistant", "content": "done"},
    ])
    assert [m.role for m in msgs] == ["system", "user", "assistant", "tool", "assistant"]
    assert msgs[2].tool_calls[0].function == "fs.read_file" and msgs[2].tool_calls[0].arguments == {"path": "a"}
    assert msgs[3].function == "fs.read_file"


def test_bad_tool_arguments_are_kept_as_parse_errors():
    msgs = to_inspect_messages([{"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "fs__read_file", "arguments": "{not json"}}]}])
    assert msgs[0].tool_calls[0].parse_error


def test_factory_runs_as_inspect_eval(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_RESULTS_URL", f"sqlite:///{tmp_path / 'results.db'}")
    from harness_factory import settings
    monkeypatch.setattr(settings, "RESULTS_URL", f"sqlite:///{tmp_path / 'results.db'}")

    [log] = inspect_eval(harness_factory(recipe="v3-staged", tasks="fs-02,sql-01"), model="mockllm/model",
                         epochs=2, log_dir=str(tmp_path / "logs"), display="none")
    assert log.status == "success" and len(log.samples) == 4
    metrics = {s.name: {k: v.value for k, v in s.metrics.items()} for s in log.results.scores}
    assert metrics["composite"]["mean"] == pytest.approx(1.0)
    assert metrics["success"]["mean"] == pytest.approx(1.0)

    sample = next(s for s in log.samples if s.id == "fs-02" and s.epoch == 2)
    score = sample.scores["effectiveness"]
    assert score.answer == "SUCCESS" and score.value["composite"] == 1.0
    assert score.metadata["run_key"]["seed"] == 1                   # epoch 2 -> seed 1
    called = [c.function for m in sample.messages for c in (getattr(m, "tool_calls", None) or [])]
    assert called == ["fs.read_file", "fs.write_file"]              # full transcript in the Inspect log
    assert sample.output.completion.startswith("Changed the port")


def test_inspect_does_not_regrade_failures(tmp_path, monkeypatch):
    from harness_factory import settings
    monkeypatch.setattr(settings, "RESULTS_URL", f"sqlite:///{tmp_path / 'results.db'}")
    [log] = inspect_eval(harness_factory(recipe="v1-base", tasks="sql-01"), model="mockllm/model",
                         log_dir=str(tmp_path / "logs"), display="none")
    score = log.samples[0].scores["effectiveness"]
    assert score.answer == "PARTIAL" and score.value["success"] == 0.0
    assert "answer" in score.explanation                            # names the failed oracle check
