"""Read-only view over one account's swarm state, for the dashboards.

Both terminals render the swarm from THIS module — the MOMENTUM/3R terminal's
SWARM tab (`trading_algo/dashboard`) and the classic FX candlestick page
(`forex/dashboard.py`) — so the two can never disagree about what the breeder
found, or about why the gate did or did not promote anything.

Offline by construction: everything here comes from `swarm_log_{account}.json`,
`champions_{account}.json` and the book's own state file. No market data and no
network — the persisted files ARE the record (the same principle `verify.py`
runs on). In particular the gate verdict is *reported* from what the gate
already wrote; it is never re-derived here, because re-running it would need the
hold-out panel and therefore the network.
"""
from __future__ import annotations

import json

MAX_LINEAGE_NODES = 600     # cap the baked payload; alive + fittest survive
TOP_FINALISTS = 12          # rows in the "closest misses" table

# Verdict codes, in the order a swarm moves through them.
NO_LOG = "no_log"                    # nothing bred for this book yet
GATE_NOT_RUN = "gate_not_run"        # bred, but champions.py has not judged it
COHORT_OVERFIT = "cohort_overfit"    # PBO over the ceiling -> whole cohort binned
NONE_CLEARED = "none_cleared"        # PBO fine, but nobody cleared the DSR bar
PROMOTED = "promoted"                # at least one champion in the live roster


def _read_json(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _champions_live(account: str) -> bool | None:
    """Is the bred roster actually voting in the live book?

    The engine's champion pool is opt-in (`--champions`), so a promoted roster
    can sit on disk without ever reaching the book. The persisted decision book
    settles it: champion agents name themselves `champ:<gid>` (genome.py), so
    their presence in a decision's agent votes is proof they ran. None when the
    book has no decisions to read.
    """
    from . import fx_book
    try:
        state = fx_book.load_state(account)
    except (Exception, SystemExit):        # load_state raises SystemExit when absent
        return None
    decisions = state.get("decisions") or {}
    if not isinstance(decisions, dict) or not decisions:
        return None
    for d in decisions.values():
        for name in (d.get("agents") or {}):
            if str(name).startswith("champ:"):
                return True
    return False


def _lineage(registry: dict, alive: set[str]) -> dict:
    """Genome family tree, capped at MAX_LINEAGE_NODES.

    Over the cap we keep every live champion plus the fittest of the rest, so
    the trim can never drop the nodes the roster points at.
    """
    def fitness(v: dict) -> float:
        f = v.get("fitness")
        return float(f) if isinstance(f, (int, float)) else float("-inf")

    items = list(registry.items())
    truncated = len(items) > MAX_LINEAGE_NODES
    if truncated:
        items.sort(key=lambda kv: (kv[0] in alive, fitness(kv[1])), reverse=True)
        items = items[:MAX_LINEAGE_NODES]
    kept = {gid for gid, _ in items}

    # Fitness ranks the trim above but is deliberately NOT emitted: the picture
    # plots birth generation and archetype only, and 400-odd unread floats are
    # real weight in a baked, offline export.
    nodes = [{"gid": gid,
              "gen": int(v.get("born_gen") or 0),
              "archetype": (v.get("dna") or {}).get("archetype", "?"),
              "alive": gid in alive}
             for gid, v in items]
    nodes.sort(key=lambda n: (n["gen"], n["gid"]))
    edges = [[par, gid] for gid, v in items
             for par in (v.get("parents") or []) if par in kept]
    return {"nodes": nodes, "edges": edges, "truncated": truncated,
            "total_nodes": len(registry)}


def _verdict(log, champ_payload: dict | None, roster_gids: list[str],
             pbo: float | None, dsr_min: float, pbo_max: float) -> dict:
    """Why the roster looks the way it does, in one code + one sentence."""
    n = getattr(log, "n_trials", 0)
    if champ_payload is None:
        return {"code": GATE_NOT_RUN,
                "reason": (f"{n} genomes bred, but the promotion gate has not "
                           f"run for this book yet — nothing can be promoted "
                           f"until `forex.champions` judges the finalists.")}
    if roster_gids:
        return {"code": PROMOTED,
                "reason": (f"{len(roster_gids)} champion(s) cleared the gate "
                           f"and are in this book's roster.")}
    if pbo is not None and pbo > pbo_max:
        return {"code": COHORT_OVERFIT,
                "reason": (f"PBO {pbo:.2f} is over the {pbo_max:.2f} ceiling — "
                           f"the whole cohort was judged overfit, so nothing "
                           f"was promoted regardless of individual scores.")}
    pbo_txt = f"PBO {pbo:.2f} is within the {pbo_max:.2f} ceiling, but " if pbo is not None else ""
    return {"code": NONE_CLEARED,
            "reason": (f"{pbo_txt}no genome cleared the DSR ≥ {dsr_min:.2f} bar "
                       f"on the hold-out the search never saw.")}


def summary(account: str) -> dict:
    """Everything the dashboards show about one account's swarm.

    `available` is False only when no swarm has ever been bred for the book;
    a bred-but-nothing-promoted swarm is very much available — reporting *that*
    is the whole point, since an empty roster is a gate verdict, not an absence.
    """
    from . import champions, evolve

    empty = {"available": False, "account": account, "n_trials": 0,
             "generations": [], "roster": [], "top_finalists": [],
             "archetypes": [], "lineage": {"nodes": [], "edges": [],
                                           "truncated": False, "total_nodes": 0},
             "holdout_frac": None, "champions_live": None,
             "gate": {"code": NO_LOG, "pbo": None, "pbo_max": champions.PBO_MAX,
                      "dsr_min": champions.DSR_MIN, "passed": 0, "promoted": 0,
                      "finalists": 0,
                      "reason": "No swarm has been bred for this book yet."}}

    log = evolve.read_log(account)
    if log is None:
        return empty

    champ = _read_json(champions.champions_path(account))
    meta = (champ or {}).get("meta") or {}
    dsr_by_gid = meta.get("dsr") or {}
    pbo = meta.get("pbo")
    pbo = float(pbo) if isinstance(pbo, (int, float)) else None

    roster, roster_gids = [], []
    for dna in (champ or {}).get("roster") or []:
        try:
            g = evolve.genome_from_dna(dna)
        except (KeyError, TypeError, ValueError):
            continue                       # a malformed row must not kill the tab
        roster_gids.append(g.gid)
        roster.append({"gid": g.gid, "label": g.describe(),
                       "archetype": g.archetype, "dsr": dsr_by_gid.get(g.gid)})
    alive = set(roster_gids)

    # Finalists are best-first out of the breeder, and live OUTSIDE the registry
    # (they index into it), so join rather than assume the key is present.
    finalists = [gid for gid in log.finalists if gid in log.registry]
    top = []
    for gid in finalists[:TOP_FINALISTS]:
        v = log.registry[gid]
        top.append({"gid": gid,
                    "label": v.get("describe", gid),
                    "archetype": (v.get("dna") or {}).get("archetype", "?"),
                    "fitness": v.get("fitness"),
                    "sharpe_pp": v.get("sharpe_pp"),
                    "dsr": dsr_by_gid.get(gid),
                    "promoted": gid in alive})

    counts: dict[str, int] = {}
    for gid in finalists:
        arch = (log.registry[gid].get("dna") or {}).get("archetype", "?")
        counts[arch] = counts.get(arch, 0) + 1
    archetypes = [{"name": k, "count": v} for k, v in
                  sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    gate = _verdict(log, champ, roster_gids, pbo,
                    champions.DSR_MIN, champions.PBO_MAX)
    gate.update({"pbo": pbo, "pbo_max": champions.PBO_MAX,
                 "dsr_min": champions.DSR_MIN, "passed": len(dsr_by_gid),
                 "promoted": len(roster_gids), "finalists": len(finalists)})

    return {"available": True, "account": account,
            "n_trials": int(log.n_trials),
            "generations": [{"gen": g.get("gen"), "best": g.get("best"),
                             "median": g.get("median"),
                             "births": g.get("births", 0),
                             "deaths": g.get("deaths", 0)}
                            for g in log.generations],
            "roster": roster, "top_finalists": top, "archetypes": archetypes,
            "lineage": _lineage(log.registry, alive),
            "holdout_frac": log.holdout_frac,
            "champions_live": _champions_live(account),
            "gate": gate}
