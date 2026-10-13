"""Citadel's 'allocate risk, not dollars' on our four sleeves.
Equal capital (today) vs inverse-vol (risk budget) sleeve weights, monthly,
using trailing 36m sleeve vol known at the time (causal). LOCAL-currency sleeve
returns (no FX translation) — indicative only; the AUD layer is not modelled."""
import warnings
import numpy as np
import pandas as pd
from trading_algo import backtest as bt, data, regions, config as cfg
warnings.filterwarnings("ignore")
rf = cfg.RISK_FREE / 12
M = {}
for k in ["US", "ASX", "FTSE", "TSX"]:
    r = regions.get_region(k); px, ix = data.load_region(r, cfg.START)
    res = bt.run_backtest(px, ix, r)
    M[k] = (1 + res["returns"].dropna()).resample("ME").prod() - 1
df = pd.DataFrame(M).dropna()
df = df[df.index >= "2013-04-30"]
vol = df.rolling(36, min_periods=24).std().shift(1)           # known before the month
w_iv = (1 / vol).div((1 / vol).sum(axis=1), axis=0)
valid = w_iv.dropna().index
ew = df.loc[valid].mean(axis=1); iv = (df.loc[valid] * w_iv.loc[valid]).sum(axis=1)
def sr(x): return (x - rf).mean() / x.std(ddof=1) * np.sqrt(12)
rng = np.random.default_rng(1); n = len(ew); diffs = []
a, b = (ew - rf).to_numpy(), (iv - rf).to_numpy()
for _ in range(5000):                       # stationary-ish block bootstrap, block 6
    idx = np.concatenate([np.arange(s, s + 6) % n for s in rng.integers(0, n, n // 6 + 1)])[:n]
    diffs.append(b[idx].mean()/b[idx].std(ddof=1)*np.sqrt(12) - a[idx].mean()/a[idx].std(ddof=1)*np.sqrt(12))
diffs = np.array(diffs)
print("months", n, valid[0].date(), "->", valid[-1].date())
print("avg inverse-vol weights:", w_iv.loc[valid].mean().round(3).to_dict())
print(f"equal-capital SR {sr(ew):.3f} vol {ew.std()*np.sqrt(12):.4f} | inverse-vol SR {sr(iv):.3f} vol {iv.std()*np.sqrt(12):.4f}")
print(f"dSR {sr(iv)-sr(ew):+.3f}  95% CI [{np.quantile(diffs,.025):+.3f}, {np.quantile(diffs,.975):+.3f}]  P(d>0) {np.mean(diffs>0):.3f}")
print("sleeve corr (local, monthly):"); print(df.corr().round(2))
