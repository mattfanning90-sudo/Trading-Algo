"""In-sample permutation test for the swarm search (step 2).

The problem
-----------
`evolve.breed` tries hundreds of genomes and keeps the best. Keeping the best of
N tries makes the best look good even when none of them are any good — put 424
people in a room flipping coins and someone gets 9 heads. That is **selection
bias**, and correcting for it is what `champions.gate` uses the Deflated Sharpe
Ratio for.

But DSR needs `n_trials`: how many *independent* things you tried.
`docs/MONTE_CARLO_RESEARCH.md` §3 measured three accepted estimators of that
number on our own registry and found they **disagree by 40×**, with nothing
available to adjudicate. A gate that cannot measure its own penalty has to
assume the worst, and ours blocks everything.

The escape
----------
Re-run the ENTIRE search on a market with no structure, and compare
best-against-best. Whatever the best genome scores on noise is what the search
itself is worth. No `n_trials` is ever needed: the cost of searching hard is
priced into the null by construction, because the null searched just as hard.

    p = (number of shuffles whose best matched or beat the real best + 1)
        ------------------------------------------------------------------
        (number of shuffles + 1)

Design decisions worth not undoing
----------------------------------
* **Re-breed on every permutation.** The cheap alternative — re-scoring the
  genomes already bred from real data — is INVALID: those genomes are already
  fitted to the real panel, so they underperform on noise, the null comes out
  too easy and the p-value too small. An error in the flattering direction. It
  is not even cheaper (424 vs 480 evaluations, both measured).
* **One breeding seed for the real run and every permutation.** Breeding is
  stochastic. Holding its seed fixed means the only thing that differs between
  runs is the DATA, which is the entire question. The *data* seed varies per
  permutation; the *search* seed never does.
* **The statistic is the best hold-out Sharpe among finalists** — precisely the
  quantity `champions.gate` already feeds to DSR, so the p-value and the DSR
  verdict are talking about the same number. It is net of costs (invariant #2)
  because `evolve.genome_returns` applies the half-spread.

This REPORTS alongside DSR/PBO. It does not gate. See
`docs/specs/swarm-insample-permutation.md`.

    python -m trading_algo.forex.permtest --synthetic --permutations 3 --quick
    python -m trading_algo.forex.permtest --account matt --permutations 200
"""
from __future__ import annotations

import argparse
import json
import os
import time
from typing import Sequence

from . import bar_permute, evolve, validation

STATE_DIR = None      # test hook; None -> defer to fx_book.STATE_DIR at call time

STATISTIC = "best_holdout_sharpe"
MIN_HOLDOUT_BARS = 10

# Production budget mirrors evolve's own CLI defaults, so the test measures the
# search you actually run. --quick tests a SMALLER search: useful for iteration,
# not a production verdict.
PROD_GENERATIONS, PROD_POP = 12, 40
QUICK_GENERATIONS, QUICK_POP = 3, 10


def _state_dir() -> str:
    from . import fx_book
    return STATE_DIR or fx_book.STATE_DIR


def report_path(account: str) -> str:
    return os.path.join(_state_dir(), f"permtest_{account}.json")


def write_report(account: str, result: dict) -> str:
    path = report_path(account)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)
    return path


def load_report(account: str) -> dict | None:
    """The report if one exists, else None. Never raises — a missing report is
    the normal state, and must not break promotion."""
    try:
        with open(report_path(account)) as fh:
            return json.load(fh)
    except (FileNotFoundError, NotADirectoryError, json.JSONDecodeError):
        return None


def profile_for(account: str, override: str | None) -> str:
    """The profile this account actually breeds with.

    `champions.main` resolves it from `ACCOUNTS[account]["profile"]`, so we must
    too: testing `matt` (balanced) under `daytrader`'s intraday knobs would
    produce a p-value for a search that book never runs.
    """
    if override:
        return override
    from . import fx_config as cfg
    return cfg.ACCOUNTS.get(account, {}).get("profile", "balanced")


def common_window_panel(panel: dict) -> tuple[dict, dict]:
    """Trim every symbol to the dates they ALL share.

    Instruments list at different times — on the live `matt` panel the majors
    start 2003, AUDUSD 2006, BTC 2014, ETH 2017, SOL 2020. A shared shuffle
    needs one common timeline, because sharing it across symbols is what keeps
    cross-symbol co-movement intact (see `bar_permute`).

    Trimming to the intersection costs history, and that cost is REPORTED rather
    than buried: the comparison stays fair because the real search and every
    shuffled search run on the identical trimmed panel. What it changes is the
    *scope* of the verdict — the p-value describes the search over this window,
    not over full history.

    Bars are only ever dropped, never padded or forward-filled: inventing bars
    would put fabricated prices into the null.
    """
    if not panel:
        return {}, {"n_bars": 0, "dropped_bars": 0, "start": None, "end": None}

    common = None
    for df in panel.values():
        common = df.index if common is None else common.intersection(df.index)
    common = common.sort_values()

    longest = max(len(df) for df in panel.values())
    info = {
        "n_bars": int(len(common)),
        "dropped_bars": int(longest - len(common)),
        "start": str(common[0].date()) if len(common) else None,
        "end": str(common[-1].date()) if len(common) else None,
        "limited_by": min(panel, key=lambda s: len(panel[s])),
    }
    return {s: df.loc[common] for s, df in panel.items()}, info


def p_value(perm_scores: Sequence[float], real: float) -> float:
    """p = (k+1)/(m+1), k = shuffles that matched or beat `real`.

    The +1s are not decoration. Without them a strategy beating every shuffle
    scores p=0 — a claim of literal impossibility that m shuffles cannot
    support. Counting the real result as one more draw is the honest floor.
    """
    scores = list(perm_scores)
    matched = sum(1 for s in scores if s >= real)
    return (matched + 1) / (len(scores) + 1)


def search_best(panel: dict, p, *, seed: int, generations: int, pop_size: int,
                holdout_frac: float = 0.25) -> float:
    """Run the whole search on `panel`; return the best hold-out Sharpe found.

    Genomes whose hold-out series is too short or never moves are skipped — an
    inert genome is not a strategy, and `MONTE_CARLO_RESEARCH.md` §6 documents
    what happens when one is handed a score. If nothing traded, the answer is
    0.0, which makes the comparison conservative rather than flattering.
    """
    _, holdout_panel, scored = evolve.breed(
        panel, p, generations=generations, pop_size=pop_size, seed=seed,
        holdout_frac=holdout_frac)

    sharpes = []
    for genome, _ in scored:
        rets = evolve.genome_returns(genome, holdout_panel, p)
        if len(rets) >= MIN_HOLDOUT_BARS and float(rets.std()) > 0:
            sharpes.append(float(validation.sharpe_ratio(rets.to_numpy())))
    return max(sharpes) if sharpes else 0.0


def run(panel: dict, p, *, permutations: int, seed: int, generations: int,
        pop_size: int, holdout_frac: float = 0.25, progress=None) -> dict:
    """Real search vs `permutations` searches on shuffled panels."""
    # Both sides must see the identical panel or the comparison is meaningless.
    panel, window = common_window_panel(panel)
    real_best = search_best(panel, p, seed=seed, generations=generations,
                            pop_size=pop_size, holdout_frac=holdout_frac)

    perm_best: list[float] = []
    for i in range(permutations):
        # Data seed varies; SEARCH seed does not (see module docstring).
        shuffled = bar_permute.permute_panel(panel, seed=seed + 1 + i)
        perm_best.append(search_best(shuffled, p, seed=seed, generations=generations,
                                     pop_size=pop_size, holdout_frac=holdout_frac))
        if progress:
            progress(i + 1, permutations, perm_best[-1])

    symbols = list(panel)
    return {
        "statistic": STATISTIC,
        "p_value": p_value(perm_best, real_best),
        "real_best": real_best,
        "perm_best": perm_best,
        "n_permutations": permutations,
        "seed": seed,
        "generations": generations,
        "pop_size": pop_size,
        "holdout_frac": holdout_frac,
        "symbols": symbols,
        "n_bars": window["n_bars"],
        "window": window,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _window_line(res: dict) -> str:
    w = res.get("window") or {}
    if not w.get("dropped_bars"):
        return f"window: {w.get('start')} -> {w.get('end')} ({w.get('n_bars')} bars, full panel)"
    return (f"window: {w['start']} -> {w['end']} ({w['n_bars']} bars) — TRIMMED to the dates "
            f"all symbols share;\n        {w['dropped_bars']} bars dropped, limited by "
            f"{w.get('limited_by')}. The verdict covers THIS window, not full history.")


def format_report(res: dict, *, synthetic: bool = False) -> str:
    perm = res["perm_best"]
    beat = sum(1 for s in perm if s >= res["real_best"])
    lines = [
        "",
        f"In-sample permutation test — {res['n_permutations']} shuffles, "
        f"search {res['generations']}gen x {res['pop_size']}pop, seed {res['seed']}",
        f"statistic: {res['statistic']} (net of costs)",
        _window_line(res),
        "",
        f"  real search best      {res['real_best']:+.4f}",
    ]
    if perm:
        srt = sorted(perm)
        lines += [
            f"  shuffled search best  mean {sum(perm) / len(perm):+.4f}   "
            f"median {srt[len(srt) // 2]:+.4f}   max {max(perm):+.4f}",
            f"  shuffles matching or beating real: {beat}/{len(perm)}",
        ]
    lines += [
        "",
        f"  p-value = (k+1)/(m+1) = {res['p_value']:.4f}",
        "",
        "  Reported ALONGSIDE the DSR/PBO gate — it does not gate promotion.",
        "  A low p-value says the search found more than the same search finds",
        "  on noise. It does not say the edge is large, or that it will persist.",
    ]
    if synthetic:
        lines += ["", "  SYNTHETIC DATA — pipeline test only, never a verdict (invariant #5)."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="In-sample permutation test for the swarm search.")
    ap.add_argument("--account", default="matt")
    ap.add_argument("--profile", default=None,
                    help="override; defaults to the account's own profile")
    ap.add_argument("--permutations", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--synthetic", action="store_true", help="offline; pipeline test only")
    ap.add_argument("--quick", action="store_true",
                    help=f"reduced search ({QUICK_GENERATIONS}gen x {QUICK_POP}pop) for "
                         "iteration — tests a SMALLER search than production, so its "
                         "p-value is not the production verdict")
    ap.add_argument("--no-write", action="store_true", help="print only, write no report")
    args = ap.parse_args(argv)

    from . import fx_config as cfg
    profile_name = profile_for(args.account, args.profile)
    p = cfg.profile(profile_name)
    panel = evolve._panel_for(args.account, args.synthetic)
    generations = QUICK_GENERATIONS if args.quick else PROD_GENERATIONS
    pop_size = QUICK_POP if args.quick else PROD_POP

    started = time.time()

    def progress(done, total, score):
        rate = (time.time() - started) / done
        print(f"  [{done:>4}/{total}] best {score:+.4f}   "
              f"eta {rate * (total - done) / 60:.1f} min", flush=True)

    res = run(panel, p, permutations=args.permutations, seed=args.seed,
              generations=generations, pop_size=pop_size, progress=progress)
    res["synthetic"] = args.synthetic
    res["quick"] = args.quick
    res["account"] = args.account
    res["profile"] = profile_name

    print(format_report(res, synthetic=args.synthetic))
    if not args.no_write:
        print(f"  written to {write_report(args.account, res)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
