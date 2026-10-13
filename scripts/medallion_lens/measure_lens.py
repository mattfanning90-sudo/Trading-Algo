"""Medallion / Citadel lens — measured on the REAL equity panel.

Evidence for docs/MEDALLION_CITADEL_RESEARCH.md. Reads prices through the repo's own loader
and signal functions so the numbers describe the signal the books trade.

Four questions:
  A. Signal quality per bet (Medallion's "50.75%"): monthly rank IC of 12-1
     momentum vs the next month's return, top-10 hit rate, top-10 spread, each
     against a SIGNAL-SHUFFLE null (the calibrated null for cross-sectional
     momentum, docs/PERMUTATION_TESTING.md §4).
  B. Breadth (Grinold's IR = IC * sqrt(BR)): naive vs effective independent
     bets per year, from the realised IC volatility.
  C. Decay: IC of the k-th month ahead, k = 1..12 (how long the signal lasts).
  D. Residual momentum (Citadel-style: rank on what is left after hedging the
     market) vs raw momentum, on the common sample.
  E. Beta / alpha of each sleeve's real backtest (costs on), against the price
     index and against an equal-weight basket of the sleeve's own universe.

Everything in A-D is a GROSS, PRE-COST, FILTER-FREE signal diagnostic, not a
strategy result (invariant #2). E uses the repo backtest with costs on.
All on today's constituents: survivorship-biased (no membership file exists).
"""
from __future__ import annotations

import json
import sys
import warnings

import numpy as np
import pandas as pd

from trading_algo import backtest as bt
from trading_algo import config as cfg
from trading_algo import data, regions
from trading_algo import signals as sig

warnings.filterwarnings("ignore")
REGION_KEYS = sys.argv[1:] or ["US", "ASX", "FTSE", "TSX"]
TOP_N = 10
N_SHUF = 500
MIN_NAMES = 20
RF_M = cfg.RISK_FREE / 12
rng = np.random.default_rng(20261013)


def decision_dates(prices: pd.DataFrame, rule: str) -> pd.DatetimeIndex:
    """Last trading day on or before each COMPLETED period end (as backtest.py)."""
    idx = prices.index
    marks = prices.resample(rule).last().index
    marks = marks[marks <= idx[-1]]
    out = [idx[idx.searchsorted(m, side="right") - 1] for m in marks]
    return pd.DatetimeIndex(sorted(set(out)))


def next_bar(idx: pd.DatetimeIndex, dd: pd.DatetimeIndex) -> pd.DatetimeIndex:
    pos = idx.searchsorted(dd, side="right")
    return pd.DatetimeIndex([idx[p] if p < len(idx) else pd.NaT for p in pos])


def rank(a: np.ndarray) -> np.ndarray:
    return pd.Series(a).rank().to_numpy()


def corr(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    d = np.sqrt((a @ a) * (b @ b))
    return float(a @ b / d) if d > 0 else np.nan


def nw_t(x, lag: int) -> tuple[float, int]:
    """Newey-West t-stat of the mean (Bartlett kernel)."""
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    n = len(x)
    e = x - x.mean()
    s = e @ e / n
    for L in range(1, lag + 1):
        s += 2 * (1 - L / (lag + 1)) * (e[L:] @ e[:-L]) / n
    return float(x.mean() / np.sqrt(s / n)), n


def ols(y: np.ndarray, x: np.ndarray):
    X = np.column_stack([np.ones_like(x), x])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    r2 = 1 - resid.var() / y.var()
    return coef, resid, r2


def xs_stats(S: np.ndarray, R: np.ndarray, top_n: int = TOP_N):
    """Per-month cross-sectional stats of score S[i] vs next-month return R[i]."""
    rows = []
    for i in range(len(R)):
        v = np.isfinite(S[i]) & np.isfinite(R[i])
        if v.sum() < MIN_NAMES:
            rows.append(None)
            continue
        s, r = S[i][v], R[i][v]
        order = np.argsort(-s)
        top, bot = order[:top_n], order[-top_n:]
        med = np.median(r)
        rows.append(dict(
            ic=corr(rank(s), rank(r)), n=int(v.sum()),
            hit=float((r[top] > med).mean()),
            spread=float(r[top].mean() - r.mean()),
            bspread=float(r[bot].mean() - r.mean()),
            top_ret=float(r[top].mean()), ew_ret=float(r.mean()),
            top_names=set(np.flatnonzero(v)[top])))
    return rows


def shuffle_null(S: np.ndarray, R: np.ndarray, n_shuf: int = N_SHUF):
    """Signal shuffle: permute scores across names within each month."""
    prepared = []
    for i in range(len(R)):
        v = np.isfinite(S[i]) & np.isfinite(R[i])
        if v.sum() < MIN_NAMES:
            continue
        r = R[i][v]
        prepared.append((rank(S[i][v]), rank(r), r, np.median(r)))
    ics, hits, spreads, ic_sds = [], [], [], []
    for _ in range(n_shuf):
        ic_m, hit_m, sp_m = [], [], []
        for rs, rr, r, med in prepared:
            perm = rng.permutation(rs)
            ic_m.append(corr(perm, rr))
            top = np.argsort(-perm)[:TOP_N]
            hit_m.append((r[top] > med).mean())
            sp_m.append(r[top].mean() - r.mean())
        ics.append(np.mean(ic_m))
        ic_sds.append(np.std(ic_m, ddof=1))
        hits.append(np.mean(hit_m))
        spreads.append(np.mean(sp_m))
    return dict(ic=np.array(ics), hit=np.array(hits), spread=np.array(spreads),
                ic_sd=np.array(ic_sds))


def summarise(rows, label: str, null=None) -> dict:
    ok = [r for r in rows if r is not None]
    ic = np.array([r["ic"] for r in ok])
    hit = np.array([r["hit"] for r in ok])
    spread = np.array([r["spread"] for r in ok])
    bspread = np.array([r["bspread"] for r in ok])
    n = np.array([r["n"] for r in ok])
    t_ic, months = nw_t(ic, 3)
    t_sp, _ = nw_t(spread, 3)
    ic_ir_ann = ic.mean() / ic.std(ddof=1) * np.sqrt(12)
    sp_ir_ann = spread.mean() / spread.std(ddof=1) * np.sqrt(12)
    turn = []
    for a, b in zip(ok[:-1], ok[1:]):
        turn.append(len(b["top_names"] - a["top_names"]) / TOP_N)
    out = dict(
        label=label, months=months, mean_names=float(n.mean()),
        ic_mean=float(ic.mean()), ic_sd=float(ic.std(ddof=1)), ic_t=t_ic,
        ic_pos_frac=float((ic > 0).mean()),
        hit_rate=float(hit.mean()),
        spread_mean_m=float(spread.mean()), spread_t=t_sp,
        bottom_spread_mean_m=float(bspread.mean()),
        ic_ir_ann=float(ic_ir_ann), spread_ir_ann=float(sp_ir_ann),
        naive_breadth_yr=float(n.mean() * 12),
        effective_breadth_yr=float(12 / ic.var(ddof=1)),
        top_turnover_m=float(np.mean(turn)) if turn else np.nan,
    )
    if null is not None:
        out.update(
            null_ic_mean=float(null["ic"].mean()),
            null_ic_p95=float(np.quantile(null["ic"], 0.95)),
            p_ic=float((null["ic"] >= ic.mean()).mean()),
            null_hit_mean=float(null["hit"].mean()),
            null_hit_p95=float(np.quantile(null["hit"], 0.95)),
            p_hit=float((null["hit"] >= hit.mean()).mean()),
            null_spread_mean=float(null["spread"].mean()),
            p_spread=float((null["spread"] >= spread.mean()).mean()),
            null_ic_sd=float(null["ic_sd"].mean()),
            null_effective_breadth_yr=float(12 / null["ic_sd"].mean() ** 2),
        )
    return out


def residual_scores(mret: np.ndarray, mkt: np.ndarray, i: int,
                    est: int = 36, form: int = 11) -> np.ndarray:
    """Blitz-Huij-Martens residual momentum at formation month i.

    Regress each name's monthly returns over months i-est+1..i on the
    equal-weight market; score = sum of residuals over months i-form..i-1
    (12-1: skip month i) divided by their standard deviation. Causal: uses
    returns of months ending on or before decision date i only.
    """
    lo = i - est + 1
    if lo < 1:
        return np.full(mret.shape[1], np.nan)
    out = np.full(mret.shape[1], np.nan)
    x = mkt[lo:i + 1]
    for n in range(mret.shape[1]):
        y = mret[lo:i + 1, n]
        v = np.isfinite(y) & np.isfinite(x)
        if v.sum() < 30:
            continue
        coef, *_ = np.linalg.lstsq(np.column_stack([np.ones(v.sum()), x[v]]),
                                   y[v], rcond=None)
        e = y - coef[0] - coef[1] * x          # residual incl. NaN where y NaN
        w = e[est - 1 - form: est - 1]         # months i-11 .. i-1
        if np.isfinite(w).sum() < 9:
            continue
        sd = np.nanstd(w, ddof=1)
        out[n] = np.nansum(w) / sd if sd > 0 else np.nan
    return out


def portfolio_diag(rows) -> dict:
    ok = [r for r in rows if r is not None]
    top = np.array([r["top_ret"] for r in ok])
    ew = np.array([r["ew_ret"] for r in ok])
    coef, resid, r2 = ols(top - RF_M, ew - RF_M)
    eq = np.cumprod(1 + top)
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return dict(top_mean_ann=float(top.mean() * 12),
                top_vol_ann=float(top.std(ddof=1) * np.sqrt(12)),
                top_sr_excess=float((top - RF_M).mean() / top.std(ddof=1) * np.sqrt(12)),
                top_maxdd=float(dd), beta_to_ew=float(coef[1]),
                alpha_vs_ew_ann=float(coef[0] * 12),
                worst_month=float(top.min()))


def sleeve_beta(key: str, px: pd.DataFrame, ix: pd.Series, r) -> dict:
    """Run the real backtest (costs on) and decompose its monthly return."""
    res = bt.run_backtest(px, ix, r)
    d = res["returns"].dropna()
    m = (1 + d).resample("ME").prod() - 1
    # start once the book has actually traded (weights is {date: Series})
    traded = [t for t, w in res["weights"].items() if float(w.abs().sum()) > 0]
    if traded:
        m = m[m.index >= min(traded)]
    idx_m = ix.ffill().resample("ME").last().pct_change()
    ew_d = px.pct_change(fill_method=None).mean(axis=1)
    ew_m = (1 + ew_d).resample("ME").prod() - 1
    out = {"months": int(len(m)), "start": str(m.index[0].date()),
           "sleeve_sr_excess": float((m - RF_M).mean() / m.std(ddof=1) * np.sqrt(12)),
           "sleeve_cagr": float((1 + m).prod() ** (12 / len(m)) - 1),
           "cost_fraction_total": float(res["total_cost_fraction"])}
    for name, mk in (("index_price", idx_m), ("ew_universe_tr", ew_m)):
        j = pd.concat([m, mk], axis=1, join="inner").dropna()
        y = j.iloc[:, 0].to_numpy() - RF_M
        x = j.iloc[:, 1].to_numpy() - RF_M
        coef, resid, r2 = ols(y, x)
        # NW t on alpha: t of mean of (y - beta x)
        t_a, _ = nw_t(y - coef[1] * x, 3)
        rv = resid.std(ddof=2) * np.sqrt(12)
        out[name] = dict(beta=float(coef[1]), alpha_ann=float(coef[0] * 12),
                         alpha_t=t_a, r2=float(r2), resid_vol_ann=float(rv),
                         appraisal=float(coef[0] * 12 / rv),
                         mkt_sr_excess=float(x.mean() / x.std(ddof=1) * np.sqrt(12)))
    return out


def main() -> None:
    report = {}
    for key in REGION_KEYS:
        r = regions.get_region(key)
        p = r.params
        px, ix = data.load_region(r, cfg.START)
        px = px.dropna(how="all")
        pxf = px.ffill(limit=5)
        score = sig.momentum_score(px, p)
        dd = decision_dates(px, p.rebalance)
        fd = next_bar(px.index, dd)
        keep = ~fd.isna()
        dd, fd = dd[keep], fd[keep]
        fp = pxf.reindex(fd).to_numpy()
        R1 = fp[1:] / fp[:-1] - 1                 # R1[i]: month held after decision i
        S = score.reindex(dd).to_numpy()[:-1]     # align with R1

        rows = xs_stats(S, R1)
        null = shuffle_null(S, R1)
        A = summarise(rows, "raw_12_1", null)

        # C. decay: IC of the k-th month ahead
        decay = {}
        for k in range(1, 13):
            ics = []
            for i in range(len(R1) - (k - 1)):
                Rk = R1[i + k - 1]
                v = np.isfinite(S[i]) & np.isfinite(Rk)
                if v.sum() >= MIN_NAMES:
                    ics.append(corr(rank(S[i][v]), rank(Rk[v])))
            t, n = nw_t(ics, 3)
            decay[k] = dict(ic=float(np.mean(ics)), t=t, months=n)

        # D. residual momentum on the common sample
        mp = pxf.reindex(dd).to_numpy()
        mret = np.vstack([np.full(mp.shape[1], np.nan), mp[1:] / mp[:-1] - 1])
        mkt = np.nanmean(mret, axis=1)
        RS = np.vstack([residual_scores(mret, mkt, i) for i in range(len(dd) - 1)])
        common = np.array([np.isfinite(RS[i]).sum() >= MIN_NAMES for i in range(len(RS))])
        S_c = np.where(common[:, None], S, np.nan)
        RS_c = np.where(common[:, None], RS, np.nan)
        rows_raw_c = xs_stats(S_c, R1)
        rows_res_c = xs_stats(RS_c, R1)
        null_res = shuffle_null(RS_c, R1, n_shuf=200)
        rank_corr = []
        for i in np.flatnonzero(common):
            v = np.isfinite(S[i]) & np.isfinite(RS[i])
            rank_corr.append(corr(rank(S[i][v]), rank(RS[i][v])))
        D = dict(raw=summarise(rows_raw_c, "raw_common"),
                 residual=summarise(rows_res_c, "residual_common", null_res),
                 raw_port=portfolio_diag(rows_raw_c),
                 residual_port=portfolio_diag(rows_res_c),
                 mean_rank_corr_raw_vs_residual=float(np.mean(rank_corr)))

        E = sleeve_beta(key, px, ix, r)
        report[key] = dict(A=A, raw_port=portfolio_diag(rows), decay=decay, D=D, E=E,
                           fees=dict(commission_bps=r.commission_bps,
                                     slippage_bps=r.slippage_bps,
                                     stamp_duty_bps=r.stamp_duty_bps,
                                     min_commission=r.min_commission))
        print(json.dumps({key: report[key]}, indent=1, default=float), flush=True)
    with open(sys.path[0] + "/lens_results.json", "w") as f:
        json.dump(report, f, indent=1, default=float)


if __name__ == "__main__":
    main()
