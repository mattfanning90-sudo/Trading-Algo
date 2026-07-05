"""Backlog F17 / foundation P0-G: run manifests + experiment ledger."""
import json

import pytest

from trading_algo import manifest
from trading_algo.config import DEFAULT_PARAMS


def test_params_fingerprint_is_stable_and_sensitive():
    a = manifest.params_fingerprint(DEFAULT_PARAMS)
    b = manifest.params_fingerprint(DEFAULT_PARAMS)
    assert a == b, "same params must fingerprint identically"
    changed = DEFAULT_PARAMS.with_overrides(top_n=DEFAULT_PARAMS.top_n + 1)
    assert manifest.params_fingerprint(changed) != a, "changed knob must change the id"


def test_build_manifest_is_valid_and_complete():
    m = manifest.build_manifest(
        "backtest", params=DEFAULT_PARAMS, regions=["US"],
        metrics={"CAGR": 0.1}, data_range=("2012-01-01", "2026-01-01"),
        synthetic=True, created_utc="2026-07-04T00:00:00+00:00")
    assert manifest.validate_manifest(m) == []
    assert m["kind"] == "backtest"
    assert m["regions"] == ["US"]
    assert m["synthetic"] is True
    assert m["data_range"] == {"start": "2012-01-01", "end": "2026-01-01"}
    assert m["params"]["top_n"] == DEFAULT_PARAMS.top_n


def test_unknown_kind_rejected():
    with pytest.raises(ValueError):
        manifest.build_manifest("nonsense", params=DEFAULT_PARAMS, regions=["US"],
                                metrics={})


def test_validate_catches_bad_manifest():
    assert manifest.validate_manifest({"kind": "backtest"})  # missing fields
    assert manifest.validate_manifest("not a dict")


def test_ledger_appends_and_counts(tmp_path):
    ledger = str(tmp_path / "experiment_ledger.jsonl")
    assert manifest.trial_count(ledger) == 0
    for i in range(3):
        m = manifest.build_manifest(
            "sweep", params=DEFAULT_PARAMS.with_overrides(top_n=5 + i),
            regions=["US"], metrics={"Sharpe": 0.5 + i},
            created_utc="2026-07-04T00:00:00+00:00")
        manifest.append_run(ledger, m)
    assert manifest.trial_count(ledger) == 3
    assert manifest.trial_count(ledger, kind="sweep") == 3
    assert manifest.trial_count(ledger, kind="backtest") == 0

    # filtering by fingerprint isolates one variant's trials (the DSR n_trials)
    fp = manifest.params_fingerprint(DEFAULT_PARAMS.with_overrides(top_n=5))
    assert manifest.trial_count(ledger, params_fingerprint_filter=fp) == 1

    rows = manifest.read_ledger(ledger)
    assert len(rows) == 3 and all("git_commit" in r for r in rows)


def test_write_manifest_roundtrip(tmp_path):
    m = manifest.build_manifest("portfolio", params=DEFAULT_PARAMS,
                                regions=["US", "ASX"], metrics={"CAGR": 0.05},
                                created_utc="2026-07-04T00:00:00+00:00")
    path = manifest.write_manifest(m, str(tmp_path / "sub" / "run.json"))
    with open(path) as f:
        assert json.load(f)["regions"] == ["US", "ASX"]
