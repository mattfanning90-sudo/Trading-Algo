"""Background scheduler — runs each regional sleeve after its market close.

Two ways to run:

  one-shot (recommended; drive it from cron/systemd-timer):
      python -m trading_algo.engine --once --account full
      # cron, fire after every regional close (times are UTC here):
      #   ASX close ~06:00 UTC, LSE ~15:30 UTC, US ~21:00 UTC
      0 6,15,21 * * 1-5  cd /path/to/Trading-Algo && \
          python -m trading_algo.engine --once --account full >> paper.log 2>&1

  long-lived loop (sleeps until the next regional close, then runs):
      python -m trading_algo.engine --loop --account full

`run_once` updates every sleeve via the paper engine; each sleeve only rebalances
on the first run of a new month and marks to market otherwise, so running it
several times a day (once per regional close) is safe and idempotent.
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import calendars
from . import paper_trade
from .config import ALLOCATIONS
from .regions import get_region

_WAKE_BUFFER = timedelta(minutes=15)  # run a little after the close


def run_once(account: str, synthetic: bool = False) -> None:
    print(f"\n=== engine run @ {datetime.now(ZoneInfo('UTC')):%Y-%m-%d %H:%M UTC} "
          f"account={account} ===")
    paper_trade.run_daily(account, synthetic)


def next_wake(now_utc: datetime | None = None) -> datetime:
    """Soonest upcoming regional close (+buffer), across all sleeves, in UTC."""
    now_utc = now_utc or datetime.now(ZoneInfo("UTC"))
    closes = []
    for key in ALLOCATIONS:
        region = get_region(key)
        local_now = now_utc.astimezone(ZoneInfo(region.timezone))
        nxt = calendars.next_close(region, local_now)
        closes.append(nxt.astimezone(ZoneInfo("UTC")) + _WAKE_BUFFER)
    return min(closes)


def run_loop(account: str, synthetic: bool = False, max_iter: int | None = None) -> None:
    """Sleep until the next regional close, run, repeat. `max_iter` bounds the
    loop (used by tests); None = run forever."""
    i = 0
    while max_iter is None or i < max_iter:
        wake = next_wake()
        delay = max(0.0, (wake - datetime.now(ZoneInfo("UTC"))).total_seconds())
        print(f"[engine] sleeping {delay/3600:.2f}h until {wake:%Y-%m-%d %H:%M UTC}")
        time.sleep(delay)
        try:
            run_once(account, synthetic)
        except Exception as exc:  # never let one bad run kill the daemon
            print(f"[engine] run failed: {exc!r}")
        i += 1


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Background scheduler for the momentum sleeves")
    ap.add_argument("--account", default="main")
    ap.add_argument("--once", action="store_true", help="single pass (cron-friendly)")
    ap.add_argument("--loop", action="store_true", help="run forever, waking at each close")
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args(argv)

    if args.loop:
        run_loop(args.account, args.synthetic)
    else:
        run_once(args.account, args.synthetic)


if __name__ == "__main__":
    main()
