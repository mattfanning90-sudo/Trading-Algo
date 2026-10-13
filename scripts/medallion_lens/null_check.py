"""Second null for the IC / hit / spread: pair each month's scores with a
RANDOM OTHER month's returns (same names). Keeps the factor structure of
both scores and returns, breaks only the time link. Compare to the
within-month signal shuffle, which destroys the factor structure."""
import sys
import warnings
import numpy as np
import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measure_lens import decision_dates, next_bar, rank, corr, nw_t, MIN_NAMES, TOP_N
from trading_algo import data, regions, config as cfg, signals as sig
warnings.filterwarnings("ignore")
rng = np.random.default_rng(7)

def month_stats(s, r):
    v = np.isfinite(s) & np.isfinite(r)
    if v.sum() < MIN_NAMES:
        return None
    s, r = s[v], r[v]
    top = np.argsort(-s)[:TOP_N]
    return corr(rank(s), rank(r)), (r[top] > np.median(r)).mean(), r[top].mean() - r.mean()

for key in ["US", "ASX", "FTSE", "TSX"]:
    reg = regions.get_region(key); p = reg.params
    px, ix = data.load_region(reg, cfg.START); px = px.dropna(how="all")
    score = sig.momentum_score(px, p)
    dd = decision_dates(px, p.rebalance); fd = next_bar(px.index, dd)
    keep = ~fd.isna(); dd, fd = dd[keep], fd[keep]
    fp = px.ffill(limit=5).reindex(fd).to_numpy()
    R = fp[1:] / fp[:-1] - 1; S = score.reindex(dd).to_numpy()[:-1]
    real = [month_stats(S[i], R[i]) for i in range(len(R))]
    idx = [i for i, x in enumerate(real) if x is not None]
    real = np.array([real[i] for i in idx])
    hit_t, _ = nw_t(real[:, 1] - 0.5, 3)
    # time-pairing null: months derangement, skip pairings within 12 months (signal horizon)
    null = []
    for _ in range(500):
        perm = rng.permutation(idx)
        out = []
        for i, j in zip(idx, perm):
            if abs(i - j) <= 12:
                continue
            m = month_stats(S[i], R[j])
            if m is not None:
                out.append(m)
        null.append(np.mean(out, axis=0))
    null = np.array(null)
    rm = real.mean(axis=0)
    print(f"{key:5} real IC {rm[0]:+.4f} hit {rm[1]:.3f} (NW t vs 0.5 {hit_t:.2f}) spread {rm[2]*100:.2f}%/m | "
          f"time-null mean IC {null[:,0].mean():+.4f} sd {null[:,0].std():.4f} p {np.mean(null[:,0]>=rm[0]):.3f} | "
          f"hit {null[:,1].mean():.3f} p {np.mean(null[:,1]>=rm[1]):.3f} | spread {null[:,2].mean()*100:+.2f} p {np.mean(null[:,2]>=rm[2]):.3f}")
