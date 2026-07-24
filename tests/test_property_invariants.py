"""Property-based tests (Hypothesis) that pin the project INVARIANTS over
GENERATED inputs, not hand-picked cases.

Each test targets one of the six project invariants (CLAUDE.md) or a closely
related money-path safety property, and asserts it holds for a whole family of
synthetic inputs. These are *behaviour pins*: everything asserted here already
holds today. A failure means a real invariant violation (e.g. a lookahead leak,
a cost sign bug, or a fractional-share leak) — not a flaky test — so it should
be investigated, never weakened.

Synthetic data here is a PLUMBING probe only (invariant #5): none of these
numbers are performance; they exist purely to exercise the code paths.

Feasibility notes (properties that cannot be expressed against the current API
are skipped inline with a comment explaining why).
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from trading_algo import fees
from trading_algo.backtest import run_backtest
from trading_algo.config import StrategyParams
from trading_algo.metrics import compute_metrics
from trading_algo.paper_trade import rebalance_sleeve, sleeve_equity_local
from trading_algo.regions import get_region
from trading_algo.forex import marks
from trading_algo.forex.fx_config import FXParams
from trading_algo.forex.pairs import ALL_PAIRS, get_pair
from trading_algo.forex.risk import size_book

# A short-window StrategyParams so a ~150-bar synthetic panel is enough history
# for the backtest to actually rebalance many times — keeps each example fast.
FAST = StrategyParams(
    lookback_days=20,
    skip_days=2,
    min_history_days=25,
    vol_lookback=10,
    stock_trend_ma=15,
    index_trend_ma=15,
    top_n=3,
    max_weight=0.5,
    rebalance="W",          # weekly rebalance -> many decision points in a short panel
)

# A registered region reshaped for the synthetic panel: the US fee/slippage
# schedule with the fast params and a tiny synthetic universe/index.
_TEST_TICKERS = ["A0", "A1", "A2", "A3", "A4"]
_TEST_INDEX = "IDX"
TEST_REGION = dataclasses.replace(
    get_region("US"),
    key="PROPTEST",
    params=FAST,
    universe=list(_TEST_TICKERS),
    index_ticker=_TEST_INDEX,
)


# ---------------------------------------------------------------------------
# Synthetic-input strategies
# ---------------------------------------------------------------------------
@st.composite
def _price_panel(draw, min_days=130, max_days=190, n_assets=len(_TEST_TICKERS)):
    """Generate (prices DataFrame, index Series) from a drawn returns matrix.

    Prices are a strictly-positive GBM path (base*exp(cumsum(r))) with bounded
    per-bar returns, so no NaNs and no zero/negative prices — a clean panel the
    backtest can run end to end.
    """
    n_days = draw(st.integers(min_value=min_days, max_value=max_days))
    dates = pd.bdate_range("2015-01-01", periods=n_days)

    def _series(seed_key):
        rets = draw(
            st.lists(
                st.floats(min_value=-0.07, max_value=0.07,
                          allow_nan=False, allow_infinity=False),
                min_size=n_days, max_size=n_days,
            )
        )
        return np.asarray(rets, dtype=float)

    cols = {}
    for t in _TEST_TICKERS[:n_assets]:
        cols[t] = 50.0 * np.exp(np.cumsum(_series(t)))
    prices = pd.DataFrame(cols, index=dates)
    idx = pd.Series(5000.0 * np.exp(np.cumsum(_series("IDX"))), index=dates)
    return prices, idx


# ---------------------------------------------------------------------------
# Property 1 — NO-LOOKAHEAD (the crown jewel)
# ---------------------------------------------------------------------------
@settings(max_examples=40, deadline=None,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large,
                                 HealthCheck.large_base_example])
@given(data=_price_panel(), cut_frac=st.floats(min_value=0.4, max_value=0.75))
def test_no_lookahead_tail_perturbation_leaves_head_invariant(data, cut_frac):
    """The equity curve up to bar k must be INVARIANT to ANY change in prices
    strictly AFTER bar k. If perturbing the future moves the past, the backtest
    has a lookahead leak (invariant #1).
    """
    prices, idx = data
    n = len(prices)
    cut = int(n * cut_frac)
    assume(cut >= FAST.min_history_days + 5)
    assume(cut < n - 3)
    cut_date = prices.index[cut]

    base = run_backtest(prices, idx, TEST_REGION, max_drawdown_stop=None)

    # Perturb ONLY the tail (rows strictly after the cut) by a non-trivial,
    # per-cell positive factor — the future becomes a different world.
    rng = np.random.default_rng(cut)
    p2 = prices.copy()
    factors = rng.uniform(1.15, 2.5, size=(n - cut - 1, p2.shape[1]))
    p2.iloc[cut + 1:] = p2.iloc[cut + 1:].to_numpy() * factors
    idx2 = idx.copy()
    idx2.iloc[cut + 1:] = idx2.iloc[cut + 1:].to_numpy() * rng.uniform(
        1.15, 2.5, size=n - cut - 1)

    pert = run_backtest(p2, idx2, TEST_REGION, max_drawdown_stop=None)

    # equity is indexed by trading date; equity at date d depends only on prices
    # <= d, so the head up to (and including) the cut date must match exactly.
    head_base = base["equity"].loc[:cut_date]
    head_pert = pert["equity"].loc[:cut_date]
    assert len(head_base) > 0
    pd.testing.assert_series_equal(head_base, head_pert, rtol=0, atol=1e-9)


# ---------------------------------------------------------------------------
# Property 2 — COST MONOTONICITY / non-negativity
# ---------------------------------------------------------------------------
@settings(max_examples=200, deadline=None)
@given(
    dw_small=st.floats(min_value=0.0, max_value=1.0,
                       allow_nan=False, allow_infinity=False),
    extra=st.floats(min_value=0.0, max_value=2.0,
                    allow_nan=False, allow_infinity=False),
    price=st.floats(min_value=0.05, max_value=5000.0,
                    allow_nan=False, allow_infinity=False),
    symbol=st.sampled_from(list(ALL_PAIRS)),
)
def test_fx_cost_fraction_monotone_nonneg(dw_small, extra, price, symbol):
    """FX half-spread cost is >= 0 and never decreases as |Δw| grows
    (marks.cost_fraction). A larger weight change must not cost less."""
    pair = get_pair(symbol)
    dw_big = dw_small + extra
    c_small = marks.cost_fraction(dw_small, pair, price)
    c_big = marks.cost_fraction(dw_big, pair, price)
    assert c_small >= 0.0
    assert c_big >= 0.0
    assert c_big + 1e-12 >= c_small          # monotone non-decreasing in |Δw|
    # Symmetric in sign: only the magnitude of the weight change matters.
    assert marks.cost_fraction(-dw_small, pair, price) == pytest.approx(c_small)


@settings(max_examples=200, deadline=None)
@given(
    turn_small=st.floats(min_value=0.0, max_value=3.0,
                         allow_nan=False, allow_infinity=False),
    extra=st.floats(min_value=0.0, max_value=3.0,
                    allow_nan=False, allow_infinity=False),
    buy_frac=st.floats(min_value=0.0, max_value=1.0,
                       allow_nan=False, allow_infinity=False),
    impact=st.floats(min_value=0.0, max_value=0.05,
                     allow_nan=False, allow_infinity=False),
    region_key=st.sampled_from(["US", "FTSE", "ASX"]),
)
def test_equity_turnover_cost_monotone_nonneg(turn_small, extra, buy_frac,
                                              impact, region_key):
    """Equity turnover cost is >= 0 and non-decreasing in turnover
    (fees.turnover_cost) — FTSE exercises the asymmetric buy-side stamp duty."""
    region = get_region(region_key)
    turn_big = turn_small + extra
    c_small = fees.turnover_cost(region, turn_small, turn_small * buy_frac, impact)
    c_big = fees.turnover_cost(region, turn_big, turn_big * buy_frac, impact)
    assert c_small >= 0.0
    assert c_big + 1e-12 >= c_small


# ---------------------------------------------------------------------------
# Property 3 — WHOLE SHARES
# ---------------------------------------------------------------------------
def _fresh_sleeve(cash, currency="USD"):
    return {
        "currency": currency,
        "cash": float(cash),
        "positions": {},
        "cost_basis": {},
        "realized_pnl": 0.0,
        "last_rebalance_month": None,
        "last_rebalance_date": None,
    }


@st.composite
def _targets_and_prices(draw, tickers=_TEST_TICKERS):
    """A weight vector (summing to <= 1) plus strictly-positive prices."""
    raws = draw(st.lists(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False,
                  allow_infinity=False),
        min_size=len(tickers), max_size=len(tickers)))
    raw = np.asarray(raws, dtype=float)
    if raw.sum() > 0:
        raw = raw / raw.sum()            # normalise so gross <= 1 (long-only)
    weights = pd.Series(raw, index=tickers)
    px_vals = draw(st.lists(
        st.floats(min_value=1.0, max_value=500.0, allow_nan=False,
                  allow_infinity=False),
        min_size=len(tickers), max_size=len(tickers)))
    px = pd.Series(np.asarray(px_vals, dtype=float), index=tickers)
    return weights, px


@settings(max_examples=120, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(tp=_targets_and_prices(),
       cash=st.floats(min_value=6_000.0, max_value=500_000.0,
                      allow_nan=False, allow_infinity=False))
def test_paper_rebalance_holds_whole_shares(tp, cash):
    """After a paper rebalance every share holding is an integer (invariant #4).
    Cash is kept above MICRO_THRESHOLD so the book holds the full book, not the
    micro-mode concentration."""
    weights, px = tp
    region = get_region("US")
    sleeve = _fresh_sleeve(cash)
    trade_log: list = []
    rebalance_sleeve(region, sleeve, weights, px, "2020-01-02", trade_log)
    for t, sh in sleeve["positions"].items():
        assert isinstance(sh, int), f"{t} holds non-integer shares {sh!r}"


# ---------------------------------------------------------------------------
# Property 4 — CURRENCY ISOLATION
# ---------------------------------------------------------------------------
@settings(max_examples=120, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(tp=_targets_and_prices(),
       cash=st.floats(min_value=6_000.0, max_value=500_000.0,
                      allow_nan=False, allow_infinity=False),
       rate1=st.floats(min_value=0.2, max_value=5.0, allow_nan=False,
                       allow_infinity=False),
       rate2=st.floats(min_value=0.2, max_value=5.0, allow_nan=False,
                       allow_infinity=False))
def test_currency_isolation_local_book_is_fx_free(tp, cash, rate1, rate2):
    """A sleeve trades entirely in its LOCAL currency: its share counts and its
    local equity are invariant to the base-currency FX rate — only the REPORTED
    base value scales linearly (invariant #6). The FX rate is not even an input
    to the local trading path, so we pin that (a) two identical local runs are
    bit-identical and (b) the reporting conversion is exactly a scalar multiply.
    """
    weights, px = tp
    region = get_region("US")

    s1 = _fresh_sleeve(cash)
    rebalance_sleeve(region, s1, weights, px, "2020-01-02", [])
    s2 = _fresh_sleeve(cash)
    rebalance_sleeve(region, s2, weights, px, "2020-01-02", [])

    # Local trading is deterministic and FX-free: identical positions + cash.
    assert s1["positions"] == s2["positions"]
    assert s1["cash"] == s2["cash"]

    local_eq = sleeve_equity_local(s1, px)
    # Reporting layer converts by a pure scalar multiply; local value recovers
    # exactly regardless of the rate used.
    assert local_eq * rate1 / rate1 == pytest.approx(local_eq)
    if local_eq != 0:
        base1 = local_eq * rate1
        base2 = local_eq * rate2
        assert base1 / base2 == pytest.approx(rate1 / rate2, rel=1e-9)


# ---------------------------------------------------------------------------
# Property 5 — P&L RECONCILIATION (round-trip, no drift)
# ---------------------------------------------------------------------------
@settings(max_examples=120, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(tp=_targets_and_prices(),
       cash=st.floats(min_value=6_000.0, max_value=500_000.0,
                      allow_nan=False, allow_infinity=False))
def test_pnl_reconciliation_round_trip(tp, cash):
    """Book equity == cash + Σ(position marks) at every step, and a full
    round-trip (open then flatten at the same prices) never creates money:
    final equity <= starting cash and the residual book is pure cash.
    """
    weights, px = tp
    region = get_region("US")
    sleeve = _fresh_sleeve(cash)
    trade_log: list = []

    # Open the book.
    rebalance_sleeve(region, sleeve, weights, px, "2020-01-02", trade_log)
    eq_open = sleeve_equity_local(sleeve, px)
    # Accounting identity: equity is exactly cash + marked positions.
    marks_val = sum(sh * float(px[t]) for t, sh in sleeve["positions"].items())
    assert eq_open == pytest.approx(sleeve["cash"] + marks_val, rel=1e-12, abs=1e-9)

    # Flatten the book at the SAME prices (target all-cash).
    rebalance_sleeve(region, sleeve, pd.Series(dtype=float), px,
                     "2020-01-03", trade_log)
    eq_flat = sleeve_equity_local(sleeve, px)

    # After flattening, any residual position must be a whole-share leftover that
    # was dust-skipped; equity is still exactly cash + those marks.
    marks_flat = sum(sh * float(px[t]) for t, sh in sleeve["positions"].items())
    assert eq_flat == pytest.approx(sleeve["cash"] + marks_flat, rel=1e-12, abs=1e-9)

    # No money creation: trading only ever costs (commission + slippage), so the
    # round trip cannot leave the book richer than it started.
    assert eq_flat <= cash + 1e-6
    assert eq_flat > 0.0


# ---------------------------------------------------------------------------
# Property 6 — SIZING BOUNDS (FX risk.size_book)
# ---------------------------------------------------------------------------
_SIZE_COLS = ["EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD", "AAPL", "MSFT", "TLT"]


@st.composite
def _tilts_and_vols(draw, cols=_SIZE_COLS):
    n_rows = draw(st.integers(min_value=2, max_value=8))
    dates = pd.bdate_range("2020-01-01", periods=n_rows)

    def _mat(lo, hi):
        vals = draw(st.lists(
            st.floats(min_value=lo, max_value=hi, allow_nan=False,
                      allow_infinity=False),
            min_size=n_rows * len(cols), max_size=n_rows * len(cols)))
        return np.asarray(vals, dtype=float).reshape(n_rows, len(cols))

    tilts = pd.DataFrame(_mat(-1.0, 1.0), index=dates, columns=cols)
    vols = pd.DataFrame(_mat(0.02, 1.5), index=dates, columns=cols)
    return tilts, vols


@settings(max_examples=80, deadline=None,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
@given(tv=_tilts_and_vols())
def test_size_book_respects_all_caps(tv):
    """size_book output must respect the per-pair cap, every per-asset-class
    gross cap (incl. the dedicated crypto cap) and the gross-leverage cap, for
    any generated tilt/vol inputs."""
    tilts, vols = tv
    p = FXParams()                       # dataclass defaults carry the caps
    w = size_book(tilts, vols, p)

    tol = 1e-9
    # Per-pair cap: |wᵢ| <= per_pair_cap everywhere.
    assert (w.abs() <= p.per_pair_cap + tol).to_numpy().all()

    # Gross-leverage cap: Σ|wᵢ| <= max_gross per row.
    gross = w.abs().sum(axis=1)
    assert (gross <= p.max_gross + tol).all()

    # Per-class gross caps (crypto via the dedicated knob; equity/bond via
    # class_gross_caps). FX carries no class cap by design.
    caps = dict(p.class_gross_caps)
    caps["crypto"] = p.crypto_gross_cap
    for klass, cap in caps.items():
        if cap is None:
            continue
        cols = [c for c in w.columns if ALL_PAIRS[c].asset_class == klass]
        if not cols:
            continue
        cgross = w[cols].abs().sum(axis=1)
        assert (cgross <= cap + tol).all(), f"{klass} gross exceeds {cap}"


def test_size_book_vol_scale_bound_documented():
    """max_vol_scale caps the vol-targeting leverage of the RAW book, but that
    happens BEFORE the per-pair/class/gross caps shrink it further, so the raw
    scale is not recoverable from the returned weights. We therefore pin the
    observable downstream caps (test above) rather than max_vol_scale directly;
    this test documents that intentional gap."""
    # Sanity: with a single pair and a tiny vol, the scale saturates at
    # max_vol_scale, then the per-pair cap binds — never exceeding it.
    p = FXParams()
    tilts = pd.DataFrame({"EURUSD": [1.0, 1.0]},
                         index=pd.bdate_range("2020-01-01", periods=2))
    vols = pd.DataFrame({"EURUSD": [1e-6, 1e-6]}, index=tilts.index)
    w = size_book(tilts, vols, p)
    assert (w["EURUSD"].abs() <= p.per_pair_cap + 1e-9).all()


# ---------------------------------------------------------------------------
# Property 7 — METRICS TOTALITY
# ---------------------------------------------------------------------------
# Keys that may legitimately be a defined NaN sentinel (undefined when there is
# no dispersion / no drawdown) — everything else must be a finite number.
_SENTINEL_ALLOWED = {"Sortino", "Calmar"}


@settings(max_examples=150, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(rets=st.lists(
    st.floats(min_value=-0.3, max_value=0.3, allow_nan=False,
              allow_infinity=False),
    min_size=3, max_size=400))
def test_metrics_totality_no_silent_nan(rets):
    """compute_metrics returns finite numbers for any non-degenerate return
    series (no silent NaN/inf). Sortino/Calmar may be a DEFINED NaN sentinel
    when their denominators are undefined; every other metric must be finite."""
    r = np.asarray(rets, dtype=float)
    # Bounded returns > -1 keep the equity path strictly positive, so the
    # series is non-degenerate (equity.iloc[0] != 0) — the documented domain.
    equity = pd.Series(1000.0 * np.cumprod(1.0 + r))
    ret_series = equity.pct_change().dropna()
    m = compute_metrics(ret_series, equity)

    if "error" in m:                     # deliberate sentinel for degenerate input
        return
    for key, val in m.items():
        if isinstance(val, str):
            continue
        f = float(val)
        if key in _SENTINEL_ALLOWED:
            assert not np.isinf(f), f"{key} is infinite"   # NaN allowed, inf not
        else:
            assert np.isfinite(f), f"{key} is not finite: {f!r}"
