"""Write the `candles.json` the terminal already knows how to read.

The dashboard has always preferred real bars: `app.js`'s `synthCandles()` looks
for `S.candles[pair]` first and only falls back to its seeded generator, and the
chart caption already switches between ``LIVE OHLC (candles.json)`` and
``SYNTHETIC BARS ANCHORED TO REAL LAST CLOSE``. The consumer was complete. The
producer was never written, so every FX chart drew invented bars.

This is that producer, and nothing more. It fetches through the same
`feeds.load_routed` the books trade on — so the candles are the bars the agents
actually saw, from whichever provider that book is configured with, rather than
a second opinion fetched a different way.

Honesty, which is the whole reason this file is careful
------------------------------------------------------
A chart is a claim. Two ways this could quietly lie, both refused here:

* **Range-less bars.** A close-only source (ECB fixings) yields
  ``open == high == low == close``. Drawn as candlesticks those are flat ticks
  that *look* like a real but very quiet market. `bar_quality.has_true_range`
  decides per symbol, and any symbol without a range is reported in `_meta` so
  the terminal can label it instead of presenting it as an observed range.
* **A stale file.** `candles.json` is written beside a dashboard that may be
  rebuilt later; a file left over from an earlier run would silently date the
  charts. Every payload carries `generated_at` and the `bar` it was built at, so
  the age is always answerable.

The output is deliberately the shape `app.js` already parses::

    {"EURUSD": [[ts_ms, o, h, l, c], ...], ..., "_meta": {...}}

Bars are the array form (not objects) because it is ~40% smaller over ten
symbols, and this file is published on every site build.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timezone

from ..forex import bar_quality, feeds, fx_book

# Enough to draw the terminal's longest candle view (60 bars) with headroom for
# the range chips, and small enough that the published file stays a few hundred
# KB across every book. Not a strategy window — purely what the chart draws.
DEFAULT_BARS = 180


def _finite(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _rows(df, bars: int) -> list[list]:
    """`[[epoch_ms, o, h, l, c], ...]` for the last `bars` rows.

    A row with any non-finite field is DROPPED rather than patched: a gap in a
    chart is honest, an invented bar is not. Prices keep full precision — JPY
    crosses and BTC differ by six orders of magnitude, so a fixed round() would
    flatten one or bloat the other.
    """
    out: list[list] = []
    for ts, r in df.tail(bars).iterrows():
        o, h, l, c = (_finite(r.get(k)) for k in ("open", "high", "low", "close"))
        if None in (o, h, l, c):
            continue
        out.append([int(ts.timestamp() * 1000), o, h, l, c])
    return out


def build(symbols: list[str], *, interval: str = "1d", bars: int = DEFAULT_BARS,
          synthetic: bool = False) -> dict:
    """Fetch `symbols` and shape them into the terminal's candles payload."""
    panel, report = feeds.load_routed(
        symbols, synthetic=synthetic, interval=interval,
        # Ask for more than we draw: providers trim leading NaNs on alignment,
        # and a short book would otherwise publish a stub chart.
        min_bars=bars + 20,
    )
    out: dict = {}
    for sym, df in panel.items():
        rows = _rows(df, bars)
        if rows:
            out[sym] = rows

    # Per symbol, not per panel: these payloads are mixed (real ccxt crypto bars
    # beside FX), so one boolean would answer for the wrong instrument.
    range_less = [s for s in bar_quality.close_only_symbols(panel) if s in out]
    out["_meta"] = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bar": interval,
        "synthetic": bool(synthetic),
        # Charts for these must NOT be drawn as observed candlesticks — the
        # high/low are the close repeated, not a measured range.
        "range_less": range_less,
        "symbols": sorted(k for k in out if k != "_meta"),
        # Which provider's bars these actually are (invariant #5: synthetic
        # never wears a provider's name), and what nobody could serve — a
        # symbol charting nothing should be answerable, not just blank.
        "served": {s: p for s, p in report.served.items() if s in out},
        "missing": dict(report.missing),
    }
    return out


def book_symbols(accounts: list[str] | None = None) -> dict[str, list[str]]:
    """`{interval: [symbols]}` actually needed by the live books.

    Grouped by bar because the daytrader book charts 60m and the rest daily —
    publishing one merged daily file would draw the wrong bars on that page.
    """
    by_bar: dict[str, set] = {}
    for name in (accounts or fx_book.list_accounts()):
        if not fx_book.account_exists(name):
            continue
        state = fx_book.load_state(name)
        bar = state.get("bar") or "1d"
        by_bar.setdefault(bar, set()).update(state.get("symbols") or [])
    return {b: sorted(s) for b, s in by_bar.items()}


def write(out_dir: str, *, accounts: list[str] | None = None,
          bars: int = DEFAULT_BARS, synthetic: bool = False) -> list[str]:
    """Write `candles.json` (and `candles-<bar>.json` per non-daily cadence).

    Returns the paths written. A provider failure for one cadence must not cost
    the others their charts, so each is attempted independently.
    """
    os.makedirs(out_dir, exist_ok=True)
    written: list[str] = []
    for bar, symbols in sorted(book_symbols(accounts).items()):
        if not symbols:
            continue
        name = "candles.json" if bar in ("1d", "B") else f"candles-{bar}.json"
        path = os.path.join(out_dir, name)
        try:
            payload = build(symbols, interval=bar, bars=bars, synthetic=synthetic)
        except Exception as exc:                    # noqa: BLE001 — see docstring
            print(f"  candles: skip {bar} ({exc!r})")
            continue
        got = [k for k in payload if k != "_meta"]
        if not got:
            print(f"  candles: skip {bar} (no symbol returned usable bars)")
            continue
        with open(path, "w") as f:
            json.dump(payload, f, separators=(",", ":"))
        rl = payload["_meta"]["range_less"]
        print(f"  candles: {path} — {len(got)} symbols @ {bar}"
              + (f", {len(rl)} RANGE-LESS ({', '.join(rl)})" if rl else ""))
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        description="Write the dashboard's real-OHLC candles.json")
    ap.add_argument("--out-dir", default="public")
    ap.add_argument("--account", action="append", dest="accounts",
                    help="limit to these books (repeatable); default: all")
    ap.add_argument("--bars", type=int, default=DEFAULT_BARS)
    ap.add_argument("--synthetic", action="store_true",
                    help="offline pipeline check — writes synthetic bars, "
                         "flagged as such in _meta so the UI can say so")
    args = ap.parse_args(argv)
    if not write(args.out_dir, accounts=args.accounts, bars=args.bars,
                 synthetic=args.synthetic):
        print("  candles: nothing written (no books, or no data reachable)")


if __name__ == "__main__":
    main()
