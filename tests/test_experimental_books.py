"""Ultra-aggressive and experimental (long/short market-neutral) paper books.

Covers the strategy layer (dollar-neutral long/short weights, leverage caps),
the profiles registry, the short-aware paper engine, and the dashboard overview
group split (experimental books ring-fenced into their own separate total).
"""
import numpy as np
import pytest

from trading_algo import data, paper_trade as pt, pnl, profiles, strategy
from trading_algo.regions import get_region


@pytest.fixture
def synth_us():
    region = get_region("US")
    return data.synthetic_region(region, start="2014-01-01", end="2024-01-01")


@pytest.fixture
def us_region():
    return get_region("US")


@pytest.fixture
def ls_params(us_region):
    return us_region.params.with_overrides(**profiles.PROFILES["experimental"].param_overrides)


# --- long/short weight function -------------------------------------------
def test_long_short_is_dollar_neutral(synth_us, ls_params):
    prices, index_px = synth_us
    w = strategy.compute_targets(prices, index_px, ls_params)
    assert not w.empty, "expected a hedged book on a full universe"
    assert (w > 0).any() and (w < 0).any(), "must have both a long and a short leg"
    # net exposure hedged to ~0 (dollar-neutral)
    assert abs(float(w.sum())) < 1e-6


def test_long_short_gross_within_leverage_cap(synth_us, ls_params):
    prices, index_px = synth_us
    w = strategy.compute_targets(prices, index_px, ls_params)
    assert float(w.abs().sum()) <= ls_params.max_gross + 1e-9


def test_long_short_deterministic_and_no_lookahead(synth_us, ls_params):
    prices, index_px = synth_us
    asof = prices.index[-40]
    full = strategy.compute_targets(prices, index_px, ls_params, asof=asof)
    a = strategy.compute_targets(prices, index_px, ls_params, asof=asof)
    truncated = strategy.compute_targets(
        prices.loc[:asof], index_px.loc[:asof], ls_params, asof=asof)
    # deterministic
    assert full.sort_index().equals(a.sort_index())
    # future data past asof cannot change the weights
    assert np.allclose(full.sort_index().values,
                       truncated.reindex(full.sort_index().index).values)


def test_ultra_geared_higher_than_long_only(synth_us, us_region):
    """The ultra profile can gear well above the long-only book's ≤1.0 gross."""
    prices, index_px = synth_us
    ultra = us_region.params.with_overrides(**profiles.PROFILES["ultra"].param_overrides)
    w = strategy.compute_targets(prices, index_px, ultra)
    if not w.empty:
        assert (w >= 0).all(), "ultra is long-only"
        assert float(w.sum()) <= ultra.max_gross + 1e-9
    # a long-only book with leverage headroom should be allowed to exceed 1.0
    assert ultra.max_gross == 3.0 and ultra.max_vol_scale > 1.5


# --- profiles registry -----------------------------------------------------
def test_profiles_are_ringfenced_and_shaped():
    for key in ("ultra", "experimental"):
        prof = profiles.get_profile(key)
        assert prof.group == profiles.EXPERIMENTAL
    assert profiles.PROFILES["ultra"].max_drawdown_stop is None       # breaker off
    assert profiles.PROFILES["experimental"].param_overrides["long_short"] is True


# --- short-aware FIFO P&L --------------------------------------------------
def test_short_round_trip_realizes_correctly():
    key = ("US", "SHRT")
    lots: dict = {}
    # short 10 @ 100 (sell to open) — no realised yet
    assert pnl.apply_fill(lots, key, -10, 100.0, 0.0, "2026-06-01") is None
    # cover 10 @ 90 (buy to close) — profit of (100-90)*10 = 100
    r = pnl.apply_fill(lots, key, +10, 90.0, 0.0, "2026-07-01")
    assert r is not None
    assert r["gross"] == pytest.approx(100.0)
    assert r["net"] == pytest.approx(100.0)
    assert key not in lots            # fully covered → no open lot


def test_short_via_build_lots_matches():
    trades = [
        {"date": "2026-06-01", "region": "US", "ticker": "SHRT", "side": "SELL",
         "shares": 10, "fill": 100.0, "commission": 1.0, "stamp_duty": 0.0, "currency": "USD"},
        {"date": "2026-07-01", "region": "US", "ticker": "SHRT", "side": "BUY",
         "shares": 10, "fill": 80.0, "commission": 1.0, "stamp_duty": 0.0, "currency": "USD"},
    ]
    open_lots, realized = pnl.build_lots(trades)
    assert not open_lots
    assert len(realized) == 1
    assert realized[0]["gross"] == pytest.approx(200.0)       # (100-80)*10
    assert realized[0]["net"] == pytest.approx(200.0 - 2.0)   # minus both commissions


# --- paper engine (profiled books) ----------------------------------------
@pytest.fixture
def acct(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    return tmp_path


def test_ultra_book_disables_breaker_and_gears(acct):
    pt.init_account("ultra", capital=10_000, synthetic=True, profile="ultra")
    state = pt.load_state("ultra")
    assert state["group"] == "EXPERIMENTAL"
    assert state["max_drawdown_stop"] is None
    assert pt._account_drawdown_stop(state) is None
    assert state["param_overrides"]["max_gross"] == 3.0
    pt.run_daily("ultra", synthetic=True)                     # must run cleanly
    state = pt.load_state("ultra")
    assert state["equity_history"][-1][1] > 0
    assert all(sh > 0 for s in state["sleeves"].values()
               for sh in s["positions"].values())             # long-only


def test_experimental_book_opens_shorts(acct):
    pt.init_account("experimental", capital=10_000, synthetic=True, profile="experimental")
    state = pt.load_state("experimental")
    assert state["group"] == "EXPERIMENTAL"
    pt.run_daily("experimental", synthetic=True)
    state = pt.load_state("experimental")
    shares = [sh for s in state["sleeves"].values() for sh in s["positions"].values()]
    assert any(sh < 0 for sh in shares), "market-neutral book must hold shorts"
    assert any(sh > 0 for sh in shares), "...and longs"
    # book still marks to a positive equity (shorts are a liability, not cash gone)
    assert state["equity_history"][-1][1] > 0


# --- dashboard overview group split ---------------------------------------
def test_overview_ringfences_experimental(acct, monkeypatch):
    from trading_algo.dashboard import overview, registry
    monkeypatch.setattr(registry.fx_book, "STATE_DIR", str(acct))
    # one CORE book + two EXPERIMENTAL books
    pt.init_account("full", capital=100_000, synthetic=True, allocations={"US": 1.0})
    pt.run_daily("full", synthetic=True)
    pt.init_account("ultra", capital=10_000, synthetic=True, profile="ultra")
    pt.run_daily("ultra", synthetic=True)
    pt.init_account("experimental", capital=10_000, synthetic=True, profile="experimental")
    pt.run_daily("experimental", synthetic=True)

    ov = overview.build_overview()
    groups = {g["name"]: g for g in ov["groups"]}
    assert "EXPERIMENTAL" in groups and "CORE" in groups
    assert groups["EXPERIMENTAL"]["books"] == 2
    # headline AUM = CORE only; it must NOT include the two 10k experimental books
    assert ov["totals"]["books"] == 1
    exp_aum = groups["EXPERIMENTAL"]["aum"]
    assert exp_aum > 0
    assert ov["totals"]["aum"] == pytest.approx(groups["CORE"]["aum"])
    # the experimental capital is genuinely excluded from the headline
    all_aum = sum(c["equity"] for c in ov["accounts"])
    assert ov["totals"]["aum"] < all_aum
    assert ov["totals"]["aum"] + exp_aum == pytest.approx(all_aum)
