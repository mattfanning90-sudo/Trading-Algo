"""The holding-policy sweep.

Synthetic throughout — these are pipeline tests, never performance claims
(invariant #5). What they pin is the sweep's HONESTY machinery: that the test
slice is genuinely untouched by the search, that a jagged peak loses to a
plateau, and that a synthetic run says so in its own report.
"""
import pandas as pd
import pytest

from trading_algo.forex import policy_sweep as ps
from trading_algo.forex.fx_data import synthetic_panel
from trading_algo.forex.pairs import DEFAULT_UNIVERSE


def test_grid_never_inverts_the_hysteresis():
    """exit > entry is refused by FXParams, so it must not be generated."""
    assert all(g["exit_threshold"] <= g["entry_threshold"] for g in ps.grid())
    assert len(ps.grid(quick=True)) < len(ps.grid())


def test_split_is_chronological_and_disjoint():
    panel = synthetic_panel(DEFAULT_UNIVERSE[:3], start="2018-01-01", end="2024-01-01")
    train, test = ps.split_panel(panel, 0.7)
    for s in train:
        assert train[s].index.max() <= test[s].index.min()
        assert len(train[s]) > len(test[s])          # 70/30


def test_split_refuses_a_too_short_history():
    tiny = synthetic_panel(["EURUSD"], start="2023-01-01", end="2023-02-01")
    with pytest.raises(SystemExit):
        ps.split_panel(tiny)


# --- median_hold: the number the whole exercise is judged on ----------------

def _hist(rows):
    """rows: list of dicts {symbol: weight} -> weights_hist keyed by date."""
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="D")
    return {t: pd.Series(r, dtype=float) for t, r in zip(idx, rows)}


def test_median_hold_counts_a_simple_round_trip():
    med, trips = ps.median_hold(_hist([{"A": 0.2}] * 4 + [{"A": 0.0}]))
    assert med == 4 and trips == 1


def test_median_hold_treats_a_sign_flip_as_a_new_round_trip():
    """Matches verify.py's live convention — a reversal ends the trip."""
    med, trips = ps.median_hold(_hist([{"A": 0.2}] * 3 + [{"A": -0.2}] * 5))
    assert trips == 2 and med == 4          # runs of 3 and 5

def test_median_hold_is_empty_without_positions():
    med, trips = ps.median_hold(_hist([{"A": 0.0}, {"A": 0.0}]))
    assert trips == 0 and med != med        # nan


# --- ranking ----------------------------------------------------------------

def _row(e, x, h, sharpe):
    return {"entry_threshold": e, "exit_threshold": x, "min_hold_bars": h,
            "train": {"sharpe": sharpe}}


def test_flat_ranking_prefers_a_plateau_to_an_isolated_spike():
    """A peak surrounded by bad neighbours does not survive live data; the
    plateau does. This is the sweep.py philosophy, enforced."""
    rows = [_row(0.04, 0.02, 3, 0.1), _row(0.06, 0.02, 3, 9.0),   # spike
            _row(0.08, 0.02, 3, 0.1),
            _row(0.08, 0.03, 10, 2.0), _row(0.12, 0.03, 10, 2.0),
            _row(0.08, 0.05, 10, 2.0), _row(0.08, 0.03, 20, 2.0)]
    flat = ps.robust_rank([dict(r) for r in rows], flat=True)
    peak = ps.robust_rank([dict(r) for r in rows], flat=False)
    assert peak[0]["train"]["sharpe"] == 9.0            # raw ranking takes the spike
    assert flat[0]["train"]["sharpe"] == 2.0            # robust ranking does not


# --- end to end -------------------------------------------------------------

def test_quick_sweep_runs_and_scores_every_row_out_of_sample():
    res = ps.run("balanced", synthetic=True, quick=True, train_frac=0.7,
                 bar=None, flat=True)
    assert res["rows"], "sweep produced nothing"
    for r in res["rows"]:
        assert "train" in r and "test" in r, "every row must be scored out of sample"
        assert set(r["test"]) >= {"sharpe", "cost", "median_hold", "max_drawdown"}


def test_synthetic_report_refuses_to_look_like_performance():
    res = ps.run("balanced", synthetic=True, quick=True, train_frac=0.7,
                 bar=None, flat=True)
    text = ps.report(res)
    assert "SYNTHETIC" in text and "invariant #5" in text
    assert "current (knobs off)" in text, "the baseline must always be shown"


def test_weights_reuse_matches_recomputing_them():
    """The sweep hands run_backtest a precomputed weight frame. If that were not
    identical to deriving it from the panel, every number here would be wrong."""
    from trading_algo.forex import fx_backtest, fx_config as cfg, fx_strategy
    panel = synthetic_panel(DEFAULT_UNIVERSE[:4], start="2019-01-01", end="2023-01-01")
    p = cfg.profile("balanced").with_overrides(entry_threshold=0.08,
                                               exit_threshold=0.03, min_hold_bars=5)
    w = fx_strategy.target_weights_history(panel, p)
    a = fx_backtest.run_backtest(panel, p)
    b = fx_backtest.run_backtest(panel, p, weights=w)
    pd.testing.assert_series_equal(a["equity"], b["equity"])
