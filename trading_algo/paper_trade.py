"""Multi-region paper-trading engine — no broker connection required.

Models three self-contained regional sub-books (FTSE / US / ASX). At init the
base-currency capital is split by ALLOCATIONS and converted to each sleeve's
local currency; thereafter each sleeve compounds in its own currency with
whole-share lots, the per-region fee schedule (commission floor + UK stamp duty)
and slippage. Combined equity is reported in the base currency via current FX.

State persists per account in a SQLite store (paper_books.db), with a
paper_state_{account}.json copy dual-written as a fallback. Each sleeve trades
only on the first run of a new month (mirroring the backtest's month-end signal
-> next-day execution).

Note vs the portfolio backtest: this sim funds each sleeve ONCE and lets
allocations drift (the realistic "fund each sub-account, run it" model). The
backtest trues allocations back to target each period — see CLAUDE.md.

Usage
-----
    python -m trading_algo.paper_trade --init --capital 100000   # open account
    python -m trading_algo.paper_trade                           # daily run
    python -m trading_algo.paper_trade --status                  # report
    python -m trading_algo.paper_trade --force-rebalance         # rebalance now
    python -m trading_algo.paper_trade --compare micro full
    (append --synthetic to run fully offline)
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date

import numpy as np
import pandas as pd

from . import config as cfg
from . import signals as sig
from . import (data, data_quality, fees, fx, notifications, pnl, profiles,
               storage, strategy, tca)
from . import state_schema
from .regions import REGIONS, get_region

# State location: env override (used by CI to persist to a tracked dir), else repo root.
STATE_DIR = os.environ.get("MOMENTUM_STATE_DIR") or os.path.join(os.path.dirname(__file__), "..")
MICRO_THRESHOLD = 5_000.0     # below this (local ccy) a sleeve concentrates


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
                prev: dict[str, float] | None = None) -> dict[str, float]:
    """Latest base-per-local multiplier for every funded currency.

    A single failed pair fetch (e.g. AUDUSD=X 403s while AUDGBP=X succeeds)
    comes back as an all-NaN column that ffill can't repair, so its `.iloc[-1]`
    is NaN. Left alone that NaN flows straight into `eq_base = eq_local · rate`
    and poisons the whole book's equity (this is what NaN'd the `full` book's US
    sleeve and headline AUM). FX rates are persistent, so the correct fallback is
    the last known-good rate from `prev` — never a NaN.
    """
    currencies = [get_region(k).currency for k in _regions()]
    if synthetic:
        tbl = fx.synthetic_fx(currencies, base=cfg.BASE_CURRENCY)
    else:
        tbl = fx.load_fx(currencies, cfg.START, base=cfg.BASE_CURRENCY, use_cache=False)
    prev = prev or {}
    snap = {}
    for c in currencies:
        rate = float(tbl[c].iloc[-1])
        if not (rate == rate) or rate <= 0.0:            # NaN or non-positive → failed pair
            fallback = prev.get(c)
            if fallback is not None and fallback == fallback and fallback > 0.0:
                print(f"  ⚠ FX rate for {c} unavailable this run — "
                      f"carrying forward last good {fallback:.4f}")
                rate = float(fallback)
            else:
                print(f"  ⚠ FX rate for {c} unavailable and no prior rate to "
                      f"fall back on — {c} sleeve valuation will be skipped this run")
        snap[c] = rate
    return snap


def latest_region_data(region, synthetic: bool):
    if synthetic:
        return data.synthetic_region(region)
    return data.load_region(region, cfg.START, use_cache=False)


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------
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


def init_account(account: str, capital: float, synthetic: bool,
                 allocations: dict[str, float] | None = None,
                 profile: str | None = None) -> None:
    """Open a paper account. `allocations` overrides which regions it trades and
    their weights (e.g. {"US": 1.0} for a single-region small account). Default
    is the global 3-region split from config.

    `profile` (see trading_algo.profiles) bakes a canned strategy/risk/reporting
    preset into the book: its region allocations, StrategyParams overrides
    (leverage, long/short, …), drawdown-breaker override, and reporting group.
    An explicit `allocations` still wins over the profile's, so a profile can be
    funded across custom regions if desired.
    """
    prof = profiles.get_profile(profile) if profile else None
    if prof is not None and allocations is None:
        allocations = prof.allocations
    alloc_src = allocations if allocations is not None else cfg.ALLOCATIONS
    regions = list(alloc_src)
    unknown = [k for k in regions if k not in REGIONS]
    if unknown:
        raise SystemExit(f"Unknown region(s) {unknown}. Known: {list(REGIONS)}")

    snap = fx_snapshot(synthetic)
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
        picks = affordable[:max(1, min(3, int(equity // 40)))] if affordable else []
        if picks:
            targets = pd.Series(0.97 / len(picks), index=picks)
            print(f"    ⚠ micro mode: concentrating into {picks}")
        else:
            targets = pd.Series(dtype=float)
            print("    ⚠ no affordable names — staying in cash")

    dust = min(200.0, equity * 0.05)
    desired = {}
    for t, w in targets.items():
        price = px.get(t)
        if price and price == price and price > 0:
            desired[t] = int((equity * w) / price)

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
    state = load_state(account)
    # Carry forward the last known-good rates so a transient single-pair fetch
    # failure can't NaN the book's equity (see fx_snapshot).
    snap = fx_snapshot(synthetic, prev=state.get("fx_snapshot"))
    state["fx_snapshot"] = snap

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
    for k in _account_regions(state):
        region = get_region(k)
        prices, index_px = latest_region_data(region, synthetic)
        px_today = prices.iloc[-1]
        today = prices.index[-1].strftime("%Y-%m-%d")
        report_date = max(report_date, today)
        sleeve = state["sleeves"][k]
        this_month = today[:7]

        params = _account_params(state, region)
        status = None                       # why the sleeve ended this run as it did
        if halted:
            if sleeve["positions"]:
                print(f"  [{k}] ⛔ drawdown halt — liquidating to cash.")
                rebalance_sleeve(region, sleeve, pd.Series(dtype=float),
                                 px_today, today, state["trades"])
                sleeve["last_rebalance_date"] = today
            sleeve["last_rebalance_month"] = this_month
            status = "cash:halted"
        elif _should_rebalance(sleeve, today, this_month):
            eq_base_pre = sleeve_equity_local(sleeve, px_today) * snap[region.currency]
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
                else:
                    status = "rebalanced"
                rebalance_sleeve(region, sleeve, targets, px_today, today,
                                 state["trades"], frozen=dq.excluded)
                sleeve["last_rebalance_date"] = today
            sleeve["last_rebalance_month"] = this_month
        else:
            status = "held" if sleeve["positions"] else "cash:idle"

        _record_sleeve_status(sleeve, today, status)

        eq_local = sleeve_equity_local(sleeve, px_today)
        eq_base = eq_local * snap[region.currency]
        breakdown[k] = (eq_local, region.currency, eq_base)
        combined += eq_base

    # Update peak and decide whether to trip / clear the breaker for the next run.
    # The threshold is per-book: a profile may loosen it or disable it (None) for
    # a max-risk book.
    stop = _account_drawdown_stop(state)
    peak = max(state.get("peak_equity_base", state["initial_capital_base"]), combined)
    state["peak_equity_base"] = peak
    if halted:
        # Cool down one distinct market day at a time (multiple runs on the same
        # report_date count once), then re-arm trading on the next run.
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
    else:
        state["risk_halted"] = False

    # F12: alert exactly once on a halt/resume transition (not every halted day).
    transition = notifications.breaker_transition(was_halted, state["risk_halted"])
    if transition:
        dd = combined / peak - 1
        notifications.notify(
            f"breaker_{transition}",
            f"[{account}] drawdown breaker {transition.upper()} on {report_date} — "
            f"equity {combined:,.0f} {cfg.BASE_CURRENCY} ({dd:+.1%} vs peak)",
            level="alert", account=account, transition=transition,
            equity=round(combined, 2), drawdown=round(float(dd), 4))

    # Never persist a NaN into the equity/sleeve history — a residual NaN here
    # means a currency failed to fetch AND had no prior rate to carry forward
    # (fx_snapshot already handles the common case). Skip the row rather than
    # corrupt the series the dashboard and metrics read.
    if combined != combined:
        print(f"  ⚠ combined equity is NaN for {report_date} (FX data gap) — "
              f"skipping history update this run.")
    elif not state["equity_history"] or state["equity_history"][-1][0] != report_date:
        state["equity_history"].append([report_date, round(combined, 2)])
        sleeve_row = {"date": report_date}
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
            print(f"  Ann. vol         {rets.std() * np.sqrt(252):.1%}")
            print(f"  Max drawdown     {(s / s.cummax() - 1).min():.2%}")
        print(f"  Trades to date   {len(state['trades'])}")

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
              f"{eq / s['initial_capital_base'] - 1:>+8.2%} {len(s['trades']):>7}")


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
    ap.add_argument("--force-rebalance", action="store_true")
    ap.add_argument("--compare", nargs="+", metavar="ACCT")
    ap.add_argument("--synthetic", action="store_true", help="run offline on synthetic data")
    args = ap.parse_args(argv)

    if args.compare:
        compare(args.compare)
    elif args.init:
        allocations = {r: 1.0 for r in args.regions} if args.regions else None
        init_account(args.account, args.capital, args.synthetic, allocations,
                     profile=args.profile)
    elif args.status:
        status(args.account)
    elif args.tca:
        tca_status(args.account)
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
