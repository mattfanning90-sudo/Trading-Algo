"""FX invariant: backtest and paper book share ONE weight function.

The analog of the equity system's invariant #3 — there is no second copy of the
sizing logic, and the low-latency `fast=True` trim must produce the *identical*
latest weight as the full-history computation.
"""
import numpy as np
import pytest

from trading_algo.forex import fx_book, fx_strategy
from trading_algo.forex.agents import AgentPool
from trading_algo.forex.fx_config import profile
from trading_algo.forex.fx_data import synthetic_panel
from trading_algo.forex.pairs import DEFAULT_UNIVERSE


@pytest.fixture
def panel():
    return synthetic_panel(DEFAULT_UNIVERSE, start="2017-01-01", end="2023-01-01")


@pytest.fixture
def params():
    return profile("balanced")


def test_compute_targets_matches_history_last_row(panel, params):
    pool = AgentPool(max_workers=1)
    hist = fx_strategy.target_weights_history(panel, params, pool=pool)
    latest = fx_strategy.compute_targets(panel, params, pool=pool, fast=False)
    np.testing.assert_allclose(latest.reindex(hist.columns).values,
                               hist.iloc[-1].values, rtol=1e-9, atol=1e-12)


def test_fast_trim_is_exact(panel, params):
    """The low-latency windowed path == full recompute for the latest bar."""
    pool = AgentPool(max_workers=1)
    full = fx_strategy.compute_targets(panel, params, pool=pool, fast=False)
    fast = fx_strategy.compute_targets(panel, params, pool=pool, fast=True)
    # "Exact" up to the exponentially-small EWM truncation tail — far below the
    # book's 1e-5 weight rounding and 2e-2 no-churn band, so trade-irrelevant.
    np.testing.assert_allclose(fast.reindex(full.index).values, full.values,
                               rtol=1e-4, atol=1e-5)


def test_book_first_rebalance_uses_compute_targets(tmp_path, monkeypatch, panel, params):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    pool = AgentPool(max_workers=1)
    fx_book.init_account("matt", 5_000, "balanced")
    fx_book.run_once("matt", synthetic=True, pool=pool)

    # Recreate what the book saw: synthetic panel + the shared target function.
    full_panel = synthetic_panel(DEFAULT_UNIVERSE)
    target = fx_strategy.compute_targets(full_panel, params, pool=pool)
    positions = fx_book.load_state("matt")["positions"]

    # Every position the book opened must match the shared target (post no-churn band).
    for pair, w in positions.items():
        assert abs(w - float(target.get(pair, 0.0))) < 1e-3
