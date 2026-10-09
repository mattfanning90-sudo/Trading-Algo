"""Multi-region paper-trading engine — no broker connection required.

Models three self-contained regional sub-books (FTSE / US / ASX). At init the
base-currency capital is split by ALLOCATIONS and converted to each sleeve's
local currency; thereafter each sleeve compounds in its own currency with
whole-share lots, the per-region fee schedule (commission floor + UK stamp duty)
and slippage. Combined equity is reported in the base currency via current FX.

State persists per account in a SQLite store (paper_books.db), with a
paper_state_{account}.json copy dual-written as a fallback. Each sleeve decides
on the first run of a new month and stages that target in its state
(`pending_target`); it fills at the close of the next session the sleeve's data
shows, never at the close that produced it — the same next-close convention as
`backtest.py`. A drawdown-breaker exit is staged and filled the same way.

Note vs the portfolio backtest: this sim funds each sleeve ONCE and lets
allocations drift (the realistic "fund each sub-account, run it" model). The
backtest trues allocations back to target each period — see CLAUDE.md.

Usage
-----
    python -m trading_algo.paper_trade --init --capital 100000   # open account
    python -m trading_algo.paper_trade                           # daily run
    python -m trading_algo.paper_trade --status                  # report
    python -m trading_algo.paper_trade --force-rebalance         # decide now, fill next session
    python -m trading_algo.paper_trade --compare micro full
    (append --synthetic to run fully offline)
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import config as cfg
from . import signals as sig
from . import (attribution, data, data_quality, fees, fx, metrics,
               notifications, pnl, profiles, promotion, storage, strategy, tca)
from . import state_schema
from .regions import REGIONS, get_region, scale_for

# State location: env override, else the repo's tracked state directory.
STATE_DIR = os.environ.get("MOMENTUM_STATE_DIR") or os.path.join(
    os.path.dirname(__file__), "..", "state")
MICRO_THRESHOLD = 5_000.0     # below this (local ccy) a sleeve concentrates
# A dividend window still open after this many sessions is a feed that is not
# coming back on its own; its alert says so.
DIVIDEND_GAP_SESSIONS = 3


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------
# SQLite is the source of truth (atomic, durable, lock-safe); the per-account
# JSON file is dual-written as a fallback so dashboards / CI globs keep working.
# See trading_algo/storage.py and BACKLOG.md.
def _db_path() -> str:
    return os.path.join(STATE_DIR, "paper_books.db")


def _state_file(account: str) -> str:
    return os.path.join(STATE_DIR, f"paper_state_{account}.json")


def account_exists(account: str) -> bool:
    return storage.db_has(_db_path(), account) or os.path.exists(_state_file(account))


def load_state(account: str) -> dict:
    state = storage.db_load(_db_path(), account)
    if state is None:
        # Fallback: a book created before the DB existed still lives in JSON only.
        path = _state_file(account)
        if not os.path.exists(path):
            raise SystemExit(f"No account '{account}'. Run --init --capital <amt> first.")
        with open(path) as f:
            state = json.load(f)
    # Upgrade older books additively (never destructive), then validate. With the
    # gate on, an invalid book fails safe — we raise rather than trade on / reset
    # a corrupted book. With it off, we only warn (shadow mode). See config F18.
    state, _applied = state_schema.migrate_state(state)
    errors = state_schema.validate_state(state)
    if errors:
        msg = (f"State file for account '{account}' is invalid:\n  - "
               + "\n  - ".join(errors))
        if cfg.VALIDATE_STATE_FILES:
            raise state_schema.StateValidationError(msg)
        print(f"⚠ {msg}\n  (VALIDATE_STATE_FILES is off — continuing in shadow mode)")
    return state


def save_state(account: str, state: dict) -> None:
    # Refuse to persist a corrupt state when the gate is on, so a bug can't write
    # garbage that the next run would fail on.
    if cfg.VALIDATE_STATE_FILES:
        errors = state_schema.validate_state(state)
        if errors:
            raise state_schema.StateValidationError(
                f"Refusing to save invalid state for '{account}':\n  - "
                + "\n  - ".join(errors))
    # SQLite is the source of truth (atomic, durable, lock-safe); the per-account
    # JSON file is dual-written as a fallback so dashboards / CI globs keep working.
    storage.db_save(_db_path(), account, state)
    storage.atomic_write_json(_state_file(account), state)


# ---------------------------------------------------------------------------
# Data / FX helpers
# ---------------------------------------------------------------------------
def _regions() -> list[str]:
    return list(cfg.ALLOCATIONS)


def _account_regions(state: dict) -> list[str]:
    """The regions this specific account trades (may be a subset of all)."""
    return list(state.get("allocations") or cfg.ALLOCATIONS)


def _account_params(state: dict, region):
    """Region defaults with this book's profile overrides layered on top.

    Keeps invariant #3 intact: a profile changes the STRATEGY KNOBS, then the
    same `strategy.compute_targets` builds the weights — there is no second
    sizing path. A book with no overrides gets the region defaults unchanged.
    """
    overrides = state.get("param_overrides") or {}
    return region.params.with_overrides(**overrides) if overrides else region.params


def _account_drawdown_stop(state: dict):
    """This book's drawdown circuit-breaker threshold. A profile may override it
    (and set it to None to disable the breaker for a max-risk book); absent, the
    global default applies."""
    return state.get("max_drawdown_stop", cfg.MAX_DRAWDOWN_STOP)


def fx_snapshot(synthetic: bool,
               prev: dict[str, float] | None = None,
               regions: list[str] | None = None) -> dict[str, float]:
    """Latest base-per-local multiplier for every currency THIS account trades.

    `regions` is the account's own region keys (from `state['allocations']` /
    the `--regions` selection). It must NOT default to the global
    `cfg.ALLOCATIONS`: an account funded on a registered-but-UNFUNDED region
    (e.g. TSX/CAD, the documented register→backtest→fund flow) trades a currency
    that ALLOCATIONS never lists, and keying off the global set would omit its
    rate and KeyError on valuation. Absent, falls back to the funded set.

    A single failed pair fetch (e.g. AUDUSD=X 403s while AUDGBP=X succeeds)
    comes back as an all-NaN column that ffill can't repair, so its `.iloc[-1]`
    is NaN. Left alone that NaN flows straight into `eq_base = eq_local · rate`
    and poisons the whole book's equity (this is what NaN'd the `full` book's US
    sleeve and headline AUM). FX rates are persistent, so the correct fallback is
    the last known-good rate from `prev`. When there is NO prior good rate either
    the currency is OMITTED from the snapshot — never emitted as NaN — so a
    failed pair can never persist a NaN or be multiplied into an equity.
    """
    keys = list(regions) if regions is not None else _regions()
    currencies = [get_region(k).currency for k in keys]
    if synthetic:
        tbl = fx.synthetic_fx(currencies, base=cfg.BASE_CURRENCY)
    else:
        tbl = fx.load_fx(currencies, cfg.START, base=cfg.BASE_CURRENCY, use_cache=False)
    prev = prev or {}
    snap = {}
    for c in currencies:
        rate = fx.valid_rate(tbl[c].iloc[-1])
        if rate is None:                  # NaN, inf or non-positive → failed pair
            fallback = fx.valid_rate(prev.get(c))
            if fallback is not None:
                print(f"  ⚠ FX rate for {c} unavailable this run — "
                      f"carrying forward last good {fallback:.4f}")
                rate = fallback
            else:
                # No usable rate — skip rather than persist a NaN (which would
                # both corrupt state and slip past a `NaN < threshold` gate).
                print(f"  ⚠ FX rate for {c} unavailable and no prior rate to "
                      f"fall back on — {c} sleeve valuation will be skipped this run")
                continue
        snap[c] = rate
    return snap


def latest_region_data(region, synthetic: bool):
    if synthetic:
        return data.synthetic_region(region)
    return data.load_region(region, cfg.START, use_cache=False)


# Max business-day age of the latest bar before we refuse to trade/mark a sleeve.
# A daily-bar, monthly-rebalanced sleeve should see a bar at most one trading day
# old; the slack absorbs a long weekend + a public holiday or two (holidays are
# not modelled in calendars.py) so a normal Fri→Mon gap never false-positives.
STALE_BAR_MAX_BDAYS = 4


def _is_stale(region, last_bar, now: datetime | None = None) -> bool:
    """True if `last_bar` (the latest available session) is too old for the
    region's daily cadence to trust — measured in BUSINESS days so weekends and
    (with the slack above) holidays never trip it. `now` defaults to the current
    time in the region's timezone."""
    tz = ZoneInfo(region.timezone)
    now = now.astimezone(tz) if now is not None else datetime.now(tz)
    last_date = pd.Timestamp(last_bar).date()
    now_date = now.date()
    if now_date <= last_date:                 # bar is same-day or (clock skew) ahead
        return False
    bdays = int(np.busday_count(last_date, now_date))
    return bdays > STALE_BAR_MAX_BDAYS


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------
def warn_if_capacity_unhonoured() -> None:
    """Refuse to let a capacity feature diverge backtest from paper in silence.

    The F15 ADV cap and the F6 impact cost are applied in `backtest.py`, which
    builds a per-name `capacity` series and hands it to `strategy.targets_at`.
    Paper trading calls `compute_targets` WITHOUT a capacity argument, so it
    does not honour either. With both off (the default) that is a perfect no-op
    and the two paths agree exactly.

    Switch one on, though, and the same signal would be sized one way in the
    backtest and another in the live book — which is the spirit of invariant #3
    broken quietly. Wiring the cap into paper is a deliberate change to how a
    live book sizes, so it is a decision, not a default. Until it is made, say
    so out loud on every run that could be affected.
    """
    from . import config as cfg
    on = [name for name, val in (("ADV_CAP_PCT", cfg.ADV_CAP_PCT),
                                 ("IMPACT_COEF", cfg.IMPACT_COEF)) if val]
    if not on:
        return
    print(f"  ⚠ {' and '.join(on)} set, but paper trading does not apply "
          "capacity limits — they bind in the BACKTEST path only, so backtest "
          "and paper will size differently from the same signal. See "
          "config.ADV_CAP_PCT.")


def _empty_target_reason(prices: pd.DataFrame, index_px: pd.Series,
                         p, eligible: set[str] | None) -> str:
    """Diagnose WHY `compute_targets` returned an all-cash book, so an idle
    sleeve is explainable instead of silent.

      regime-off        the index is below its trend MA — de-risking as DESIGNED
      no-eligible-names regime is on but nothing cleared the momentum/trend gate
      data-quality      every candidate was frozen as untrustworthy
      insufficient-names a long/short book couldn't form both legs

    Read-only — it does NOT build weights (invariant #3 keeps the one weight path
    in strategy.compute_targets); it only re-reads the regime for reporting.
    """
    if eligible is not None and len(eligible) == 0:
        return "data-quality"
    if getattr(p, "long_short", False):
        return "insufficient-names"
    if p.regime_filter:
        asof = prices.index[-1]
        risk_on = bool(sig.index_risk_on(index_px, p)
                       .reindex(prices.index).ffill().loc[asof])
        if not risk_on:
            return "regime-off"
    return "no-eligible-names"


def _record_sleeve_status(sleeve: dict, today: str, status: str) -> None:
    """Persist a one-line, machine-readable reason for the sleeve's state this
    run (see `_empty_target_reason`). `flat_since` tracks how long a sleeve has
    been in cash so a sleeve that sits idle for weeks is visible, not silent."""
    prev = sleeve.get("last_status") or {}
    flat = status.startswith("cash")
    was_flat = str(prev.get("status", "")).startswith("cash")
    sleeve["last_status"] = {
        "date": today,
        "status": status,
        "positions": len(sleeve["positions"]),
        "flat_since": (prev.get("flat_since", today) if (flat and was_flat) else
                       today if flat else None),
    }


def sleeve_equity_local(sleeve: dict, px: pd.Series) -> float:
    holdings = 0.0
    for t, sh in sleeve["positions"].items():
        price = px.get(t)
        if price is not None and price == price:  # not NaN
            holdings += sh * float(price)
    return sleeve["cash"] + holdings


def unpriced_holdings(sleeve: dict, px: pd.Series) -> list[str]:
    """Held names with no usable price on this bar.

    `sleeve_equity_local` skips them, so a sleeve whose holdings are ALL
    unpriced silently marks at cash only — an invented collapse indistinguishable
    from a real loss. Callers must check this before trusting the valuation; the
    caller that matters is the daily run, which otherwise feeds the number
    straight to the drawdown breaker.
    """
    out = []
    for t, sh in sleeve["positions"].items():
        if not sh:
            continue
        price = px.get(t)
        if price is None or price != price:       # missing or NaN
            out.append(t)
    return out


def rebalance_allocations(sleeves: dict, snap: dict[str, float],
                          allocations: dict[str, float],
                          px_by_region: dict[str, pd.Series],
                          spread_bps: float = cfg.FX_SPREAD_BPS) -> float:
    """True paper sleeves back to target allocation by transferring CASH across
    sleeves (backlog F4). Base value is conserved except for the FX spread charged
    on the crossing amount. Only cash moves — positions are never sold — so the
    true-up is bounded by donor sleeves' available cash. Returns the FX cost (base
    currency). Keeps each sleeve in its own currency (invariant #6): the transfer
    is the ONLY place currencies cross, and it always pays the spread (#2)."""
    base = {k: sleeve_equity_local(sl, px_by_region[k]) * snap[sl["currency"]]
            for k, sl in sleeves.items()}
    total = sum(base.values())
    tot_alloc = sum(allocations[k] for k in sleeves) or 1.0
    if total <= 0:
        return 0.0
    target = {k: total * allocations[k] / tot_alloc for k in sleeves}

    donors = {k: min(base[k] - target[k],                     # surplus over target
                     sleeves[k]["cash"] * snap[sleeves[k]["currency"]])   # cap: cash only
              for k in sleeves if base[k] > target[k]}
    donors = {k: v for k, v in donors.items() if v > 0}
    receivers = {k: target[k] - base[k] for k in sleeves if base[k] < target[k]}
    pool, need = sum(donors.values()), sum(receivers.values())
    moved = min(pool, need)
    if moved <= 0:
        return 0.0

    for k, don in donors.items():                       # withdraw pro-rata
        take = moved * (don / pool)
        sleeves[k]["cash"] -= take / snap[sleeves[k]["currency"]]
    spread = spread_bps / 1e4
    cost = 0.0
    for k, rec in receivers.items():                    # deposit net of spread
        give = moved * (rec / need)
        cost += give * spread
        sleeves[k]["cash"] += give * (1 - spread) / snap[sleeves[k]["currency"]]
    return cost


def init_account(account: str, capital: float, synthetic: bool,
                 allocations: dict[str, float] | None = None,
                 profile: str | None = None, force: bool = False) -> None:
    """Open a paper account. `allocations` overrides which regions it trades and
    their weights (e.g. {"US": 1.0} for a single-region small account). Default
    is the global 3-region split from config.

    `profile` (see trading_algo.profiles) bakes a canned strategy/risk/reporting
    preset into the book: its region allocations, StrategyParams overrides
    (leverage, long/short, …), drawdown-breaker override, and reporting group.
    An explicit `allocations` still wins over the profile's, so a profile can be
    funded across custom regions if desired.

    Refuses to touch a book that already exists unless `force=True`: this builds
    a FRESH state with an empty `trades` ledger, so overwriting a live book
    destroys the only record its P&L is derived from. `forex.fx_book.init_account`
    has always guarded this; the equity side did not, while CLAUDE.md documents
    `--init` as a plain command — so re-running the documented line wiped the
    book. `--force` is the deliberate escape hatch, as on the FX side.

    It RAISES where `fx_book.init_account` prints and skips, and the difference is
    intended. `fx_book.init_defaults` opens four books in one sweep, so skipping an
    existing one is how "top up whatever is missing" works — the FX workflows call
    `--init` on every run and rely on it. The equity CLI inits ONE named account
    because a human asked for that account, so a silent skip would report success
    for work it did not do. Nothing calls this in a loop: `paper-trade.yml` guards
    with `[ ! -f … ]`, so raising cannot break a scheduled run.
    """
    if account_exists(account) and not force:
        raise SystemExit(
            f"Paper account '{account}' already exists — refusing to overwrite it.\n"
            f"  --init builds a fresh book with an EMPTY trade ledger, and all P&L "
            f"is derived from that ledger, so this would destroy the record.\n"
            f"  Run without --init to advance the existing book, or pass --force "
            f"to deliberately reset it.")
    prof = profiles.get_profile(profile) if profile else None
    if prof is not None and allocations is None:
        allocations = prof.allocations
    alloc_src = allocations if allocations is not None else cfg.ALLOCATIONS
    regions = list(alloc_src)
    unknown = [k for k in regions if k not in REGIONS]
    if unknown:
        raise SystemExit(f"Unknown region(s) {unknown}. Known: {list(REGIONS)}")

    snap = fx_snapshot(synthetic, regions=regions)
    total = sum(alloc_src[k] for k in regions)
    norm = {k: alloc_src[k] / total for k in regions}
    sleeves = {}
    for k in regions:
        region = get_region(k)
        local_cash = (capital * norm[k]) / snap[region.currency]
        sleeves[k] = {
            "currency": region.currency,
            "cash": local_cash,
            "positions": {},
            # cost_basis / realized_pnl are retained for the state schema only —
            # they are NOT the source of truth and nothing reads them. All P&L is
            # derived from the fills log (state["trades"]) via trading_algo.pnl,
            # so it can never disagree with the actual trade record.
            "cost_basis": {},
            "realized_pnl": 0.0,
            "last_rebalance_month": None,
            "last_rebalance_date": None,
            # The bar of this sleeve's first decision, stamped once by
            # `_stage_target`. None = not decided yet; a sleeve saved without
            # the key predates it and is never stamped.
            "opened_on": None,
        }
    state = {
        "account": account,
        "schema_version": state_schema.STATE_SCHEMA_VERSION,
        "base_currency": cfg.BASE_CURRENCY,
        "initial_capital_base": capital,
        "allocations": norm,
        "sleeves": sleeves,
        "trades": [],
        "equity_history": [],
        "sleeve_history": [],
        "fx_snapshot": snap,
        # Reporting group + strategy/risk shape. CORE books sum into the headline
        # AUM; other groups get their own separate total on the overview.
        "group": prof.group if prof else profiles.CORE,
        "profile": prof.key if prof else None,
        "param_overrides": dict(prof.param_overrides) if prof else {},
    }
    if prof is not None:
        # Store the breaker override explicitly (None = disabled). Absent means
        # "use the global default", so we only set the key for a profiled book.
        state["max_drawdown_stop"] = prof.max_drawdown_stop
    save_state(account, state)
    where = "single region" if len(regions) == 1 else f"{len(regions)} regions"
    tag = f" [{prof.label}]" if prof else ""
    print(f"Paper account '{account}'{tag} opened with {capital:,.0f} "
          f"{cfg.BASE_CURRENCY} across {where}")
    for k in regions:
        s = sleeves[k]
        print(f"  {k:<5} ({norm[k]:.0%}) funded {s['cash']:>12,.2f} {s['currency']}")


# ---------------------------------------------------------------------------
# Rebalancing one sleeve
# ---------------------------------------------------------------------------
def _fit_leg(weights: dict, leg_gross: float, px, equity: float,
             min_value: float) -> dict:
    """The largest top-k subset of one leg that can actually be held.

    Names are ranked by CONVICTION (|weight|) and dropped smallest-first, never
    by price: dropping the expensive names would be a price-based selection the
    strategy never asked for, and would bias the book toward cheap stocks.
    Each surviving name is re-allocated its share of the leg's original gross,
    so shrinking the count makes the remaining positions bigger rather than
    leaving the leg under-deployed.
    """
    ordered = sorted(weights, key=lambda t: -abs(weights[t]))
    for k in range(len(ordered), 0, -1):
        picks = ordered[:k]
        total = sum(abs(weights[t]) for t in picks)
        if total <= 0:
            continue
        alloc = {t: leg_gross * abs(weights[t]) / total for t in picks}
        ok = True
        for t in picks:
            price = px.get(t)
            dollars = alloc[t] * equity
            if (not price or price != price or price <= 0
                    or int(dollars / price) < 1 or dollars < min_value):
                ok = False
                break
        if ok:
            return alloc
    return {}


def fit_long_short_to_lots(targets, px, equity: float, *, min_value: float,
                           max_net: float, max_passes: int = 6):
    """Shrink a long/short book until it can be held in whole shares — hedged.

    `select_long_short` hedges in WEIGHT space. Whole-share rounding then
    truncates toward zero, and on a small sleeve an expensive name buys NOTHING
    at its target weight: the live `experimental` book could not afford three of
    its six longs, breached the neutrality cap, and so held 100% cash
    permanently. Refusing forever is a worse outcome than holding a smaller,
    genuinely hedged book.

    So both legs are concentrated TOGETHER — micro mode already does this for a
    long-only book but deliberately skipped long/short, because concentrating
    one leg alone would break the hedge. Each leg keeps its highest-conviction
    names; if the rounded legs are still too far apart, the larger one is scaled
    toward the smaller and refitted.

    Returns adjusted weights, or an EMPTY series if either leg cannot be formed
    at all — concentration is not a licence to run unhedged.

    TRADE-OFF, deliberately accepted: fewer names is a lumpier, less diversified
    book with more idiosyncratic risk. That is the price of trading at this size,
    and it is preferable to a funded book that never deploys.
    """
    import pandas as _pd

    longs = {t: float(w) for t, w in targets.items() if w > 0}
    shorts = {t: float(w) for t, w in targets.items() if w < 0}
    if not longs or not shorts:
        return _pd.Series(dtype=float)

    long_gross = sum(longs.values())
    short_gross = -sum(shorts.values())

    def notional(alloc, sign):
        out = 0.0
        for t, w in alloc.items():
            price = px.get(t)
            if price and price == price and price > 0:
                out += int(w * equity / price) * price
        return out

    for _ in range(max_passes):
        la = _fit_leg(longs, long_gross, px, equity, min_value)
        sa = _fit_leg(shorts, short_gross, px, equity, min_value)
        if not la or not sa:
            return _pd.Series(dtype=float)
        ln, sn = notional(la, 1), notional(sa, -1)
        gross = ln + sn
        if gross <= 0:
            return _pd.Series(dtype=float)
        if abs(ln - sn) / gross <= max_net:
            return _pd.Series({**la, **{t: -w for t, w in sa.items()}})
        # Still lop-sided: pull the larger leg toward the smaller and refit.
        if ln > sn:
            long_gross *= max(sn / ln, 0.5)
        else:
            short_gross *= max(ln / sn, 0.5)
    return _pd.Series(dtype=float)


def rebalance_sleeve(region, sleeve: dict, targets: pd.Series, px: pd.Series,
                     today: str, trade_log: list,
                     frozen: set[str] | None = None) -> None:
    equity = sleeve_equity_local(sleeve, px)
    frozen = frozen or set()
    print(f"\n  [{region.key}] rebalancing — equity {equity:,.0f} {region.currency}")

    # Micro-account mode: too small to hold the full book in whole shares.
    # Only for a long-only book — a long/short (market-neutral) book must keep
    # both legs, so concentrating it would break the hedge.
    long_only = targets.empty or bool((targets >= 0).all())
    if long_only and equity < MICRO_THRESHOLD and not targets.empty:
        affordable = [t for t in targets.index
                      if px.get(t) and px[t] <= equity / 1.05]
        # One share fitting in the WHOLE sleeve doesn't mean it fits in a pick's
        # share of it: three picks at a third each rounded to 0 shares and the
        # `small` book bought nothing for months. Like `_fit_leg`, shrink the pick
        # count in rank order — never by price — until every pick buys a share.
        picks = []
        for k in range(min(len(affordable), max(1, min(3, int(equity // 40)))), 0, -1):
            if all(int((equity * (0.97 / k)) / px[t]) >= 1 for t in affordable[:k]):
                picks = affordable[:k]
                break
        if picks:
            targets = pd.Series(0.97 / len(picks), index=picks)
            print(f"    ⚠ micro mode: concentrating into {picks}")
        else:
            targets = pd.Series(dtype=float)
            print("    ⚠ no affordable names — staying in cash")

    # Long/short: concentrate BOTH legs so a book too small to hold every name
    # trades a smaller HEDGED book instead of nothing at all. Micro mode above
    # does this for a long-only book and deliberately skips long/short, because
    # shrinking one leg alone would break the hedge — this shrinks them together.
    # Without it, `experimental` could not afford 3 of its 6 longs, breached the
    # neutrality cap every month and sat in 100% cash permanently.
    if not long_only and not targets.empty and cfg.LS_MAX_NET_EXPOSURE is not None:
        fitted = fit_long_short_to_lots(
            targets, px, equity, min_value=region.min_trade_value,
            max_net=cfg.LS_MAX_NET_EXPOSURE)
        if fitted.empty:
            # Neither concentration nor the full book can be hedged at this size.
            # Say so: a funded book that quietly never deploys looks like a calm
            # strategy rather than a size problem it cannot solve on its own.
            longs = sum(w for w in targets if w > 0)
            shorts = -sum(w for w in targets if w < 0)
            print(f"    ⚠ long/short book cannot be hedged at this size even "
                  f"concentrated (target long {longs:.2f} vs short {shorts:.2f} "
                  f"of equity, {equity:,.0f} {region.currency}) — holding cash. "
                  "The sleeve is too small to hold both legs in whole shares.")
            notifications.notify(
                "ls_not_neutral",
                f"[{region.key}] market-neutral book could not be formed at "
                f"{equity:,.0f} {region.currency} even after concentrating to "
                "the highest-conviction names — holding cash. This repeats every "
                "rebalance until the sleeve is funded larger or holds fewer names.",
                level="alert", region=region.key, net_exposure=1.0,
                equity=round(float(equity), 2))
        elif len(fitted) < len(targets):
            print(f"    ⚠ concentrating the long/short book "
                  f"{len(targets)} -> {len(fitted)} names so it can be held in "
                  "whole shares (highest-conviction names kept, hedge preserved)")
        targets = fitted

    dust = min(200.0, equity * 0.05)
    desired = {}
    for t, w in targets.items():
        price = px.get(t)
        if price and price == price and price > 0:
            desired[t] = int((equity * w) / price)

    # --- post-rounding neutrality gate (long/short books only) --------------
    # `select_long_short` builds both legs to ±1 and stays flat when it can't
    # hedge — but that check happens on WEIGHTS. Whole-share rounding then
    # truncates toward zero, and on a small sleeve an expensive name rounds to 0
    # shares. Lose enough of one leg and a "market-neutral" book is a directional
    # bet carrying a breaker sized for a hedged one. So re-apply the same
    # can't-hedge-then-stay-flat rule to the book actually executable.
    if not long_only and desired and cfg.LS_MAX_NET_EXPOSURE is not None:
        longs = sum(q * px[t] for t, q in desired.items() if q > 0 and px.get(t))
        shorts = sum(-q * px[t] for t, q in desired.items() if q < 0 and px.get(t))
        gross = longs + shorts
        net = abs(longs - shorts) / gross if gross > 0 else 1.0
        if net > cfg.LS_MAX_NET_EXPOSURE:
            print(f"    ⚠ long/short book is {net:.0%} net after whole-share "
                  f"rounding (long {longs:,.0f} vs short {shorts:,.0f} "
                  f"{region.currency}) — exceeds the "
                  f"{cfg.LS_MAX_NET_EXPOSURE:.0%} cap, holding cash instead of "
                  f"running an unhedged bet.")
            notifications.notify(
                "ls_not_neutral",
                f"[{region.key}] market-neutral book could not be formed: "
                f"{net:.0%} net exposure after rounding (long {longs:,.0f} vs "
                f"short {shorts:,.0f} {region.currency}) — flattening to cash",
                level="alert", region=region.key,
                net_exposure=round(net, 4),
                long_notional=round(longs, 2), short_notional=round(shorts, 2))
            desired = {}          # flatten: every held name exits below

    # Seed the FIFO lot book for this region from the fills ledger — the single
    # source of truth for cost basis. Each executed trade updates it; each sell
    # is stamped with its actual realised P&L (net of entry + exit costs) drawn
    # from the real lots it consumes, exactly like a broker trade confirmation.
    lots, _ = pnl.build_lots([t for t in trade_log if t["region"] == region.key])

    for t in sorted(set(sleeve["positions"]) | set(desired)):
        if t in frozen:   # data-quality: don't trade a name on an untrusted price
            continue
        cur = sleeve["positions"].get(t, 0)
        tgt = desired.get(t, 0)
        delta = tgt - cur
        if delta == 0:
            continue
        price = px.get(t)
        if not price or price != price:
            continue
        fill = price * (1 + np.sign(delta) * region.slippage_bps / 1e4)
        notional = abs(delta) * fill
        if notional < dust and tgt != 0:   # skip dust adjustments, allow full exits
            continue
        fee = fees.commission(region, notional)
        duty = fees.stamp_duty(region, notional) if delta > 0 else 0.0
        # A short sale (delta<0 opening) CREDITS the book with the proceeds; the
        # sign of `delta * fill` already handles both directions.
        sleeve["cash"] -= delta * fill + fee + duty

        trade = {"date": today, "region": region.key, "ticker": t,
                 "side": "BUY" if delta > 0 else "SELL", "shares": abs(delta),
                 "decision": round(float(price), 4),   # pre-slippage close (F11 TCA)
                 "fill": round(fill, 4), "commission": round(fee, 2),
                 "stamp_duty": round(duty, 2), "currency": region.currency}
        key = (region.key, t)
        # Signed FIFO: opens add a (long or short) lot; closes/covers realise
        # against the actual lots consumed and stamp the round-trip P&L.
        r = pnl.apply_fill(lots, key, delta, fill, fee + duty, today)
        if r is not None:
            trade["entry"] = round(r["entry"], 4)      # actual FIFO cost basis
            trade["realized"] = round(r["net"], 2)     # actual gain/loss on this close

        if tgt == 0:
            sleeve["positions"].pop(t, None)
        else:
            sleeve["positions"][t] = tgt
        trade_log.append(trade)
        extra = f" duty {duty:.2f}" if duty else ""
        booked = f"  P&L {trade['realized']:+.2f}" if "realized" in trade else ""
        print(f"    {trade['side']:<4} {abs(delta):>7} {t:<10} @ {fill:>10.3f}  "
              f"(fee {fee:.2f}{extra} {region.currency}){booked}")


# ---------------------------------------------------------------------------
# Daily run
# ---------------------------------------------------------------------------
def _held_at(sleeve: dict, fills: list, ticker: str, ex_date: str) -> int:
    """Shares of `ticker` held at the OPEN of `ex_date`, which is what a
    dividend pays on: today's position less every fill dated on or after it
    (a fill on the ex-date itself happens at that close, after it went ex)."""
    later = sum((1 if f["side"] == "BUY" else -1) * int(f["shares"])
                for f in fills if f["ticker"] == ticker and f["date"] >= ex_date)
    return int(sleeve["positions"].get(ticker, 0)) - later


def _dividend_gap_alert(region, account, last: str, today: str, missing: list,
                        abandoned: list, sessions: int) -> None:
    """The alert for a dividend window held open: what it waits for, "stuck"
    once that has gone on for more than a few sessions, and what it gave up."""
    waiting = [n for n in missing if n not in abandoned]
    parts = []
    if waiting:
        stuck = (f" — stuck for {sessions} sessions: a feed that has not "
                 "answered this long is not coming back by itself (a renamed "
                 "or delisted name?)" if sessions > DIVIDEND_GAP_SESSIONS else "")
        parts.append(f"no dividend data for {', '.join(waiting)}; the window "
                     f"since {last} stays open and every dividend in it for "
                     f"those names waits{stuck}")
    if abandoned:
        parts.append(f"abandoned {', '.join(abandoned)}: no dividend data for "
                     f"{sessions} sessions and no price either, so any dividend "
                     f"owed in ({last}, {today}] is not credited")
    who = f"[{account}] " if account else ""
    notifications.notify(
        "dividend_feed_gap", f"{who}{region.key}: " + "; ".join(parts),
        level="alert", account=account, region=region.key, missing=missing,
        abandoned=abandoned, window_start=last, stuck_sessions=sessions)


def _accrue_cash_interest(region, sleeve: dict, px: pd.Series, today: str,
                          trade_log: list) -> float:
    """Credit interest on the cash the sleeve held since its last accrual.

    The backtest's rule, applied the backtest's way. For each bar, backtest.py
    credits

        fees.idle_cash_credit(float(current_w.sum()), (today - dates[i - 1]).days,
                              CASH_RATE_ANNUAL)

    on the NAV the bar started from, where `current_w` is the book held
    THROUGH the bar: that bar's staged fill happens after the credit. In money
    that is the cash held over the bar x rate x calendar days / 365. Here the
    window is (last_interest_date, today]. Nothing moves a sleeve's cash
    between runs, so the run accrues first, before this run's dividend credit
    and fill, on the cash as it stood over the window. Σw is 1 - cash / equity
    and the credit is that fraction of equity, so the money is the same at
    any close's marks. ACT/365 on CALENDAR days: a weekend accrues three, and
    a window a stale bar held open accrues all of its days on the next good
    run. Negative cash (a levered book's debit) earns nothing and is charged
    nothing; the borrow cost is unmodelled (`fees.idle_cash_credit`).

    The first run only arms the window, as `_credit_dividends` does: paying a
    book's past is not something to do silently. A bar at or before the
    window's end (the engine runs up to three times a bar) pays nothing and
    never moves the window back.

    Recorded as an `INT` ledger row, one per sleeve per paying run, with
    shares 1 and fill the credit, so `pnl.cash_amount` reads the credit and
    `pnl.is_fill` is False. Returns the credit in the sleeve's local currency.
    """
    last = sleeve.get("last_interest_date")
    if last and last >= today:
        return 0.0                       # this bar's window is already credited
    sleeve["last_interest_date"] = today
    cash, equity = float(sleeve["cash"]), sleeve_equity_local(sleeve, px)
    if not last or not (cfg.CREDIT_IDLE_CASH and cfg.CASH_RATE_ANNUAL) or equity <= 0:
        return 0.0
    days = (date.fromisoformat(today) - date.fromisoformat(last)).days
    # Σw, NET and signed: see fees.idle_cash_credit on why net, not gross.
    credit = fees.idle_cash_credit(1.0 - cash / equity, days,
                                   cfg.CASH_RATE_ANNUAL) * equity
    if not credit > 0:
        return 0.0
    sleeve["cash"] = cash + credit
    trade_log.append({
        "date": today, "region": region.key, "ticker": "CASH", "side": pnl.INTEREST,
        "shares": 1, "fill": credit, "commission": 0.0, "stamp_duty": 0.0,
        "currency": region.currency})
    print(f"    INT  {days:>3}d on {cash:>12,.2f} = {credit:>10,.2f} {region.currency}")
    return credit


def _credit_dividends(region, sleeve: dict, today: str, trade_log: list,
                      synthetic: bool, *, account: str | None = None,
                      px: pd.Series | None = None) -> float:
    """Credit cash for dividends that went ex since the last run.

    The paper book marks at the latest UNADJUSTED close, so every ex-dividend
    price drop is booked as a loss and the cash never arrives — while the
    backtest runs on yfinance's adjusted series and is therefore already a
    total-return number. This is the credit that makes the two engines measure
    the same thing.

    Each dividend pays the shares held at its ex-date, read off the ledger
    (`_held_at`); a SHORT owes it, so the signed count is used and a short's
    credit comes out negative. Recorded as a `DIV` ledger row so
    `pnl.build_lots` skips it (it moves cash, not shares); no `decision` key,
    because nothing was executed and TCA must not count it as a fill.

    The window is (last_dividend_date, today]. yfinance never raises on a
    failed download — it drops the name — so the window advances only once
    EVERY name held in it came back; until then it stays open, each run pays
    what did come back, and an ex-date already paid is recognised by its DIV
    row rather than paid again. A window never moves backward. A gap alerts,
    and says so once it has been open for more than a few sessions. Past that,
    a missing name with no usable close in `px` (this bar) either is gone, not
    late — a dead held name is frozen and never sold, a sold one stays in the
    window's names — so it is abandoned, named in the alert, and the window
    moves on rather than waiting for good.

    Synthetic data has no dividend feed, and invariant #5 says synthetic results
    are plumbing only — so it returns zero offline rather than inventing one.
    """
    last = sleeve.get("last_dividend_date")
    if last and last >= today:
        return 0.0                      # this bar's window is already credited
    fills = [f for f in trade_log if f.get("region") == region.key and pnl.is_fill(f)]
    # Every name held at any point in the window: held now, or traded inside it.
    names = sorted({t for t, sh in sleeve["positions"].items() if sh}
                   | {f["ticker"] for f in fills if last and f["date"] > last})
    if synthetic or not last or not names:
        sleeve["last_dividend_date"] = today
        return 0.0
    # yfinance's `end` is exclusive: ask through the day after `today`, or a
    # name going ex on this very bar falls in no window, this run's or the next.
    end = (pd.Timestamp(today) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        div = data.dividends(names, last, end)
    except Exception:
        div = pd.DataFrame()            # same as a name that did not come back
    paid = {(r["ticker"], r["date"]) for r in trade_log
            if r.get("region") == region.key and r.get("side") == pnl.DIVIDEND
            and r["date"] > last}
    lo, hi = pd.Timestamp(last), pd.Timestamp(today)
    total = 0.0
    for t in [n for n in names if n in div.columns]:
        col = div[t]
        for stamp, raw_dps in col[(col.index > lo) & (col.index <= hi)].items():
            ex = str(pd.Timestamp(stamp).date())
            dps = round(float(raw_dps) * scale_for(region, t), 6)
            shares = _held_at(sleeve, fills, t, ex)
            if (t, ex) in paid or not dps > 0 or not shares:   # `not >` refuses NaN
                continue
            amount = shares * dps
            sleeve["cash"] += amount
            trade_log.append({
                "date": ex, "region": region.key, "ticker": t, "side": pnl.DIVIDEND,
                "shares": shares, "fill": dps, "commission": 0.0,
                "stamp_duty": 0.0, "currency": region.currency})
            total += amount
            print(f"    DIV  {shares:>7} {t:<10} @ {dps:.4f} "
                  f"= {amount:>10,.2f} {region.currency}")
    missing = [n for n in names if n not in div.columns]
    sessions = int(np.busday_count(last, today)) if missing else 0
    abandoned = ([n for n in missing if not px.get(n, 0) > 0]   # NaN: no close
                 if px is not None and sessions > DIVIDEND_GAP_SESSIONS else [])
    if missing:
        _dividend_gap_alert(region, account, last, today, missing, abandoned,
                            sessions)
    if len(abandoned) == len(missing):
        sleeve["last_dividend_date"] = today
    return total


def _stage_target(sleeve: dict, today: str, targets: pd.Series,
                  frozen: set[str] | None = None) -> None:
    """Record a decision made at TODAY's close, for filling at the next one.

    The book cannot trade at the price that produced its own signal: a target
    decided on bar D_k is executable no earlier than D_{k+1}'s close, which is
    exactly what `backtest.py` stages. Persisting it in the sleeve is what lets
    the decision survive to the next run — the engine is a fresh process each
    time, so an in-memory target would simply be lost.

    The FIRST target a sleeve ever stages also records its bar as `opened_on`,
    once: the bridge's comparison opens this sleeve there
    (`_backtest_for_book`). It cannot be recovered later — a skipped run breaks
    "first fill bar minus one". `init_account` seeds the key as None, so None
    means "a new book that has not decided yet" and is stamped here; a sleeve
    WITHOUT the key was saved before the record existed, so its next decision
    is not its first and it is never stamped (a `--force-rebalance` or a
    resume can clear every other trace of its history).
    """
    if sleeve.get("opened_on", "legacy") is None:
        sleeve["opened_on"] = today
    sleeve["pending_target"] = {
        "date": today,
        "weights": {str(t): float(w) for t, w in targets.items()},
        "frozen": sorted(frozen or ()),
    }


def _fill_pending(region, sleeve: dict, px: pd.Series, today: str,
                  trade_log: list) -> bool:
    """Execute the target staged on an EARLIER bar, at today's close.

    Returns True when a fill was attempted. A target staged on today's own bar
    is left alone: that is the whole convention, and the engine runs up to three
    times a day against the same bar. A sleeve with no `pending_target` — every
    book saved before staging existed — has nothing to fill.
    """
    pending = sleeve.get("pending_target")
    if not pending or str(pending.get("date", "")) >= today:
        return False
    targets = pd.Series(pending.get("weights") or {}, dtype=float)
    rebalance_sleeve(region, sleeve, targets, px, today, trade_log,
                     frozen=set(pending.get("frozen") or ()))
    sleeve["last_rebalance_date"] = today          # the FILL date, not the decision's
    sleeve["last_decision_date"] = str(pending["date"])   # ...kept: the diagnosis asks there
    sleeve.pop("pending_target", None)
    return True


def _should_rebalance(sleeve: dict, today: str, this_month: str) -> bool:
    """Whether a sleeve should rebalance on this run.

    Fires once per calendar month (mirroring the backtest's month-end signal),
    but only if at least `cfg.MIN_REBALANCE_GAP_DAYS` have elapsed since the last
    rebalance — so a book funded late in a month isn't churned days later on the
    1st. When the gap blocks it, `last_rebalance_month` is left untouched so the
    next run re-checks and trades as soon as the gap clears.
    """
    if sleeve.get("last_rebalance_month") == this_month:
        return False
    gap = cfg.MIN_REBALANCE_GAP_DAYS
    last = sleeve.get("last_rebalance_date")
    if gap and last:
        try:
            if (date.fromisoformat(today) - date.fromisoformat(last)).days < gap:
                return False
        except (ValueError, TypeError):
            pass
    return True


def run_daily(account: str, synthetic: bool) -> None:
    """Run one daily paper cycle for `account`.

    The whole load→mutate→save cycle is serialised under a per-account advisory
    lock so a manual run and the background scheduler can't interleave and
    clobber each other's writes: SQLite makes each individual commit atomic but
    does NOT stop a read-modify-write race across two processes (see
    trading_algo.storage). The lock sits alongside the state it guards.
    """
    with storage.account_lock(account, lock_dir=os.path.abspath(STATE_DIR)):
        _run_daily_locked(account, synthetic)


def _run_daily_locked(account: str, synthetic: bool) -> None:
    warn_if_capacity_unhonoured()
    state = load_state(account)
    # Carry forward the last known-good rates so a transient single-pair fetch
    # failure can't NaN the book's equity (see fx_snapshot). Merge rather than
    # replace: a currency with no rate THIS run keeps its prior key untouched,
    # and a failed pair (omitted by fx_snapshot) can never overwrite a good rate.
    snap = fx_snapshot(synthetic, prev=state.get("fx_snapshot"),
                       regions=_account_regions(state))
    state["fx_snapshot"] = {**(state.get("fx_snapshot") or {}), **snap}

    # Drawdown circuit breaker: was the account halted by a prior run? Stay flat
    # while cooling off; the re-entry decision is made AFTER the report date is
    # known (below) so the cooldown counts distinct MARKET DAYS, not runs — the
    # engine fires up to 3x a day (one pass per regional close), and a per-run
    # countdown would expire ~3x too fast.
    halted = state.get("risk_halted", False)
    was_halted = halted                       # F12: detect the halt/resume transition

    report_date = ""
    combined = 0.0
    breakdown = {}
    px_by_region: dict = {}
    bar_of: dict[str, str] = {}  # each sleeve's own bar this run (a trip stages on it)
    rebalanced_this_run = False
    all_valued = True            # every sleeve had a fresh bar AND a usable FX rate
    for k in _account_regions(state):
        region = get_region(k)
        prices, index_px = latest_region_data(region, synthetic)
        px_today = prices.iloc[-1]
        today = prices.index[-1].strftime("%Y-%m-%d")
        sleeve = state["sleeves"][k]
        this_month = today[:7]

        # Wall-clock freshness gate: never trade or mark on a stale bar (a feed
        # that stopped updating). Synthetic data is a fixed offline fixture, so
        # the gate only applies to real runs; weekends/holidays don't trip it.
        if not synthetic and _is_stale(region, prices.index[-1]):
            print(f"  [{k}] ⚠ latest bar {today} is stale — skipping trade & mark.")
            notifications.notify(
                "stale_data",
                f"[{account}] {k} latest bar {today} is stale — skipping trade & "
                f"mark this run (data feed may be down)",
                level="alert", account=account, region=k, last_bar=today)
            _record_sleeve_status(sleeve, today, "cash:stale-data")
            all_valued = False
            continue

        # A bar can be fresh by DATE and still carry no usable close for what we
        # HOLD. `sleeve_equity_local` skips unpriced positions, so the sleeve
        # would mark at cash only — an invented collapse that reads exactly like
        # a real loss and goes straight to the drawdown breaker. This is what
        # liquidated the live `full` book on 2026-08-27: FTSE marked £4,175
        # instead of £17,129 and tripped a 25% stop that had not been breached.
        # Treat it like a stale bar: don't trade, don't mark, valuation is
        # incomplete. The status carries no "cash:" prefix because the sleeve is
        # NOT flat — it holds positions we merely cannot price, and calling it
        # flat would start `flat_since` and raise a false idle-sleeve finding.
        unpriced = unpriced_holdings(sleeve, px_today)
        if unpriced:
            held = sum(1 for sh in sleeve["positions"].values() if sh)
            blackout = len(unpriced) == held      # nothing we hold has a price
            shown = ", ".join(sorted(unpriced)[:5])
            more = f" +{len(unpriced) - 5} more" if len(unpriced) > 5 else ""
            print(f"  [{k}] ⚠ {len(unpriced)}/{held} held name(s) unpriced on "
                  f"{today} ({shown}{more}) — "
                  f"{'skipping trade & mark' if blackout else 'valuation untrusted'}.")
            notifications.notify(
                "unpriced_holdings",
                f"[{account}] {k}: {len(unpriced)} of {held} held name(s) have no "
                f"price on {today}; the sleeve would mark at cash only, so this "
                f"run's valuation is not trusted for the drawdown breaker",
                level="alert", account=account, region=k, last_bar=today,
                unpriced=sorted(unpriced), blackout=blackout)
            # Either way the total is untrustworthy, so the breaker and the
            # equity history must not act on it.
            all_valued = False
            if blackout:
                # A total blackout is a feed outage — trading would be sized off
                # cash-only equity, which is exactly how the live FTSE sleeve
                # liquidated at £4,175 instead of £17,129. Sit the run out. The
                # status carries no "cash:" prefix because the sleeve is NOT flat
                # (it holds positions we cannot price); calling it flat would
                # start `flat_since` and raise a false idle-sleeve finding.
                _record_sleeve_status(sleeve, today, "unpriced")
                continue
            # A PARTIAL gap is usually one name going dark for good (a delisting),
            # not an outage. Skipping the sleeve on that would freeze it forever,
            # so keep trading — only the valuation is held back.

        px_by_region[k] = px_today
        bar_of[k] = today
        report_date = max(report_date, today)
        rate = fx.base_per_local(snap, region.currency, cfg.BASE_CURRENCY)
        rate_ok = rate is not None

        params = _account_params(state, region)
        # Cash events first: they accrue to the book AS IT STOOD over the period,
        # before any of this run's trades change it.
        _accrue_cash_interest(region, sleeve, px_today, today, state["trades"])
        _credit_dividends(region, sleeve, today, state["trades"], synthetic,
                          account=account, px=px_today)
        status = None                       # why the sleeve ended this run as it did
        pending = sleeve.get("pending_target")
        if halted and (pending.get("weights") if pending else sleeve["positions"]):
            # A halted book's only legal target is cash. The trip itself staged
            # the exit (below); this catches anything else — a stale buy staged
            # before the trip, or a book halted before staging existed — and
            # overwrites it so it can never fill. An exit already staged is left
            # alone, or re-dating it every run would keep it from ever filling.
            print(f"  [{k}] ⛔ drawdown halt — staging a liquidation for the "
                  f"next close.")
            _stage_target(sleeve, today, pd.Series(dtype=float))

        # Execute the target staged on an EARLIER bar, at today's close. Every
        # fill in this book therefore lands one session after its decision.
        if rate_ok and _fill_pending(region, sleeve, px_today, today,
                                     state["trades"]):
            rebalanced_this_run = True

        if halted:
            # Do NOT stamp last_rebalance_month while halted: that would calendar-
            # pin the sleeve flat until the next month even after the cooldown
            # clears. Re-entry is cooldown-driven (month/date cleared on resume).
            status = "cash:halted"
        elif not rate_ok:
            # No base-currency rate → the min-viable gate and the mark can't be
            # sized. Hold cash rather than let `NaN < MIN_VIABLE` (False) fall
            # through and trade on an unvaluable book.
            print(f"  [{k}] ⚠ no valuation rate for {region.currency} — holding cash.")
            notifications.notify(
                "fx_unavailable",
                f"[{account}] {k} valuation rate for {region.currency} unavailable "
                f"— holding cash this run",
                level="alert", account=account, region=k, currency=region.currency)
            status = "cash:fx-unavailable"
        elif _should_rebalance(sleeve, today, this_month):
            # Reached only when rate_ok is True (the `not rate_ok` branch above
            # was skipped), so `rate` is a positive float, not None.
            assert rate is not None
            eq_base_pre = sleeve_equity_local(sleeve, px_today) * rate
            if eq_base_pre < cfg.MIN_VIABLE_EQUITY_BASE:
                print(f"  [{k}] below min viable size "
                      f"({eq_base_pre:,.0f} {cfg.BASE_CURRENCY}) — holding cash.")
                status = "cash:below-min"
            else:
                elig, dq = data_quality.eligible(prices, region, prices.index[-1])
                if dq.excluded:
                    print(f"  [{k}] data-quality: freezing "
                          + ", ".join(f"{t} ({dq.reasons[t]})" for t in sorted(dq.excluded)))
                targets = strategy.compute_targets(prices, index_px, params,
                                                   eligible=elig)
                if targets.empty:
                    reason = _empty_target_reason(prices, index_px, params, elig)
                    print(f"  [{k}] flat — {reason} (holding cash).")
                    status = f"cash:{reason}"
                    # Persist WHY across the days that follow. The daily status
                    # is overwritten with the generic 'cash:idle' on every
                    # non-rebalance day, which erases the difference between
                    # "the regime gate said cash" (correct, and the audit should
                    # stay quiet) and "the feed was broken" (an emergency).
                    sleeve["last_flat_reason"] = reason
                else:
                    status = "rebalanced"
                    sleeve.pop("last_flat_reason", None)
                _stage_target(sleeve, today, targets, frozen=dq.excluded)
            sleeve["last_rebalance_month"] = this_month
        else:
            status = "held" if sleeve["positions"] else "cash:idle"

        _record_sleeve_status(sleeve, today, status)

        if rate_ok:
            assert rate is not None      # rate_ok implies a positive float rate
            eq_local = sleeve_equity_local(sleeve, px_today)
            eq_base = eq_local * rate
            breakdown[k] = (eq_local, region.currency, eq_base)
            combined += eq_base
        else:
            all_valued = False       # unvaluable sleeve → don't fabricate a total

    # F4: on a monthly rebalance, optionally true sleeves back to target
    # allocation (cash transfers, FX spread charged). Default off; needs >1 sleeve
    # AND a complete valuation (every sleeve fresh & priced) so the cross is sound.
    if (cfg.PAPER_ALLOCATION_REBALANCE and rebalanced_this_run and not halted
            and all_valued and len(state["sleeves"]) > 1):
        fx_cost = rebalance_allocations(
            state["sleeves"], snap, state.get("allocations") or cfg.ALLOCATIONS,
            px_by_region)
        if fx_cost:
            state["fx_rebalance_cost"] = state.get("fx_rebalance_cost", 0.0) + fx_cost
            print(f"  ↔ allocation true-up — FX cost {fx_cost:,.2f} {cfg.BASE_CURRENCY}")
            combined = sum(sleeve_equity_local(state["sleeves"][k], px_by_region[k])
                           * snap[get_region(k).currency] for k in _account_regions(state))

    # Update peak and decide whether to trip / clear the breaker for the next run.
    # The threshold is per-book: a profile may loosen it or disable it (None) for
    # a max-risk book. Only act on a COMPLETE valuation — never trip or resume the
    # breaker (nor move the peak) off a partial book (a stale bar / failed pair).
    stop = _account_drawdown_stop(state)
    peak = float(state.get("peak_equity_base") or state["initial_capital_base"])
    if not all_valued:
        print("  ⚠ incomplete valuation this run — breaker & history untouched.")
    else:
        peak = max(peak, combined)
        state["peak_equity_base"] = peak
        if halted:
            # Cool down one distinct market day at a time (multiple runs on the
            # same report_date count once), then re-arm trading on the next run.
            if state.get("halt_last_day") != report_date:
                state["halt_cooldown"] = state.get("halt_cooldown", 0) - 1
                state["halt_last_day"] = report_date
            state["risk_halted"] = state["halt_cooldown"] > 0
        elif stop is not None and combined / peak - 1 <= -stop:
            state["risk_halted"] = True
            state["halt_cooldown"] = cfg.DRAWDOWN_COOLDOWN_DAYS
            state["halt_last_day"] = report_date
            print(f"  ⛔ drawdown {combined / peak - 1:.1%} breached "
                  f"{stop:.0%} stop — halting for "
                  f"{cfg.DRAWDOWN_COOLDOWN_DAYS} market days.")
            # The breaker decides at this close, as a rebalance does, so its exit
            # is staged here on each sleeve's own bar and fills at that sleeve's
            # NEXT close — `backtest.py` stages CASH the same way. It replaces
            # anything staged earlier this run. A complete valuation means every
            # sleeve was priced this run, so each has a bar.
            for k, bar in bar_of.items():
                sl = state["sleeves"][k]
                if sl["positions"] or sl.get("pending_target"):
                    _stage_target(sl, bar, pd.Series(dtype=float))
        else:
            state["risk_halted"] = False

    # F12: alert exactly once on a halt/resume transition (not every halted day).
    transition = notifications.breaker_transition(
        was_halted, state.get("risk_halted", was_halted))
    if transition == "resume":
        # Cooldown cleared: let every sleeve re-enter on the NEXT run per the
        # cooldown, not the calendar — clear the month/date pins so a book halted
        # mid-month isn't stuck flat until the 1st.
        for sl in state["sleeves"].values():
            sl["last_rebalance_month"] = None
            sl["last_rebalance_date"] = None
    if transition:
        dd = combined / peak - 1 if peak else 0.0
        notifications.notify(
            f"breaker_{transition}",
            f"[{account}] drawdown breaker {transition.upper()} on {report_date} — "
            f"equity {combined:,.0f} {cfg.BASE_CURRENCY} ({dd:+.1%} vs peak)",
            level="alert", account=account, transition=transition,
            equity=round(combined, 2), drawdown=round(float(dd), 4))

    # Never persist a NaN/partial total into the equity/sleeve history — a
    # residual gap here means a currency failed to fetch AND had no prior rate, or
    # a bar was stale. Skip the row rather than corrupt the series the dashboard
    # and metrics read.
    if not all_valued:
        print(f"  ⚠ incomplete valuation for {report_date or 'this run'} — "
              f"skipping history update this run.")
    elif combined != combined:
        print(f"  ⚠ combined equity is NaN for {report_date} (FX data gap) — "
              f"skipping history update this run.")
    elif not state["equity_history"] or state["equity_history"][-1][0] != report_date:
        state["equity_history"].append([report_date, round(combined, 2)])
        sleeve_row: dict[str, str | float] = {"date": report_date}
        sleeve_row.update({k: round(v[2], 2) for k, v in breakdown.items()})
        state.setdefault("sleeve_history", []).append(sleeve_row)
    save_state(account, state)

    pnl = combined / state["initial_capital_base"] - 1
    print(f"\n{report_date}  combined {combined:,.0f} {cfg.BASE_CURRENCY} "
          f"({pnl:+.2%} since inception)")
    for k, (loc, ccy, base) in breakdown.items():
        print(f"    {k:<5} {loc:>12,.0f} {ccy}  ->  {base:>12,.0f} {cfg.BASE_CURRENCY}")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def status(account: str) -> None:
    state = load_state(account)
    eq = pd.DataFrame(state["equity_history"], columns=["date", "equity"])
    print("=" * 52)
    print(f"  Paper Account '{account}'  (base {state['base_currency']})")
    print("=" * 52)
    if eq.empty:
        print("  No history yet — run a daily update first.")
    else:
        eq["date"] = pd.to_datetime(eq["date"])
        s = eq.set_index("date")["equity"]
        rets = s.pct_change(fill_method=None).dropna()
        print(f"  Inception        {s.index[0].date()}  "
              f"({state['initial_capital_base']:,.0f} {state['base_currency']})")
        print(f"  Current equity   {s.iloc[-1]:,.2f} {state['base_currency']}")
        print(f"  Total return     {s.iloc[-1] / state['initial_capital_base'] - 1:+.2%}")
        if len(rets) > 20:
            ppy = metrics.periods_per_year(s.index)     # daily marks -> 252
            vol = metrics.annualised_vol(rets, periods_per_year=ppy)
            print(f"  Ann. vol         {vol:.1%}")
            print(f"  Max drawdown     {metrics.max_drawdown(s).value:.2%}")
        print(f"  Trades to date   {sum(1 for t in state['trades'] if pnl.is_fill(t))}")

    fees_by_ccy: dict[str, float] = {}
    for t in state["trades"]:
        fees_by_ccy[t["currency"]] = fees_by_ccy.get(t["currency"], 0.0) \
            + t["commission"] + t.get("stamp_duty", 0.0)
    if fees_by_ccy:
        print("  Fees paid        " + ", ".join(f"{v:,.2f} {c}"
                                                 for c, v in fees_by_ccy.items()))
    print("\n  Holdings by sleeve:")
    for k in _account_regions(state):
        sleeve = state["sleeves"][k]
        print(f"    [{k}] cash {sleeve['cash']:,.2f} {sleeve['currency']}")
        for t, sh in sorted(sleeve["positions"].items()):
            print(f"        {t:<10} {sh:>8} shares")
        if not sleeve["positions"]:
            print("        (all cash)")


def compare(accounts: list[str]) -> None:
    print(f"{'Account':<10} {'Capital':>14} {'Equity':>14} {'Return':>9} {'Trades':>7}")
    for name in accounts:
        if not account_exists(name):
            continue
        s = load_state(name)
        eq = s["equity_history"][-1][1] if s["equity_history"] else s["initial_capital_base"]
        print(f"{name:<10} {s['initial_capital_base']:>14,.0f} {eq:>14,.2f} "
              f"{eq / s['initial_capital_base'] - 1:>+8.2%} "
              f"{sum(1 for t in s['trades'] if pnl.is_fill(t)):>7}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def tca_status(account: str) -> None:
    """Print the execution-quality (implementation-shortfall) report (F11)."""
    state = load_state(account)
    rep = tca.tca_report(state.get("trades", []))
    print("=" * 52)
    print(f"  Execution TCA — account '{account}'")
    print("=" * 52)
    regions = [k for k in rep if k != "alerts"]
    if not regions:
        print("  No fills with a decision price yet.")
    for rk in regions:
        e = rep[rk]
        modelled = e["modelled_slippage_bps"]
        modelled_s = f"{modelled:.1f}" if modelled is not None else "n/a"
        flag = "  ⚠ ALERT" if e.get("alert") else ""
        print(f"  [{rk}] {e['n_fills']} fills | realized "
              f"{e['realized_slippage_bps']:.1f}bps vs modelled {modelled_s}bps | "
              f"IS {e['implementation_shortfall']:,.2f} {e['currency']}{flag}")
    for a in rep["alerts"]:
        print(f"  ⚠ {a}")


def _backtest_for_book(state: dict, synthetic: bool) -> dict:
    """The portfolio backtest this book is compared against, like for like.

    The one place `--attribution` and `--reconcile` build their comparison, so
    the two cannot drift apart. Returns `run_portfolio_backtest`'s full result.
    Four things make it the book's own strategy:

      * REGIONS AND ALLOCATIONS are the book's (`state["allocations"]`), not
        `config.ALLOCATIONS`, which funds a sleeve `full` does not hold.
      * CAPITAL AND START are the book's own: each sleeve TRADES FROM the bar
        it first decided on (`opened_on`, recorded by `_stage_target`) with
        `initial_capital_base` split by the book's allocations. On a first run
        each sleeve decides on its OWN latest bar, so a sleeve whose bar was
        older that run opens before the book's first equity date; a sleeve
        with no recorded bar (every book opened before the record existed, or
        a new one that has not decided yet) opens on that first equity date. History before a sleeve's open is
        warm-up only — the signals read it, the sleeve holds nothing — and
        its first bar on or after it decides, as the book did. The per-order
        commission floor (Task 13) makes cost depend on size, so the size has
        to be the book's on the day the window opens: funded at the book's
        capital in 2012 instead, the comparison had compounded to 1.3-5.3x the
        book by its open date and charged the floor at that size, and
        `_predicted_curve` could re-level the curve but not undo a cost that
        depends on size. Each sleeve now stands at the book's funded sleeve
        capital on its open bar, and the whole comparison at the book's
        capital before its first open: the bridge's since-inception anchor.
      * PARAMS are the paper path's (`_account_params`): the profile's overrides
        layered on each region's defaults. `run_portfolio_backtest(params=...)`
        replaces EVERY region's params wholesale, so a plain book passes none
        and a profiled book passes its one layered set. It refuses when the
        regions would end up with different params, because no single set
        then reproduces the book.
      * The window reaches the book's LAST equity date. On real data `end`
        goes to yfinance, whose `end` is exclusive, so the request runs one
        day past it; `_predicted_curve` trims anything beyond.

    The profile's drawdown-breaker override is NOT reproduced: the backtest
    applies `cfg.MAX_DRAWDOWN_STOP`, and `run_portfolio_backtest` has no seam
    for a per-book threshold.
    """
    from .portfolio_backtest import run_portfolio_backtest

    regions = _account_regions(state)
    params = None
    if state.get("param_overrides"):
        layered = [_account_params(state, get_region(k)) for k in regions]
        if any(p != layered[0] for p in layered[1:]):
            raise ValueError(
                f"the profile leaves regions {regions} with different params; "
                "run_portfolio_backtest takes one set for every region, so it "
                "cannot reproduce this book")
        params = layered[0]
    last = pd.Timestamp(state["equity_history"][-1][0])
    first = state["equity_history"][0][0]
    sleeves = state.get("sleeves") or {}
    return run_portfolio_backtest(
        regions=regions, synthetic=synthetic, params=params,
        end=(last + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        allocations=state.get("allocations"),
        initial_capital=state["initial_capital_base"],
        trade_from={k: pd.Timestamp((sleeves.get(k) or {}).get("opened_on") or first)
                    for k in regions})


def _predicted_curve(state: dict, backtest: dict) -> pd.Series:
    """The backtest's equity over the book's own dates, in the book's money:
    levelled so the backtest's INCEPTION, its first value (before any sleeve
    opened), is the book's initial capital — the bridge's anchor on both
    sides. `_backtest_for_book` funds it at that capital, so the factor is
    1.0; levelling at the book's first equity date instead would drop the
    build-out a sleeve that opened a bar earlier paid on that date. Fewer
    than 2 points means there is nothing to compare."""
    eh = state["equity_history"]
    eq = backtest["equity"]
    window = eq[(eq.index >= pd.Timestamp(eh[0][0]))
                & (eq.index <= pd.Timestamp(eh[-1][0]))]
    if len(window) < 2:
        return window
    return window / float(eq.iloc[0]) * float(state["initial_capital_base"])


def attribution_status(account: str, synthetic: bool) -> None:
    """Live-vs-backtest tracking + attribution report for a book (F3).

    Builds the backtest-predicted curve over the SAME window the book traded
    (full history for signal warm-up, then sliced to the book's dates, in the
    book's money — no hindsight refetch), compares, and alerts
    if tracking error blows the budget.
    """
    state = load_state(account)
    eh = state.get("equity_history", [])
    predicted = None
    if len(eh) >= 2:
        try:
            window = _predicted_curve(state, _backtest_for_book(state, synthetic))
            if len(window) >= 2:
                predicted = window
        except Exception as exc:            # real data needs network; synthetic is offline
            print(f"  (predicted curve unavailable: {exc})")

    rep = attribution.attribution_report(state, predicted)
    print("=" * 52)
    print(f"  Live-vs-backtest attribution — account '{account}'")
    print("=" * 52)
    print(f"  Realized return   {rep['realized_total_return']:+.2%}  "
          f"(since inception, {rep['n_equity_points']} points)")
    if "predicted_total_return" in rep:
        print(f"  Backtest return   {rep['predicted_total_return']:+.2%}")
        print(f"  Divergence        {rep['divergence']:+.2%}")
        te = rep["tracking_error_bps"]
        te_s = f"{te:.0f}bps" if te is not None else "n/a"
        flag = "  ⚠ OVER BUDGET" if rep.get("tracking_alert") else ""
        print(f"  Tracking error    {te_s} (budget "
              f"{int(attribution.TRACKING_ERROR_ALERT_BPS)}bps){flag}")
    else:
        print("  Backtest return   n/a (need a predicted curve)")
    print("  Realized cost drag by sleeve:")
    for rk, c in rep["cost_drag_by_region"].items():
        print(f"    [{rk}] {c['cost_drag_bps']:.1f}bps  "
              f"({c['cost']:,.2f} {c['currency']} on {c['notional']:,.0f} traded)")

    if rep.get("tracking_alert"):
        _print_tracking_diagnosis(account, state)
        notifications.notify(
            "tracking_error",
            f"[{account}] live tracking error {rep['tracking_error_bps']:.0f}bps "
            f"exceeds the {int(attribution.TRACKING_ERROR_ALERT_BPS)}bps budget "
            "— check the exposure gap and rebalance dates in the report before "
            "concluding the book is mis-executing",
            level="alert", account=account,
            tracking_error_bps=rep["tracking_error_bps"])


def _print_tracking_diagnosis(account: str, state: dict) -> None:
    """Explain a tracking-error breach instead of just announcing it.

    Measured on `full` 2026-09-19 (733bps against a 200bps budget): the mean
    daily difference was +1.4bps, i.e. no systematic drift at all — the breach
    is dispersion, and it has two structural sources that are NOT execution
    error:

      1. EXPOSURE GAP. Paper holds whole shares above a per-region dust floor,
         so it lands at some fraction of the gross the strategy asked for. The
         backtest holds the target exactly.
      2. REBALANCE TIMING. Paper rebalances on the first run of a calendar month
         subject to MIN_REBALANCE_GAP_DAYS; the backtest rebalances at month
         END. For most of a month the two hold different books, so on the days
         one turns over and the other does not, returns diverge sharply.

    Printing both makes the number interpretable. A breach with a large exposure
    gap or mismatched dates is a COMPARISON artefact; a breach with neither is
    the execution problem the budget was written to catch.
    """
    from . import data, data_quality, strategy
    from .regions import get_region

    def bar(index, day):
        """The last bar on or before `day`; the latest bar when there is none."""
        if day:
            loc = index.searchsorted(pd.Timestamp(day), side="right") - 1
            if loc >= 0:
                return index[loc]
        return index[-1]

    print("  Tracking-error diagnosis (before blaming execution):")
    for k, sl in (state.get("sleeves") or {}).items():
        try:
            region = get_region(k)
            px, ix = data.load_region(region, cfg.START)
            # Ask exactly what the live rebalance asked (see run_daily): the
            # gated candidate set, as of the DECISION bar. Then mark the book at
            # the FILL bar, where it was executed — a decision fills one session
            # later. Asking at today's bar, or at the fill bar, with or without
            # the gate, compares two different questions and invents an
            # exposure gap. A book saved before decisions were recorded has one
            # date for both; and a fill date cleared on resume or by
            # --force-rebalance takes its decision date with it.
            lrd = sl.get("last_rebalance_date")
            filled = bar(px.index, lrd)
            decided = bar(px.index, (sl.get("last_decision_date") if lrd else None)
                          or lrd)
            elig, _dq = data_quality.eligible(px, region, decided)
            # Positions have not moved since the fill (rebalances are monthly),
            # so marking them at the fill bar is the book as executed.
            invested = sum(n * float(px[t].loc[filled])
                           for t, n in (sl.get("positions") or {}).items()
                           if t in px.columns)
            eq = invested + float(sl.get("cash", 0.0))
            target = strategy.compute_targets(
                px, ix, _account_params(state, region),
                asof=decided, eligible=elig).abs().sum()
            held = invested / eq if eq else 0.0
            gap = (held / target - 1.0) if target else 0.0
            print(f"    [{k}] held gross {held:6.1%} vs target {target:6.1%}"
                  f"  ({gap:+.0%} of target)   target as of {decided.date()},"
                  f" held as of {filled.date()}"
                  f"   last rebalance {lrd or 'never'}")
        except Exception as exc:                     # diagnosis must never fail the report
            print(f"    [{k}] diagnosis unavailable: {exc}")
    print("    NOTE: the predicted curve trades each sleeve from the bar it first "
          "decided on (or, without that record, this book's first equity date "
          f"{(state.get('equity_history') or [['?']])[0][0]}) at its capital; "
          f"history from {cfg.START} is signal warm-up only. The two causes above "
          "remain by construction — expect dispersion that is not mis-execution.")


def reconcile_status(account: str, synthetic: bool) -> None:
    """Print the backtest -> paper reconciliation bridge (spec §5).

    Same predicted curve `--attribution` builds: a backtest over the SAME window
    the book traded, sliced to the book's dates, in the book's money — no
    hindsight refetch (invariant #1). The bridge measures both sides since
    inception, so the money column is priced at its denominator, the book's
    initial capital, and reads the book's actual amounts since then.
    """
    state = load_state(account)
    eh = state.get("equity_history", [])
    print("=" * 52)
    print(f"  Reconciliation bridge — account '{account}'")
    print("=" * 52)
    if len(eh) < 2:
        print("  Not enough history yet — run a daily update first.")
        return
    try:
        res = _backtest_for_book(state, synthetic)
        rep = attribution.reconcile(state, _predicted_curve(state, res),
                                    backtest=res)
    except Exception as exc:        # real data needs network; synthetic is offline
        print(f"  bridge unavailable: {exc}")
        return
    print(attribution.format_bridge(
        rep, state.get("base_currency", cfg.BASE_CURRENCY), rep["start_equity"]))


def promotion_status(account: str) -> None:
    """Show the paper->live promotion checklist for a book (F10)."""
    state = load_state(account)
    v = promotion.promotion_check(state)
    print("=" * 52)
    print(f"  Promotion readiness — account '{account}'")
    print("=" * 52)
    print(f"  {'READY FOR LIVE ✅' if v['ready'] else 'NOT READY ❌'}   "
          f"({v['rebalance_months']} rebalance months, equity {v['equity']:,.0f})")
    for name, ok in v["checks"].items():
        print(f"    [{'✓' if ok else '✗'}] {name}")
    for r in v["reasons"]:
        print(f"    - {r}")
    if v["reasons"]:
        print("  (DSR/PBO come from `sweep --purged-cv`; tracking from "
              "`--attribution`; pass them to the gate before going live.)")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Multi-region momentum paper trader")
    ap.add_argument("--account", default="main", help="account name (separate state per name)")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--capital", type=float, default=cfg.INITIAL_CAPITAL)
    ap.add_argument("--regions", nargs="+", metavar="KEY", choices=list(REGIONS),
                    help="(--init) restrict the account to these regions, equal-weighted "
                         "(e.g. --regions US). Default: all three.")
    ap.add_argument("--profile", choices=list(profiles.PROFILES),
                    help="(--init) open the book from a named strategy/risk preset "
                         "(e.g. ultra, experimental). See trading_algo/profiles.py.")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--tca", action="store_true",
                    help="execution-quality report: implementation shortfall vs "
                         "modelled slippage per region (F11)")
    ap.add_argument("--attribution", action="store_true",
                    help="live-vs-backtest tracking + attribution report (F3)")
    ap.add_argument("--reconcile", action="store_true",
                    help="reconciliation bridge: the signed, line-by-line walk "
                         "from the backtest's return to this book's (spec §5)")
    ap.add_argument("--promotion", action="store_true",
                    help="paper->live promotion readiness checklist (F10)")
    ap.add_argument("--force", action="store_true",
                    help="(--init) RESET an existing book. Destroys its trade "
                         "ledger, and all P&L is derived from that ledger.")
    ap.add_argument("--force-rebalance", action="store_true")
    ap.add_argument("--compare", nargs="+", metavar="ACCT")
    ap.add_argument("--synthetic", action="store_true", help="run offline on synthetic data")
    args = ap.parse_args(argv)

    if args.compare:
        compare(args.compare)
    elif args.init:
        allocations = {r: 1.0 for r in args.regions} if args.regions else None
        init_account(args.account, args.capital, args.synthetic, allocations,
                     profile=args.profile, force=args.force)
    elif args.status:
        status(args.account)
    elif args.tca:
        tca_status(args.account)
    elif args.attribution:
        attribution_status(args.account, args.synthetic)
    elif args.reconcile:
        reconcile_status(args.account, args.synthetic)
    elif args.promotion:
        promotion_status(args.account)
    elif args.force_rebalance:
        state = load_state(args.account)
        for s in state["sleeves"].values():
            s["last_rebalance_month"] = None
            s["last_rebalance_date"] = None   # bypass the min-gap guard
        save_state(args.account, state)
        run_daily(args.account, args.synthetic)
    else:
        run_daily(args.account, args.synthetic)


if __name__ == "__main__":
    main()
