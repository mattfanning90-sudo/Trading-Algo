"""Measure the champion promotion gate: what do the finalists actually score,
and what would pure noise score through the identical pipeline?

READ-ONLY. Downloads panels and re-scores; writes no state and promotes nothing.

The question this answers: the FX swarm has evaluated 424 genomes a month since
July and promoted ZERO. Is `DSR_MIN = 0.95` correctly rejecting 424 overfit
genomes, or is it miscalibrated and rejecting a real edge? Loosening a threshold
before measuring it is how noise gets promoted into a live book.

It mirrors `champions.promote` exactly — same hold-out split, same `n_trials`,
same population-wide `sr_variance` — so the numbers it prints ARE the numbers
the gate judged on, not an approximation of them.

    python3 scripts/measure_champion_gate.py [--account matt] [--synthetic]
"""
from __future__ import annotations

import argparse
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# Running `python3 scripts/x.py` puts scripts/ on sys.path, not the repo root
# (same convention as scripts/build_walkthrough.py:32).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_algo.forex import champions, evolve, fx_config, validation  # noqa: E402

NULL_DRAWS = 300


def _sr_variance(log) -> float | None:
    """Population-wide Sharpe dispersion — exactly what `promote` feeds the gate."""
    pps = [float(pp) for v in log.registry.values()
           if isinstance(v, dict) and (pp := v.get("sharpe_pp")) is not None]
    return float(np.var(pps)) if len(pps) > 1 else None


def measure(account: str, synthetic: bool) -> dict | None:
    log = evolve.read_log(account)
    if log is None:
        print(f"{account}: no swarm log — run evolve first")
        return None

    profile_name = fx_config.ACCOUNTS.get(account, {}).get("profile", "balanced")
    p = fx_config.profile(profile_name)
    finalists = [evolve.genome_from_dna(log.registry[g]["dna"]) for g in log.finalists
                 if g in log.registry and "dna" in log.registry[g]]
    _, holdout = evolve.split_history(evolve._panel_for(account, synthetic),
                                      log.holdout_frac)

    rets = {g.gid: evolve.genome_returns(g, holdout, p) for g in finalists}
    mat = pd.DataFrame(rets).dropna()
    if mat.shape[1] < 2 or len(mat) < 10:
        print(f"{account}: hold-out too small to judge ({mat.shape})")
        return None

    sr_var = _sr_variance(log)
    n_trials = log.n_trials

    real_dsr = np.array([validation.deflated_sharpe_ratio(
        mat[c].to_numpy(), n_trials, sr_var) for c in mat.columns])
    real_sr = np.array([validation.sharpe_ratio(mat[c].to_numpy())
                        for c in mat.columns])

    # The NULL: same length, same realised vol, ZERO edge, scored through the
    # identical call. If the real finalists do not separate from this, the gate
    # is right to reject them and the threshold is not the problem.
    rng = np.random.default_rng(0)
    n, vol = len(mat), float(np.nanstd(mat.to_numpy()))
    null_dsr = np.array([validation.deflated_sharpe_ratio(
        rng.normal(0.0, vol, n), n_trials, sr_var) for _ in range(NULL_DRAWS)])

    pbo = validation.pbo(mat.to_numpy(), n_splits=min(10, max(2, len(mat) // 50)))

    print(f"\n=== {account}  (profile {profile_name}, n_trials={n_trials}, "
          f"hold-out {log.holdout_frac:.0%} = {n} bars, {mat.shape[1]} finalists)")
    print(f"  sr_variance (population Sharpe dispersion) : {sr_var!r}")
    print(f"  hold-out Sharpe  max={real_sr.max():+.3f}  median={np.median(real_sr):+.3f}")
    print(f"  REAL DSR         max={real_dsr.max():.4f}  p90={np.percentile(real_dsr, 90):.4f}"
          f"  median={np.median(real_dsr):.4f}")
    print(f"  NULL DSR         max={null_dsr.max():.4f}  p90={np.percentile(null_dsr, 90):.4f}"
          f"  median={np.median(null_dsr):.4f}")
    print(f"  clearing DSR>={champions.DSR_MIN}: {int((real_dsr >= champions.DSR_MIN).sum())}"
          f" of {len(real_dsr)}   (noise clears it "
          f"{float((null_dsr >= champions.DSR_MIN).mean()):.1%} of the time)")
    print(f"  batch PBO = {pbo:.4f} (ceiling {champions.PBO_MAX}"
          f"{' — COHORT BINNED AS OVERFIT' if pbo > champions.PBO_MAX else ''})")

    sep = real_dsr.max() - np.percentile(null_dsr, 90)
    print(f"  separation (real max - null p90) = {sep:+.4f}  -> "
          + ("real signal above noise" if sep > 0.05 else
             "INDISTINGUISHABLE FROM NOISE — the gate is correct to reject"))
    return {"account": account, "real_max": float(real_dsr.max()),
            "null_p90": float(np.percentile(null_dsr, 90)), "pbo": float(pbo),
            "passed": int((real_dsr >= champions.DSR_MIN).sum())}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--account", help="one book (default: every configured book)")
    ap.add_argument("--synthetic", action="store_true",
                    help="offline panel — PIPELINE TEST ONLY, never evidence")
    args = ap.parse_args(argv)
    accounts = [args.account] if args.account else list(fx_config.ACCOUNTS)
    rows = [r for a in accounts if (r := measure(a, args.synthetic))]
    if rows and not args.synthetic:
        print("\n--- verdict " + "-" * 54)
        for r in rows:
            print(f"  {r['account']:11s} real_max={r['real_max']:.4f} "
                  f"null_p90={r['null_p90']:.4f} pbo={r['pbo']:.3f} "
                  f"passed={r['passed']}")


if __name__ == "__main__":
    main()
