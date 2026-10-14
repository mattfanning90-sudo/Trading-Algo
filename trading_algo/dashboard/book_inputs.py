"""Everything one book's screens need, gathered from SAVED data only — no
price download, no network (measured 2026-10-14: the live re-mark took ~50 s
per equity book and disagreed with the home screen). `book_summary` and
`book_positions` are pure functions of what this returns; the only optional
live part is `live`, a payload the background fetch (`live_marks`) finished."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import config as cfg
from .. import fx, verify
from ..forex import fx_book as fxb
from ..forex import fx_pnl, permtest
from . import api, backtest_store, episodes, home, overview


@dataclass
class BookInputs:
    entry: dict
    state: dict
    card: dict
    book: dict
    now: datetime
    findings: list = field(default_factory=list)
    backtest: dict = field(default_factory=lambda: {"available": False})
    permtest: dict | None = None
    roster: int | None = None
    costs: dict = field(default_factory=dict)
    fx_book: dict | None = None
    closed_rows: list = field(default_factory=list)
    positions_closed: list = field(default_factory=list)
    excluded_forced: int = 0
    live: dict | None = None
    market: str = "n/a"
    errors: dict = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return self.entry["kind"]

    @property
    def account(self) -> str:
        return self.entry["account"]


def _safe(errors: dict, name: str, fn, default):
    try:
        return fn()
    except Exception as exc:            # one bad input must not blank the screen
        errors[name] = repr(exc)
        return default


def _equity_costs(state: dict) -> dict:
    base = state.get("base_currency", cfg.BASE_CURRENCY)
    snap = state.get("fx_snapshot") or {}
    fees, stamp = api.fees_by_currency(state.get("trades") or [])

    def conv(ccy, v):
        return v * (fx.base_per_local(snap, ccy, base) or 0.0)

    by_ccy = {c: round(conv(c, v), 2) for c, v in fees.items()}
    return {"total": round(sum(by_ccy.values()), 2), "by_ccy": by_ccy,
            "stamp": {c: round(conv(c, v), 2) for c, v in stamp.items()},
            "includes": "commission and stamp duty"}


def _roster(account: str) -> int | None:
    try:
        with open(os.path.join(fxb.STATE_DIR, f"champions_{account}.json")) as fh:
            return len(json.load(fh).get("roster") or [])
    except (OSError, ValueError):
        return None


def load_inputs(entry: dict, now: datetime, *, live: dict | None = None,
                market: str = "n/a") -> BookInputs | None:
    state = overview._load(entry)
    if state is None:
        return None
    card = overview._card(entry, state)
    if card is None:
        return None
    errors: dict = {}
    inp = BookInputs(entry=entry, state=state, card=card,
                     book=home._book(entry, state, card, now), now=now,
                     live=live, market=market, errors=errors)
    today = now.astimezone(timezone.utc).replace(tzinfo=None)
    inp.findings = _safe(errors, "audit", lambda: verify.verify_book(
        entry["kind"], entry["account"], state, today), [])
    inp.backtest = _safe(errors, "backtest", lambda: backtest_store.load_backtest(
        entry["kind"], entry["account"]), {"available": False})
    forced = episodes.forced_dates(state)
    trades = state.get("trades") or []
    if entry["kind"] == "fx":
        inp.permtest = _safe(errors, "luck test", lambda: permtest.load_report(entry["account"]), None)
        inp.roster = _roster(entry["account"])
        inp.fx_book = _safe(errors, "costs", lambda: fx_pnl.book_pnl(state), None)
        if inp.fx_book is not None:
            inp.costs = {"total": inp.fx_book["costs"], "includes": "the spread paid on trades"}
            inp.closed_rows = inp.fx_book["closed"]
            inp.positions_closed, inp.excluded_forced = _safe(
                errors, "closed positions", lambda: episodes.closed_positions(
                    episodes.fx_events(trades), inp.closed_rows,
                    symbol_of=lambda r: r["pair"], net_of=lambda r: r["net"],
                    exclude_dates=forced), ([], 0))
    else:
        inp.costs = _safe(errors, "costs", lambda: _equity_costs(state), {})
        base = state.get("base_currency", cfg.BASE_CURRENCY)
        closed = _safe(errors, "closed trades", lambda: api.closed_trades(
            trades, state.get("fx_snapshot") or {}, base), {"rows": []})
        inp.closed_rows = closed["rows"]
        inp.positions_closed, inp.excluded_forced = _safe(
            errors, "closed positions", lambda: episodes.closed_positions(
                episodes.equity_events(trades), inp.closed_rows,
                symbol_of=lambda r: (r["region"], r["ticker"]),
                net_of=lambda r: r["net_base"], exclude_dates=forced), ([], 0))
    return inp
