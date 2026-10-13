"""Daily vs lead-lag-adjusted daily vs weekly vs monthly cross-sleeve correlation,
same sample, LOCAL currency. Tests whether time-zone asynchrony distorts the
daily matrix in docs/SHARPE_RESEARCH.md §6."""
import warnings
import pandas as pd
from trading_algo import backtest as bt, data, regions, config as cfg
warnings.filterwarnings("ignore")
D = {}
for k in ["US", "ASX", "FTSE", "TSX"]:
    r = regions.get_region(k); px, ix = data.load_region(r, cfg.START)
    D[k] = bt.run_backtest(px, ix, r)["returns"]
d = pd.DataFrame(D)
d.index = pd.to_datetime(d.index)
d = d[(d.index >= "2013-05-01") & (d.index <= "2026-09-30")]
# calendar-day union: a sleeve with no session that day contributes 0 return
d = d.fillna(0.0)
d = d[(d != 0).any(axis=1)]
def dimson(a, b):
    """Lead-lag corrected correlation: sum of cross-covariances at lags -1,0,+1."""
    c = sum(a.cov(b.shift(L)) for L in (-1, 0, 1))
    return c / (a.std() * b.std())
pairs = [("US","ASX"),("US","FTSE"),("US","TSX"),("ASX","FTSE"),("ASX","TSX"),("FTSE","TSX")]
wk = (1 + d).resample("W-FRI").prod() - 1
mo = (1 + d).resample("ME").prod() - 1
print(f"{'pair':10}{'daily':>8}{'dimson':>8}{'weekly':>8}{'monthly':>8}")
for a, b in pairs:
    print(f"{a+'-'+b:10}{d[a].corr(d[b]):8.2f}{dimson(d[a], d[b]):8.2f}{wk[a].corr(wk[b]):8.2f}{mo[a].corr(mo[b]):8.2f}")
print("n days", len(d), "weeks", len(wk), "months", len(mo))
