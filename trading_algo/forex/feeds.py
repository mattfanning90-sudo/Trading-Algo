"""Pluggable market-data sources behind one resolver.

Every source returns the *same* panel shape (``dict[symbol -> OHLC DataFrame]``,
aligned + forward-filled) so the agents / ensemble / risk / book / backtest stay
completely source-agnostic — exactly like the equity sleeve's `Region` record.

| source   | what                                   | cost / access            |
|----------|----------------------------------------|--------------------------|
| `yahoo`  | FX majors + crypto (delayed intraday)  | free, no key (default)   |
| `crypto` | real-time crypto OHLCV via ccxt        | free, no key             |
| `oanda`  | real-time FX via a practice account    | free account + token     |
| `alpaca` | real-time US equities (free IEX feed)  | free account + keys      |
| `openbb` | open-source research aggregator        | research, not a live feed|
| `frankfurter` | ECB daily reference fixings (fiat FX) | free, **no key**    |
| `auto`   | **per-symbol** routing across the above| whatever each leg needs  |

Live sources need credentials (env vars) and that source's optional dependency;
every source also ships a synthetic generator so the whole pipeline is testable
offline. Full setup + honesty on each: docs/DATA_FEEDS.md.

Per-symbol routing (`auto`)
---------------------------
`load(symbols, source=X)` sends the WHOLE list to one provider, which breaks for
`matt`/`partner`, whose single universe is FX majors **and** crypto: no provider
serves both (an FX venue has no BTCUSD; a crypto exchange has no EURUSD).

`source="auto"` groups the request by each symbol's `pairs.Pair.asset_class` —
the registry is already the one place that knows what an instrument *is*, so
routing reads it rather than inventing a second mapping — fetches each group from
its own provider, and merges them through the same `fx_data._align` every
single-source load already uses, so the panel is indistinguishable in shape.

Two rules make it safe rather than merely convenient: a symbol no provider can
serve is *reported* (never dropped — a book trading a smaller universe than it
believes is the bug class this project keeps closing), and each symbol carries an
ordered fallback chain, so a dead crypto exchange costs crypto, not FX.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from . import fx_config as cfg
from . import fx_data
from . import pairs

# The meta-source: not a provider, a routing instruction (see load_routed).
AUTO = "auto"

# Everything a CLI's `--source` accepts. A new adapter registers by adding its
# name here (that is also what makes it usable in ROUTES below).
SOURCES = ["yahoo", "crypto", "oanda", "alpaca", "openbb", "frankfurter", AUTO]

# Sources that publish ONE bar per working day (the ECB fixing behind
# frankfurter), so routing skips them for an intraday request and falls through to
# a provider that really has intraday bars. A plain table, not a capability
# queried from the adapter — routing stays a lookup, not a plugin protocol. The
# adapter refuses a non-daily timeframe itself, so a stale entry here only costs
# one wasted request, never a wrong answer.
DAILY_ONLY: set[str] = {"frankfurter"}

# asset_class -> ordered provider preference. This is the whole routing config.
# Names that are not registered in SOURCES are skipped at PLAN time (not fetch
# time), so this table may name a provider whose adapter has not shipped yet.
#
# FX routes to yahoo, not to the keyless frankfurter, because this panel feeds
# `fx_strategy.compute_targets`, whose ADX/Donchian/ATR read HIGH and LOW — and an
# ECB fixing has no intrabar range at all. Opting in is explicit and needs a book
# policy too (`FXParams.close_only_signals`), since the range indicators refuse
# such bars: docs/DATA_FEEDS.md ("Close-only sources").
#     load_routed(syms, routes={**ROUTES, "fx": ("frankfurter", "yahoo")})
# `oanda`/`alpaca` are absent for a different reason: they raise SystemExit
# without credentials, so putting them in the default chain would make every
# routed run print a credential error for a source the user never asked for.
ROUTES: dict[str, tuple[str, ...]] = {
    "fx":     ("yahoo",),
    "crypto": ("crypto", "yahoo"),
    "equity": ("yahoo",),
    "bond":   ("yahoo",),
}


def _intraday_start(interval: str) -> str:
    """How far back to fetch Yahoo intraday (its history limits)."""
    days = {"60m": 700, "1h": 700, "30m": 55, "15m": 55, "5m": 55, "1m": 7}.get(interval, 700)
    return (pd.Timestamp.utcnow() - pd.Timedelta(days=days)).strftime("%Y-%m-%d")


def is_provider(name: str) -> bool:
    """Is `name` a registered adapter we can actually dispatch a fetch to?"""
    return name != AUTO and name in SOURCES


def resolve_source(source: str | None, exchange: str | None) -> str:
    """Normalise the source name. ``--exchange`` implies crypto (back-compat)."""
    source = (source or "yahoo").lower()
    if exchange and source == "yahoo":
        return "crypto"
    if source not in SOURCES:
        raise SystemExit(f"unknown --source {source!r}; known: {SOURCES}")
    return source


def default_universe(source: str, profile_name: str | None = None) -> list[str]:
    """The natural instrument universe for a source (FX majors / crypto / equities)."""
    from .pairs import DEFAULT_UNIVERSE
    if source == "crypto" or profile_name == "hf_crypto":
        from . import crypto_data
        return list(crypto_data.CRYPTO_UNIVERSE)
    if source == "oanda":
        from . import oanda_data
        return list(oanda_data.OANDA_UNIVERSE)
    if source in ("alpaca", "openbb"):
        from .pairs import EQUITY_UNIVERSE
        return list(EQUITY_UNIVERSE)
    if source == "frankfurter":
        from . import frankfurter_data
        return list(frankfurter_data.FRANKFURTER_UNIVERSE)
    # yahoo and `auto`: the mixed FX-majors + crypto universe. For `auto` that is
    # the point — it is the universe no single provider can serve.
    return list(DEFAULT_UNIVERSE)


# ---------------------------------------------------------------------------
# Per-symbol routing (source="auto")
# ---------------------------------------------------------------------------
# The fetch seam: anything with `load`'s keywords. Injected by tests so routing
# is verified against fake providers, with no network anywhere near it.
LoaderFn = Callable[..., dict[str, pd.DataFrame]]


@dataclass
class RouteReport:
    """Who served what, and what nobody could serve.

    `served` maps symbol -> the provider whose bars are in the panel (or
    ``"synthetic"``; invariant #5 — fabricated bars never wear a provider's
    name). `missing` maps symbol -> why it is ABSENT from the panel, with one
    entry per attempted provider joined by "; " so a fallback chain reads as a
    trail. `errors` maps provider -> the failure that took it out wholesale.
    """
    served: dict[str, str] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True when every requested symbol made it into the panel."""
        return not self.missing

    def note_missing(self, symbol: str, reason: str) -> None:
        prev = self.missing.get(symbol)
        self.missing[symbol] = f"{prev}; {reason}" if prev else reason


def routes_for(symbol: str, routes: dict[str, tuple[str, ...]] | None = None,
               interval: str = "1d") -> list[str]:
    """Ordered providers that can serve `symbol` at `interval` — may be empty.

    Empty is a real answer, not an error: the caller turns it into a reported
    `missing` entry rather than a silently shrunken universe.
    """
    table = ROUTES if routes is None else routes
    try:
        asset_class = pairs.get_pair(symbol).asset_class
    except KeyError:
        return []
    daily = interval in ("1d", "B")
    return [s for s in table.get(asset_class, ())
            if is_provider(s) and (daily or s not in DAILY_ONLY)]


def plan_routes(symbols: list[str], routes: dict[str, tuple[str, ...]] | None = None,
                interval: str = "1d") -> tuple[dict[str, list[str]], dict[str, str]]:
    """Group `symbols` by their FIRST-choice provider; report the unroutable.

    Returns ``(groups, unroutable)`` where `groups` maps provider -> symbols (in
    request order) and `unroutable` maps symbol -> why nothing can serve it.
    Pure and offline — this is the introspection point for "what would a routed
    load actually fetch, and from where?".
    """
    groups: dict[str, list[str]] = {}
    unroutable: dict[str, str] = {}
    for sym in symbols:
        chain = routes_for(sym, routes, interval)
        if chain:
            groups.setdefault(chain[0], []).append(sym)
            continue
        if sym not in pairs.ALL_PAIRS:
            unroutable[sym] = "unknown symbol (not in the pairs registry)"
        else:
            asset_class = pairs.get_pair(sym).asset_class
            configured = list((ROUTES if routes is None else routes).get(asset_class, ()))
            if not configured:
                unroutable[sym] = f"no provider configured for asset class {asset_class!r}"
            else:
                unroutable[sym] = (
                    f"no configured provider serves {interval} bars for "
                    f"{asset_class!r} (tried: {', '.join(configured)})")
    return groups, unroutable


def _naive_utc(df: pd.DataFrame) -> pd.DataFrame:
    """Drop tz info (converting to UTC first) so providers can share ONE index.

    Providers disagree on this: ccxt and Alpaca hand back naive UTC, OANDA and
    Yahoo-intraday hand back tz-aware — and pandas refuses to union a tz-aware
    index with a naive one, so a mixed fetch would explode in `_align`. Only the
    routed merge normalises; a single-source load is left bit-for-bit alone.
    """
    idx = df.index
    if isinstance(idx, pd.DatetimeIndex) and idx.tz is not None:
        df = df.copy()
        df.index = idx.tz_convert("UTC").tz_localize(None)
    return df


def load_routed(symbols: list[str], synthetic: bool = False, interval: str = "1d",
                exchange: str | None = None, use_cache: bool = False,
                min_bars: int | None = None,
                routes: dict[str, tuple[str, ...]] | None = None,
                loader: LoaderFn | None = None
                ) -> tuple[dict[str, pd.DataFrame], RouteReport]:
    """Fetch each symbol from a provider that can serve it; merge into one panel.

    `loader` is the fetch seam — it defaults to `load` and takes the same
    keywords, so tests inject fake providers and never touch the network.
    """
    loader = loader or load
    _, unroutable = plan_routes(symbols, routes, interval)
    report = RouteReport(missing=dict(unroutable))
    chains = {s: routes_for(s, routes, interval) for s in symbols if s not in unroutable}
    if not chains:
        return {}, report

    if synthetic:
        # Synthetic mode has no providers to route BETWEEN: every adapter's
        # generator fabricates the same shape, but over different windows (the
        # crypto one is a 3-day stub), so routing them separately would merge a
        # decade of FX against three days of crypto and call it a panel. One
        # coherent fabricated panel for the routable set instead — labelled
        # "synthetic" in the report, never as a provider (invariant #5).
        panel = loader(list(chains), synthetic=True, interval=interval,
                       source="yahoo", exchange=None, use_cache=False,
                       min_bars=min_bars) or {}
        for sym in chains:
            if sym in panel:
                report.served[sym] = "synthetic"
            else:
                report.note_missing(sym, "synthetic generator produced no bars")
        return {s: panel[s] for s in symbols if s in panel}, report

    # Walk the fallback chains tier by tier: every symbol still without bars is
    # regrouped onto its NEXT provider, so one provider failing costs only the
    # symbols nobody else can serve. Chains shrink each pass, so this terminates.
    frames: dict[str, pd.DataFrame] = {}
    pending = [(sym, chain) for sym, chain in chains.items()]
    while pending:
        groups: dict[str, list[tuple[str, list[str]]]] = {}
        for sym, chain in pending:
            groups.setdefault(chain[0], []).append((sym, chain[1:]))
        pending = []
        for src, items in groups.items():
            group_syms = [s for s, _ in items]
            failure = None
            try:
                # `exchange` is a ccxt option and ONLY a ccxt option: resolve_source
                # upgrades ("yahoo", exchange) to "crypto" for back-compat, so
                # forwarding it to a non-crypto group would silently redirect that
                # group's FX/equity legs to a crypto exchange.
                panel = loader(group_syms, synthetic=False, interval=interval,
                               source=src, use_cache=use_cache, min_bars=min_bars,
                               exchange=exchange if src == "crypto" else None) or {}
            except (Exception, SystemExit) as exc:
                # SystemExit is what the adapters raise for a missing optional
                # dependency or credential, and it is NOT an Exception — catching
                # only Exception here would let a keyless OANDA kill the whole
                # routed load, including the legs that were fine.
                panel = {}
                failure = f"{type(exc).__name__}: {exc}".strip()
                report.errors[src] = failure
            for sym, rest in items:
                df = panel.get(sym)
                if df is not None and len(df):
                    frames[sym] = _naive_utc(df)
                    report.served[sym] = src
                    report.missing.pop(sym, None)
                    continue
                report.note_missing(sym, f"{src} unavailable — {failure}" if failure
                                    else f"{src} returned no bars")
                if rest:
                    pending.append((sym, rest))

    # ONE merge, and it is the merge the rest of the pipeline already trusts:
    # fx_data._align outer-joins onto the union calendar and forward-fills, which
    # is exactly what a single-source load returns (and what fx_data_quality is
    # written to police afterwards). Key order follows the request.
    ordered = {s: frames[s] for s in symbols if s in frames}
    return fx_data._align(ordered), report


def load(symbols: list[str], synthetic: bool = False, interval: str = "1d",
         source: str = "yahoo", exchange: str | None = None,
         use_cache: bool = False, min_bars: int | None = None) -> dict[str, pd.DataFrame]:
    """Load an aligned OHLC panel for `symbols` from the chosen `source`.

    `min_bars` bounds how much daily history is *fetched* (yahoo source): callers
    that only need the strategy's warm-up window (the live book, the dashboard)
    pass their `min_history + display` need instead of downloading the full
    archive since START — a pure latency/bandwidth win; decisions are unchanged
    because the strategy trims to `min_history` anyway. Backtests omit it and
    keep the full history.

    ``source="auto"`` routes PER SYMBOL (see load_routed) and returns the same
    merged panel shape. Use `load_routed` directly when you want the
    `RouteReport` — this wrapper only prints what went unserved, because a
    dropped instrument must never be silent.
    """
    source = resolve_source(source, exchange)

    if source == AUTO:
        panel, report = load_routed(symbols, synthetic=synthetic, interval=interval,
                                    exchange=exchange, use_cache=use_cache,
                                    min_bars=min_bars)
        if report.missing:
            print("  [feeds] UNSERVED: " + "; ".join(
                f"{s} ({why})" for s, why in sorted(report.missing.items())))
        return panel

    if source == "crypto":
        from . import crypto_data
        if synthetic:
            return crypto_data.synthetic_crypto_panel(symbols, timeframe=interval)
        return crypto_data.load_ohlcv(symbols, timeframe=interval,
                                      exchange=exchange or "binance")
    if source == "oanda":
        from . import oanda_data
        if synthetic:
            return oanda_data.synthetic_panel(symbols, timeframe=interval)
        return oanda_data.load_ohlcv(symbols, timeframe=interval)
    if source == "alpaca":
        from . import alpaca_data
        if synthetic:
            return alpaca_data.synthetic_panel(symbols, timeframe=interval)
        return alpaca_data.load_ohlcv(symbols, timeframe=interval)
    if source == "openbb":
        from . import openbb_data
        if synthetic:
            return openbb_data.synthetic_panel(symbols, timeframe=interval)
        return openbb_data.load_ohlcv(symbols, timeframe=interval)
    if source == "frankfurter":
        from . import frankfurter_data
        if synthetic:
            return frankfurter_data.synthetic_panel(symbols, timeframe=interval)
        # Mirror the yahoo path's history semantics: `min_bars` bounds the fetch
        # to the strategy's warm-up window, otherwise take the full archive from
        # cfg.START (a backtest must not be silently capped at the adapter's
        # default warm-up depth). The panel is CLOSE-ONLY — see ROUTES.
        if min_bars:
            return frankfurter_data.load_ohlcv(symbols, timeframe=interval,
                                               limit=int(min_bars))
        return frankfurter_data.load_ohlcv(symbols, timeframe=interval,
                                           start=cfg.START)

    # default: yahoo (fx_data)
    daily = interval in ("1d", "B")
    if synthetic:
        if daily:
            return fx_data.synthetic_panel(symbols)
        return fx_data.synthetic_panel(symbols, start="2025-01-01",
                                       end="2025-04-01", freq=interval)
    start = cfg.START if daily else _intraday_start(interval)
    if min_bars:
        if daily:
            # ~1.55 calendar days per business day, plus margin for holidays.
            days = int(min_bars * 1.6) + 20
        elif interval in ("60m", "1h"):
            # conservative ≥6 bars per trading day (equities; FX has ~24).
            days = int(min_bars / 6 * 1.6) + 10
        else:
            days = None                      # finer intervals: Yahoo limits apply anyway
        if days:
            bounded = (pd.Timestamp.utcnow().tz_localize(None)
                       - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
            start = max(start, bounded)
    return fx_data.load_panel(symbols, start, interval=interval, use_cache=use_cache)
