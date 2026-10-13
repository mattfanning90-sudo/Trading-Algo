"""Multi-sleeve portfolio backtest combined in AUD."""
import numpy as np
import pandas as pd
import pytest

from trading_algo import config as cfg
from trading_algo.portfolio_backtest import run_portfolio_backtest


def test_portfolio_runs_on_synthetic():
    result = run_portfolio_backtest(synthetic=True, start="2016-01-01", end="2022-01-01")

    assert (result["equity"] > 0).all()
    assert set(result["sleeves"]) == set(cfg.ALLOCATIONS)
    assert set(result["sleeve_equity"].columns) == set(cfg.ALLOCATIONS)
    assert "CAGR" in result["metrics"]


def test_allocations_normalised():
    result = run_portfolio_backtest(synthetic=True, start="2016-01-01", end="2020-01-01")
    assert abs(sum(result["allocations"].values()) - 1.0) < 1e-9


def test_params_and_allocation_overrides():
    from trading_algo.config import DEFAULT_PARAMS
    base = run_portfolio_backtest(synthetic=True, start="2018-01-01", end="2021-01-01")
    over = run_portfolio_backtest(
        synthetic=True, start="2018-01-01", end="2021-01-01",
        params=DEFAULT_PARAMS.with_overrides(rebalance="QE", regime_filter=False, top_n=6),
        allocations={"US": 0.5, "ASX": 0.25, "FTSE": 0.25})
    # the override changes the equity path and records the tilted allocation
    assert abs(over["allocations"]["US"] - 0.5) < 1e-9
    assert not base["equity"].equals(over["equity"])


def test_portfolio_has_benchmark():
    result = run_portfolio_backtest(synthetic=True, start="2018-01-01", end="2021-01-01")
    assert (result["benchmark"] > 0).all()
    for k in ("BenchmarkCAGR", "Beta", "Alpha", "ActiveReturn", "InfoRatio"):
        assert k in result["benchmark_stats"]


def test_each_sleeve_has_base_returns():
    result = run_portfolio_backtest(synthetic=True, start="2016-01-01", end="2020-01-01")
    for s in result["sleeves"].values():
        assert "base_returns" in s
        assert not s["base_returns"].isna().any()


@pytest.mark.parametrize("kwargs, expected", [
    ({}, cfg.MAX_DRAWDOWN_STOP),              # a plain book: the global stop
    ({"max_drawdown_stop": None}, None),      # ultra: the breaker is OFF
    ({"max_drawdown_stop": 0.4}, 0.4)])       # a loosened stop
def test_the_drawdown_stop_reaches_every_sleeve(monkeypatch, kwargs, expected):
    """A book's own breaker threshold must reach each sleeve's run_backtest.
    Without the seam every comparison ran the global 25% stop: `ultra`'s
    profile turns the breaker OFF, yet its comparison sat almost entirely in
    cash behind a stop the paper book never had."""
    import trading_algo.portfolio_backtest as pb

    seen: dict = {}
    real_run_backtest = pb.run_backtest

    def spy(prices, index_px, region, **kw):
        seen[region.key] = kw.get("max_drawdown_stop", cfg.MAX_DRAWDOWN_STOP)
        return real_run_backtest(prices, index_px, region, **kw)

    monkeypatch.setattr(pb, "run_backtest", spy)
    pb.run_portfolio_backtest(regions=["US", "FTSE"], synthetic=True,
                              start="2018-01-01", end="2020-01-01", **kwargs)
    assert seen == {"US": expected, "FTSE": expected}


def test_each_sleeve_funded_at_allocation_in_local_currency(monkeypatch):
    """I-1 (fix round 1): each sleeve's run_backtest must be funded at
    initial_capital * alloc[k], converted to the sleeve's own LOCAL currency
    at a non-1.0 FX rate — not at a flat cfg.INITIAL_CAPITAL in local currency
    regardless of allocation. The per-order commission floor (Task 13) made
    the backtest size-dependent, so this now changes the measured cost."""
    import trading_algo.portfolio_backtest as pb

    captured: dict[str, float] = {}
    real_run_backtest = pb.run_backtest

    def spy(prices, index_px, region, **kwargs):
        captured[region.key] = kwargs["initial_capital"]
        return real_run_backtest(prices, index_px, region, **kwargs)

    monkeypatch.setattr(pb, "run_backtest", spy)

    # A hand-built, deterministic FX table: a NON-1.0 multiplier for every
    # sleeve currency, so "local" and "base" provably differ throughout.
    rate = {"USD": 2.0, "GBP": 4.0}

    def fake_synthetic_fx(currencies, start=None, end=None, base="AUD", seed=7):
        idx = pd.bdate_range(start, end)
        return pd.DataFrame({c: rate.get(c, 1.0) for c in currencies}, index=idx)

    monkeypatch.setattr(pb.fx, "synthetic_fx", fake_synthetic_fx)

    allocations = {"US": 0.6, "FTSE": 0.4}
    initial_capital = 50_000.0
    pb.run_portfolio_backtest(
        regions=["US", "FTSE"], synthetic=True,
        start="2016-01-01", end="2020-01-01",
        allocations=allocations, initial_capital=initial_capital)

    tot = sum(allocations.values())
    alloc = {k: v / tot for k, v in allocations.items()}
    assert captured["US"] == pytest.approx(initial_capital * alloc["US"] / rate["USD"])
    assert captured["FTSE"] == pytest.approx(initial_capital * alloc["FTSE"] / rate["GBP"])
    # not the un-scaled default, and not equal to each other despite a 0.6/0.4
    # allocation split — the FX rate must have actually been applied.
    assert captured["US"] != pytest.approx(cfg.INITIAL_CAPITAL)
    assert captured["US"] != pytest.approx(captured["FTSE"])


# --- per-sleeve open dates (Task 13d) ---------------------------------------
def _drifting_fx(currencies, start="2012-01-01", end="2026-01-01", base="AUD", seed=7):
    """An FX table that moves every day, so funding a sleeve at any bar other
    than its own open bar shows up in its LOCAL capital."""
    idx = pd.bdate_range(start, end)
    ramp = np.linspace(1.0, 2.0, len(idx))
    return pd.DataFrame({c: 1.0 if c == base else ramp * {"USD": 1.5}.get(c, 2.0)
                         for c in currencies}, index=idx)


def test_each_sleeve_opens_on_its_own_bar(monkeypatch):
    """`trade_from` may name each sleeve's own open date. Each sleeve is then
    funded at ITS open bar's rate, holds nothing and earns no FX move through
    that bar, is worth exactly its allocated capital there, decides there and
    fills on its next bar — independently of the other sleeve's date."""
    from trading_algo import fx
    from trading_algo.regions import get_region

    monkeypatch.setattr(fx, "synthetic_fx", _drifting_fx)
    opened = {"US": pd.Timestamp("2020-03-10"), "FTSE": pd.Timestamp("2020-03-12")}
    res = run_portfolio_backtest(regions=["US", "FTSE"], synthetic=True,
                                 end="2020-06-30", allocations={"US": 1, "FTSE": 1},
                                 initial_capital=10_000.0, trade_from=opened)
    rate = _drifting_fx(["USD", "GBP"], end="2020-06-30")
    for k, day in opened.items():
        bt = res["sleeves"][k]
        bars = bt["equity"].index
        assert float(res["sleeve_equity"].loc[day, k]) == pytest.approx(5_000.0, abs=1e-6)
        local = 5_000.0 / rate.loc[day, get_region(k).currency]
        assert np.allclose(bt["equity"].loc[:day], local, rtol=0.0, atol=1e-6), k
        assert (bt["base_returns"].loc[:day] == 0.0).all(), k
        assert bt["turnover"].index[0] == bars[bars.get_loc(day) + 1], k
        assert bt["weights"][day].empty, k
        # from its OWN open on, the sleeve's base value is its local value at
        # each day's rate (up to the month-end true-up), so no FX move after
        # its open bar is dropped because ANOTHER sleeve opened later
        live = bars[(bars >= day) & (bars < pd.Timestamp("2020-03-31"))]
        ccy = get_region(k).currency
        assert np.allclose(res["sleeve_equity"].loc[live, k],
                           bt["equity"].loc[live] * rate.loc[live, ccy],
                           rtol=1e-12, atol=0.0), k


def test_one_date_for_every_sleeve_is_the_single_date_exactly():
    """A mapping that names the same date for every sleeve is the single-date
    comparison, array for array: the per-sleeve path adds no arithmetic."""
    kw = dict(regions=["ASX", "US", "FTSE"], synthetic=True, end="2020-06-30",
              allocations={"ASX": 1, "US": 1, "FTSE": 1}, initial_capital=30_000.0)
    day = pd.Timestamp("2020-03-11")
    one = run_portfolio_backtest(**kw, trade_from=day)
    each = run_portfolio_backtest(**kw, trade_from={k: day for k in kw["regions"]})
    pd.testing.assert_series_equal(each["equity"], one["equity"], check_exact=True)
    pd.testing.assert_frame_equal(each["sleeve_equity"], one["sleeve_equity"],
                                  check_exact=True)
    for k in kw["regions"]:
        for key in ("equity", "returns", "turnover", "costs", "base_returns"):
            pd.testing.assert_series_equal(each["sleeves"][k][key],
                                           one["sleeves"][k][key], check_exact=True)


def test_a_mapping_that_misses_a_sleeve_is_refused():
    """A sleeve with no open date would silently run the whole sample from
    2012 next to sleeves opened this year. Refuse instead."""
    with pytest.raises(ValueError, match="FTSE"):
        run_portfolio_backtest(regions=["US", "FTSE"], synthetic=True,
                               end="2020-06-30", allocations={"US": 1, "FTSE": 1},
                               trade_from={"US": pd.Timestamp("2020-03-11")})


def test_a_mapping_with_no_date_for_a_sleeve_is_refused():
    """`{"FTSE": None}` names the sleeve but gives it no open date, and None
    means "the whole sample" — FTSE would silently trade from 2012 beside a US
    sleeve opened this year. Refused like a missing key, naming the sleeve."""
    with pytest.raises(ValueError, match="FTSE"):
        run_portfolio_backtest(regions=["US", "FTSE"], synthetic=True,
                               end="2020-06-30", allocations={"US": 1, "FTSE": 1},
                               trade_from={"US": pd.Timestamp("2020-03-11"),
                                           "FTSE": None})


def test_each_sleeve_names_the_bar_it_opened_on():
    """The reconciliation bridge measures a comparison sleeve from the bar it
    opened on: before it the sleeve did not exist, so a day there is no day
    of idle cash. Each sleeve's result carries that bar (Saturday 2020-03-14
    opens on Monday the 16th), and a whole-sample run, which opens nowhere,
    carries None."""
    from trading_algo.backtest import first_bar_on_or_after

    opened = {"US": pd.Timestamp("2020-03-10"), "FTSE": pd.Timestamp("2020-03-14")}
    res = run_portfolio_backtest(regions=["US", "FTSE"], synthetic=True,
                                 end="2020-06-30", allocations={"US": 1, "FTSE": 1},
                                 initial_capital=10_000.0, trade_from=opened)
    for k, day in opened.items():
        bars = res["sleeves"][k]["equity"].index
        assert res["sleeves"][k]["open_bar"] == first_bar_on_or_after(bars, day), k
    assert res["sleeves"]["FTSE"]["open_bar"] == pd.Timestamp("2020-03-16")
    whole = run_portfolio_backtest(regions=["US"], synthetic=True, end="2020-06-30")
    assert whole["sleeves"]["US"]["open_bar"] is None


# --- total-return benchmark (the strategy's own prices are auto_adjust=True) --
def test_every_region_declares_a_total_return_proxy():
    """A price index is not the strategy's opponent: the strategy compounds
    dividends (data.py downloads with auto_adjust=True) and the index does not.
    Each region names a dividend-reinvesting ETF that tracks the same market."""
    from trading_algo.regions import REGIONS
    assert {k: r.benchmark_ticker for k, r in REGIONS.items()} == {
        "ASX": "STW.AX", "US": "SPY", "FTSE": "ISF.L", "TSX": "XIU.TO"}


def test_blend_prefers_the_total_return_proxy_where_one_exists():
    """`_blend` is scale-free (it takes a pct_change), so a pence-quoted proxy
    needs no price_scale. A reinvesting proxy compounds ABOVE the price index of
    the same market; the blend has to show that."""
    from trading_algo import portfolio_backtest as pb

    idx = pd.date_range("2020-01-01", periods=5, freq="D")
    price = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0], index=idx)
    total = pd.Series([100.0, 102.0, 104.0, 106.0, 108.0], index=idx)
    fx_tbl = pd.DataFrame({"AUD": [1.0] * 5}, index=idx)
    by_region = {"ASX": (price, "AUD")}

    price_ret = pb._blend(by_region, idx, fx_tbl, {})
    tr_ret = pb._blend(by_region, idx, fx_tbl, {"ASX": total})
    assert abs(float((1 + price_ret).prod()) - 1.04) < 1e-9
    assert float((1 + tr_ret).prod()) > float((1 + price_ret).prod())


def test_offline_benchmark_falls_back_to_the_price_index_and_says_so():
    """Synthetic has no ETF history. Falling back is fine; falling back
    silently is not — the label has to carry the word the reader needs."""
    result = run_portfolio_backtest(synthetic=True, start="2018-01-01",
                                    end="2021-01-01")
    assert result["benchmark_kind"] == "price-index"
    assert "NO dividends" in result["benchmark_label"]
    assert result["benchmark_proxies"] == {}
    assert result["benchmark"].equals(result["benchmark_price_index"])
    assert "CAGR" in result["benchmark_price_index_metrics"]


def test_blend_holds_each_region_flat_through_its_own_open_bar():
    """A sleeve opened on a later bar earns nothing before it (its base
    returns are zeroed through its open bar). The benchmark's leg for that
    region must do the same, or the yardstick is credited with days the
    strategy was never invested."""
    from trading_algo import portfolio_backtest as pb

    idx = pd.date_range("2020-01-01", periods=6, freq="D")
    up = pd.Series([100.0, 110.0, 121.0, 133.1, 146.41, 161.051], index=idx)
    fx_tbl = pd.DataFrame({"AUD": [1.0] * 6}, index=idx)
    by_region = {"ASX": (up, "AUD"), "US": (up, "AUD")}
    opens = {"ASX": idx[1], "US": idx[3]}

    r = pb._blend(by_region, idx, fx_tbl, {}, open_bars=opens)
    # each leg is +10% a day; half the book per leg
    assert list(r.round(12)) == [0.0, 0.0, 0.05, 0.05, 0.10, 0.10]
    # no open bars: the whole sample, unchanged
    assert list(pb._blend(by_region, idx, fx_tbl, {}).round(12)) == [
        0.0, 0.1, 0.1, 0.1, 0.1, 0.1]


def test_the_benchmark_is_measured_on_the_strategys_capital_and_window():
    """Same capital, same AUD conversion, same window: a book opened at
    `trade_from` is flat at `initial_capital` through its earliest open bar,
    so the benchmark (both of them) must be too — and on the same dates."""
    opened = {"US": pd.Timestamp("2020-03-10"), "FTSE": pd.Timestamp("2020-03-12")}
    res = run_portfolio_backtest(regions=["US", "FTSE"], synthetic=True,
                                 end="2020-06-30", allocations={"US": 1, "FTSE": 1},
                                 initial_capital=10_000.0, trade_from=opened)
    first_open = min(s["open_bar"] for s in res["sleeves"].values())
    for key in ("benchmark", "benchmark_price_index"):
        bench = res[key]
        assert bench.index.equals(res["equity"].index), key
        assert (bench.loc[:first_open] == 10_000.0).all(), key
        assert (res["equity"].loc[:first_open] == 10_000.0).all()
        assert bench.loc[first_open:].iloc[1:].ne(10_000.0).any(), key


def test_a_later_sleeves_benchmark_leg_waits_for_its_own_open(monkeypatch):
    """Per-sleeve opens reach the benchmark region by region: between the US
    open and the later FTSE open, the two-sleeve benchmark moves by exactly
    half the US-only benchmark (the FTSE half is still flat), and from the
    FTSE open on it does not. Opening every leg at the EARLIEST open would
    credit the FTSE leg with days its sleeve held nothing."""
    from trading_algo import fx

    monkeypatch.setattr(fx, "synthetic_fx", _drifting_fx)   # same rates per currency
    us_open, ft_open = pd.Timestamp("2020-03-10"), pd.Timestamp("2020-03-17")
    kw = dict(synthetic=True, end="2020-06-30", initial_capital=10_000.0)
    both = run_portfolio_backtest(regions=["US", "FTSE"],
                                  allocations={"US": 1, "FTSE": 1},
                                  trade_from={"US": us_open, "FTSE": ft_open}, **kw)
    us_only = run_portfolio_backtest(regions=["US"], trade_from={"US": us_open}, **kw)
    assert both["sleeves"]["US"]["open_bar"] < both["sleeves"]["FTSE"]["open_bar"]
    for key in ("benchmark", "benchmark_price_index"):
        two = both[key].pct_change(fill_method=None)
        half_us = us_only[key].pct_change(fill_method=None) / 2.0
        between = two.loc[us_open:ft_open].index[1:]          # after US open, through FTSE's
        assert len(between) >= 4, key
        assert np.allclose(two.loc[between], half_us.loc[between],
                           rtol=0.0, atol=1e-12), key
        after = two.loc[ft_open:].index[1:]
        assert not np.allclose(two.loc[after], half_us.loc[after],
                               rtol=0.0, atol=1e-12), key


def test_a_proxy_that_lists_after_the_window_opens_is_not_spliced(monkeypatch):
    """An ETF with no price at the start of the window would sit in cash (a
    zero return) until it lists. That region falls back to its price index,
    and the kind/label say the basis is mixed — never a silent splice."""
    import trading_algo.portfolio_backtest as pb
    from trading_algo import data

    _, us_idx = data.synthetic_region(pb.get_region("US"), start="2018-01-01",
                                      end="2020-01-01")
    _, ft_idx = data.synthetic_region(pb.get_region("FTSE"), start="2018-01-01",
                                      end="2020-01-01")
    late = ft_idx.loc["2019-01-01":] * 1.5             # lists a year in
    monkeypatch.setattr(pb, "_total_return_proxies",
                        lambda *a, **k: {"US": us_idx * 2.0, "FTSE": late})
    res = pb.run_portfolio_backtest(regions=["US", "FTSE"], synthetic=True,
                                    start="2018-01-01", end="2020-01-01")
    assert res["benchmark_kind"] == "mixed"
    assert res["benchmark_proxies"] == {"US": "SPY"}
    assert "mixed" in res["benchmark_label"]
