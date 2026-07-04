"""Predictive-model pipeline: dataset assembly, PURGED walk-forward, baseline model.

Ties `features` + `labels` into an honest out-of-sample predictor:

  dataset  →  purged/embargoed walk-forward  →  cross-sectional model  →  OOS scores  →  backtest

The one thing that separates real ML research from self-deception is leakage control,
so it is enforced here, not left to the caller:
- samples are taken at rebalance (month-end) dates → non-overlapping label windows;
- each walk-forward fold trains only on data strictly before the test block, with an
  EMBARGO of one horizon so a training label's look-ahead can't touch the test period;
- the model is fit ONLY on training rows and scored on held-out rows.

Baseline model is a dependency-light cross-sectional **ridge** (closed form, pure
NumPy) — swap in a GBM/NN later; the pipeline is model-agnostic. On price-only
features this is expected to land near the existing book (see PREDICTIVE_MODEL.md);
its value is a validated pipeline that's ready the moment real data is added.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import features as feat
from . import labels as lab
from .config import DEFAULT_PARAMS, StrategyParams

_PPY = 12  # monthly rebalance → periods per year


def build_dataset(prices: pd.DataFrame, index_prices: pd.Series,
                  p: StrategyParams = DEFAULT_PARAMS, horizon: int = 21,
                  rebalance: str = "ME") -> pd.DataFrame:
    """Aligned (features + forward-return label) panel, sampled at rebalance dates so
    label windows don't overlap. Index (date, ticker); columns = FEATURES + 'fwd_ret'."""
    X = feat.build_feature_panel(prices, index_prices, p)
    y = lab.forward_return(prices, horizon)
    df = X.join(y, how="inner").dropna()
    # last trading date of each rebalance period (as-of dates)
    asof = pd.Series(prices.index, index=prices.index).resample(rebalance).max().dropna()
    asof = pd.DatetimeIndex(pd.to_datetime(asof.values))
    return df[df.index.get_level_values("date").isin(asof)]


def purged_walk_forward(dates, n_folds: int = 5, embargo: int = 1) -> list[tuple]:
    """Expanding-window walk-forward splits with a purge/embargo.

    Returns [(train_dates, test_dates), ...]. Train is everything strictly before the
    test block MINUS the last `embargo` as-of dates (whose forward labels overlap the
    test period). This is the leakage guard — without it the model peeks."""
    dates = pd.DatetimeIndex(sorted(pd.unique(pd.to_datetime(dates))))
    n = len(dates)
    if n < n_folds + 2:
        return []
    step = n // (n_folds + 1)
    splits = []
    for i in range(1, n_folds + 1):
        ts, te = step * i, (step * (i + 1) if i < n_folds else n)
        test_dates = dates[ts:te]
        train_dates = dates[:ts]
        if embargo and len(train_dates) > embargo:
            train_dates = train_dates[:-embargo]     # purge the overlapping tail
        if len(train_dates) and len(test_dates):
            splits.append((train_dates, test_dates))
    return splits


def cross_sectional_ridge(X: np.ndarray, y: np.ndarray, alpha: float = 1.0) -> np.ndarray:
    """Closed-form ridge weights: (XᵀX + αI)⁻¹ Xᵀy. Features are pre-standardised, so
    no intercept is needed. Pure NumPy — no framework dependency."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    A = X.T @ X + alpha * np.eye(X.shape[1])
    return np.linalg.solve(A, X.T @ y)


def fit_predict_walk_forward(df: pd.DataFrame, n_folds: int = 5, embargo: int = 1,
                             alpha: float = 1.0) -> pd.Series:
    """Out-of-sample predicted scores per (date, ticker): for each fold, fit the ridge
    on the purged training rows and score the held-out test rows. Concatenated OOS."""
    dates = df.index.get_level_values("date")
    preds = []
    for train_dates, test_dates in purged_walk_forward(pd.unique(dates), n_folds, embargo):
        tr = df[dates.isin(train_dates)]
        te = df[dates.isin(test_dates)]
        if tr.empty or te.empty:
            continue
        w = cross_sectional_ridge(tr[feat.FEATURES].to_numpy(), tr["fwd_ret"].to_numpy(), alpha)
        score = te[feat.FEATURES].to_numpy() @ w
        preds.append(pd.Series(score, index=te.index, name="score"))
    return pd.concat(preds) if preds else pd.Series(dtype=float, name="score")


def run_ml_backtest(prices: pd.DataFrame, index_prices: pd.Series,
                    p: StrategyParams = DEFAULT_PARAMS, horizon: int = 21,
                    rebalance: str = "ME", top_n: int = 20, n_folds: int = 5,
                    embargo: int = 1, alpha: float = 1.0, cost_bps: float = 10.0) -> dict:
    """Walk-forward backtest of the ridge predictor: each as-of date, long the top-N
    names by OOS score (equal weight), realise the next-period return, charge turnover
    cost. Returns a non-overlapping per-period return series + the OOS scores."""
    df = build_dataset(prices, index_prices, p, horizon, rebalance)
    scores = fit_predict_walk_forward(df, n_folds, embargo, alpha)
    if scores.empty:
        return {"returns": pd.Series(dtype=float), "scores": scores, "n_periods": 0}
    fwd = df["fwd_ret"]
    rows, prev = {}, set()
    for date, grp in scores.groupby(level="date"):
        picks = list(grp.nlargest(min(top_n, len(grp))).index.get_level_values("ticker"))
        realised = np.nanmean([fwd.get((date, t), np.nan) for t in picks]) if picks else 0.0
        turnover = len(set(picks) ^ prev) / max(len(picks), 1)      # symmetric-diff fraction
        rows[date] = (realised if realised == realised else 0.0) - turnover * cost_bps / 1e4
        prev = set(picks)
    r = pd.Series(rows).sort_index()
    return {"returns": r, "scores": scores, "n_periods": len(r),
            "metrics": summarise(r)}


def summarise(r: pd.Series) -> dict:
    """Annualised stats for a monthly return series."""
    r = r.dropna()
    if len(r) < 2:
        return {k: float("nan") for k in ("CAGR", "Vol", "Sharpe", "hit_rate")}
    eq = (1 + r).cumprod()
    cagr = eq.iloc[-1] ** (_PPY / len(r)) - 1
    vol = r.std() * np.sqrt(_PPY)
    return {"CAGR": float(cagr), "Vol": float(vol),
            "Sharpe": float(r.mean() / r.std() * np.sqrt(_PPY)) if r.std() else float("nan"),
            "hit_rate": float((r > 0).mean())}
