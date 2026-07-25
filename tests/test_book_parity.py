"""Parity across EVERY book, not just the default one.

`test_consistency` pins invariant #3 (one weight function) for a plain core book.
But the paper accounts that actually run are not all plain: `ultra` turns the
regime gate off and gears to 3x, `experimental` is a dollar-neutral long/short
book that skips the trend and regime branches entirely, and `use_value` blends a
second factor in. Each of those takes a DIFFERENT branch of the weight formula.

Since the signal frames are now built once by `strategy.precompute()` and read
per date by `strategy.targets_at()` (rather than recomputed inside every
`compute_targets` call), the thing that must not drift is:

    targets_at(precompute(prices, index, p), p, asof)  ==  compute_targets(...)

for every book — because the backtester takes the first road and paper/live
trading takes the second. This file pins that equality on all of them, on the
same synthetic data, bit-for-bit, and additionally pins that the panel builds
exactly the branches each book's formula reads (so a future "optimisation" can't
quietly drop one).
"""
from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from trading_algo import backtest, data, profiles, strategy
from trading_algo.regions import get_region

# Every shape of book the system can run. `core` is the funded default; `ultra`
# and `experimental` are the real profiled paper books; `value` is the
# momentum+value blend the tuner searches over.
BOOKS = {
    "core": {},
    "ultra": dict(profiles.PROFILES["ultra"].param_overrides),
    "experimental": dict(profiles.PROFILES["experimental"].param_overrides),
    "value": {"use_value": True},
}

# Every registered sleeve, including the registered-but-unfunded TSX — a sleeve
# is backtestable from the registry before it is funded, so it needs parity too.
REGIONS = ["US", "ASX", "FTSE", "TSX"]


def _book(region_key: str, book: str):
    """(prices, index, region, params) for one book on one sleeve, built the same
    way `paper_trade._account_params` builds a profiled book: region defaults
    with the profile's overrides layered on top."""
    region = get_region(region_key)
    prices, index_px = data.synthetic_region(region, start="2014-01-01", end="2024-01-01")
    overrides = BOOKS[book]
    params = region.params.with_overrides(**overrides) if overrides else region.params
    return prices, index_px, replace(region, params=params), params


def _asof_dates(prices: pd.DataFrame, params, n: int = 6) -> list[pd.Timestamp]:
    """A spread of rebalance dates with enough history for every book (the value
    book needs its long-term-reversal window before it can score anything)."""
    min_hist = params.min_history_days
    if params.use_value:
        min_hist = max(min_hist, params.value_lookback_days + 5)
    marks = [d for d in prices.resample(params.rebalance).last().index
             if prices.index.searchsorted(d, side="right") - 1 >= min_hist]
    step = max(1, len(marks) // n)
    return [prices.index[prices.index.searchsorted(d, side="right") - 1]
            for d in marks[::step]][:n]


# ---------------------------------------------------------------------------
# 1. The two roads to a weight vector agree, for every book on every sleeve
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("book", sorted(BOOKS))
@pytest.mark.parametrize("region_key", REGIONS)
def test_panel_path_equals_single_date_path(region_key, book):
    """The backtest road (shared panel) and the paper road (compute_targets)
    produce bit-identical weights on the same date, for every book."""
    prices, index_px, _region, params = _book(region_key, book)
    panel = strategy.precompute(prices, index_px, params)

    dates = _asof_dates(prices, params)
    assert dates, f"no usable rebalance dates for {region_key}/{book}"

    compared = 0
    for asof in dates:
        via_panel = strategy.targets_at(panel, params, asof)
        via_single = strategy.compute_targets(prices, index_px, params, asof=asof)
        assert list(via_panel.index) == list(via_single.index), (
            f"{region_key}/{book} @ {asof.date()}: different names selected")
        pd.testing.assert_series_equal(via_panel, via_single, check_names=False)
        compared += 1
    assert compared == len(dates)


@pytest.mark.parametrize("book", sorted(BOOKS))
def test_panel_path_equals_single_date_path_under_eligibility(book):
    """Parity holds with a point-in-time eligible set and an ADV capacity cap
    too — both are applied inside the shared formula, not around it."""
    prices, index_px, _region, params = _book("US", book)
    panel = strategy.precompute(prices, index_px, params)
    asof = _asof_dates(prices, params)[-1]

    eligible = set(sorted(prices.columns)[: max(20, params.top_n * 3)])
    capacity = pd.Series(0.05, index=prices.columns)

    for elig, cap in ((eligible, None), (None, capacity), (eligible, capacity)):
        via_panel = strategy.targets_at(panel, params, asof, eligible=elig, capacity=cap)
        via_single = strategy.compute_targets(prices, index_px, params, asof=asof,
                                              eligible=elig, capacity=cap)
        pd.testing.assert_series_equal(via_panel, via_single, check_names=False)


# ---------------------------------------------------------------------------
# 2. The panel builds exactly the branches each book's formula reads
# ---------------------------------------------------------------------------
def test_panel_builds_only_the_branches_the_formula_takes():
    """A long/short book is purely cross-sectional (no trend, no regime); a book
    with the regime gate off needs no regime series; only a value book needs the
    value frame. Pinning this stops a future change from computing a frame the
    formula never reads — or, worse, dropping one it does."""
    prices, index_px, _r, _p = _book("US", "core")

    core = strategy.precompute(prices, index_px, get_region("US").params)
    assert core.trend is not None, "long-only book must have the trend filter"
    assert core.risk_on is not None, "core book has the regime gate ON"
    assert core.value is None, "core book is pure momentum"

    ultra = strategy.precompute(prices, index_px,
                                get_region("US").params.with_overrides(**BOOKS["ultra"]))
    assert ultra.trend is not None
    assert ultra.risk_on is None, "ultra runs with regime_filter off — no regime series"

    exp = strategy.precompute(
        prices, index_px, get_region("US").params.with_overrides(**BOOKS["experimental"]))
    assert exp.trend is None and exp.risk_on is None, (
        "a market-neutral book takes neither the trend nor the regime branch")

    val = strategy.precompute(prices, index_px,
                              get_region("US").params.with_overrides(use_value=True))
    assert val.value is not None, "value book must carry the value frame"

    # Momentum and vol are read by every branch, so they are never optional.
    for panel in (core, ultra, exp, val):
        assert panel.scores is not None and panel.vols is not None


def test_panel_built_for_another_book_is_refused():
    """The one new failure mode the precompute/targets_at split introduces: a
    panel built for book A used with book B's params. It must raise, not quietly
    price a book with a filter missing."""
    prices, index_px, _r, _p = _book("US", "core")
    us = get_region("US").params

    ls_panel = strategy.precompute(
        prices, index_px, us.with_overrides(**BOOKS["experimental"]))
    momentum_panel = strategy.precompute(prices, index_px, us)
    asof = _asof_dates(prices, us)[-1]

    # long/short panel (no trend frame) + long-only params
    with pytest.raises(ValueError, match="trend"):
        strategy.targets_at(ls_panel, us, asof)

    # momentum-only panel (no value frame) + a value book's params
    with pytest.raises(ValueError, match="value"):
        strategy.targets_at(momentum_panel, us.with_overrides(use_value=True), asof)

    # regime-off panel (ultra) + params that expect the gate. Silently skipping
    # the regime filter would leave a book fully invested through a risk-off
    # regime — the exact de-risking the gate exists to do.
    ultra_panel = strategy.precompute(prices, index_px,
                                      us.with_overrides(**BOOKS["ultra"]))
    with pytest.raises(ValueError, match="regime"):
        strategy.targets_at(ultra_panel, us, asof)


def test_regime_gate_follows_the_params_not_the_panel():
    """A book with the gate OFF must stay invested even when handed a panel that
    happens to carry a regime series — the params are authoritative."""
    prices, index_px, _r, _p = _book("US", "core")
    us = get_region("US").params
    gate_off = us.with_overrides(regime_filter=False)

    # A panel built for gate-off params, and one built for gate-on params, must
    # give the same answer when priced with gate-off params.
    asof = _asof_dates(prices, gate_off)[-1]
    from_own = strategy.targets_at(
        strategy.precompute(prices, index_px, gate_off), gate_off, asof)
    from_richer = strategy.targets_at(
        strategy.precompute(prices, index_px, us), gate_off, asof)
    pd.testing.assert_series_equal(from_own, from_richer, check_names=False)


# ---------------------------------------------------------------------------
# 3. No lookahead survives the hoist — the panel is causal
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("book", sorted(BOOKS))
def test_panel_rows_do_not_depend_on_the_future(book):
    """Perturbing prices AFTER `asof` must not change the weights AT `asof`.

    This is invariant #1 restated for the panel: because the panel is now built
    once over the whole history and indexed per date, a non-causal op inside it
    would leak the future into every earlier decision. Truncating the data at
    `asof` must give the identical answer.
    """
    prices, index_px, _region, params = _book("US", book)
    asof = _asof_dates(prices, params)[-2]

    full = strategy.targets_at(
        strategy.precompute(prices, index_px, params), params, asof)
    truncated = strategy.targets_at(
        strategy.precompute(prices.loc[:asof], index_px.loc[:asof], params),
        params, asof)
    pd.testing.assert_series_equal(full, truncated, check_names=False)


# ---------------------------------------------------------------------------
# 4. The backtest acts on the shared weights, for every book
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("book", sorted(BOOKS))
def test_backtest_acts_on_shared_weights_for_every_book(book):
    """Generalises `test_consistency`'s golden equality to every book shape: the
    weights the backtester actually holds equal what the shared weight function
    returns for that date — no per-profile reweighting layer anywhere."""
    prices, index_px, region, params = _book("US", book)
    result = backtest.run_backtest(prices, index_px, region, max_drawdown_stop=None)
    weights_hist = result["weights"]

    checked = 0
    for asof in _asof_dates(prices, params, n=12):
        expected = strategy.compute_targets(prices, index_px, params, asof=asof)
        if expected.empty:
            continue                       # risk-off / unhedgeable: nothing to compare
        loc = prices.index.get_loc(asof)
        matched = False
        for lag in (1, 2, 3):
            if loc + lag >= len(prices):
                break
            got = weights_hist.get(prices.index[loc + lag])
            if got is None or got.empty:
                continue
            if len(got) == len(expected) and got.reindex(expected.index).equals(expected):
                matched = True
                break
        assert matched, (
            f"{book}: target for {asof.date()} was not applied unmodified")
        checked += 1
        if checked >= 3:
            break
    assert checked > 0, f"{book}: no non-empty target was validated"
