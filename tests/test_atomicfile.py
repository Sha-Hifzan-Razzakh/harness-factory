"""Atomic replacement that survives Windows readers (#111, lab D1), and the quota ledger's first load under its lock."""

from __future__ import annotations

import os

import pytest

from harness_factory import atomicfile, settings
from harness_factory.providers import quota
from harness_factory.providers.catalog import load_provider_catalog
from harness_factory.providers.quota import QuotaLedger


def _flaky_replace(monkeypatch, failures: int, exc: type[OSError] = PermissionError) -> list[int]:
    """os.replace that raises `exc` `failures` times (a Windows reader holding the file), then works."""
    real, calls = os.replace, []

    def fake(src, dst):
        calls.append(1)
        if len(calls) <= failures:
            raise exc(5, "Access is denied")
        real(src, dst)

    monkeypatch.setattr(atomicfile.os, "replace", fake)
    return calls


def test_replace_retries_a_permission_error_until_the_reader_lets_go(tmp_path, monkeypatch):
    src, dst = tmp_path / "f.tmp", tmp_path / "f"
    src.write_text("new")
    dst.write_text("old")
    calls, slept = _flaky_replace(monkeypatch, failures=3), []
    atomicfile.replace(src, dst, sleep=slept.append)
    assert dst.read_text() == "new" and not src.exists()
    assert len(calls) == 4 and slept == list(atomicfile.RETRY_DELAYS_S[:3])


def test_replace_gives_up_with_the_original_error(tmp_path, monkeypatch):
    src, dst = tmp_path / "f.tmp", tmp_path / "f"
    src.write_text("new")
    calls = _flaky_replace(monkeypatch, failures=99)
    with pytest.raises(PermissionError):
        atomicfile.replace(src, dst, sleep=lambda _s: None)
    assert len(calls) == len(atomicfile.RETRY_DELAYS_S) + 1 and src.read_text() == "new"


def test_replace_never_retries_other_errors(tmp_path, monkeypatch):
    src, dst = tmp_path / "f.tmp", tmp_path / "f"
    src.write_text("new")
    calls = _flaky_replace(monkeypatch, failures=99, exc=FileNotFoundError)
    with pytest.raises(FileNotFoundError):
        atomicfile.replace(src, dst, sleep=lambda _s: None)
    assert len(calls) == 1


def test_the_ledger_save_survives_a_windows_reader(tmp_path, monkeypatch):
    state = tmp_path / "quota.json"
    ledger = QuotaLedger(load_provider_catalog(settings.PROVIDERS_FILE), jitter_s=0, state_path=state)
    monkeypatch.setattr(atomicfile.time, "sleep", lambda _s: None)
    calls = _flaky_replace(monkeypatch, failures=2)
    ledger.save()
    assert state.is_file() and len(calls) == 3


def test_the_first_load_takes_the_lock(tmp_path, monkeypatch):
    state = tmp_path / "quota.json"
    catalog = load_provider_catalog(settings.PROVIDERS_FILE)
    QuotaLedger(catalog, jitter_s=0, state_path=state).save()           # a file another process wrote
    locked, real = [], quota.file_lock

    def spy(path, *a, **kw):
        locked.append(path.name)
        return real(path, *a, **kw)

    monkeypatch.setattr(quota, "file_lock", spy)
    QuotaLedger(catalog, jitter_s=0, state_path=state)
    assert locked == ["quota.json.lock"]
    QuotaLedger(catalog, jitter_s=0, state_path=tmp_path / "missing.json")   # nothing to read: no lock needed
    assert locked == ["quota.json.lock"]
