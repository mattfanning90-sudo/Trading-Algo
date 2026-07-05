"""Backlog F16: the synthetic backtest must match the committed baseline.

This is the same check the CI 'Backtest regression gate' step runs, wired into
the test suite so a behavioural regression (accidental lookahead, dropped cost,
sizing drift) fails locally too. Synthetic data only — invariant #5.
"""
import pytest

from trading_algo import ci_regression


@pytest.fixture(scope="module")
def current():
    return ci_regression.synthetic_metrics()


def test_baseline_exists():
    assert ci_regression.load_baseline() is not None, (
        "no committed baseline — run `python -m trading_algo.ci_regression --update`")


def test_synthetic_backtest_matches_baseline(current):
    baseline = ci_regression.load_baseline()
    drift = ci_regression.compare(baseline, current)
    assert not drift, "synthetic backtest drifted from baseline:\n" + "\n".join(drift)


def test_metrics_are_deterministic():
    a = ci_regression.synthetic_metrics()
    b = ci_regression.synthetic_metrics()
    assert a == b, "synthetic metrics must be reproducible run-to-run"


def test_compare_detects_injected_drift(current):
    # A lookahead leak would move CAGR far more than tolerance; simulate it.
    tampered = {**current, "portfolio": {**current["portfolio"],
                                         "CAGR": current["portfolio"]["CAGR"] + 0.5}}
    drift = ci_regression.compare(current, tampered)
    assert any("CAGR" in d for d in drift), "a large CAGR move must be flagged"


def test_compare_tolerates_small_noise(current):
    nudged = {**current, "portfolio": {**current["portfolio"],
                                       "AnnVol": current["portfolio"]["AnnVol"] + 0.001}}
    assert ci_regression.compare(current, nudged) == []
