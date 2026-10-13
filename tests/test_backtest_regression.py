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


@pytest.mark.parametrize("base, cur", [
    (0.0863, float("nan")),               # a metric that turned into NaN
    (float("nan"), 0.0863),               # a NaN baseline compares to nothing
    (float("nan"), float("nan")),         # no gate metric is legitimately NaN
    (float("inf"), float("inf")),         # inf - inf is NaN too
])
def test_compare_fails_on_a_non_finite_metric(base, cur):
    """`abs(base - cur) > tol` is False whenever either side is NaN, so a metric
    that turned into NaN (Task 13b made a CAGR of fewer than 2 marks NaN) used
    to pass the gate silently."""
    drift = ci_regression.compare({"portfolio": {"CAGR": base}},
                                  {"portfolio": {"CAGR": cur}})
    assert len(drift) == 1 and drift[0].startswith("portfolio.CAGR:"), drift


# The resolution each gate metric is reported at: metrics.compute_metrics rounds
# CAGR, AnnVol and MaxDrawdown to 4 dp and Sharpe to 2 dp.
_STEP = {"CAGR": 1e-4, "AnnVol": 1e-4, "MaxDrawdown": 1e-4, "Sharpe": 0.01}


def _shift(metrics: dict, steps: float) -> dict:
    """Every portfolio and sleeve return metric moved by `steps` rounding steps."""
    def move(d):
        return {k: v + steps * _STEP[k] if k in _STEP else v for k, v in d.items()}
    return {"portfolio": move(metrics["portfolio"]),
            "sleeves": {k: move(s) for k, s in metrics["sleeves"].items()}}


def test_compare_tolerates_small_noise(current):
    """The worst float noise can do to a rounded metric is flip its last digit
    at a rounding boundary (measured raw noise, Task 20: <= 2.7e-15 across
    arm64/x86_64, Python 3.11-3.14, numpy 2.3-2.5, pandas 3.0.5-3.0.6). One
    step on every metric at once must pass."""
    assert ci_regression.compare(current, _shift(current, 1)) == []
    assert ci_regression.compare(current, _shift(current, -1)) == []


@pytest.mark.parametrize("metric", sorted(_STEP))
def test_compare_flags_two_rounding_steps(current, metric):
    """Two steps is no longer noise. Task 9's next-close fills moved synthetic
    portfolio CAGR by 0.0011 and Sharpe by 0.02 — inside the old 0.02 / 0.15."""
    moved = {**current, "portfolio": {**current["portfolio"],
                                      metric: current["portfolio"][metric]
                                      + 2 * _STEP[metric]}}
    drift = ci_regression.compare(current, moved)
    assert [d.split(":")[0] for d in drift] == [f"portfolio.{metric}"], drift


def test_gate_sees_a_return_convention_change_not_only_its_cost_line(
        current, monkeypatch):
    """A 0.4pp/yr change to a convention that is not a cost — idle cash earning
    0.4pp less — moves every synthetic CAGR by ~0.003. Under the old tolerances
    the gate flagged only the cost lines (cost is a fraction of a NAV the
    interest changes), so it could see a dropped cost but not the convention."""
    from trading_algo import config
    monkeypatch.setattr(config, "CASH_RATE_ANNUAL", config.CASH_RATE_ANNUAL - 0.004)
    drift = ci_regression.compare(current, ci_regression.synthetic_metrics())
    flagged = {d.split(":")[0].split(".")[-1] for d in drift}
    assert flagged & {"CAGR", "Sharpe", "MaxDrawdown"}, drift


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
    CAGR alone moved less than TOL when every transaction cost was deleted
    (the 0.02 CAGR tolerance before Task 20)."""
    baseline = ci_regression.load_baseline()
    for key, sleeve in baseline["sleeves"].items():
        assert "total_cost_fraction" in sleeve, f"{key} has no cost line"
        assert sleeve["total_cost_fraction"] > 0, f"{key} back-tested at zero cost"


def test_gate_fails_when_transaction_costs_are_deleted(monkeypatch):
    """Invariant #2 (costs always on), enforced by the gate rather than by
    convention. Deleting every transaction cost passed on CAGR/Vol/Sharpe/DD
    alone under the tolerances before Task 20 — measured: the sleeve CAGRs moved
    0.0022 to 0.0114 against TOL 0.02. The cost line must name it either way."""
    from trading_algo import backtest

    monkeypatch.setattr(backtest.fees, "turnover_cost", lambda *a, **k: 0.0)
    drift = ci_regression.compare(ci_regression.load_baseline(),
                                  ci_regression.synthetic_metrics())
    assert any("total_cost_fraction" in d for d in drift), (
        "the gate did not notice that every transaction cost was removed: "
        + ("\n".join(drift) or "(no drift at all)"))
