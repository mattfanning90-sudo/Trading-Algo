"""Low-latency runner for the FX paper books.

The engine drives the paper accounts on a polling cycle. Two design choices keep
per-cycle latency low and flat as the universe/roster grows:

* a **single long-lived `AgentPool`** (thread pool) is reused across cycles, so
  the parallel agents are evaluated concurrently every tick without re-spawning
  workers; and
* the whole signal→ensemble→risk path is **vectorized numpy/pandas**, so one
  cycle is a handful of array passes, not a Python loop over bars.

FX trades ~24×5, so unlike the equity scheduler (which wakes at each cash close)
this polls on a fixed interval, skipping the weekend.

    python -m trading_algo.forex.engine --once               # one pass, all accounts
    python -m trading_algo.forex.engine --once --account matt
    python -m trading_algo.forex.engine --loop --interval 300 # poll every 5 min
    python -m trading_algo.forex.engine --benchmark           # cycle latency
"""
from __future__ import annotations

import argparse
import asyncio
import time
from datetime import datetime, timezone

from . import fx_book
from .agents import AgentPool
from .fx_config import START
from .fx_data import load_panel, synthetic_panel
from .fx_strategy import compute_targets
from .pairs import DEFAULT_UNIVERSE


def fx_market_open(dt: datetime | None = None) -> bool:
    """True during the FX week (Sun 22:00 UTC → Fri 22:00 UTC, roughly)."""
    dt = (dt or datetime.now(timezone.utc)).astimezone(timezone.utc)
    wd, hour = dt.weekday(), dt.hour          # Mon=0 .. Sun=6
    if wd == 5:                                # Saturday
        return False
    if wd == 6:                                # Sunday: opens ~22:00 UTC
        return hour >= 22
    if wd == 4:                                # Friday: closes ~22:00 UTC
        return hour < 22
    return True


def run_once(account: str | None, synthetic: bool, pool: AgentPool) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n=== FX engine @ {stamp}  account={account or 'ALL'} ===")
    if account:
        fx_book.run_once(account, synthetic, pool=pool)
    else:
        fx_book.run_all(synthetic, pool=pool)


async def run_loop(account: str | None, synthetic: bool, pool: AgentPool,
                   interval: float = 300.0, max_cycles: int | None = None) -> None:
    """Poll every `interval` seconds; `max_cycles` bounds the loop (tests)."""
    i = 0
    while max_cycles is None or i < max_cycles:
        if synthetic or fx_market_open():
            try:
                run_once(account, synthetic, pool)
            except Exception as exc:                 # never let one bad cycle kill it
                print(f"[engine] cycle failed: {exc!r}")
        else:
            print("[engine] FX market closed — idling.")
        i += 1
        if max_cycles is not None and i >= max_cycles:
            break
        await asyncio.sleep(interval)


def benchmark(synthetic: bool = True, workers: int | None = None, runs: int = 5) -> float:
    """Time one live decision cycle (`compute_targets`, warm). Returns median sec."""
    panel = (synthetic_panel(DEFAULT_UNIVERSE) if synthetic
             else load_panel(DEFAULT_UNIVERSE, START, use_cache=True))
    pool = AgentPool(max_workers=workers)
    from .fx_config import profile
    p = profile("balanced")
    compute_targets(panel, p, pool=pool)              # warm caches
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        compute_targets(panel, p, pool=pool)
        times.append(time.perf_counter() - t0)
    times.sort()
    med = times[len(times) // 2]
    print(f"  live cycle latency (median of {runs}): {med * 1e3:.1f} ms "
          f"over {len(panel)} pairs × {len(pool.agents)} agents")
    return med


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Low-latency FX paper engine")
    ap.add_argument("--account", default=None, help="single account (omit = all)")
    ap.add_argument("--once", action="store_true", help="single pass (cron-friendly)")
    ap.add_argument("--loop", action="store_true", help="poll forever on --interval")
    ap.add_argument("--interval", type=float, default=300.0, help="loop poll seconds")
    ap.add_argument("--workers", type=int, default=None, help="agent-pool threads")
    ap.add_argument("--ml", action="store_true",
                    help="include the trained deep-learning agent (if a model exists)")
    ap.add_argument("--benchmark", action="store_true", help="measure cycle latency")
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args(argv)

    if args.benchmark:
        benchmark(synthetic=args.synthetic, workers=args.workers)
        return

    pool = fx_book.ml_pool() if args.ml else AgentPool(max_workers=args.workers)
    if args.loop:
        asyncio.run(run_loop(args.account, args.synthetic, pool, interval=args.interval))
    else:
        run_once(args.account, args.synthetic, pool)


if __name__ == "__main__":
    main()
