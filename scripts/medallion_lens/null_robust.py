import sys
import warnings
import numpy as np
import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measure_lens import decision_dates, next_bar, rank, corr, MIN_NAMES
from trading_algo import data, regions, config as cfg, signals as sig
warnings.filterwarnings("ignore")
rng = np.random.default_rng(11)
for key in ["US", "ASX", "FTSE", "TSX"]:
    reg = regions.get_region(key); p = reg.params
    px, ix = data.load_region(reg, cfg.START); px = px.dropna(how="all")
    score = sig.momentum_score(px, p)
    dd = decision_dates(px, p.rebalance); fd = next_bar(px.index, dd)
    keep = ~fd.isna(); dd, fd = dd[keep], fd[keep]
    fp = px.ffill(limit=5).reindex(fd).to_numpy()
    R = fp[1:] / fp[:-1] - 1; S = score.reindex(dd).to_numpy()[:-1]
    def ic(i, j):
        v = np.isfinite(S[i]) & np.isfinite(R[j])
        return corr(rank(S[i][v]), rank(R[j][v])) if v.sum() >= MIN_NAMES else np.nan
    real = np.nanmean([ic(i, i) for i in range(len(R))])
    out = []
    for gap in (12, 24, 36):
        null = []
        for _ in range(300):
            vals = []
            for i in range(len(R)):
                cand = [j for j in range(len(R)) if abs(i - j) > gap]
                vals.append(ic(i, rng.choice(cand)))
            null.append(np.nanmean(vals))
        null = np.array(null)
        out.append(f"gap>{gap}: null {null.mean():+.4f} p {np.mean(null >= real):.3f}")
    # "answer key": rank corr of each month's score with each name's full-sample mean monthly return
    mean_ret = np.nanmean(R, axis=0)
    ak = []
    for i in range(len(R)):
        v = np.isfinite(S[i]) & np.isfinite(mean_ret)
        if v.sum() >= MIN_NAMES:
            ak.append(corr(rank(S[i][v]), rank(mean_ret[v])))
    print(f"{key:5} real IC {real:+.4f} | " + " | ".join(out) + f" | score~full-sample-mean rank corr {np.mean(ak):+.3f}")
