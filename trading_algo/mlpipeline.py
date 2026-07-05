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


# ---------------------------------------------------------------------------
# Nonlinear learner — gradient-boosted regression trees (pure NumPy, deterministic)
# ---------------------------------------------------------------------------
# The pooled ridge is a weak LINEAR extractor; a ~0 linear increment bounds only that
# model, not a nonlinear/interaction PEAD or tone effect. This GBRT is the nonlinear
# hypothesis test — hand-written NumPy so the zero-heavy-dependency invariant holds. Its
# hyperparameters are PRE-REGISTERED (ESL/Friedman defaults, never swept on this sample), so
# the model is ONE extra trial, not a grid — the anti-overfit cost is paid by pre-registration.
GBRT_PARAMS = {"n_rounds": 200, "learning_rate": 0.05, "max_depth": 3, "min_leaf": 20}
GBRT_BINS = 32   # features are cross-sectionally z-scored & clipped to ±3, so fixed [-3,3]
                 # bins make split search O(p·bins) instead of O(p·n·log n) — scales to the
                 # full PIT universe in CI while staying exact for the z-scored feature grid.


def _bin_features(X: np.ndarray, n_bins: int = GBRT_BINS) -> np.ndarray:
    """Map z-scored features (clipped ~±3) to integer bins once → histogram split search."""
    return np.clip(((np.asarray(X, float) + 3.0) / 6.0 * n_bins).astype(np.int64), 0, n_bins - 1)


def _bin_edge(b: int, n_bins: int = GBRT_BINS) -> float:
    """Real-valued upper edge of bin b on the z-score scale (the stored split threshold)."""
    return -3.0 + (b + 1) / n_bins * 6.0


def _best_split(Xb: np.ndarray, resid: np.ndarray, min_leaf: int, n_bins: int = GBRT_BINS):
    """Greedy MSE split on BINNED features: maximise Sₗ²/kₗ + Sᵣ²/kᵣ via per-bin residual
    histograms (bincount) + a prefix scan over bins. Returns (gain, feature, bin) or None."""
    n, p = Xb.shape
    S_tot = resid.sum()
    parent = S_tot * S_tot / n
    best = None
    for j in range(p):
        sums = np.bincount(Xb[:, j], weights=resid, minlength=n_bins)
        cnts = np.bincount(Xb[:, j], minlength=n_bins).astype(float)
        SL = np.cumsum(sums)[:-1]            # residual sum in bins <= b, b = 0..n_bins-2
        kL = np.cumsum(cnts)[:-1]
        kR = n - kL
        with np.errstate(invalid="ignore", divide="ignore"):
            gain = SL * SL / kL + (S_tot - SL) ** 2 / kR - parent
        gain = np.where((kL >= min_leaf) & (kR >= min_leaf), gain, -np.inf)
        b = int(np.argmax(gain))
        if gain[b] > -np.inf and (best is None or gain[b] > best[0]):
            best = (float(gain[b]), j, b)
    return best


def _fit_tree(Xb: np.ndarray, resid: np.ndarray, max_depth: int, min_leaf: int, depth: int = 0) -> dict:
    """Greedy regression tree on the current pseudo-residuals (binned features). Leaf =
    mean(resid); split stores the real-valued bin-edge threshold so prediction uses raw X."""
    node_val = float(resid.mean()) if len(resid) else 0.0
    if depth >= max_depth or len(resid) < 2 * min_leaf:
        return {"leaf": True, "value": node_val}
    split = _best_split(Xb, resid, min_leaf)
    if split is None:
        return {"leaf": True, "value": node_val}
    _, j, b = split
    left = Xb[:, j] <= b                     # bin <= b  ⟺  raw feature <= edge(b)
    if left.sum() < min_leaf or (~left).sum() < min_leaf:
        return {"leaf": True, "value": node_val}
    return {"leaf": False, "feat": j, "thr": _bin_edge(b),
            "left": _fit_tree(Xb[left], resid[left], max_depth, min_leaf, depth + 1),
            "right": _fit_tree(Xb[~left], resid[~left], max_depth, min_leaf, depth + 1)}


def _predict_tree(tree: dict, X: np.ndarray) -> np.ndarray:
    """Vectorised per-row descent to the leaf value."""
    out = np.empty(len(X))

    def rec(node, idx):
        if node["leaf"]:
            out[idx] = node["value"]
            return
        go_left = X[idx, node["feat"]] <= node["thr"]
        rec(node["left"], idx[go_left])
        rec(node["right"], idx[~go_left])

    rec(tree, np.arange(len(X)))
    return out


def gradient_boost(X: np.ndarray, y: np.ndarray, n_rounds: int = 200,
                   learning_rate: float = 0.05, max_depth: int = 3, min_leaf: int = 20) -> dict:
    """Additive GBRT for squared-error loss (pseudo-residual = y − F). Deterministic
    (subsample=1.0, no RNG → no seed knob). Returns {base, trees, lr}."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    Xb = _bin_features(X)                     # bin once; every round splits on the histogram
    base = float(y.mean())
    F = np.full(len(y), base)
    trees = []
    for _ in range(n_rounds):
        tree = _fit_tree(Xb, y - F, max_depth, min_leaf)
        F = F + learning_rate * _predict_tree(tree, X)
        trees.append(tree)
    return {"base": base, "trees": trees, "lr": learning_rate}


def gbrt_predict(model: dict, X: np.ndarray) -> np.ndarray:
    """Score rows: base + lr·Σ tree predictions — the nonlinear analogue of te @ w."""
    X = np.asarray(X, float)
    out = np.full(len(X), model["base"])
    for tree in model["trees"]:
        out = out + model["lr"] * _predict_tree(tree, X)
    return out


def _fit_predict(cols: list[str], tr: pd.DataFrame, te: pd.DataFrame,
                 model: str = "ridge", alpha: float = 1.0, gbrt: dict | None = None) -> np.ndarray:
    """The ONE fit/predict dispatch both estimators pass through inside the fold loop —
    keeps a single score path (invariant #3). `gbrt` overrides the pre-registered GBRT_PARAMS."""
    Xtr, ytr, Xte = tr[cols].to_numpy(), tr[LABEL].to_numpy(), te[cols].to_numpy()
    if model == "gbrt":
        return gbrt_predict(gradient_boost(Xtr, ytr, **(gbrt or GBRT_PARAMS)), Xte)
    return Xte @ cross_sectional_ridge(Xtr, ytr, alpha)


def fit_predict_walk_forward(df: pd.DataFrame, n_folds: int = 5, embargo: int = 1,
                             alpha: float = 1.0, model: str = "ridge",
                             gbrt: dict | None = None) -> pd.Series:
    """Out-of-sample predicted scores per (date, ticker): for each fold, fit `model`
    ('ridge' or 'gbrt') on the purged training rows and score the held-out test rows.
    GBRT reuses the identical purged/embargoed splits and the single `_fit_predict` path,
    so every leakage guard is preserved — only the estimator swaps."""
    dates = df.index.get_level_values("date")
    cols = feature_cols(df)
    preds = []
    for train_dates, test_dates in purged_walk_forward(pd.unique(dates), n_folds, embargo):
        tr = df[dates.isin(train_dates)]
        te = df[dates.isin(test_dates)]
        if tr.empty or te.empty:
            continue
        score = _fit_predict(cols, tr, te, model, alpha, gbrt)
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
                    ls_frac: float = 0.2, target_vol: float = 0.10,
                    model: str = "ridge", gbrt: dict | None = None) -> dict:
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
    scores = fit_predict_walk_forward(train_df, n_folds, embargo, alpha, model, gbrt)
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


def covered_sub_universe(df: pd.DataFrame, min_names: int = 5) -> pd.Series:
    """Survivor-conditioned coverage mask (has_sentiment==1) as a labelled bool Series for
    `partial_incremental_ic(sub_universe=...)`. CORROBORATION ONLY: it restricts sentiment
    scoring to GDELT-covered (current-filer) names, so its IC is survivor-conditioned and must
    NEVER drive the pass gate — only the display row and the forward monitor's sent_* fields.
    (min_names is advisory; degenerate dates are dropped downstream by partial_incremental_ic.)"""
    if "has_sentiment" not in df.columns:
        return pd.Series(True, index=df.index)
    return df["has_sentiment"] == 1


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
