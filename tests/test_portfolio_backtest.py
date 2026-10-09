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
