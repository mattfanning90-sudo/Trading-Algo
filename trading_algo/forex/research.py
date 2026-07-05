"""Quant-research agent: systematically search for candidate edges, then judge
every one with the same overfitting-aware statistics the rest of the system uses.

This is the honest version of "a PhD mathematician finds alpha". It does NOT
conjure edges — it *generates* a basket of candidate strategies (OU mean-
reversion, trend/breakout parameter variants, cross-sectional momentum, and
statistical-arbitrage pairs), measures each net-of-cost out-of-sample, and then
applies the multiple-testing correction: the **Deflated Sharpe Ratio** (which
penalises you for how many candidates you tried) and the **Probability of
Backtest Overfitting**. The expected, honest outcome on real G10 FX is that few
or none clear the bar — and *knowing that* is the deliverable.

Every candidate signal is causal (uses only past data); returns are cost-aware
(half-spread per unit turnover) via `ml_backtest.strategy_returns`.

    python -m trading_algo.forex.research --synthetic
    python -m trading_algo.forex.research                 # real Yahoo data
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from . import fx_config as cfg
from . import fx_data, validation
from . import indicators as ind
from .fx_config import profile
from .marks import periods_per_year
from .ml_backtest import strategy_returns
from .pairs import UNIVERSES, resolve_universe

# Economically-related pairs to test for statistical arbitrage (cointegration-ish).
_STATARB_PAIRS = [("AUDUSD", "NZDUSD"), ("EURUSD", "GBPUSD"), ("USDCHF", "USDCAD")]


def _zscore(s: pd.Series, window: int) -> pd.Series:
    mu = s.rolling(window).mean()
    sd = s.rolling(window).std().replace(0.0, np.nan)
    return (s - mu) / sd


# ---------------------------------------------------------------------------
# Candidate signal generators — each returns a signal panel (time x pair) in [-1,1]
# ---------------------------------------------------------------------------
def _ou_meanrev(closes: pd.DataFrame, window: int) -> pd.DataFrame:
    """Fade each pair's z-score back toward its rolling mean (OU mean-reversion)."""
    return closes.apply(lambda c: (-_zscore(c, window) / 1.5).clip(-1, 1))


def _trend(closes: pd.DataFrame, fast: int, slow: int) -> pd.DataFrame:
    f = closes.apply(lambda c: ind.ema(c, fast))
    s = closes.apply(lambda c: ind.ema(c, slow))
    atr = closes.apply(lambda c: c.rolling(slow).std()).replace(0.0, np.nan)
    return np.tanh((f - s) / atr).clip(-1, 1)


def _breakout(closes: pd.DataFrame, window: int) -> pd.DataFrame:
    hi = closes.rolling(window).max().shift(1)
    lo = closes.rolling(window).min().shift(1)
    sig = pd.DataFrame(np.nan, index=closes.index, columns=closes.columns)
    sig[closes > hi] = 1.0
    sig[closes < lo] = -1.0
    return sig.ffill(limit=2 * window).fillna(0.0)


def _xs_momentum(closes: pd.DataFrame, window: int) -> pd.DataFrame:
    """Cross-sectional: long the strongest pairs, short the weakest."""
    roc = closes.pct_change(window, fill_method=None)
    rank = roc.rank(axis=1, pct=True)            # 0..1 across pairs each bar
    return (2.0 * rank - 1.0).clip(-1, 1)        # +1 top, -1 bottom


def _statarb(closes: pd.DataFrame, a: str, b: str, window: int) -> pd.DataFrame:
    """Trade the spread between two related pairs (long one, short the other)."""
    sig = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
    if a not in closes or b not in closes:
        return sig
    spread = np.log(closes[a]) - np.log(closes[b])
    z = _zscore(spread, window).clip(-3, 3) / 2.0
    sig[a] = (-z).clip(-1, 1)                     # spread rich -> short a, long b
    sig[b] = (z).clip(-1, 1)
    return sig


def candidates(closes: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """The full search space of candidate strategies."""
    c: dict[str, pd.DataFrame] = {}
    for w in (20, 40, 60):
        c[f"ou_meanrev_{w}"] = _ou_meanrev(closes, w)
    for f, s in ((10, 50), (20, 100), (50, 200)):
        c[f"trend_{f}_{s}"] = _trend(closes, f, s)
    for w in (20, 55):
        c[f"breakout_{w}"] = _breakout(closes, w)
    for w in (21, 63):
        c[f"xs_momentum_{w}"] = _xs_momentum(closes, w)
    for a, b in _STATARB_PAIRS:
        c[f"statarb_{a}_{b}"] = _statarb(closes, a, b, 30)
    return c


# ---------------------------------------------------------------------------
# Run + score
# ---------------------------------------------------------------------------
def run_research(panel: dict, p, n_bars: int | None = None) -> dict:
    closes = fx_data.closes(panel)
    if n_bars:
        closes = closes.tail(n_bars)
        panel = {s: df.tail(n_bars) for s, df in panel.items()}
    cands = candidates(closes)

    rets = {name: strategy_returns(panel, sig, p) for name, sig in cands.items()}
    mat = pd.DataFrame(rets).dropna()
    n_trials = mat.shape[1]
    per_period_sr = {c: validation.sharpe_ratio(mat[c].to_numpy()) for c in mat.columns}
    sr_var = float(np.var(list(per_period_sr.values()))) if n_trials > 1 else 0.0

    out = {}
    for name, r in rets.items():
        r = r.dropna()
        if len(r) < 20 or r.std() == 0:
            out[name] = {"sharpe": 0.0, "psr": 0.0, "dsr": 0.0, "total": 0.0}
            continue
        eq = (1 + r).cumprod()
        out[name] = {
            "sharpe": round(float(r.mean() / r.std() * np.sqrt(periods_per_year(r.index))), 2),
            "total": round(float(eq.iloc[-1] - 1), 4),
            "psr": round(validation.probabilistic_sharpe_ratio(r.to_numpy()), 3),
            "dsr": round(validation.deflated_sharpe_ratio(r.to_numpy(), n_trials, sr_var), 3),
        }
    pbo = validation.pbo(mat.to_numpy(), n_splits=min(10, max(2, len(mat) // 50)))
    return {"metrics": out, "n_trials": n_trials, "pbo": pbo}


def format_report(res: dict) -> str:
    lines = ["", "=== Quant-research search (out-of-sample, costs on) ===",
             f"{'candidate':<22}{'Sharpe':>8}{'Total':>9}{'PSR':>7}{'DSR':>7}"]
    order = sorted(res["metrics"], key=lambda k: -res["metrics"][k]["sharpe"])
    survivors = 0
    for name in order:
        m = res["metrics"][name]
        flag = "  <-- clears DSR>0.95" if m["dsr"] > 0.95 else ""
        survivors += m["dsr"] > 0.95
        lines.append(f"{name:<22}{m['sharpe']:>8.2f}{m['total']:>9.2%}"
                     f"{m['psr']:>7.2f}{m['dsr']:>7.2f}{flag}")
    lines += [
        f"\nCandidates searched (N): {res['n_trials']}",
        f"Probability of Backtest Overfitting (PBO): {res['pbo']:.2f}",
        f"Candidates clearing the Deflated-Sharpe bar (>0.95): {survivors}",
        ("VERDICT: no candidate shows a statistically credible edge after correcting "
         "for the number searched — the honest, expected result for daily G10 FX."
         if survivors == 0 else
         f"VERDICT: {survivors} candidate(s) cleared the bar — re-test on fresh data "
         "before trusting (could still be luck)."),
    ]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Quant-research agent: search + deflated validation")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--profile", default="balanced", choices=cfg.profile_names())
    ap.add_argument("--bars", type=int, default=None, help="limit to the last N bars")
    ap.add_argument("--out", default=None, help="write the report to a markdown file")
    ap.add_argument("--universe", default="default",
                    help=f"named preset ({', '.join(UNIVERSES)}) or a comma-"
                         f"separated symbol list. Default: the live universe.")
    args = ap.parse_args(argv)

    universe = resolve_universe(args.universe)
    print(f"Universe ({args.universe}): {', '.join(universe)}")
    if args.synthetic:
        print("⚠ SYNTHETIC DATA — pipeline test only, not performance.")
        panel = fx_data.synthetic_panel(universe)
    else:
        panel = fx_data.load_panel(universe, cfg.START, use_cache=True)
    if not panel:
        raise SystemExit("No FX data (offline? try --synthetic).")

    res = run_research(panel, profile(args.profile), n_bars=args.bars)
    report = format_report(res)
    print(report)
    if args.out:
        with open(args.out, "w") as f:
            f.write("# Quant-research report\n\n```\n" + report + "\n```\n")
        print(f"\nReport written to {args.out}")


if __name__ == "__main__":
    main()
