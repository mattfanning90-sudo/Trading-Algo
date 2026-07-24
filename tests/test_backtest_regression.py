"""Backlog F16: the synthetic backtest must match the committed baseline.

This is the same check the CI 'Backtest regression gate' step runs, wired into
the test suite so a behavioural regression (accidental lookahead, dropped cost,
sizing drift) fails locally too. Synthetic data only — invariant #5.
"""
import pandas as pd
import pytest

from trading_algo import backtest, ci_regression, strategy


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


def test_target_first_affects_equity_at_t_plus_one(synth_asx, asx_region):
    """Execution-timing invariant: a month-end target computed as-of D_k must
    first affect the book that earns returns on bar D_{k+1} (true t+1), and must
    NEVER be the book on D_k or earlier (no lookahead — invariant #1).

    `weights_hist[D]` is exactly the book used to compute bar D's return in
    `run_backtest`, so it is direct evidence of which bar a target first moves
    equity. Pinned here so the t+2 lag bug can't return.
    """
    prices, index_px = synth_asx
    p = asx_region.params
    result = backtest.run_backtest(prices, index_px, asx_region, max_drawdown_stop=None)
    weights_hist = result["weights"]

    rebal_marks = prices.resample(p.rebalance).last().index
    checked = 0
    for d in rebal_marks:
        loc = prices.index.searchsorted(d, side="right") - 1
        if loc < p.min_history_days or loc + 1 >= len(prices):
            continue
        asof = prices.index[loc]
        expected = strategy.compute_targets(prices, index_px, p, asof=asof)
        if expected.empty:
            continue
        d_k = prices.index[loc]          # signal / as-of bar
        d_k1 = prices.index[loc + 1]     # t+1

        # t+1: the target first takes effect on D_{k+1}, exactly (pre-drift book).
        got_next = weights_hist.get(d_k1)
        assert got_next is not None and not got_next.empty, (
            f"target for {asof.date()} did not take effect at t+1 ({d_k1.date()})")
        assert len(got_next) == len(expected)
        pd.testing.assert_series_equal(
            got_next.sort_index(), expected.sort_index(), check_names=False)

        # no lookahead: the fresh target must not be the book on its own as-of bar.
        got_asof = weights_hist.get(d_k)
        if got_asof is not None and not got_asof.empty and len(got_asof) == len(expected):
            assert not got_asof.reindex(expected.index).equals(expected), (
                f"target for {asof.date()} leaked onto its own as-of bar (lookahead)")

        checked += 1
        if checked >= 3:
            break
    assert checked > 0, "no non-empty rebalance target was validated"
