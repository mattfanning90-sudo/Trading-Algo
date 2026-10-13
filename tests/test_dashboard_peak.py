"""PEAK and OFF-PEAK on the screens are the book's ALL-TIME high.

Task 14 changed what the breaker's stored mark means: `peak_equity_base` /
`peak_equity` is re-based to the equity at RESUME (risk_breaker), so after a
first halt and resume it is no longer the best the book has ever been. The
screens labelled PEAK read that field. They now take the high from the book's
own record instead (`metrics.all_time_peak`); the breaker keeps the stored mark.
"""
import json

import pytest

from trading_algo import metrics
from trading_algo import paper_trade as pt
from trading_algo.dashboard import api, fx_api, overview
from trading_algo.forex import fx_book


@pytest.fixture()
def books(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    return tmp_path


def test_the_all_time_peak_is_the_best_finite_mark():
    hist = [["2026-01-01", 100.0], ["2026-01-02", float("nan")],
            ["2026-01-03", 130.0], ["2026-01-04", None], ["2026-01-05", 90.0]]
    assert metrics.all_time_peak(hist) == 130.0
    assert metrics.all_time_peak(hist, 90.0, None, 140.0) == 140.0
    assert metrics.all_time_peak([], 100.0) == 100.0
    assert metrics.all_time_peak([]) == 0.0


# --- a book that halted and resumed ------------------------------------------
def _resumed_fx(account="rr"):
    """Rose to 6,000, fell, halted, resumed at 5,200: the breaker's mark is
    re-based to 5,200, the book's high is still 6,000."""
    return {"account": account, "currency": "AUD", "profile": "balanced",
            "bar": "1d", "initial_capital": 5000.0, "equity": 5200.0,
            "positions": {}, "last_close": {}, "trades": [], "decisions": {},
            "peak_equity": 5200.0, "risk_halted": False, "halt_cooldown": 0,
            "equity_history": [["2026-07-01", 5000.0], ["2026-07-10", 6000.0],
                               ["2026-08-01", 4700.0], ["2026-09-01", 5200.0]]}


def test_equity_api_shows_the_all_time_high_after_a_resume(books, paper_cycle):
    pt.init_account("full", capital=300_000, synthetic=True)
    state = paper_cycle("full")
    # It once stood at 600,000; since its resume the breaker measures from the
    # equity it resumed at, its latest mark.
    state["equity_history"].insert(0, ["2000-01-03", 600_000.0])
    state["peak_equity_base"] = float(state["equity_history"][-1][1])
    pt.save_state("full", state)

    snap = api.build_snapshot("full", synthetic=True)
    total = snap["kpis"]["total_equity"]
    assert snap["peak_equity"] == pytest.approx(600_000.0)
    assert snap["off_peak"] == pytest.approx(total / 600_000.0 - 1, abs=1e-5)


def test_fx_api_shows_the_all_time_high_after_a_resume(books):
    with open(fx_book._state_file("rr"), "w") as f:
        json.dump(_resumed_fx(), f)
    snap = fx_api.build_fx_snapshot("rr")
    assert snap["peak"] == pytest.approx(6000.0)
    assert snap["off_peak"] == pytest.approx(5200.0 / 6000.0 - 1, abs=1e-6)


def test_overview_cards_show_the_all_time_high_after_a_resume():
    fx_entry = {"key": "RR", "account": "rr", "kind": "fx", "micro": False,
                "label": "FX · RR", "sub": ""}
    card = overview._card(fx_entry, _resumed_fx())
    assert card["status"] == "OFF PEAK −13.3%", card["status"]

    eq_entry = {"key": "FULL", "account": "full", "kind": "equity",
                "micro": False, "label": "FULL", "sub": ""}
    eq = {"initial_capital_base": 100_000.0, "group": "CORE", "trades": [],
          "sleeves": {"US": {"positions": {"AAPL": 3}}},
          "equity_history": [["2026-01-01", 100_000.0], ["2026-02-01", 120_000.0],
                             ["2026-03-01", 90_000.0], ["2026-04-01", 96_000.0]],
          "peak_equity_base": 96_000.0, "risk_halted": False}
    card = overview._card(eq_entry, eq)
    assert card["status"] == "OFF PEAK −20.0%", card["status"]


# --- a book that never halted: the tiles are what they were ------------------
def test_a_never_halted_equity_book_shows_what_it_showed(books, paper_cycle):
    pt.init_account("full", capital=300_000, synthetic=True)
    state = paper_cycle("full")
    snap = api.build_snapshot("full", synthetic=True)
    total = snap["kpis"]["total_equity"]
    was = max(float(state["peak_equity_base"]), total)    # the old formula
    assert snap["peak_equity"] == pytest.approx(was, abs=0.01)
    assert snap["off_peak"] == pytest.approx(total / was - 1, abs=1e-6)


def test_a_never_halted_fx_book_shows_what_it_showed(books):
    st = {**_resumed_fx("nn"), "peak_equity": 6000.0}     # a running high
    with open(fx_book._state_file("nn"), "w") as f:
        json.dump(st, f)
    snap = fx_api.build_fx_snapshot("nn")
    assert snap["peak"] == 6000.0
    assert snap["off_peak"] == pytest.approx(5200.0 / 6000.0 - 1, abs=1e-6)
    entry = {"key": "NN", "account": "nn", "kind": "fx", "micro": False,
             "label": "FX · NN", "sub": ""}
    assert overview._card(entry, st)["status"] == "OFF PEAK −13.3%"
