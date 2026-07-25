"""End-to-end verification of the LIVE paper books — "does this behave like real life?"

The backtest suite proves the *maths* is right on a clean price matrix. This
module proves the *books* are right: it reads the persisted state that the
schedulers actually wrote (`state/paper_state_*.json`, `state/fx_state_*.json`)
and re-derives, from the trade ledger alone, what a real broker statement would
have to look like. Anything the ledger can't explain is a finding.

It deliberately does NOT hit the network: the persisted state IS the record of
what the system did, so the audit is reproducible offline and in CI, and can
never be "fixed" by a lucky re-download.

Five families of check:

* **RECONCILE** — replay the trade ledger from initial capital and confirm it
  reproduces the stored cash / positions / equity. Catches silent state drift.
* **REALISM**   — things a real broker would have rejected: trades on a closed
  market, fills at a forward-filled dead price, fractional shares, missing
  commission.
* **LIVENESS**  — silent failure. A sleeve parked in cash for weeks, a book whose
  last bar has stopped advancing, a sleeve calendar-pinned by a rebalance stamp
  it never earned.
* **HORIZON**   — are positions held long enough for their own signal to play
  out? A 55-bar breakout closed after 4 bars never gets to be right.
* **COST**      — turnover and spread drag relative to the book's actual return.

Run it:

    python -m trading_algo.verify                 # every book, human report
    python -m trading_algo.verify --json          # machine-readable
    python -m trading_algo.verify --account matt  # one book
    python -m trading_algo.verify --strict        # exit 1 on any ERROR

Exit code is 0 when nothing above the failure threshold fired, so it can gate a
scheduled run in CI.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

# Levels, most severe first.
ERROR, WARN, INFO = "ERROR", "WARN", "INFO"
_RANK = {ERROR: 0, WARN: 1, INFO: 2}

# A sleeve sitting in cash longer than this is a problem, not a decision: the
# equity books rebalance monthly, so ~6 weeks means it has skipped a full cycle.
IDLE_DAYS = 45
# A book whose newest bar is older than this has a dead feed or a dead scheduler.
STALE_BOOK_DAYS = 5
# Positions closed inside this fraction of their own signal horizon are "cut
# short" — the signal that opened them has not had a chance to resolve.
HORIZON_FRACTION = 0.25


@dataclass
class Finding:
    level: str
    book: str
    code: str
    message: str
    detail: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# State discovery
# ---------------------------------------------------------------------------
def _state_dir(kind: str) -> str:
    """Where the schedulers write. Mirrors paper_trade/fx_book STATE_DIR."""
    env = "FX_STATE_DIR" if kind == "fx" else "MOMENTUM_STATE_DIR"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.environ.get(env) or os.path.join(root, "state")


def discover(account: str | None = None) -> list[tuple[str, str, dict]]:
    """Return [(kind, account, state)] for every persisted book."""
    out: list[tuple[str, str, dict]] = []
    for kind, prefix in (("equity", "paper_state_"), ("fx", "fx_state_")):
        pattern = os.path.join(_state_dir(kind), f"{prefix}*.json")
        for path in sorted(glob.glob(pattern)):
            name = os.path.basename(path)[len(prefix):-len(".json")]
            if account and name != account:
                continue
            try:
                with open(path) as fh:
                    out.append((kind, name, json.load(fh)))
            except (OSError, json.JSONDecodeError) as exc:
                out.append((kind, name, {"_unreadable": repr(exc)}))
    return out


def _parse(stamp: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(stamp, fmt)
        except ValueError:
            pass
    raise ValueError(f"unparseable timestamp {stamp!r}")


def _bar_hours(state: dict) -> float:
    """Length of one bar in hours, from the book's declared interval."""
    return {"1m": 1 / 60, "5m": 1 / 12, "15m": 0.25, "30m": 0.5,
            "60m": 1.0, "1h": 1.0, "1d": 24.0}.get(state.get("bar") or "1d", 24.0)


# ---------------------------------------------------------------------------
# RECONCILE — does the trade ledger reproduce the stored book?
# ---------------------------------------------------------------------------
def reconcile_equity(account: str, state: dict) -> list[Finding]:
    """Replay every equity trade and compare against the stored sleeve.

    A paper sleeve's cash is fully determined by its trades: start at the funded
    allocation, subtract buys (plus commission and stamp duty), add sells (less
    commission). If the replay and the stored cash disagree by more than a cent,
    something wrote the book outside the ledger — which would make every reported
    number downstream untrustworthy.
    """
    found: list[Finding] = []
    sleeves = state.get("sleeves") or {}
    allocations = state.get("allocations") or {}
    initial = float(state.get("initial_capital_base") or 0.0)
    fx = state.get("fx_snapshot") or {}

    by_region: dict[str, list[dict]] = {}
    for t in state.get("trades") or []:
        by_region.setdefault(t.get("region"), []).append(t)

    for key, sleeve in sleeves.items():
        trades = by_region.get(key, [])
        rate = fx.get(sleeve.get("currency"))
        if not rate:
            found.append(Finding(
                WARN, account, "no-fx-rate",
                f"sleeve {key}: no {sleeve.get('currency')} rate in fx_snapshot — "
                "cannot reconcile its funding in base currency"))
            continue
        # Funded amount, converted into the sleeve's local currency.
        funded = initial * float(allocations.get(key, 0.0)) / float(rate)

        cash = funded
        pos: dict[str, float] = {}
        for t in trades:
            shares = float(t.get("shares", 0))
            fill = float(t.get("fill", 0.0))
            fee = float(t.get("commission", 0.0)) + float(t.get("stamp_duty", 0.0))
            if t.get("side") == "BUY":
                cash -= shares * fill + fee
                pos[t["ticker"]] = pos.get(t["ticker"], 0.0) + shares
            else:
                cash += shares * fill - fee
                pos[t["ticker"]] = pos.get(t["ticker"], 0.0) - shares
        pos = {k: v for k, v in pos.items() if abs(v) > 1e-9}

        stored_cash = float(sleeve.get("cash", 0.0))
        stored_pos = {k: float(v) for k, v in (sleeve.get("positions") or {}).items()}

        # Funding is derived from a *current* FX rate but the sleeve was funded at
        # inception's rate, so cash can legitimately differ. Positions cannot.
        if stored_pos != {k: v for k, v in pos.items()}:
            found.append(Finding(
                ERROR, account, "position-mismatch",
                f"sleeve {key}: ledger replay does not reproduce stored positions",
                {"replayed": pos, "stored": stored_pos}))

        # Cash drift beyond the FX-conversion ambiguity means an unledgered write.
        drift = abs(cash - stored_cash)
        if trades and drift > max(1.0, 0.02 * abs(stored_cash)):
            found.append(Finding(
                WARN, account, "cash-drift",
                f"sleeve {key}: replayed cash {cash:,.2f} vs stored "
                f"{stored_cash:,.2f} {sleeve.get('currency')} "
                f"(drift {drift:,.2f}); inception-vs-current FX explains some of "
                "this, a large gap does not",
                {"replayed": round(cash, 2), "stored": round(stored_cash, 2)}))
    return found


def reconcile_fx(account: str, state: dict) -> list[Finding]:
    """FX books are weight-based, so check the weight ledger against positions."""
    found: list[Finding] = []
    cur: dict[str, float] = {}
    for t in sorted(state.get("trades") or [], key=lambda x: _parse(x["date"])):
        cur[t["pair"]] = float(t.get("target_weight", 0.0))
    stored = {k: float(v) for k, v in (state.get("positions") or {}).items()}
    for pair, w in cur.items():
        if abs(w) < 1e-9:
            continue
        if pair not in stored:
            found.append(Finding(
                ERROR, account, "missing-position",
                f"{pair}: ledger ends at weight {w:+.4f} but the pair is absent "
                "from stored positions"))
        elif abs(stored[pair] - w) > 1e-4:
            found.append(Finding(
                ERROR, account, "weight-mismatch",
                f"{pair}: last ledger weight {w:+.4f} != stored position "
                f"{stored[pair]:+.4f}",
                {"ledger": w, "stored": stored[pair]}))
    return found


# ---------------------------------------------------------------------------
# REALISM — would a real broker have accepted this?
# ---------------------------------------------------------------------------
def check_market_hours(account: str, state: dict, kind: str) -> list[Finding]:
    """Flag trades stamped on a bar when the market for that instrument was shut.

    FX closes Friday ~22:00 UTC and reopens Sunday ~22:00 UTC; cash equities are
    shut all weekend. Crypto genuinely is 24/7, so it is exempt. A trade stamped
    outside its own session was filled against a price that no venue was quoting.
    """
    from .forex.pairs import ALL_PAIRS

    crypto = {s for s, p in ALL_PAIRS.items() if p.asset_class == "crypto"}

    offenders: dict[str, int] = {}
    for t in state.get("trades") or []:
        symbol = t.get("pair") or t.get("ticker") or "?"
        if symbol in crypto:
            continue
        try:
            ts = _parse(t["date"])
        except (ValueError, KeyError):
            continue
        weekend = ts.weekday() >= 5
        # Friday after 22:00 UTC is already the weekend for FX.
        if kind == "fx" and ts.weekday() == 4 and ts.hour >= 22:
            weekend = True
        # Sunday from 22:00 UTC the FX week has restarted.
        if kind == "fx" and ts.weekday() == 6 and ts.hour >= 22:
            weekend = False
        if weekend:
            offenders[symbol] = offenders.get(symbol, 0) + 1

    if not offenders:
        return []
    total = sum(offenders.values())
    return [Finding(
        ERROR, account, "closed-market-trade",
        f"{total} trades executed while the market for that instrument was "
        f"closed (weekend/after the FX close) across {len(offenders)} symbols — "
        "these filled against a forward-filled price no venue was quoting",
        {"by_symbol": dict(sorted(offenders.items(), key=lambda x: -x[1]))})]


def check_dead_price(account: str, state: dict) -> list[Finding]:
    """Flag position changes made at a price identical to the previous change.

    `fx_data._align` forward-fills a symbol that did not print, so a frozen feed
    keeps serving its last close. If the book keeps re-weighting a symbol while
    its price never moves, it is paying spread on every turn for an exposure
    change that cannot possibly capture a price move.
    """
    last_px: dict[str, float] = {}
    frozen: dict[str, int] = {}
    for t in sorted(state.get("trades") or [], key=lambda x: _parse(x["date"])):
        sym, px = t.get("pair"), float(t.get("price", 0.0))
        if sym in last_px and px == last_px[sym] and abs(float(
                t.get("delta_weight", 0.0))) > 1e-9:
            frozen[sym] = frozen.get(sym, 0) + 1
        last_px[sym] = px
    if not frozen:
        return []
    total = sum(frozen.values())
    return [Finding(
        WARN, account, "dead-price-turnover",
        f"{total} position changes made at an unchanged (forward-filled) price "
        f"across {len(frozen)} symbols — spread paid, no price exposure gained",
        {"by_symbol": dict(sorted(frozen.items(), key=lambda x: -x[1]))})]


def check_whole_shares(account: str, state: dict) -> list[Finding]:
    """Invariant #4: paper equity trades are whole shares."""
    bad = [t for t in state.get("trades") or []
           if abs(float(t.get("shares", 0)) - round(float(t.get("shares", 0)))) > 1e-9]
    if not bad:
        return []
    return [Finding(ERROR, account, "fractional-shares",
                    f"{len(bad)} equity trades with fractional share counts",
                    {"example": bad[0]})]


def check_costs_charged(account: str, state: dict) -> list[Finding]:
    """Invariant #2: costs are always on. A zero-commission fill is suspicious."""
    trades = state.get("trades") or []
    free = [t for t in trades if float(t.get("commission", 0.0)) <= 0.0]
    if not free:
        return []
    return [Finding(ERROR, account, "uncosted-trade",
                    f"{len(free)} of {len(trades)} equity trades booked with zero "
                    "commission — invariant #2 says costs are always on",
                    {"example": free[0]})]


# ---------------------------------------------------------------------------
# LIVENESS — is anything silently doing nothing?
# ---------------------------------------------------------------------------
def check_liveness(account: str, state: dict, kind: str,
                   today: datetime) -> list[Finding]:
    found: list[Finding] = []

    # Book freshness: has the newest bar stopped advancing?
    if kind == "fx":
        last = state.get("last_bar_date")
    else:
        hist = state.get("equity_history") or []
        last = hist[-1][0] if hist else None
    if last:
        age = (today - _parse(last)).days
        if age > STALE_BOOK_DAYS:
            found.append(Finding(
                ERROR, account, "stale-book",
                f"newest bar is {last} — {age} days old; the scheduler or the "
                "data feed has stopped advancing this book"))

    # Equity sleeves: parked in cash, and the calendar-pin signature.
    for key, sleeve in (state.get("sleeves") or {}).items():
        status = sleeve.get("last_status") or {}
        flat_since = status.get("flat_since")
        if flat_since:
            days = (today - _parse(flat_since)).days
            if days >= IDLE_DAYS:
                found.append(Finding(
                    WARN, account, "idle-sleeve",
                    f"sleeve {key} has been in cash since {flat_since} "
                    f"({days} days, status {status.get('status')!r}) — its "
                    "capital is earning nothing and nothing alerts on it"))

        # A funded sleeve that has never held anything at all. `_should_rebalance`
        # fires once per calendar month, so a sleeve that reaches month-end still
        # flat has burned a whole cycle with its capital doing nothing.
        traded = any(t.get("region") == key for t in state.get("trades") or [])
        funded = (state.get("allocations") or {}).get(key)
        if funded and not traded:
            found.append(Finding(
                ERROR, account, "never-traded",
                f"sleeve {key} is funded ({funded:.0%} of the book, "
                f"{sleeve.get('cash', 0):,.0f} {sleeve.get('currency')}) but has "
                "never executed a single trade since the book opened"))

        # Flat *and* already stamped for this month: `_should_rebalance` returns
        # False until the calendar rolls over, so this sleeve cannot re-enter
        # before the 1st however good the signal gets in between.
        stamped = sleeve.get("last_rebalance_month")
        asof = status.get("date")
        if (funded and stamped and asof and stamped == asof[:7]
                and not sleeve.get("positions")):
            found.append(Finding(
                WARN, account, "pinned-flat",
                f"sleeve {key} holds nothing and is already stamped for "
                f"{stamped} — _should_rebalance() is False until the next "
                f"calendar month, so it stays in cash regardless of signal "
                f"(status {status.get('status')!r})"))
    return found


# ---------------------------------------------------------------------------
# HORIZON — do positions live long enough for their signal to resolve?
# ---------------------------------------------------------------------------
def round_trips(state: dict) -> list[tuple[str, float, float, float]]:
    """Reconstruct closed round-trips from the FX weight ledger.

    Returns [(symbol, bars_held, gross_return, avg_weight)]. A round-trip opens
    when a symbol's signed weight leaves zero and closes when it returns to zero
    or flips sign.
    """
    hours = _bar_hours(state)
    trades = sorted(state.get("trades") or [], key=lambda t: _parse(t["date"]))
    open_pos: dict[str, tuple[datetime, int, float, float]] = {}
    cur: dict[str, float] = {}
    out: list[tuple[str, float, float, float]] = []
    for t in trades:
        sym, ts, px = t["pair"], _parse(t["date"]), float(t["price"])
        w = float(t.get("target_weight", 0.0))
        prev, cur[sym] = cur.get(sym, 0.0), w
        sp = (prev > 0) - (prev < 0)
        sw = (w > 0) - (w < 0)
        if sp == 0 and sw != 0:
            open_pos[sym] = (ts, sw, px, abs(w))
        elif sp != 0 and sw != sp:
            if sym in open_pos:
                entry, sign, epx, ew = open_pos.pop(sym)
                bars = (ts - entry).total_seconds() / 3600.0 / hours
                gross = sign * (px - epx) / epx if epx else 0.0
                out.append((sym, bars, gross, ew))
            if sw != 0:
                open_pos[sym] = (ts, sw, px, abs(w))
    return out


def signal_horizon(state: dict) -> tuple[int, str]:
    """The holding horizon (in bars) the book's agents are implicitly claiming.

    Deliberately built from the two windows that *imply a holding period* — the
    Donchian breakout channel (a turtle-style channel break is a claim about the
    following ~channel length) and the ROC momentum window (a 60-bar momentum
    reading cannot meaningfully change in 4 bars). `ema_slow` is excluded: it is
    a smoothing constant, not a horizon, so including it would overstate the gap.
    """
    from .forex.fx_config import profile

    try:
        p = profile(state.get("profile") or "balanced")
    except Exception:
        return 0, "unknown"
    windows = [w for w in (getattr(p, "donchian_window", 0),
                           getattr(p, "roc_window", 0)) if w]
    if not windows:
        return 0, "unknown"
    return int(statistics.mean(windows)), "breakout/momentum window"


def check_horizon(account: str, state: dict) -> list[Finding]:
    """Compare how long positions actually live against their signal's horizon.

    A 55-bar Donchian breakout is a claim about what happens over the *next*
    several weeks. Closing the position after 4 bars does not test that claim —
    it just pays the spread. This does not prove longer holds would earn more
    (winners run longer partly by construction), but a median hold far inside the
    signal's own window means the strategy is never actually being expressed.
    """
    trips = round_trips(state)
    if len(trips) < 5:
        return []
    bars = sorted(t[1] for t in trips)
    median = statistics.median(bars)
    horizon, driver = signal_horizon(state)
    if not horizon:
        return []

    found: list[Finding] = []
    ratio = median / horizon
    cut_short = sum(1 for b in bars if b < HORIZON_FRACTION * horizon)
    if ratio < HORIZON_FRACTION:
        found.append(Finding(
            WARN, account, "hold-shorter-than-signal",
            f"median hold {median:.1f} bars vs a {horizon}-bar signal horizon "
            f"({driver}) — {ratio:.0%} of the window. {cut_short}/{len(bars)} "
            "round-trips closed before their own signal could resolve",
            {"median_bars": round(median, 1), "horizon_bars": horizon,
             "driver": driver, "cut_short": cut_short, "round_trips": len(bars)}))

    # Split realised return by whether the trip ran past a quarter of its horizon.
    short = [t for t in trips if t[1] < HORIZON_FRACTION * horizon]
    long_ = [t for t in trips if t[1] >= HORIZON_FRACTION * horizon]
    if short and long_:
        s_contrib = sum(g * w for _, _, g, w in short)
        l_contrib = sum(g * w for _, _, g, w in long_)
        if s_contrib < 0 < l_contrib:
            found.append(Finding(
                INFO, account, "short-holds-lose",
                f"round-trips cut short (<{HORIZON_FRACTION:.0%} of horizon) "
                f"contributed {s_contrib:+.2%} of equity across {len(short)} "
                f"trips; those given room contributed {l_contrib:+.2%} across "
                f"{len(long_)}. Suggestive, not causal — a position survives "
                "longer precisely when its signal keeps confirming",
                {"short_contrib": round(s_contrib, 4),
                 "long_contrib": round(l_contrib, 4),
                 "short_n": len(short), "long_n": len(long_)}))
    return found


# ---------------------------------------------------------------------------
# COST — is turnover eating the book?
# ---------------------------------------------------------------------------
def check_turnover(account: str, state: dict) -> list[Finding]:
    """Gross weight turnover per bar, and the spread it implies."""
    from .forex.pairs import ALL_PAIRS

    trades = state.get("trades") or []
    if not trades:
        return []
    stamps = {t["date"] for t in trades}
    turnover = sum(abs(float(t.get("delta_weight", 0.0))) for t in trades)
    cost = 0.0
    for t in trades:
        pair = ALL_PAIRS.get(t.get("pair"))
        px = float(t.get("price", 0.0))
        # fx_book crosses half the dealing spread on each weight change.
        frac = pair.spread_fraction(px) if pair else 0.0
        cost += abs(float(t.get("delta_weight", 0.0))) * frac / 2.0

    ret = 0.0
    init = float(state.get("initial_capital") or 0.0)
    if init:
        ret = float(state.get("equity", init)) / init - 1.0
    return [Finding(
        INFO, account, "turnover",
        f"gross turnover {turnover:.1f}x equity over {len(stamps)} bars "
        f"({turnover / max(len(stamps), 1):.2f}x per bar); implied spread cost "
        f"{cost:.2%} against a net return of {ret:+.2%}",
        {"turnover": round(turnover, 2), "bars": len(stamps),
         "implied_spread_cost": round(cost, 5), "net_return": round(ret, 5)})]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def verify_book(kind: str, account: str, state: dict,
                today: datetime | None = None) -> list[Finding]:
    today = today or datetime.utcnow()
    if state.get("_unreadable"):
        return [Finding(ERROR, account, "unreadable-state",
                        f"state file could not be parsed: {state['_unreadable']}")]
    found: list[Finding] = []
    found += check_market_hours(account, state, kind)
    found += check_liveness(account, state, kind, today)
    if kind == "equity":
        found += reconcile_equity(account, state)
        found += check_whole_shares(account, state)
        found += check_costs_charged(account, state)
    else:
        found += reconcile_fx(account, state)
        found += check_dead_price(account, state)
        found += check_horizon(account, state)
        found += check_turnover(account, state)
    return sorted(found, key=lambda f: _RANK[f.level])


def verify_all(account: str | None = None,
               today: datetime | None = None) -> dict[str, list[Finding]]:
    return {f"{kind}:{name}": verify_book(kind, name, state, today)
            for kind, name, state in discover(account)}


def _render(results: dict[str, list[Finding]]) -> str:
    icon = {ERROR: "✗", WARN: "!", INFO: "·"}
    lines: list[str] = []
    counts = {ERROR: 0, WARN: 0, INFO: 0}
    for book, findings in results.items():
        lines.append(f"\n=== {book} " + "=" * max(0, 60 - len(book)))
        if not findings:
            lines.append("  ✓ no findings")
            continue
        for f in findings:
            counts[f.level] += 1
            lines.append(f"  {icon[f.level]} [{f.level}] {f.code}: {f.message}")
    lines.append(f"\n{'-' * 66}")
    lines.append(f"{counts[ERROR]} errors, {counts[WARN]} warnings, "
                 f"{counts[INFO]} notes across {len(results)} books")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--account", help="verify a single book")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any ERROR finding fired")
    args = ap.parse_args(argv)

    results = verify_all(args.account)
    if args.json:
        print(json.dumps(
            {b: [asdict(f) for f in fs] for b, fs in results.items()}, indent=2))
    else:
        print(_render(results))

    errors = sum(1 for fs in results.values() for f in fs if f.level == ERROR)
    return 1 if (args.strict and errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
