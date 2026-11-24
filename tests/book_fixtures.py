"""Shared helpers for the book-screen tests: the frozen 13 Oct books, a
network tripwire, and a minimal hand-built BookInputs."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "books_20261013"
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
BOOK_FILES = sorted(p.name for p in FIXTURE.glob("*_state_*.json"))


def use_snapshot(tmp_path, monkeypatch) -> Path:
    """Copy the frozen books to a temp dir and point every reader at it — the
    module constants (fixed at import) AND the env vars (read at call time)."""
    import trading_algo.paper_trade as pt
    from trading_algo.forex import fx_book

    dst = tmp_path / "state"
    shutil.copytree(FIXTURE, dst)
    monkeypatch.setattr(pt, "STATE_DIR", str(dst))
    monkeypatch.setattr(fx_book, "STATE_DIR", str(dst))
    monkeypatch.setenv("MOMENTUM_STATE_DIR", str(dst))
    monkeypatch.setenv("FX_STATE_DIR", str(dst))
    return dst


def no_network(monkeypatch) -> None:
    """Any price or FX download fails the test loudly."""
    from trading_algo import data, fx, paper_trade

    def boom(*_a, **_k):
        raise AssertionError("network access during a saved-values build")

    monkeypatch.setattr(data, "_download_primary", boom)
    monkeypatch.setattr(paper_trade, "latest_region_data", boom)
    monkeypatch.setattr(fx, "load_fx", boom)


def make_inp(kind: str = "equity", *, state=None, card=None, book=None, findings=(),
             costs=None, fx_book=None, positions_closed=(), excluded=0, backtest=None,
             permtest=None, roster=None, live=None, market="n/a", entry=None, now=NOW):
    from trading_algo.dashboard.book_inputs import BookInputs

    card = {"key": "T", "name": "T", "kind": kind, "group": "CORE", "equity": 100_000.0,
            "initial": 100_000.0, "since": "2026-06-11", "off_peak": 0.0,
            "breaker": 0.25, "halted": False, "status": "CLEAR", **(card or {})}
    marks = [(datetime(2026, 6, 11, tzinfo=timezone.utc), card["initial"]),
             (datetime(2026, 10, 13, tzinfo=timezone.utc), card["equity"])]
    book = {"halted": card["halted"], "near_breaker": False, "freshness": "fresh",
            "as_of": "2026-10-13T00:00:00+00:00", "_marks": marks, **(book or {})}
    state = {"sleeves": {}, "allocations": {}, "trades": [], "fx_snapshot": {},
             "param_overrides": {}, **(state or {})}
    entry = entry or {"key": "T", "account": "t", "kind": kind, "label": "T", "micro": False}
    return BookInputs(
        entry=entry, state=state, card=card, book=book, now=now, findings=list(findings),
        backtest=backtest or {"available": False}, permtest=permtest, roster=roster,
        costs=costs if costs is not None else {"total": 0.0, "by_ccy": {}, "stamp": {},
                                               "includes": "commission and stamp duty"},
        fx_book=fx_book, positions_closed=list(positions_closed), excluded_forced=excluded,
        live=live, market=market)
