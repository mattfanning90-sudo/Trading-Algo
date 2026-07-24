"""The promotion gate + live champion roster file.

Finalists from the breeder are re-scored on an UNTOUCHED hold-out slice (the
search never saw it) and judged with the Deflated Sharpe Ratio — deflated by the
honest N (every distinct genome the breeder evaluated) — and the batch PBO. Only
genomes clearing DSR≥dsr_min survive; if the batch PBO exceeds pbo_max the whole
cohort is treated as overfit and nothing is promoted. A rotation cap bounds how
many newcomers enter per cycle and a top_k roster size keeps a stable core (the
hand-written five always lead the live roster). The roster file is what fx_book
reads to extend the live pool — mirroring the `--ml` pool swap.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from . import evolve
from . import genome as gm
from . import validation
from .. import storage          # trading_algo.storage (NOT a forex submodule)
from .agents import Agent, default_agents

STATE_DIR = None      # test hook; None -> fx_book.STATE_DIR


def _state_dir() -> str:
    from . import fx_book
    return STATE_DIR or fx_book.STATE_DIR


def champions_path(account: str) -> str:
    return os.path.join(_state_dir(), f"champions_{account}.json")


def save_roster(account: str, roster: list[gm.Genome], meta: dict) -> None:
    payload = {"roster": [evolve._dna(g) for g in roster], "meta": meta}
    storage.atomic_write_json(champions_path(account), payload)


def load_roster(account: str) -> list[gm.Genome]:
    path = champions_path(account)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        payload = json.load(f)
    return [evolve.genome_from_dna(d) for d in payload.get("roster", [])]


def champions_agents(account: str) -> list[Agent]:
    """Stable core (the five hand-written agents) + the promoted champions."""
    return [*default_agents(), *[g.to_agent() for g in load_roster(account)]]


def gate(finalists: list[gm.Genome], holdout_panel: dict, p, n_trials: int, *,
         dsr_min: float = 0.95, pbo_max: float = 0.5,
         sr_variance: float | None = None) -> tuple[list[tuple[gm.Genome, float]], float]:
    rets = {g.gid: evolve.genome_returns(g, holdout_panel, p) for g in finalists}
    mat = pd.DataFrame(rets).dropna()
    if mat.shape[1] < 2 or len(mat) < 10:
        return [], 0.0
    if sr_variance is None:            # fall back to finalist dispersion on the hold-out
        per_sr = [validation.sharpe_ratio(mat[c].to_numpy()) for c in mat.columns]
        sr_variance = float(np.var(per_sr)) if len(per_sr) > 1 else 0.0
    sr_var = sr_variance

    passed: list[tuple[gm.Genome, float]] = []
    by_gid = {g.gid: g for g in finalists}
    for gid in mat.columns:
        dsr = validation.deflated_sharpe_ratio(mat[gid].to_numpy(), n_trials, sr_var)
        if dsr >= dsr_min:
            passed.append((by_gid[gid], round(dsr, 4)))

    pbo = validation.pbo(mat.to_numpy(), n_splits=min(10, max(2, len(mat) // 50)))
    if pbo > pbo_max:
        passed = []                                            # whole cohort overfit
    passed.sort(key=lambda gd: (-gd[1], gd[0].gid))
    return passed, float(pbo)


def apply_rotation(prev: list[gm.Genome], passed: list[gm.Genome], *,
                   rotation_cap: int, top_k: int) -> list[gm.Genome]:
    order = {g.gid: i for i, g in enumerate(passed)}       # DSR rank (passed is best-first)
    prev_gids = {g.gid for g in prev}
    passed_gids = {g.gid for g in passed}
    newcomers = [g for g in passed if g.gid not in prev_gids][:rotation_cap]
    survivors = [g for g in prev if g.gid in passed_gids]  # prior champions that still pass
    pool = survivors + newcomers
    pool.sort(key=lambda g: order.get(g.gid, 10 ** 9))     # rank survivors + capped newcomers by DSR
    stale = [g for g in prev if g.gid not in passed_gids]  # prior champions that didn't pass -> stability tail
    roster, seen = [], set()
    for g in pool + stale:
        if g.gid not in seen:
            roster.append(g)
            seen.add(g.gid)
        if len(roster) >= top_k:
            break
    return roster[:top_k] if roster else prev[:top_k]


def promote(account: str, *, synthetic: bool, profile_name: str,
            rotation_cap: int = 2, top_k: int = 6, dsr_min: float = 0.95,
            pbo_max: float = 0.5) -> dict:
    from . import fx_config as cfg
    log = evolve.read_log(account)
    if log is None:
        raise SystemExit(f"No swarm log for '{account}'. Run evolve first.")
    p = cfg.profile(profile_name)
    finalists = [evolve.genome_from_dna(log.registry[g]["dna"]) for g in log.finalists
                 if g in log.registry and "dna" in log.registry[g]]
    # reuse the SAME hold-out fraction the breeder used (never hardcode)
    _, holdout_panel = evolve.split_history(evolve._panel_for(account, synthetic),
                                            log.holdout_frac)
    # DSR deflation uses the population-wide Sharpe dispersion, consistent with n_trials
    pps = [v.get("sharpe_pp") for v in log.registry.values()
           if isinstance(v, dict) and v.get("sharpe_pp") is not None]
    sr_variance = float(np.var(pps)) if len(pps) > 1 else None
    passed, pbo = gate(finalists, holdout_panel, p, log.n_trials,
                       dsr_min=dsr_min, pbo_max=pbo_max, sr_variance=sr_variance)
    new_roster = apply_rotation(load_roster(account), [g for g, _ in passed],
                                rotation_cap=rotation_cap, top_k=top_k)
    meta = {"pbo": round(pbo, 4), "n_trials": log.n_trials,
            "promoted": [g.gid for g in new_roster],
            "dsr": {g.gid: d for g, d in passed}}
    save_roster(account, new_roster, meta)
    print(f"[{account}] gate: {len(passed)} passed DSR≥{dsr_min}, PBO={pbo:.2f} "
          f"-> roster of {len(new_roster)} (core 5 + {len(new_roster)} champions)")
    return meta


def main(argv=None):
    import argparse
    from . import fx_config as cfg
    ap = argparse.ArgumentParser(description="Promote bred swarm champions through the OOS gate")
    ap.add_argument("--account", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--profile", default="balanced", choices=cfg.profile_names())
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args(argv)
    accts = list(cfg.ACCOUNTS) if args.all else [args.account or "matt"]
    for a in accts:
        prof = cfg.ACCOUNTS.get(a, {}).get("profile", args.profile) if args.all else args.profile
        promote(a, synthetic=args.synthetic, profile_name=prof)


if __name__ == "__main__":
    main()
