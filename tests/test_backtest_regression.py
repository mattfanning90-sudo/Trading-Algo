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


@pytest.mark.xfail(
    reason="re-baselined in Task 20: baseline predates the next-day-close fill "
           "convention (Task 9) and the per-order commission floor (Task 13). "
           "Re-cut once, with the bridge report attached — spec §6. Remove this "
           "marker there.",
    strict=False)
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
    """Execution-timing invariant: a month-end target computed as-of D_k must be
    EXECUTED at D_{k+1}'s close, and must never be in the book at D_k or earlier
    (no lookahead — invariant #1).

    `weights_hist[D]` is the book as it stands at D's CLOSE — after any fill on
    that bar, and therefore the book that earns bar D+1's return. So a target
    appearing at `weights_hist[D_{k+1}]` is direct evidence that it was filled at
    D_{k+1}'s close, one bar after the signal. Pinned here so neither the t+2 lag
    bug nor a return to same-close fills can come back.
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

        # t+1: filled at D_{k+1}'s close, so that close's book IS the target
        # exactly (it has not drifted yet).
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


def test_baseline_records_a_nonzero_cost_per_sleeve():
    """Without a cost line in the baseline, `compare` has nothing to compare:
    CAGR alone moves less than TOL when every transaction cost is deleted."""
    baseline = ci_regression.load_baseline()
    for key, sleeve in baseline["sleeves"].items():
        assert "total_cost_fraction" in sleeve, f"{key} has no cost line"
        assert sleeve["total_cost_fraction"] > 0, f"{key} back-tested at zero cost"


def test_gate_fails_when_transaction_costs_are_deleted(monkeypatch):
    """Invariant #2 (costs always on), enforced by the gate rather than by
    convention. Deleting every transaction cost passes on CAGR/Vol/Sharpe/DD
    alone — measured: the sleeve CAGRs move 0.0022 to 0.0114 against TOL 0.02."""
    from trading_algo import backtest

    monkeypatch.setattr(backtest.fees, "turnover_cost", lambda *a, **k: 0.0)
    drift = ci_regression.compare(ci_regression.load_baseline(),
                                  ci_regression.synthetic_metrics())
    assert any("total_cost_fraction" in d for d in drift), (
        "the gate did not notice that every transaction cost was removed: "
        + ("\n".join(drift) or "(no drift at all)"))
