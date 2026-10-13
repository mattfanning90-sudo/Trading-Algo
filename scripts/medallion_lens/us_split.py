import sys
import warnings
import numpy as np
import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measure_lens import decision_dates, next_bar, rank, corr, nw_t, MIN_NAMES, TOP_N
from trading_algo import data, regions, config as cfg, signals as sig, universes
warnings.filterwarnings("ignore")
rng = np.random.default_rng(5)
reg = regions.get_region("US"); p = reg.params
px, ix = data.load_region(reg, cfg.START); px = px.dropna(how="all")
etfs = [t for t in universes.US_ETFS if t in px.columns]
for label, cols in (("stocks only", [c for c in px.columns if c not in etfs]), ("all (as traded)", list(px.columns))):
    sub = px[cols]
    score = sig.momentum_score(sub, p)
    dd = decision_dates(sub, p.rebalance); fd = next_bar(sub.index, dd)
    keep = ~fd.isna(); dd, fd = dd[keep], fd[keep]
    fp = sub.ffill(limit=5).reindex(fd).to_numpy()
    R = fp[1:] / fp[:-1] - 1; S = score.reindex(dd).to_numpy()[:-1]
    def st(i, j):
        v = np.isfinite(S[i]) & np.isfinite(R[j])
        if v.sum() < MIN_NAMES:
            return None
        s, r = S[i][v], R[j][v]; top = np.argsort(-s)[:TOP_N]
        return corr(rank(s), rank(r)), r[top].mean() - r.mean()
    real = np.array([x for x in (st(i, i) for i in range(len(R))) if x is not None])
    t_ic, _ = nw_t(real[:, 0], 3); t_sp, _ = nw_t(real[:, 1], 3)
    null = []
    for _ in range(400):
        vals = [st(i, rng.choice([j for j in range(len(R)) if abs(i - j) > 12])) for i in range(len(R))]
        null.append(np.mean([v for v in vals if v is not None], axis=0))
    null = np.array(null)
    print(f"{label:16} n={len(cols)} IC {real[:,0].mean():+.4f} (t {t_ic:.2f}) spread {real[:,1].mean()*100:.2f}%/m (t {t_sp:.2f}) | "
          f"time-null IC {null[:,0].mean():+.4f} p {np.mean(null[:,0]>=real[:,0].mean()):.3f} spread {null[:,1].mean()*100:+.2f} p {np.mean(null[:,1]>=real[:,1].mean()):.3f}")
# what share of US top-10 picks were ETFs, and which kinds
score = sig.momentum_score(px, p); dd = decision_dates(px, p.rebalance)
share = [np.isin(score.loc[d].dropna().nlargest(10).index, etfs).mean() for d in dd if score.loc[d].notna().sum() >= 20]
print("ETFs in universe:", len(etfs), "| mean share of pure top-10 that are ETFs:", round(float(np.mean(share)), 3))
