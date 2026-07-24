"""Pre-signal FX data-quality gate: stale / dead price detection.

Mirrors the equity-side data_quality tests. The gate trims the *candidate*
universe fed to compute_targets — it never re-weights (invariant #3) — and must
stay conservative enough that a normal quiet FX weekend (a few forward-filled
identical closes) never trips it.
"""
import numpy as np
import pandas as pd

from trading_algo.forex import fx_data_quality as dq


def _panel_closes(n=200, symbols=("EURUSD", "GBPUSD", "USDJPY")):
    idx = pd.bdate_range("2020-01-01", periods=n)
    rng = np.random.default_rng(0)
    data = {}
    for i, s in enumerate(symbols):
        steps = rng.normal(0.0, 0.003, n)
        data[s] = 1.1 * np.exp(np.cumsum(steps)) + i * 0.01
    return pd.DataFrame(data, index=idx)


def test_frozen_close_flagged_stale():
    px = _panel_closes()
    # freeze one column's tail well past the staleness threshold
    px.iloc[-(dq.STALE_BARS + 5):, px.columns.get_loc("USDJPY")] = px["USDJPY"].iloc[-(dq.STALE_BARS + 6)]
    report = dq.assess(px)
    assert "USDJPY" in report.excluded
    assert "EURUSD" not in report.excluded
    assert "GBPUSD" not in report.excluded


def test_dead_price_flagged():
    px = _panel_closes()
    px.iloc[-1, px.columns.get_loc("GBPUSD")] = 0.0        # non-positive latest close
    report = dq.assess(px)
    assert "GBPUSD" in report.excluded
    px2 = _panel_closes()
    px2.iloc[-1, px2.columns.get_loc("GBPUSD")] = np.nan   # NaN latest close
    assert "GBPUSD" in dq.assess(px2).excluded


def test_quiet_weekend_not_flagged():
    """A short run of identical forward-filled closes (weekend / holiday gap on
    a mixed FX+crypto calendar) must NOT be flagged — the gate is conservative."""
    px = _panel_closes()
    # simulate a long-weekend ffill: 3 identical closes on one pair
    px.iloc[-3:, px.columns.get_loc("EURUSD")] = px["EURUSD"].iloc[-4]
    report = dq.assess(px)
    assert "EURUSD" not in report.excluded
    assert not report.excluded


def test_eligible_is_noop_when_clean():
    px = _panel_closes()
    base, report = dq.eligible(px)
    assert base is None            # nothing flagged -> base returned unchanged
    assert not report.excluded


def test_eligible_removes_flagged_from_universe():
    px = _panel_closes()
    px.iloc[-(dq.STALE_BARS + 5):, px.columns.get_loc("USDJPY")] = px["USDJPY"].iloc[-(dq.STALE_BARS + 6)]
    elig, report = dq.eligible(px)
    assert elig == {"EURUSD", "GBPUSD"}
    assert "USDJPY" in report.excluded


def test_eligible_intersects_base():
    px = _panel_closes()
    px.iloc[-(dq.STALE_BARS + 5):, px.columns.get_loc("USDJPY")] = px["USDJPY"].iloc[-(dq.STALE_BARS + 6)]
    elig, _ = dq.eligible(px, base={"EURUSD", "USDJPY"})
    assert elig == {"EURUSD"}
