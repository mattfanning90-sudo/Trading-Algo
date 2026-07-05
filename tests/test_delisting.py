"""Backlog F13: delisting-return correction (Shumway)."""
import numpy as np
import pandas as pd
import pytest

from trading_algo import config as cfg
from trading_algo import delisting
from trading_algo.regions import get_region


@pytest.fixture
def us():
    return get_region("US")


def _frame():
    idx = pd.bdate_range("2020-01-01", periods=50)
    df = pd.DataFrame({"A": 100.0, "B": 100.0}, index=idx)
    df.iloc[30:, df.columns.get_loc("B")] = np.nan   # B delists at row 30
    return df


def test_replacement_return_reads_config(monkeypatch, us):
    monkeypatch.setattr(cfg, "DELISTING_REPLACEMENT_RETURN", None)
    assert delisting.replacement_return(us) is None
    monkeypatch.setattr(cfg, "DELISTING_REPLACEMENT_RETURN", -0.30)
    assert delisting.replacement_return(us) == -0.30


def test_region_override_wins(monkeypatch, us):
    monkeypatch.setattr(cfg, "DELISTING_REPLACEMENT_RETURN", -0.30)
    monkeypatch.setitem(delisting.REGION_REPLACEMENT, "US", -0.55)
    assert delisting.replacement_return(us) == -0.55


def test_injects_replacement_at_delisting_boundary(us):
    out = delisting.apply_delisting_returns(_frame(), us, replacement=-0.30)
    # one synthetic close at row 30 = last price * (1 - 0.30)
    assert out["B"].iloc[30] == pytest.approx(70.0)
    assert np.isnan(out["B"].iloc[31])            # nothing after the delisting point
    # the realised return on the delisting day is the replacement return
    assert out["B"].pct_change().iloc[30] == pytest.approx(-0.30)


def test_noop_when_disabled(us):
    out = delisting.apply_delisting_returns(_frame(), us, replacement=None)
    assert np.isnan(out["B"].iloc[30])


def test_noop_for_names_alive_at_sample_end(us):
    idx = pd.bdate_range("2020-01-01", periods=50)
    df = pd.DataFrame({"A": 100.0}, index=idx)         # never delists
    out = delisting.apply_delisting_returns(df, us, replacement=-0.30)
    pd.testing.assert_frame_equal(out, df)


def test_backtest_applies_delisting_when_enabled(us, monkeypatch):
    monkeypatch.setattr(cfg, "DELISTING_REPLACEMENT_RETURN", -0.30)
    from trading_algo import data
    from trading_algo.backtest import run_backtest
    prices, index_px = data.synthetic_region(us)
    # truncate one name so it "delists" midway through the sample
    victim = prices.columns[0]
    prices.iloc[len(prices) // 2:, prices.columns.get_loc(victim)] = np.nan
    res = run_backtest(prices, index_px, us, max_drawdown_stop=None,
                       apply_delisting=True)
    assert "returns" in res and len(res["returns"]) > 0
