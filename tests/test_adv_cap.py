"""Backlog F15 / P0-I / R3: pre-trade ADV cap + capacity hook + volume ingestion."""
import numpy as np
import pandas as pd

from trading_algo import config as cfg
from trading_algo import data, strategy
from trading_algo.backtest import run_backtest
from trading_algo.regions import get_region


def _invested_frame(n=450, cols=8, seed=0):
    """Rising prices + a rising index so the regime is risk-on and compute_targets
    actually holds a book (not cash)."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=n)
    market = np.cumsum(rng.normal(0.0005, 0.006, n))
    data_ = {f"S{i}": 100 * np.exp(np.cumsum(rng.normal(0.0007, 0.011, n)))
             for i in range(cols)}
    prices = pd.DataFrame(data_, index=idx)
    index_px = pd.Series(5000 * np.exp(market), index=idx)
    return prices, index_px


# --- R3: volume ingestion ---------------------------------------------------
def test_synthetic_volume_deterministic_and_shaped():
    idx = pd.bdate_range("2020-01-01", periods=50)
    a = data.synthetic_volume(["X", "Y"], idx, seed=7)
    b = data.synthetic_volume(["X", "Y"], idx, seed=7)
    assert a.shape == (50, 2) and a.equals(b) and (a > 0).all().all()


def test_adv_dollar_is_causal():
    idx = pd.bdate_range("2020-01-01", periods=40)
    prices = pd.DataFrame({"X": np.linspace(10, 20, 40)}, index=idx)
    vol = pd.DataFrame({"X": np.full(40, 1000.0)}, index=idx)
    advd = data.adv_dollar(prices, vol, window=5)
    p2 = prices.copy(); p2.iloc[-1] *= 10                  # spike AFTER the point
    assert advd["X"].iloc[20] == data.adv_dollar(p2, vol, window=5)["X"].iloc[20]


# --- P0-I: capacity hook in compute_targets ---------------------------------
def test_capacity_is_noop_when_none():
    prices, index_px = _invested_frame()
    p = get_region("US").params
    base = strategy.compute_targets(prices, index_px, p)
    pd.testing.assert_series_equal(base, strategy.compute_targets(prices, index_px, p, capacity=None))


def test_capacity_caps_weights():
    prices, index_px = _invested_frame()
    p = get_region("US").params
    base = strategy.compute_targets(prices, index_px, p)
    assert not base.empty
    cap = base.abs() * 0.5                                  # halve every name's cap
    capped = strategy.compute_targets(prices, index_px, p, capacity=cap)
    assert (capped.abs() <= base.abs() + 1e-12).all()      # never exceeds prior magnitude
    assert (capped.abs() < base.abs() - 1e-12).any()       # at least one actually shrank


# --- F15: backtest ADV cap --------------------------------------------------
def test_backtest_adv_cap_is_noop_when_disabled(monkeypatch):
    prices, index_px = _invested_frame()
    region = get_region("US")
    vol = data.synthetic_volume(list(prices.columns), prices.index)
    monkeypatch.setattr(cfg, "ADV_CAP_PCT", None)          # off
    a = run_backtest(prices, index_px, region, max_drawdown_stop=None, volume=vol)
    b = run_backtest(prices, index_px, region, max_drawdown_stop=None)
    assert a["metrics"]["CAGR"] == b["metrics"]["CAGR"]    # identical -> baseline safe


def test_backtest_adv_cap_binds_when_enabled(monkeypatch):
    prices, index_px = _invested_frame()
    region = get_region("US")
    vol = pd.DataFrame(1000.0, index=prices.index, columns=prices.columns)
    monkeypatch.setattr(cfg, "ADV_CAP_PCT", 1e-6)          # tiny cap that binds
    capped = run_backtest(prices, index_px, region, max_drawdown_stop=None, volume=vol)
    uncapped = run_backtest(prices, index_px, region, max_drawdown_stop=None)
    assert capped["metrics"] != uncapped["metrics"]


# --- F15/F6: the plumbing that makes the cap REACHABLE ----------------------
# Both features were double-gated: the config values are None AND no caller ever
# passed `volume=`, so `data.load_volume` had zero callers and setting the
# config alone changed nothing. These pin the wiring, not the maths.
def test_capacity_volume_is_none_when_both_features_are_off(monkeypatch):
    monkeypatch.setattr(cfg, "ADV_CAP_PCT", None)
    monkeypatch.setattr(cfg, "IMPACT_COEF", None)
    prices, _ = _invested_frame()
    assert data.capacity_volume(prices, "2019-01-01", None, synthetic=True) is None


def test_capacity_volume_is_fetched_when_the_adv_cap_is_on(monkeypatch):
    monkeypatch.setattr(cfg, "ADV_CAP_PCT", 0.05)
    monkeypatch.setattr(cfg, "IMPACT_COEF", None)
    prices, _ = _invested_frame()
    vol = data.capacity_volume(prices, "2019-01-01", None, synthetic=True)
    assert vol is not None and list(vol.columns) == list(prices.columns)


def test_capacity_volume_is_fetched_when_impact_cost_is_on(monkeypatch):
    monkeypatch.setattr(cfg, "ADV_CAP_PCT", None)
    monkeypatch.setattr(cfg, "IMPACT_COEF", 0.1)
    prices, _ = _invested_frame()
    assert data.capacity_volume(prices, "2019-01-01", None, synthetic=True) is not None


def _spy_on_run_backtest(monkeypatch, module, seen):
    real = module.run_backtest

    def spy(prices, index_px, region, **kw):
        seen["volume"] = kw.get("volume")
        return real(prices, index_px, region, **kw)

    monkeypatch.setattr(module, "run_backtest", spy)


def test_run_single_passes_volume_when_a_capacity_feature_is_on(monkeypatch):
    from trading_algo import run_backtest as rb
    seen = {}
    monkeypatch.setattr(cfg, "ADV_CAP_PCT", 0.05)
    _spy_on_run_backtest(monkeypatch, rb, seen)
    rb.run_single("US", synthetic=True, point_in_time=False)
    assert seen["volume"] is not None


def test_run_single_passes_no_volume_when_both_are_off(monkeypatch):
    """The default path must not pay for a second full download."""
    from trading_algo import run_backtest as rb
    seen = {}
    monkeypatch.setattr(cfg, "ADV_CAP_PCT", None)
    monkeypatch.setattr(cfg, "IMPACT_COEF", None)
    _spy_on_run_backtest(monkeypatch, rb, seen)
    rb.run_single("US", synthetic=True, point_in_time=False)
    assert seen["volume"] is None


def test_portfolio_backtest_passes_volume_when_enabled(monkeypatch):
    from trading_algo import portfolio_backtest as pb
    seen = {}
    monkeypatch.setattr(cfg, "ADV_CAP_PCT", 0.05)
    _spy_on_run_backtest(monkeypatch, pb, seen)
    pb.run_portfolio_backtest(synthetic=True)
    assert seen["volume"] is not None


def test_paper_warns_loudly_if_the_cap_is_on_but_unhonoured(monkeypatch, capsys):
    """The cap is applied in the BACKTEST path only. If it is switched on while
    paper trading cannot honour it, backtest and paper would size differently
    from the same signal — invariant #3's spirit, broken silently. Refuse to be
    silent about it."""
    from trading_algo import paper_trade
    monkeypatch.setattr(cfg, "ADV_CAP_PCT", 0.05)
    paper_trade.warn_if_capacity_unhonoured()
    out = capsys.readouterr().out
    assert "ADV_CAP_PCT" in out and "backtest" in out.lower()


def test_paper_is_silent_when_no_capacity_feature_is_on(monkeypatch, capsys):
    from trading_algo import paper_trade
    monkeypatch.setattr(cfg, "ADV_CAP_PCT", None)
    monkeypatch.setattr(cfg, "IMPACT_COEF", None)
    paper_trade.warn_if_capacity_unhonoured()
    assert capsys.readouterr().out == ""
