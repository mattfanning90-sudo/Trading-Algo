"""Benchmark statistics."""
import math

import numpy as np
import pandas as pd
import pytest

from trading_algo.metrics import (ANNUALIZATION, Measure, annualised_vol,
                                  benchmark_stats, cagr, compute_metrics,
                                  max_drawdown, periods_per_year, sharpe)


def test_benchmark_stats_against_self():
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.0005, 0.01, 400))
    s = benchmark_stats(r, r)
    assert abs(s["Beta"] - 1.0) < 0.01          # a series has beta 1 to itself
    assert abs(s["ActiveReturn"]) < 1e-6        # …and no active return
    for k in ("BenchmarkCAGR", "Alpha", "TrackingError", "InfoRatio"):
        assert k in s


def test_beta_against_self_is_exactly_one():
    # Beta of a series against itself is 1.0 exactly, for ANY sample size.
    # A ddof mismatch between covariance (population, /N) and variance
    # (sample, /N-1) biases it by (N-1)/N, which at small N shows up even
    # after 2dp rounding (e.g. N=50 -> 0.98).
    rng = np.random.default_rng(1)
    r = pd.Series(rng.normal(0.0005, 0.01, 50))
    s = benchmark_stats(r, r)
    assert s["Beta"] == 1.0


def test_sortino_finite_when_no_losing_days():
    # All-positive returns -> no downside deviation. Sortino must stay a
    # finite, non-nan number rather than being silently poisoned to nan by
    # the max(nan, 1e-9) idiom.
    rets = pd.Series([0.01, 0.02, 0.005, 0.03, 0.015])
    equity = (1 + rets).cumprod() * 100.0
    m = compute_metrics(rets, equity)
    assert math.isfinite(m["Sortino"])
    assert not math.isnan(m["Sortino"])


def test_single_observation_defined_values_not_nan():
    # A single observation cannot yield a ddof=1 volatility, but the values
    # that ARE well-defined must not come back nan.
    rets = pd.Series([0.02])
    equity = pd.Series([102.0])
    m = compute_metrics(rets, equity)
    assert not math.isnan(m["CAGR"])
    assert not math.isnan(m["MaxDrawdown"])
    assert not math.isnan(m["WinRate(days)"])
    assert not math.isnan(m["FinalEquity (AUD)"])
    # AnnVol is deliberately defined (no dispersion in one point) rather than
    # a silent ddof=1 nan.
    assert not math.isnan(m["AnnVol"])


def test_benchmark_stats_empty():
    assert benchmark_stats(pd.Series(dtype=float), pd.Series(dtype=float)) == {}


def test_periods_per_year_derived_from_bar_spacing():
    """THE defect this layer exists to kill: 60-minute bars annualised at 252."""
    daily = pd.date_range("2026-01-01", periods=100, freq="B")
    hourly = pd.date_range("2026-01-01", periods=100, freq="h")
    minute = pd.date_range("2026-01-01", periods=100, freq="min")
    assert periods_per_year(daily) == 252
    assert periods_per_year(hourly) == pytest.approx(24 * 365.25)      # 8766
    assert periods_per_year(minute) == 24 * 365.25                     # capped
    assert periods_per_year(pd.DatetimeIndex([])) == 252               # degenerate


def test_compute_metrics_annualises_an_hourly_book_at_its_own_spacing():
    """Same per-bar returns, two indexes: the hourly one must annualise at
    8766, i.e. sqrt(8766/252) ~ 5.9x the daily vol — with NO call-site change."""
    rng = np.random.default_rng(7)
    r = pd.Series(rng.normal(0.0002, 0.01, 500))
    eq = 10_000.0 * (1 + r).cumprod()
    hourly = pd.date_range("2026-01-01", periods=500, freq="h")
    daily = pd.date_range("2026-01-01", periods=500, freq="B")
    m_h = compute_metrics(r.set_axis(hourly), eq.set_axis(hourly))
    m_d = compute_metrics(r.set_axis(daily), eq.set_axis(daily))
    assert m_h["AnnVol"] == pytest.approx(m_d["AnnVol"] * math.sqrt(8766 / 252),
                                          rel=1e-3)
    # and an explicit convention still wins over the derived one
    m_forced = compute_metrics(r.set_axis(hourly), eq.set_axis(hourly),
                               periods_per_year=252)
    assert m_forced["AnnVol"] == pytest.approx(m_d["AnnVol"], rel=1e-9)


def test_compute_metrics_keys_are_unchanged():
    """No consumer breaks: the dict keys are frozen by this test."""
    rets = pd.Series([0.01, -0.004, 0.002, 0.006, -0.001],
                     index=pd.bdate_range("2026-01-05", periods=5))
    equity = 100.0 * (1 + rets).cumprod()
    m = compute_metrics(rets, equity)
    assert set(m) == {"CAGR", "AnnVol", "Sharpe (vs 3.5%)", "Sortino",
                      "MaxDrawdown", "Calmar", "WinRate(days)",
                      "FinalEquity (AUD)"}


def test_measure_label_names_the_convention():
    """A consumer cannot print a Sharpe without saying WHICH Sharpe."""
    rets = pd.Series([0.01, -0.004, 0.002, 0.006, -0.001],
                     index=pd.bdate_range("2026-01-05", periods=5))
    assert sharpe(rets, periods_per_year=252,
                  risk_free=0.035).label() == "Sharpe (vs 3.5%, 252/yr)"
    assert sharpe(rets, periods_per_year=8766.0,
                  risk_free=None).label() == "Sharpe (8766/yr)"
    equity = 100.0 * (1 + rets).cumprod()
    assert cagr(equity, periods_per_year=252).label() == "CAGR (252/yr)"
    assert max_drawdown(equity).label() == "MaxDrawdown"   # not annualised


def test_primitives_agree_with_compute_metrics():
    """The dict is assembled FROM the primitives — not a parallel formula."""
    rets = pd.Series([0.01, -0.004, 0.002, 0.006, -0.001],
                     index=pd.bdate_range("2026-01-05", periods=5))
    equity = 100.0 * (1 + rets).cumprod()
    m = compute_metrics(rets, equity)
    assert m["AnnVol"] == round(annualised_vol(rets, periods_per_year=252), 4)
    assert m["Sharpe (vs 3.5%)"] == round(
        sharpe(rets, periods_per_year=252, risk_free=0.035).value, 2)
    assert m["MaxDrawdown"] == round(max_drawdown(equity).value, 4)
    assert m["CAGR"] == round(cagr(equity, periods_per_year=252).value, 4)
    assert isinstance(sharpe(rets, periods_per_year=252, risk_free=0.035), Measure)
    assert ANNUALIZATION == 252


def test_marks_still_re_exports_periods_per_year():
    """Moving the function must not break the FX callers that import it."""
    from trading_algo.forex import marks
    assert marks.periods_per_year is periods_per_year


def test_benchmark_stats_annualises_at_the_bar_spacing():
    """The last hardcoded 252 inside the semantic layer's own module: an
    hourly pair must not be annualised as if its bars were days."""
    rng = np.random.default_rng(11)
    s = pd.Series(rng.normal(0.0003, 0.008, 300))
    b = pd.Series(rng.normal(0.0002, 0.007, 300))
    hourly = pd.date_range("2026-01-01", periods=300, freq="h")
    daily = pd.date_range("2026-01-01", periods=300, freq="B")
    st_h = benchmark_stats(s.set_axis(hourly), b.set_axis(hourly))
    st_d = benchmark_stats(s.set_axis(daily), b.set_axis(daily))
    assert st_h["TrackingError"] == pytest.approx(
        st_d["TrackingError"] * math.sqrt(8766 / 252), rel=1e-3)
    # …and an explicit convention still wins over the derived one
    forced = benchmark_stats(s.set_axis(hourly), b.set_axis(hourly),
                             periods_per_year=252)
    assert forced["TrackingError"] == pytest.approx(st_d["TrackingError"], rel=1e-9)


def test_benchmark_stats_keys_are_unchanged():
    """No consumer breaks: the dict keys are frozen by this test."""
    rng = np.random.default_rng(12)
    idx = pd.bdate_range("2026-01-05", periods=60)
    s = pd.Series(rng.normal(0.0004, 0.01, 60), index=idx)
    b = pd.Series(rng.normal(0.0003, 0.009, 60), index=idx)
    assert set(benchmark_stats(s, b)) == {
        "BenchmarkCAGR", "ActiveReturn", "Beta", "Alpha",
        "TrackingError", "InfoRatio"}


def test_benchmark_cagr_is_the_same_measure_as_compute_metrics_cagr():
    """`portfolio_backtest` returns `benchmark_metrics` and `benchmark_stats`
    in ONE dict, so the benchmark's CAGR appears twice. Two formulas made them
    disagree on short panels (-8.87% vs -9.26% over a year); one primitive
    makes them the same number."""
    rng = np.random.default_rng(13)
    idx = pd.bdate_range("2026-01-05", periods=252)
    s = pd.Series(rng.normal(0.0004, 0.01, 252), index=idx)
    b = pd.Series(rng.normal(0.0003, 0.009, 252), index=idx)
    bench_equity = 1000.0 * (1 + b).cumprod()
    assert (benchmark_stats(s, b)["BenchmarkCAGR"]
            == compute_metrics(b, bench_equity)["CAGR"])


def test_compute_metrics_accepts_both_legitimate_shapes():
    """`cagr` keys its exponent off len(equity) while the returns come from
    `rets` — a coupling nothing enforced. Two pairings are legitimate and must
    stay silent: N marks against N returns (backtest.py's `equity[1:]`), and
    N+1 marks against the N returns `equity.pct_change().dropna()` yields."""
    dates = pd.bdate_range("2026-01-05", periods=6)
    r = np.array([0.0, 0.01, -0.004, 0.002, 0.006, -0.001])
    equity = pd.Series(100.0 * np.cumprod(1 + r), index=dates)
    ret_series = equity.pct_change().dropna()
    assert len(ret_series) == 5 and len(equity) == 6
    assert "error" not in compute_metrics(ret_series, equity.iloc[1:])   # N : N
    assert "error" not in compute_metrics(ret_series, equity)            # N+1 : N


def test_compute_metrics_rejects_an_unalignable_pair():
    """Any other pairing silently rescales the CAGR exponent, so it is a loud
    caller error rather than a wrong number nobody sees."""
    rets = pd.Series([0.01, -0.004, 0.002, 0.006, -0.001],
                     index=pd.bdate_range("2026-01-05", periods=5))
    equity = 100.0 * (1 + rets).cumprod()
    with pytest.raises(ValueError, match=r"equity has 5 marks .*rets has 3"):
        compute_metrics(rets.iloc[2:], equity)
    # …and equity SHORTER than rets is never legitimate either.
    with pytest.raises(ValueError, match=r"equity has 3 marks .*rets has 5"):
        compute_metrics(rets, equity.iloc[2:])


def test_compute_metrics_counts_returns_after_dropna():
    """The guard must measure `rets` as `cagr` will see it — post-dropna —
    or a NaN-padded series slips past and moves the exponent."""
    dates = pd.bdate_range("2026-01-05", periods=6)
    r = np.array([0.0, 0.01, -0.004, 0.002, 0.006, -0.001])
    equity = pd.Series(100.0 * np.cumprod(1 + r), index=dates)
    padded = equity.pct_change()                 # 6 values, the first NaN
    assert len(padded) == 6 and padded.isna().sum() == 1
    with pytest.raises(ValueError, match=r"after dropna"):
        compute_metrics(padded, equity.iloc[:4])
