"""Property-based tests (Hypothesis) for money-path invariants that
`test_property_invariants.py` does not already pin as properties.

That file covers: lookahead through the BACKTEST equity curve, monotone/
non-negative costs on the bps-only `fees.turnover_cost` path and the FX spread,
whole-share HOLDINGS, currency isolation, P&L reconciliation, the FX
`size_book` caps and metrics totality. This file adds:

- No lookahead at the weight function itself: `strategy.compute_targets` at t
  is unchanged by any price after t (invariant #1, one level below the
  backtest — paper trading calls this directly).
- Sizing bounds of `compute_targets`: finite weights, gross <= max_gross,
  no shorts in a long-only book.
- Paper FILLS (the ledger rows, not just the holdings): whole shares, the
  per-region commission floor, slippage that never helps, and UK stamp duty on
  FTSE buys only (invariants #2, #4).
- Cost primitives never negative: `fees.commission`, `fees.stamp_duty`,
  `fees.square_root_impact`, and the per-order (`nav`/`orders`) path of
  `fees.turnover_cost`, which the floor can only make dearer, never cheaper.
- FX vol targeting: the scale `forex.risk.size_book` applies is in
  [0, max_vol_scale], and no cap ever flips a position's sign.

Synthetic data here is a PLUMBING probe only (invariant #5): none of these
numbers are performance.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from trading_algo import fees
from trading_algo.config import StrategyParams
from trading_algo.forex.fx_config import FXParams
from trading_algo.forex.risk import size_book
from trading_algo.paper_trade import rebalance_sleeve
from trading_algo.regions import REGIONS, get_region
from trading_algo.strategy import compute_targets

_TICKERS = ["A0", "A1", "A2", "A3", "A4", "A5"]
_FINITE = dict(allow_nan=False, allow_infinity=False)

# Short windows so a ~150-bar panel rebalances; same shape as the FAST params in
# test_property_invariants.py.
_FAST = StrategyParams(
    lookback_days=20, skip_days=2, min_history_days=25, vol_lookback=10,
    stock_trend_ma=15, index_trend_ma=15, top_n=3, max_weight=0.5,
    rebalance="W",
)


@st.composite
def _panel(draw):
    """(prices, index) — a strictly positive GBM panel from a drawn seed, drift
    and vol. Seeded numpy keeps each example cheap; Hypothesis still varies the
    shape, the regime and the cross-section."""
    n_days = draw(st.integers(min_value=60, max_value=160))
    seed = draw(st.integers(min_value=0, max_value=2**32 - 1))
    vol = draw(st.floats(min_value=0.001, max_value=0.06, **_FINITE))
    drift = draw(st.floats(min_value=-0.004, max_value=0.004, **_FINITE))
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2015-01-01", periods=n_days)
    rets = rng.normal(drift, vol, size=(n_days, len(_TICKERS) + 1))
    # Per-name drift dispersion so momentum actually ranks the names.
    rets[:, :-1] += rng.normal(0.0, 0.002, size=len(_TICKERS))
    levels = 50.0 * np.exp(np.cumsum(rets, axis=0))
    prices = pd.DataFrame(levels[:, :-1], index=dates, columns=_TICKERS)
    index = pd.Series(5000.0 * levels[:, -1] / 50.0, index=dates)
    return prices, index


@st.composite
def _params(draw):
    """FAST params with the sizing knobs and the book type drawn."""
    long_short = draw(st.booleans())
    return dataclasses.replace(
        _FAST,
        long_short=long_short,
        top_n=2 if long_short else 3,
        short_n=2 if long_short else 0,
        regime_filter=draw(st.booleans()),
        max_gross=draw(st.floats(min_value=0.25, max_value=3.0, **_FINITE)),
        max_vol_scale=draw(st.floats(min_value=0.25, max_value=3.0, **_FINITE)),
        target_vol=draw(st.floats(min_value=0.02, max_value=0.5, **_FINITE)),
    )


_SLOW_OK = [HealthCheck.too_slow, HealthCheck.data_too_large]


# ---------------------------------------------------------------------------
# No lookahead at the weight function
# ---------------------------------------------------------------------------
@settings(max_examples=60, deadline=None, suppress_health_check=_SLOW_OK)
@given(data=_panel(), p=_params(),
       cut_frac=st.floats(min_value=0.3, max_value=0.9, **_FINITE),
       seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_compute_targets_at_t_ignores_every_price_after_t(data, p, cut_frac, seed):
    """Rewriting every price strictly after `asof` (names AND the regime index)
    must leave the target weights at `asof` bit-identical, and so must deleting
    those rows outright. Either difference is a lookahead leak (invariant #1)."""
    prices, index = data
    cut = int(len(prices) * cut_frac)
    if cut >= len(prices) - 1:
        cut = len(prices) - 2
    asof = prices.index[cut]

    rng = np.random.default_rng(seed)
    p2, i2 = prices.copy(), index.copy()
    tail = len(prices) - cut - 1
    p2.iloc[cut + 1:] = p2.iloc[cut + 1:].to_numpy() * rng.uniform(
        0.3, 3.0, size=(tail, p2.shape[1]))
    i2.iloc[cut + 1:] = i2.iloc[cut + 1:].to_numpy() * rng.uniform(0.3, 3.0, size=tail)

    base = compute_targets(prices, index, p, asof=asof)
    perturbed = compute_targets(p2, i2, p, asof=asof)
    truncated = compute_targets(prices.loc[:asof], index.loc[:asof], p, asof=asof)

    pd.testing.assert_series_equal(base, perturbed, check_names=False)
    pd.testing.assert_series_equal(base, truncated, check_names=False)


# ---------------------------------------------------------------------------
# Sizing bounds of the one weight function
# ---------------------------------------------------------------------------
@settings(max_examples=60, deadline=None, suppress_health_check=_SLOW_OK)
@given(data=_panel(), p=_params())
def test_compute_targets_weights_are_finite_and_within_leverage(data, p):
    """Every target is a finite number; gross Σ|w| never exceeds `max_gross`;
    a long-only book never holds a negative weight."""
    prices, index = data
    w = compute_targets(prices, index, p)
    assert np.isfinite(w.to_numpy(dtype=float)).all(), w
    assert float(w.abs().sum()) <= p.max_gross + 1e-9
    if not p.long_short:
        assert (w >= 0).all(), w


# ---------------------------------------------------------------------------
# Paper fills: whole shares, commission floor, slippage, stamp duty
# ---------------------------------------------------------------------------
def _fresh_sleeve(cash: float, currency: str) -> dict:
    return {"currency": currency, "cash": float(cash), "positions": {},
            "cost_basis": {}, "realized_pnl": 0.0,
            "last_rebalance_month": None, "last_rebalance_date": None}


def _weights(draw) -> pd.Series:
    raw = np.asarray(draw(st.lists(st.floats(min_value=0.0, max_value=1.0, **_FINITE),
                                   min_size=len(_TICKERS), max_size=len(_TICKERS))))
    if raw.sum() > 0:
        raw = raw / raw.sum()
    return pd.Series(raw, index=_TICKERS)


@st.composite
def _two_rebalances(draw):
    """A region, starting cash, two target books and two price vectors — the
    second rebalance sells some of what the first bought."""
    key = draw(st.sampled_from(sorted(REGIONS)))
    cash = draw(st.floats(min_value=6_000.0, max_value=500_000.0, **_FINITE))
    pxs = [pd.Series(draw(st.lists(st.floats(min_value=1.0, max_value=500.0, **_FINITE),
                                   min_size=len(_TICKERS), max_size=len(_TICKERS))),
                     index=_TICKERS) for _ in range(2)]
    return key, cash, _weights(draw), _weights(draw), pxs


@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(case=_two_rebalances())
def test_paper_fills_are_whole_shares_floored_and_duty_on_ftse_buys_only(case):
    """Each ledger row a paper rebalance writes: a positive whole number of
    shares; commission at least the region's floor and at least its bps rate;
    a fill no better than the decision price (slippage only costs); stamp duty
    on an FTSE BUY at the region's rate and zero on every sell and every
    non-UK fill (invariants #2 and #4)."""
    key, cash, w1, w2, (px1, px2) = case
    region = get_region(key)
    sleeve = _fresh_sleeve(cash, region.currency)
    log: list[dict] = []
    rebalance_sleeve(region, sleeve, w1, px1, "2020-01-02", log)
    rebalance_sleeve(region, sleeve, w2, px2, "2020-02-03", log)

    cent = 0.005 + 1e-9                    # ledger rounds money to 2 dp
    for t in log:
        shares = t["shares"]
        assert isinstance(shares, int) and shares > 0, t
        # The ledger rounds the fill to 4 dp, so notional rebuilt from it is off
        # by up to shares x 0.00005; size every tolerance below with that.
        notional, slack = shares * t["fill"], shares * 5e-5
        assert t["commission"] >= region.min_commission - cent, t
        assert (t["commission"]
                >= (notional - slack) * region.commission_bps / 1e4 - cent), t
        if t["side"] == "BUY":
            assert t["fill"] >= t["decision"] - 1e-4, t
        else:
            assert t["fill"] <= t["decision"] + 1e-4, t
        if t["side"] == "BUY" and region.stamp_duty_bps > 0:
            expected = notional * region.stamp_duty_bps / 1e4
            tol = slack * region.stamp_duty_bps / 1e4 + cent
            assert t["stamp_duty"] == pytest.approx(expected, abs=tol), t
        else:
            assert t["stamp_duty"] == 0.0, t
    # Only the UK charges stamp duty at all.
    assert all(r.stamp_duty_bps == 0 for k, r in REGIONS.items() if k != "FTSE")


# ---------------------------------------------------------------------------
# Cost primitives are never negative
# ---------------------------------------------------------------------------
@settings(max_examples=300, deadline=None)
@given(key=st.sampled_from(sorted(REGIONS)),
       notional=st.floats(min_value=-1e7, max_value=1e7, **_FINITE))
def test_commission_and_stamp_duty_are_never_negative(key, notional):
    """Commission is >= 0, and >= the floor on any nonzero order whatever its
    sign; stamp duty is >= 0, zero for a non-positive (sell-side) notional, and
    zero outside FTSE."""
    region = get_region(key)
    c = fees.commission(region, notional)
    assert c >= 0.0
    if notional != 0:
        assert c >= region.min_commission
    d = fees.stamp_duty(region, notional)
    assert d >= 0.0
    if notional <= 0 or key != "FTSE":
        assert d == 0.0


@settings(max_examples=300, deadline=None)
@given(order=st.floats(min_value=-1e9, max_value=1e9, **_FINITE),
       # ADV is daily DOLLAR volume: <= 0 (unknown) or at least $1. A positive ADV
       # below ~1e-300 overflows order/ADV to inf and returns NaN, but no price x
       # volume feed can produce one, so it is outside the domain pinned here.
       adv=st.one_of(st.none(), st.just(float("nan")),
                     st.floats(min_value=-1e9, max_value=0.0, **_FINITE),
                     st.floats(min_value=1.0, max_value=1e11, **_FINITE)),
       vol=st.one_of(st.just(float("nan")), st.floats(min_value=0.0, max_value=5.0, **_FINITE)),
       coef=st.floats(min_value=0.0, max_value=5.0, **_FINITE))
def test_square_root_impact_is_never_negative(order, adv, vol, coef):
    """The market-impact rate is a finite, non-negative number for any order
    size or sign, and for an unknown/zero/negative ADV or an unknown vol."""
    r = fees.square_root_impact(order, adv, vol, coef)
    assert np.isfinite(r) and r >= 0.0


@settings(max_examples=200, deadline=None)
@given(key=st.sampled_from(sorted(REGIONS)),
       orders=st.lists(st.floats(min_value=1e-6, max_value=1.0, **_FINITE),
                       min_size=1, max_size=12),
       buy_frac=st.floats(min_value=0.0, max_value=1.0, **_FINITE),
       nav=st.floats(min_value=100.0, max_value=1e8, **_FINITE),
       impact=st.floats(min_value=0.0, max_value=0.05, **_FINITE))
def test_per_order_turnover_cost_is_never_cheaper_than_bps(key, orders, buy_frac,
                                                            nav, impact):
    """On the exact per-order path (`nav` + `orders`), the cost is >= 0 and at
    least the bps-only cost of the same turnover: a commission floor can only
    add cost, never remove it."""
    region = get_region(key)
    dw = pd.Series(orders)
    turnover = float(dw.sum())
    exact = fees.turnover_cost(region, turnover, turnover * buy_frac, impact,
                               nav=nav, orders=dw)
    bps_only = fees.turnover_cost(region, turnover, turnover * buy_frac, impact)
    assert exact >= 0.0
    assert exact >= bps_only - 1e-12


# ---------------------------------------------------------------------------
# FX vol targeting
# ---------------------------------------------------------------------------
_FX_COLS = ["EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD", "AAPL", "TLT"]


@st.composite
def _fx_case(draw):
    n_rows = draw(st.integers(min_value=1, max_value=6))
    n = n_rows * len(_FX_COLS)
    tilts = np.asarray(draw(st.lists(st.floats(min_value=-1.0, max_value=1.0, **_FINITE),
                                     min_size=n, max_size=n))).reshape(n_rows, -1)
    vols = np.asarray(draw(st.lists(
        st.one_of(st.just(float("nan")), st.floats(min_value=0.0, max_value=2.0, **_FINITE)),
        min_size=n, max_size=n))).reshape(n_rows, -1)
    idx = pd.bdate_range("2020-01-01", periods=n_rows)
    return (pd.DataFrame(tilts, index=idx, columns=_FX_COLS),
            pd.DataFrame(vols, index=idx, columns=_FX_COLS),
            draw(st.floats(min_value=0.01, max_value=0.5, **_FINITE)),
            draw(st.floats(min_value=0.1, max_value=10.0, **_FINITE)))


@settings(max_examples=150, deadline=None, suppress_health_check=_SLOW_OK)
@given(case=_fx_case())
def test_fx_vol_target_scale_is_between_zero_and_max_vol_scale(case):
    """With every downstream cap switched off, `size_book` returns exactly
    tilt x scale, so the scale is observable: one value per row, never
    negative, never above `max_vol_scale`. (test_property_invariants.py notes
    this was not recoverable under the default caps; disabling them makes it
    so without touching production code.)"""
    tilts, vols, target_vol, max_vol_scale = case
    p = dataclasses.replace(FXParams(), target_vol=target_vol, max_vol_scale=max_vol_scale,
                            per_pair_cap=float("inf"), crypto_gross_cap=None,
                            class_gross_caps=(), max_gross=float("inf"))
    w = size_book(tilts, vols, p)
    assert np.isfinite(w.to_numpy()).all()
    for d in tilts.index:
        live = tilts.loc[d].abs() > 1e-6
        if not live.any():
            continue
        scale = (w.loc[d][live] / tilts.loc[d][live]).to_numpy()
        assert (scale >= -1e-12).all(), scale
        assert (scale <= max_vol_scale * (1 + 1e-9)).all(), scale
        assert np.ptp(scale) <= 1e-9 * max(1.0, float(scale.max())), scale


@settings(max_examples=150, deadline=None, suppress_health_check=_SLOW_OK)
@given(case=_fx_case())
def test_fx_caps_never_flip_a_position(case):
    """Under the default caps, sizing only shrinks: every weight has the sign
    of its tilt (or is zero) and is no larger than tilt x max_vol_scale."""
    tilts, vols, _, _ = case
    p = FXParams()
    w = size_book(tilts, vols, p)
    assert (w.to_numpy() * tilts.to_numpy() >= 0).all()
    assert (w.abs() <= tilts.abs() * p.max_vol_scale + 1e-12).to_numpy().all()
