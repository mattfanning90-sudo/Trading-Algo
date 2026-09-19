"""Ultra-aggressive and experimental (long/short market-neutral) paper books.

Covers the strategy layer (dollar-neutral long/short weights, leverage caps),
the profiles registry, the short-aware paper engine, and the dashboard overview
group split (experimental books ring-fenced into their own separate total).
"""
import numpy as np
import pandas as pd
import pytest

from trading_algo import config as cfg
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


# --- post-rounding neutrality gate -----------------------------------------
# `select_long_short` hedges in WEIGHT space, but paper trading then rounds each
# name to whole shares with int(), which truncates toward zero. On a small sleeve
# an expensive name rounds to ZERO shares — and if that happens to one leg only,
# a "market-neutral" book silently becomes a directional bet while still carrying
# a drawdown breaker sized for a hedged one. The live `experimental` book showed
# exactly this shape: 1 long against 6 shorts on a top_n=6 / short_n=6 config.
def _ls_sleeve(cash=10_000.0):
    return {"currency": "USD", "cash": cash, "positions": {},
            "cost_basis": {}, "realized_pnl": 0.0,
            "last_rebalance_month": None, "last_rebalance_date": None}


def test_rounding_that_kills_one_leg_holds_cash(us_region, capsys):
    """Expensive long + cheap short => the long leg rounds to 0 shares. The book
    is then 100% net short, so it must flatten rather than trade."""
    sleeve = _ls_sleeve()
    px = pd.Series({"RICH": 10_000.0, "CHEAP_A": 10.0, "CHEAP_B": 10.0})
    targets = pd.Series({"RICH": 0.5, "CHEAP_A": -0.25, "CHEAP_B": -0.25})
    trades = []
    pt.rebalance_sleeve(us_region, sleeve, targets, px, "2026-01-05", trades)

    assert trades == [], "an unhedgeable neutral book must not trade"
    assert sleeve["positions"] == {}
    # The REASON changed when concentration landed — the book is now refused
    # because neither the full book nor any concentrated subset can be hedged at
    # this size, not merely because the full book rounded badly. The behaviour
    # (hold cash) and the requirement to SAY SO are unchanged.
    assert "cannot be hedged at this size" in capsys.readouterr().out


def test_both_legs_surviving_rounding_does_trade(us_region):
    """Control: when both legs round to real share counts the book trades, so the
    test above cannot pass merely because nothing was tradable."""
    sleeve = _ls_sleeve()
    px = pd.Series({"LONG_A": 20.0, "LONG_B": 20.0, "SHORT_A": 20.0, "SHORT_B": 20.0})
    targets = pd.Series({"LONG_A": 0.25, "LONG_B": 0.25,
                         "SHORT_A": -0.25, "SHORT_B": -0.25})
    trades = []
    pt.rebalance_sleeve(us_region, sleeve, targets, px, "2026-01-05", trades)

    assert trades, "a hedgeable neutral book must trade"
    longs = [t for t in trades if t["side"] == "BUY"]
    shorts = [t for t in trades if t["side"] == "SELL"]
    assert longs and shorts, "both legs must be executed"


def test_gate_does_not_touch_long_only_books(us_region):
    """A long-only book is *supposed* to be 100% net — the gate must ignore it."""
    sleeve = _ls_sleeve(cash=60_000.0)      # above MICRO_THRESHOLD
    px = pd.Series({"A": 20.0, "B": 20.0})
    targets = pd.Series({"A": 0.5, "B": 0.5})
    trades = []
    pt.rebalance_sleeve(us_region, sleeve, targets, px, "2026-01-05", trades)
    assert trades, "long-only books must be unaffected by the neutrality gate"


# --- the REAL incident, pinned --------------------------------------------
# On 2026-07-02 the live `experimental` book opened SIX shorts against ONE long:
#     SELL ACN 3@137.28  BSX 12@45.12  INTU 1@275.21
#          NFLX 9@77.61  NOW 3@106.27  ZTS 6@74.76
#     BUY  SMH 1@592.59
# That is $593 long against $2,694 short — 64% net short on a book labelled
# "market-neutral" — and it was held for a MONTH before closing on 08-03. The
# post-rounding gate did not exist yet; it landed 2026-07-25 (commit 54b5822,
# July audit C3). This pins the real numbers so the fix can never silently
# regress, and so the incident stays legible to whoever reads this next.
INCIDENT_LONGS = [(1, 592.59)]                                    # SMH
INCIDENT_SHORTS = [(3, 137.28), (12, 45.12), (1, 275.21),         # ACN BSX INTU
                   (9, 77.61), (3, 106.27), (6, 74.76)]           # NFLX NOW ZTS


def test_the_2026_07_02_book_would_now_be_refused():
    long_notional = sum(q * p for q, p in INCIDENT_LONGS)
    short_notional = sum(q * p for q, p in INCIDENT_SHORTS)
    gross = long_notional + short_notional
    net = abs(long_notional - short_notional) / gross

    assert long_notional == pytest.approx(592.59)
    assert short_notional == pytest.approx(2_694.35, abs=0.01)
    assert net == pytest.approx(0.639, abs=0.001)
    assert cfg.LS_MAX_NET_EXPOSURE is not None
    assert net > cfg.LS_MAX_NET_EXPOSURE, (
        "the 2026-07-02 experimental book must be refused by the neutrality gate")


def test_an_unhedgeable_neutral_book_raises_an_alert(us_region, monkeypatch):
    """Refusing to trade is only half the job — an unattended book that keeps
    declining to deploy has to say so, or it looks like a quiet strategy rather
    than a size problem it cannot solve on its own."""
    from trading_algo import notifications
    got = []
    notifications.register_channel("_cap_ls", got.append)
    monkeypatch.setattr(cfg, "NOTIFY_CHANNEL", "_cap_ls")

    sleeve = _ls_sleeve()
    px = pd.Series({"RICH": 10_000.0, "CHEAP_A": 10.0, "CHEAP_B": 10.0})
    targets = pd.Series({"RICH": 0.5, "CHEAP_A": -0.25, "CHEAP_B": -0.25})
    pt.rebalance_sleeve(us_region, sleeve, targets, px, "2026-01-05", [])

    events = [p["event"] for p in got]
    assert "ls_not_neutral" in events, events
    payload = next(p for p in got if p["event"] == "ls_not_neutral")
    assert payload["level"] == "alert"
    assert payload["net_exposure"] > cfg.LS_MAX_NET_EXPOSURE


# --- concentrating a long/short book so it can actually trade ---------------
# A book that refuses to trade forever is worse than a smaller book that trades.
# `experimental` asks for 6 long + 6 short, but on a US$6.7k sleeve three of the
# longs (MU $1,016, AMD $560, AMAT $445) cannot buy even ONE share at their
# ~$400 target weight — so the neutrality gate fires and it holds 100% cash,
# permanently. Micro mode already concentrates a LONG-ONLY book; the fix here is
# to concentrate BOTH LEGS TOGETHER so the hedge survives the shrink.
#
# Names are dropped by CONVICTION (smallest |weight| first), never by price:
# dropping the expensive names would be a price-based selection the strategy
# never asked for, and would systematically bias the book toward cheap stocks.
def test_a_book_that_cannot_hold_every_name_still_trades_a_hedged_subset():
    px = pd.Series({"RICH_A": 1_000.0, "RICH_B": 600.0, "MID": 150.0,
                    "SH_A": 70.0, "SH_B": 45.0, "SH_C": 40.0})
    targets = pd.Series({"RICH_A": 0.10, "RICH_B": 0.13, "MID": 0.25,
                         "SH_A": -0.16, "SH_B": -0.16, "SH_C": -0.16})
    out = pt.fit_long_short_to_lots(targets, px, equity=6_700.0,
                                    min_value=330.0, max_net=0.20)
    assert not out.empty, "must trade something rather than nothing"
    longs = {t: w for t, w in out.items() if w > 0}
    shorts = {t: w for t, w in out.items() if w < 0}
    assert longs and shorts, "both legs must survive — this is a hedged book"
    ln = sum(int(w * 6_700.0 / px[t]) * px[t] for t, w in longs.items())
    sn = sum(int(-w * 6_700.0 / px[t]) * px[t] for t, w in shorts.items())
    assert abs(ln - sn) / (ln + sn) <= 0.20 + 1e-9, "the shrunk book must still be hedged"


def test_concentration_keeps_the_highest_conviction_names():
    """MID carries the largest long weight, so it must survive when the leg is
    cut — dropping it because RICH_A is expensive would be a price bias."""
    px = pd.Series({"RICH_A": 1_000.0, "MID": 150.0, "SH_A": 50.0, "SH_B": 45.0})
    targets = pd.Series({"RICH_A": 0.10, "MID": 0.30, "SH_A": -0.20, "SH_B": -0.20})
    out = pt.fit_long_short_to_lots(targets, px, equity=6_700.0,
                                    min_value=330.0, max_net=0.20)
    assert "MID" in out.index and out["MID"] > 0


def test_a_leg_that_cannot_be_formed_at_all_still_refuses():
    """Concentration is not a licence to run unhedged: if NO long is affordable
    the book must still hold cash."""
    px = pd.Series({"RICH": 500_000.0, "SH_A": 50.0, "SH_B": 45.0})
    targets = pd.Series({"RICH": 0.40, "SH_A": -0.20, "SH_B": -0.20})
    out = pt.fit_long_short_to_lots(targets, px, equity=6_700.0,
                                    min_value=330.0, max_net=0.20)
    assert out.empty


def test_a_book_that_already_fits_is_left_alone():
    """No-op when every name is affordable — concentration must not churn a
    book that was fine."""
    px = pd.Series({"A": 10.0, "B": 10.0, "C": 10.0, "D": 10.0})
    targets = pd.Series({"A": 0.25, "B": 0.25, "C": -0.25, "D": -0.25})
    out = pt.fit_long_short_to_lots(targets, px, equity=10_000.0,
                                    min_value=330.0, max_net=0.20)
    pd.testing.assert_series_equal(out.sort_index(), targets.sort_index())
