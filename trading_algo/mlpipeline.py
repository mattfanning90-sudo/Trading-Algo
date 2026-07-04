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
from .datasources import MASK_COLS

_PPY = 12  # monthly rebalance → periods per year


LABEL = "fwd_ret"


def feature_cols(df: pd.DataFrame) -> list[str]:
    """Feature columns in a dataset (everything except the label AND coverage masks) — so
    alt-data columns from `datasources` are picked up automatically, but a coverage
    indicator (`has_sentiment`) is NEVER fed to the model: GDELT coverage is a
    survivorship/recency proxy, kept only to sub-select the covered cross-section for
    evaluation, so the ridge must not fit it."""
    return [c for c in df.columns if c != LABEL and c not in MASK_COLS]


def build_dataset(prices: pd.DataFrame, index_prices: pd.Series,
                  p: StrategyParams = DEFAULT_PARAMS, horizon: int = 21,
                  rebalance: str = "ME", extra: pd.DataFrame | None = None) -> pd.DataFrame:
    """Aligned (features + forward-return label) panel, sampled at rebalance dates so
    label windows don't overlap. Index (date, ticker); columns = features + 'fwd_ret'.
    `extra` = an as-of-merged alt-data panel (see datasources.build_extra_panel)."""
    X = feat.build_feature_panel(prices, index_prices, p, extra=extra)
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
    cols = feature_cols(df)
    preds = []
    for train_dates, test_dates in purged_walk_forward(pd.unique(dates), n_folds, embargo):
        tr = df[dates.isin(train_dates)]
        te = df[dates.isin(test_dates)]
        if tr.empty or te.empty:
            continue
        w = cross_sectional_ridge(tr[cols].to_numpy(), tr[LABEL].to_numpy(), alpha)
        score = te[cols].to_numpy() @ w
        preds.append(pd.Series(score, index=te.index, name="score"))
    return pd.concat(preds) if preds else pd.Series(dtype=float, name="score")


def oos_ic(scores: pd.Series, fwd: pd.Series) -> float:
    """Mean cross-sectional rank IC: how well OOS scores order the ACTUAL forward
    returns. The honest skill measure — and the leakage detector: under label
    shuffling it must collapse to ~0, or the pipeline is peeking."""
    df = pd.concat([scores.rename("s"), fwd.rename("f")], axis=1).dropna()
    if df.empty:
        return float("nan")
    # Spearman = Pearson of ranks (avoids a scipy dependency)
    ic = df.groupby(level="date").apply(
        lambda g: g["s"].rank().corr(g["f"].rank()) if len(g) > 2 else np.nan)
    return float(ic.mean())


def run_ml_backtest(prices: pd.DataFrame, index_prices: pd.Series,
                    p: StrategyParams = DEFAULT_PARAMS, horizon: int = 21,
                    rebalance: str = "ME", top_n: int = 20, n_folds: int = 5,
                    embargo: int = 1, alpha: float = 1.0, cost_bps: float = 10.0,
                    extra: pd.DataFrame | None = None, shuffle_seed: int | None = None,
                    ls_frac: float = 0.2, target_vol: float = 0.10) -> dict:
    """Walk-forward backtest of the ridge predictor. Two books each as-of date:

    - **long-only** top-N (reference; dominated by beta/construction), and
    - **long/short** market-neutral: long the top `ls_frac`, short the bottom `ls_frac`,
      dollar-neutral. This strips market beta, so its Sharpe measures *predictive skill*
      — the honest number. The L/S series is vol-targeted to `target_vol` (Sharpe is
      unchanged; only the CAGR becomes comparable).

    `shuffle_seed` permutes TRAINING labels within each date (leakage null → IC ≈ 0)."""
    df = build_dataset(prices, index_prices, p, horizon, rebalance, extra=extra)
    if df.empty:
        return {"returns": pd.Series(dtype=float), "scores": df, "n_periods": 0}
    fwd = df[LABEL]
    train_df = df
    if shuffle_seed is not None:
        rng = np.random.default_rng(shuffle_seed)
        train_df = df.copy()
        train_df[LABEL] = (train_df.groupby(level="date")[LABEL]
                           .transform(lambda s: rng.permutation(s.to_numpy())))
    scores = fit_predict_walk_forward(train_df, n_folds, embargo, alpha)
    if scores.empty:
        return {"returns": pd.Series(dtype=float), "scores": scores, "n_periods": 0}

    def _leg(names, date):
        vals = fwd.reindex([(date, t) for t in names]).dropna()
        return float(vals.mean()) if len(vals) else 0.0

    lo_rows, ls_rows = {}, {}
    prev_lo, prev_l, prev_s = set(), set(), set()
    for date, grp in scores.groupby(level="date"):
        g = grp.droplevel("date")
        k = max(int(len(g) * ls_frac), 1)
        longs = list(g.nlargest(k).index)
        shorts = list(g.nsmallest(k).index)
        lo = list(g.nlargest(min(top_n, len(g))).index)
        # long-only (reference)
        to_lo = len(set(lo) ^ prev_lo) / max(len(lo), 1)
        lo_rows[date] = _leg(lo, date) - to_lo * cost_bps / 1e4
        prev_lo = set(lo)
        # long/short market-neutral (skill)
        to_ls = (len(set(longs) ^ prev_l) + len(set(shorts) ^ prev_s)) / max(2 * k, 1)
        ls_rows[date] = (_leg(longs, date) - _leg(shorts, date)) - to_ls * cost_bps / 1e4
        prev_l, prev_s = set(longs), set(shorts)

    lo = pd.Series(lo_rows).sort_index()
    ls = pd.Series(ls_rows).sort_index()
    # vol-target the L/S book (Sharpe-invariant; makes CAGR comparable)
    realised = ls.std() * np.sqrt(_PPY)
    ls_vt = ls * (target_vol / realised) if realised > 0 else ls
    return {"returns": lo, "ls_returns": ls_vt, "scores": scores, "n_periods": len(lo),
            "ic": oos_ic(scores, fwd), "metrics": summarise(lo), "ls_metrics": summarise(ls_vt)}


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


# ---------------------------------------------------------------------------
# Honest marginal-edge measurement (does alt-data add anything BEYOND price?)
# ---------------------------------------------------------------------------

def _rank_ic(a: np.ndarray, b: np.ndarray) -> float:
    """Cross-sectional rank IC = Spearman = Pearson of ranks (no scipy). NaN if either
    side has <3 points or zero variance."""
    sa, sb = pd.Series(np.asarray(a, float)), pd.Series(np.asarray(b, float))
    if len(sa) < 3 or sa.std() == 0 or sb.std() == 0:
        return float("nan")
    return float(sa.rank().corr(sb.rank()))


def partial_incremental_ic(df: pd.DataFrame, price_cols: list[str], alt_cols: list[str],
                           oos_dates=None, sub_universe: pd.Series | None = None,
                           min_names: int = 5) -> dict:
    """Price-residualised marginal IC of the alt columns — the honest "edge beyond price".

    On each date: cross-sectionally regress the forward-return label on the PRICE
    z-features → residual r_y; regress each alt column on the SAME price features → r_alt
    (pure-NumPy least squares, intercept included); take rank-IC(r_alt, r_y). Averaged
    over the OOS dates, reported PER alt column and as a combined block (rank-IC of the
    equal-weight sum of standardised alt residuals). A weak-but-real alt signal that a
    pooled ridge would shrink to ~0 becomes visible here because price is projected out
    first. `sub_universe` (bool Series on df.index) restricts scoring to covered names so
    a sparse feed is not diluted across zero-filled rows (survivor-conditioned → corroborating
    evidence only, never the sole basis of a claim)."""
    d = df
    if oos_dates is not None:
        d = d[d.index.get_level_values("date").isin(pd.DatetimeIndex(oos_dates))]
    if sub_universe is not None:
        keep = sub_universe.reindex(d.index).fillna(False).to_numpy().astype(bool)
        d = d[keep]
    alt_cols = [c for c in alt_cols if c in d.columns]
    price_cols = [c for c in price_cols if c in d.columns]
    per_col = {c: [] for c in alt_cols}
    block = []
    for _, g in d.groupby(level="date"):
        if len(g) < min_names or not alt_cols:
            continue
        Xp = np.column_stack([np.ones(len(g)), g[price_cols].to_numpy()])
        y = g[LABEL].to_numpy()
        try:
            by, *_ = np.linalg.lstsq(Xp, y, rcond=None)
        except np.linalg.LinAlgError:
            continue
        r_y = y - Xp @ by
        residuals = {}
        for c in alt_cols:
            a = g[c].to_numpy()
            ba, *_ = np.linalg.lstsq(Xp, a, rcond=None)
            r_a = a - Xp @ ba
            residuals[c] = r_a
            ic = _rank_ic(r_a, r_y)
            if not np.isnan(ic):
                per_col[c].append(ic)
        std_res = []
        for r_a in residuals.values():
            s = r_a.std()
            std_res.append(r_a / s if s > 0 else r_a * 0.0)
        if std_res:
            ic = _rank_ic(np.sum(std_res, axis=0), r_y)
            if not np.isnan(ic):
                block.append(ic)
    out_per = {c: (float(np.mean(v)) if v else float("nan")) for c, v in per_col.items()}
    return {"incremental_ic": float(np.mean(block)) if block else float("nan"),
            "per_col": out_per, "n_dates": len(block)}


def incremental_delta(base_res: dict, alt_res: dict, mean_block: int = 3,
                      n_paths: int = 2000, seed: int = 0) -> dict:
    """Nested price-only vs price+alt comparison with a bootstrap CI on the PAIRED
    difference — so we deflate the INCREMENT, not the alt book.

    delta_ic = alt IC − base IC. The paired monthly difference of the market-neutral L/S
    returns, d = ls(price+alt) − ls(price-only), is the increment's OWN return stream; its
    annualised information ratio plus a stationary-block-bootstrap CI (reusing
    stress.stationary_bootstrap) is the honest number. Returns `diff` so the caller can
    DSR-deflate exactly this difference series. Consumes two run_ml_backtest dicts — no new
    backtest loop, so the single fit/predict/book path is preserved."""
    from . import stress
    delta_ic = float(alt_res.get("ic", float("nan")) - base_res.get("ic", float("nan")))
    a = alt_res.get("ls_returns", pd.Series(dtype=float))
    b = base_res.get("ls_returns", pd.Series(dtype=float))
    idx = a.index.intersection(b.index)
    d = (a.reindex(idx) - b.reindex(idx)).dropna()
    if len(d) < 3 or d.std() == 0:
        return {"delta_ic": delta_ic, "delta_ir": float("nan"), "ci_low": float("nan"),
                "ci_high": float("nan"), "n": int(len(d)), "diff": d}
    ir = float(d.mean() / d.std() * np.sqrt(_PPY))
    paths = stress.stationary_bootstrap(d, mean_block=mean_block, n_paths=n_paths, seed=seed)
    with np.errstate(invalid="ignore", divide="ignore"):
        sr = paths.mean(axis=1) / paths.std(axis=1) * np.sqrt(_PPY)
    sr = sr[np.isfinite(sr)]
    lo, hi = ((float(np.percentile(sr, 5)), float(np.percentile(sr, 95)))
              if len(sr) else (float("nan"), float("nan")))
    return {"delta_ic": delta_ic, "delta_ir": ir, "ci_low": lo, "ci_high": hi,
            "n": int(len(d)), "diff": d}
