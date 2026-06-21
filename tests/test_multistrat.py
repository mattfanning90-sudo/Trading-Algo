"""Multi-strategy combiner: risk-based weights, vol targeting, diversification,
and upside/downside capture."""
import numpy as np
import pandas as pd

from trading_algo import multistrat as ms


def _stream(n, mu, sd, seed):
    idx = pd.bdate_range("2010-01-01", periods=n)
    return pd.Series(np.random.default_rng(seed).normal(mu, sd, n), index=idx)


def test_inverse_vol_weights():
    w = ms.inverse_vol_weights(pd.Series({"A": 0.10, "B": 0.20}))
    assert abs(w.sum() - 1.0) < 1e-9
    assert w["A"] > w["B"]                      # lower vol → higher weight
    assert abs(w["A"] - 2 * w["B"]) < 1e-9      # exactly 2:1 for 0.10 vs 0.20


def test_risk_parity_equalises_risk_contributions():
    # uncorrelated assets with different vols → ERC equalises risk contributions
    cov = pd.DataFrame(np.diag([0.04, 0.01, 0.0025]),
                       index=["A", "B", "C"], columns=["A", "B", "C"])
    w = ms.risk_parity_weights(cov)
    rc = w.values * (cov.to_numpy() @ w.values)
    pct = rc / rc.sum()
    assert np.allclose(pct, 1 / 3, atol=0.02)   # each ~33% of risk
    # for uncorrelated assets ERC == inverse-vol
    iv = ms.inverse_vol_weights(pd.Series(np.sqrt(np.diag(cov)), index=cov.index))
    assert np.allclose(w.values, iv.values, atol=0.02)


def test_combine_respects_leverage_and_runs():
    streams = {"a": _stream(900, 0.0004, 0.01, 1),
               "b": _stream(900, 0.0003, 0.02, 2)}
    out = ms.combine(streams, target_vol=0.10, method="invvol", max_leverage=1.5)
    assert len(out["returns"]) > 0
    assert out["gross"].max() <= 1.5 + 1e-9     # leverage cap respected


def test_combine_diversification_lifts_sharpe():
    # two uncorrelated streams with the SAME strong positive drift → combining
    # beats the average standalone Sharpe (the √N diversification benefit)
    a = _stream(2520, 0.0008, 0.008, 10)
    b = _stream(2520, 0.0008, 0.008, 99)        # independent, same expected Sharpe
    def sharpe(r):
        r = r.dropna()
        return r.mean() / r.std() * np.sqrt(252)
    sa, sb = sharpe(a), sharpe(b)
    assert sa > 0 and sb > 0                     # strong signal → both realised positive
    combined = sharpe(ms.combine({"a": a, "b": b}, method="erc")["returns"])
    assert combined > 0.5 * (sa + sb)           # strictly above the average → diversified


def test_combine_no_lookahead_weights_shifted():
    streams = {"a": _stream(800, 0.0004, 0.01, 3), "b": _stream(800, 0.0002, 0.015, 4)}
    out = ms.combine(streams)
    # a day's return uses weights decided strictly before it (shift(1)) → the first
    # weighted day has zero weight carried in
    w = out["weights"]
    assert (w.iloc[0].abs().sum() == 0) or np.isnan(w.iloc[0].abs().sum())


def test_capture_ratios_asymmetry():
    # one move per calendar month so monthly resampling recovers the construction:
    # full upside, half the downside → up_capture 1, down_capture 0.5, ratio 2
    rng = np.random.default_rng(7)
    months = rng.normal(0.0, 0.05, 30)
    starts = pd.date_range("2015-01-01", periods=len(months), freq="MS")
    idx = pd.bdate_range(starts[0], starts[-1] + pd.offsets.MonthEnd(1), freq="B")
    bench = pd.Series(0.0, index=idx)
    strat = pd.Series(0.0, index=idx)
    for s, v in zip(starts, months):
        i = bench.index.searchsorted(s)
        bench.iloc[i] = v
        strat.iloc[i] = v if v > 0 else v * 0.5      # full up, half down
    cap = ms.capture_ratios(strat, bench)
    assert cap["down_capture"] < cap["up_capture"]
    assert cap["capture_ratio"] > 1.0           # asymmetric: takes upside, mitigates downside
