"""The all-accounts rollup: which books move the headline AUM.

CORE is the number the owner watches. An unproven book gets its own total and
is excluded — the EXPERIMENTAL equity books already work that way. The FX books
were forced into CORE, so four books back-testing at Sharpe -2 to -6 inflated
the headline. They keep their own total; it is not this one.
"""
import pytest

from trading_algo import profiles
from trading_algo.dashboard import overview


def _equity_state():
    return {"initial_capital_base": 100_000.0,
            "equity_history": [["2026-01-01", 100_000.0],
                               ["2026-01-02", 101_000.0]],
            "peak_equity_base": 101_000.0,
            "sleeves": {"US": {"positions": {"AAPL": 3}}},
            "trades": [], "group": "CORE"}


def _fx_state():
    return {"initial_capital": 5_000.0, "equity": 4_200.0,
            "equity_history": [["2026-01-01", 5_000.0],
                               ["2026-01-02", 4_200.0]],
            "peak_equity": 5_000.0, "positions": {"EURUSD": 0.3}}


@pytest.fixture
def two_books(monkeypatch):
    entries = [
        {"key": "FULL", "account": "full", "kind": "equity", "micro": False,
         "label": "FULL · EQUITIES", "sub": "EQUITIES · 4 REGIONS · MONTHLY"},
        {"key": "MATT", "account": "matt", "kind": "fx", "micro": False,
         "label": "FX · MATT", "sub": "FX + CRYPTO · DAILY BARS"},
    ]
    states = {"full": _equity_state(), "matt": _fx_state()}
    monkeypatch.setattr(overview.registry, "discover_accounts", lambda: entries)
    monkeypatch.setattr(overview, "_load", lambda e: states[e["account"]])
    return overview.build_overview()


def test_fx_books_report_under_their_own_group(two_books):
    by_key = {c["key"]: c for c in two_books["accounts"]}
    assert by_key["FULL"]["group"] == profiles.CORE
    assert by_key["MATT"]["group"] == profiles.FX


def test_fx_books_do_not_inflate_the_headline_aum(two_books):
    """The headline is CORE only. The FX book's A$4,200 must not appear in it,
    and must still be reported under a total of its own."""
    assert two_books["totals"]["aum"] == pytest.approx(101_000.0)
    assert two_books["totals"]["books"] == 1
    fx_group = next(g for g in two_books["groups"] if g["name"] == profiles.FX)
    assert fx_group["aum"] == pytest.approx(4_200.0)
    assert fx_group["books"] == 1


def test_a_books_trade_count_is_fills_not_cash_events(monkeypatch):
    """A dividend is not a trade. Task 12's interest rows would otherwise add
    ~250 'trades' a year per sleeve to the card."""
    state = _equity_state()
    state["trades"] = [{"date": "2026-01-01", "side": "BUY"},
                       {"date": "2026-01-02", "side": "DIV"}]
    entry = {"key": "FULL", "account": "full", "kind": "equity", "micro": False,
             "label": "FULL", "sub": ""}
    card = overview._card(entry, state)
    assert card["n_line"].endswith("1 TRADES")
