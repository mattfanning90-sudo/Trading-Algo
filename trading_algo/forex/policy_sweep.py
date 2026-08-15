"""Sweep the holding-policy knobs, train/test split.

The live books turn over far more than their signal horizon justifies — measured
by `verify.py` as round-trips closing at 7-11% of the 57-bar breakout/momentum
window, and as a spread bill that on the 60m book came to two thirds of the net
loss. `position_policy` supplies the controls (entry/exit hysteresis, minimum
hold, churn band); this module picks their VALUES, on real data, with the
evidence attached.

Two rules it follows, both from the house sweep philosophy in `sweep.py`:

* **Fit on train, report on test.** The grid is scored on the first `--train`
  fraction of history and the chosen row is re-run on the untouched remainder. A
  knob that only helps in-sample is a knob that found noise.
* **Prefer a flat region to a peak.** `--flat` ranks each combination by the
  WORST train Sharpe in its immediate neighbourhood, so an isolated spike
  surrounded by bad neighbours loses to a slightly lower plateau. A peak on a
  jagged surface does not survive contact with live data.

Real data is required to choose live values: with `--synthetic` this is a
pipeline test only and the report says so (invariant #5).

    python -m trading_algo.forex.policy_sweep --profile balanced
    python -m trading_algo.forex.policy_sweep --profile intraday --bar 60m
    python -m trading_algo.forex.policy_sweep --synthetic --quick   # offline smoke
"""
from __future__ import annotations

import argparse
import itertools
import json
import statistics

import pandas as pd

from . import fx_backtest, fx_config as cfg, fx_data, fx_strategy
from .fx_config import FXParams
from .pairs import DEFAULT_UNIVERSE
from ..metrics import metric as _metric

# The grid. Thresholds are fractions of equity (comparable to per_pair_cap);
# min_hold is in BARS, so its meaning follows the book's cadence.
ENTRY = (0.0, 0.04, 0.06, 0.08, 0.12)
EXIT = (0.0, 0.02, 0.03, 0.05)
HOLD = (0, 3, 5, 10, 20)
QUICK_ENTRY, QUICK_EXIT, QUICK_HOLD = (0.0, 0.08), (0.0, 0.03), (0, 5)


def grid(quick: bool = False) -> list[dict]:
    e, x, h = (QUICK_ENTRY, QUICK_EXIT, QUICK_HOLD) if quick else (ENTRY, EXIT, HOLD)
    return [{"entry_threshold": a, "exit_threshold": b, "min_hold_bars": c}
            for a, b, c in itertools.product(e, x, h) if b <= a]


def split_panel(panel: dict[str, pd.DataFrame], train: float = 0.7):
    """Chronological split. The test slice is never seen by the search."""
    stamps = sorted({t for df in panel.values() for t in df.index})
    if len(stamps) < 50:
        raise SystemExit("not enough history to split")
    cut = stamps[int(len(stamps) * train)]
    return ({s: df.loc[:cut] for s, df in panel.items()},
            {s: df.loc[cut:] for s, df in panel.items()})


def median_hold(weights_hist: dict) -> tuple[float, int]:
    """Median round-trip holding period in bars, across all instruments.

    A "round trip" ends when the position closes OR flips sign — the same
    convention `verify.py` reports live, so the sweep and the audit are directly
    comparable.
    """
    if not weights_hist:
        return float("nan"), 0
    stamps = sorted(weights_hist)
    syms = set().union(*(set(weights_hist[t].index) for t in stamps))
    holds: list[int] = []
    for s in syms:
        run, prev = 0, 0.0
        for t in stamps:
            w = float(weights_hist[t].get(s, 0.0))
            if w != 0.0 and prev != 0.0 and (w > 0) == (prev > 0):
                run += 1
            else:
                if run:
                    holds.append(run)
                run = 1 if w != 0.0 else 0
            prev = w
        if run:
            holds.append(run)
    return (statistics.median(holds) if holds else float("nan")), len(holds)


def score(panel: dict[str, pd.DataFrame], base: FXParams, over: dict,
          weights: pd.DataFrame | None = None) -> dict:
    """One backtest, reported in the terms this sweep is about: cost and hold."""
    p = base.with_overrides(**over)
    r = fx_backtest.run_backtest(panel, p, weights=weights)
    m = r["metrics"]
    med, trips = median_hold(r["weights"])
    eq = r["equity"]
    # "Sharpe (vs 3.5%)" carries its risk-free rate in the key, so read these
    # through metrics.metric() rather than guessing an exact label.
    return {"total_return": float(eq.iloc[-1] / eq.iloc[0] - 1),
            "sharpe": float(_metric(m, "Sharpe") or float("nan")),
            "cagr": float(_metric(m, "CAGR") or float("nan")),
            "max_drawdown": float(_metric(m, "MaxDrawdown") or float("nan")),
            "cost": float(r["total_cost_fraction"]),
            "turnover": float(r["turnover"].sum()),
            "median_hold": med, "round_trips": trips}


def _neighbours(row: dict, rows: list[dict]) -> list[dict]:
    """Grid-adjacent combinations (one step on any single axis)."""
    def adj(vals, v):
        i = vals.index(v) if v in vals else 0
        return {vals[j] for j in (i - 1, i, i + 1) if 0 <= j < len(vals)}
    ok_e, ok_x, ok_h = (adj(ENTRY, row["entry_threshold"]),
                        adj(EXIT, row["exit_threshold"]),
                        adj(HOLD, row["min_hold_bars"]))
    return [r for r in rows if r["entry_threshold"] in ok_e
            and r["exit_threshold"] in ok_x and r["min_hold_bars"] in ok_h]


def robust_rank(rows: list[dict], flat: bool = True) -> list[dict]:
    """Rank by the worst train Sharpe in the neighbourhood, not by the peak."""
    for r in rows:
        near = _neighbours(r, rows)
        r["neighbourhood_worst"] = min(n["train"]["sharpe"] for n in near) if near \
            else r["train"]["sharpe"]
    key = (lambda r: (r["neighbourhood_worst"], r["train"]["sharpe"])) if flat \
        else (lambda r: r["train"]["sharpe"])
    return sorted(rows, key=key, reverse=True)


def run(profile_name: str, *, synthetic: bool, quick: bool, train_frac: float,
        bar: str | None, flat: bool) -> dict:
    base = cfg.profile(profile_name)
    symbols = list(DEFAULT_UNIVERSE)
    if synthetic:
        panel = fx_data.synthetic_panel(symbols)
    else:
        panel = fx_data.load_panel(symbols, cfg.START, use_cache=True,
                                   interval=bar or base.bar)
    panel = {s: df for s, df in (panel or {}).items()
             if df is not None and len(df) > 200}
    if not panel:
        raise SystemExit("No FX data (offline? use --synthetic for a pipeline test).")

    train, test = split_panel(panel, train_frac)
    # Weights are invariant to the policy knobs, so the agent pass runs ONCE per
    # slice rather than once per combination (see run_backtest's `weights` note).
    w_train = fx_strategy.target_weights_history(train, base)
    w_test = fx_strategy.target_weights_history(test, base)

    rows = []
    combos = grid(quick)
    for i, over in enumerate(combos, 1):
        try:
            rows.append({**over, "train": score(train, base, over, weights=w_train)})
        except Exception as exc:                      # one bad combo must not kill the sweep
            print(f"  skip {over}: {exc!r}", flush=True)
        if i % 10 == 0 or i == len(combos):
            print(f"  train {i}/{len(combos)}", flush=True)

    if not rows:
        raise SystemExit("every combination failed")
    ranked = robust_rank(rows, flat=flat)
    for r in ranked:                                  # honest: score EVERY row out of sample
        over = {k: r[k] for k in ("entry_threshold", "exit_threshold", "min_hold_bars")}
        r["test"] = score(test, base, over, weights=w_test)

    return {"profile": profile_name, "synthetic": synthetic, "bar": bar or base.bar,
            "train_frac": train_frac, "symbols": sorted(panel),
            "rows": ranked}


def report(res: dict, top: int = 12) -> str:
    out = []
    if res["synthetic"]:
        out.append("⚠ SYNTHETIC DATA — pipeline test only. These values must NOT "
                   "be adopted as live settings (invariant #5).")
    out.append(f"profile={res['profile']} bar={res['bar']} "
               f"symbols={len(res['symbols'])} train={res['train_frac']:.0%}")
    head = (f"{'entry':>6}{'exit':>6}{'hold':>5} | {'trSharpe':>9}{'trCost':>8}"
            f"{'trHold':>7} | {'teSharpe':>9}{'teRet':>8}{'teCost':>8}{'teDD':>7}{'teHold':>7}")
    out.append(head)
    out.append("-" * len(head))
    base = next((r for r in res["rows"] if r["entry_threshold"] == 0
                 and r["exit_threshold"] == 0 and r["min_hold_bars"] == 0), None)
    for r in res["rows"][:top] + ([base] if base and base not in res["rows"][:top] else []):
        tr, te = r["train"], r["test"]
        tag = "  <- current (knobs off)" if r is base else ""
        out.append(f"{r['entry_threshold']:6.2f}{r['exit_threshold']:6.2f}"
                   f"{r['min_hold_bars']:5d} | {tr['sharpe']:9.2f}{tr['cost']*100:7.2f}%"
                   f"{tr['median_hold']:7.1f} | {te['sharpe']:9.2f}{te['total_return']*100:7.1f}%"
                   f"{te['cost']*100:7.2f}%{te['max_drawdown']*100:6.1f}%"
                   f"{te['median_hold']:7.1f}{tag}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Sweep the FX holding-policy knobs")
    ap.add_argument("--profile", default="balanced", choices=cfg.profile_names())
    ap.add_argument("--bar", default=None, help="override the bar interval (e.g. 60m)")
    ap.add_argument("--train", type=float, default=0.7, help="train fraction")
    ap.add_argument("--quick", action="store_true", help="tiny grid (smoke test)")
    ap.add_argument("--peak", action="store_true",
                    help="rank by raw train Sharpe instead of neighbourhood worst")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--json", default=None, help="write full results here")
    args = ap.parse_args(argv)

    res = run(args.profile, synthetic=args.synthetic, quick=args.quick,
              train_frac=args.train, bar=args.bar, flat=not args.peak)
    print(report(res))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(res, f, indent=1, default=float)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
