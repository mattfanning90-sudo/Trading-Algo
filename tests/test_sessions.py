"""Per-symbol market-hours gate (`trading_algo.forex.sessions`).

The live books were re-weighting FX majors all weekend against a forward-filled
Friday close — spread paid, no price exposure gained (docs/LIVE_BOOK_AUDIT.md).
These tests pin the gate that stops it, and the two properties that make it safe:
it is per SYMBOL (a mixed book keeps trading crypto while FX freezes), and it
FREEZES rather than flattens (you cannot liquidate on a shut venue).
"""
from datetime import datetime, timezone

import pandas as pd
import pytest

from trading_algo.forex import engine, fx_book, sessions
from trading_algo.forex.fx_config import profile


# 2026-07-03 Fri, 07-04 Sat, 07-05 Sun, 07-06 Mon
FRI = datetime(2026, 7, 3, 15)
FRI_LATE = datetime(2026, 7, 3, 23)
SAT = datetime(2026, 7, 4, 3)
SUN_EARLY = datetime(2026, 7, 5, 9)
SUN_LATE = datetime(2026, 7, 5, 23)
MON = datetime(2026, 7, 6, 15)


# ---------------------------------------------------------------------------
# FX week
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ts,expected", [
    (FRI, True),            # mid-session Friday
    (FRI_LATE, False),      # after the ~22:00 UTC Friday close
    (SAT, False),           # Saturday: shut all day
    (SUN_EARLY, False),     # Sunday morning: still shut
    (SUN_LATE, True),       # Sunday ~22:00 UTC: the week restarts
    (MON, True),
])
def test_fx_week_boundaries(ts, expected):
    assert sessions.is_open("EURUSD", ts) is expected


def test_crypto_never_closes():
    for ts in (FRI, FRI_LATE, SAT, SUN_EARLY, SUN_LATE, MON):
        assert sessions.is_open("BTCUSD", ts) is True


@pytest.mark.parametrize("ts,expected", [
    (MON, True),                          # 15:00 UTC Monday = 11:00 ET
    (datetime(2026, 7, 6, 9), False),     # pre-market
    (datetime(2026, 7, 6, 22), False),    # after the close
    (SAT, False),
    (SUN_LATE, False),                    # equities do NOT follow the FX reopen
])
def test_equities_and_bonds_trade_a_weekday_cash_session(ts, expected):
    """Intraday cash hours apply to an INTRADAY bar, which is the only bar whose
    stamp names an instant."""
    assert sessions.is_open("AAPL", ts, "60m") is expected
    assert sessions.is_open("TLT", ts, "60m") is expected


# ---------------------------------------------------------------------------
# Daily bars name a DAY, not an instant
# ---------------------------------------------------------------------------
# A daily bar is stamped at midnight (`_bar_key` -> "YYYY-MM-DD"), so judging a
# cash equity's intraday session against it asks a question nobody posed and
# answers "shut" on every ordinary weekday. The live `multiasset` book is US
# equities and bonds on DAILY bars: without this distinction the gate freezes it
# permanently — it would hold its positions and never rebalance again.
@pytest.mark.parametrize("symbol", ["AAPL", "TLT", "SPY", "AGG", "QQQ"])
def test_daily_equity_bar_on_a_weekday_is_tradable(symbol):
    wednesday = datetime(2026, 7, 22)            # midnight stamp, as daily bars are
    assert sessions.is_open(symbol, wednesday, "1d") is True
    assert sessions.is_open(symbol, wednesday) is True          # daily is default


def test_daily_equity_bar_on_a_weekend_is_still_shut():
    """The weekday rule still bites — a daily bar cannot excuse a Saturday fill."""
    assert sessions.is_open("AAPL", datetime(2026, 7, 4), "1d") is False


def test_the_live_multiasset_universe_is_not_frozen_on_an_ordinary_weekday():
    """Regression guard for the whole book, not one ticker."""
    from trading_algo.forex.pairs import MULTI_ASSET_UNIVERSE

    wednesday = datetime(2026, 7, 22)
    assert sessions.closed_symbols(MULTI_ASSET_UNIVERSE, wednesday, "1d") == set()


def test_unknown_symbol_is_not_assigned_an_invented_session():
    assert sessions.is_open("WAT.NOPE", SAT) is True


# ---------------------------------------------------------------------------
# The auditor shares these boundaries
# ---------------------------------------------------------------------------
def test_verify_and_the_gate_read_the_same_boundaries():
    """`verify.check_market_hours` must not be able to flag a fill the gate
    permits, so it consumes this module rather than its own copy."""
    from trading_algo import verify

    trades = [{"date": "2026-07-04", "pair": "EURUSD"},   # Saturday: shut
              {"date": "2026-07-04", "pair": "BTCUSD"},   # Saturday: crypto, fine
              {"date": "2026-07-22", "pair": "AAPL"}]     # weekday daily bar: fine
    found = verify.check_market_hours("acct", {"trades": trades}, "fx")
    assert len(found) == 1
    assert found[0].detail["by_symbol"] == {"EURUSD": 1}


def test_verify_still_flags_equity_sleeve_tickers_it_cannot_classify():
    """`BHP.AX` isn't in ALL_PAIRS. The live gate declines to invent a session for
    an unknown symbol; an AUDIT needs the opposite default, or weekend fills in the
    equity sleeves would stop being reported."""
    from trading_algo import verify

    found = verify.check_market_hours(
        "acct", {"trades": [{"date": "2026-07-04", "ticker": "BHP.AX"}]}, "equity")
    assert found and found[0].detail["by_symbol"] == {"BHP.AX": 1}


def test_closed_symbols_splits_a_mixed_universe():
    """The whole point: crypto keeps trading while FX and equities freeze."""
    universe = ["EURUSD", "GBPUSD", "BTCUSD", "ETHUSD", "AAPL", "TLT"]
    assert sessions.closed_symbols(universe, SAT) == {
        "EURUSD", "GBPUSD", "AAPL", "TLT"}
    assert sessions.closed_symbols(universe, MON) == set()


def test_session_report_names_what_is_frozen():
    report = sessions.session_report(["EURUSD", "BTCUSD", "AAPL"], SAT)
    assert "EURUSD" in report and "AAPL" in report and "BTCUSD" not in report


# ---------------------------------------------------------------------------
# The loop-level gate delegates to the same implementation
# ---------------------------------------------------------------------------
def test_engine_gate_delegates_to_sessions():
    """One implementation, so the idle gate and the per-symbol gate can't drift."""
    for ts in (FRI, FRI_LATE, SAT, SUN_LATE, MON):
        aware = ts.replace(tzinfo=timezone.utc)
        assert engine.fx_market_open(aware) is sessions.is_open("EURUSD", ts)


# ---------------------------------------------------------------------------
# Freeze, don't flatten
# ---------------------------------------------------------------------------
def test_frozen_symbols_hold_their_weight_instead_of_being_sold():
    """A shut pair is absent from `target`; without the freeze it reads as 0.0
    and the book would book a sell on a closed venue."""
    p = profile("balanced")
    positions = {"EURUSD": 0.25, "BTCUSD": 0.10}
    target = pd.Series({"BTCUSD": 0.30})        # EURUSD trimmed: its venue is shut

    without = fx_book._apply_band(positions, target, p)
    assert "EURUSD" not in without, "unfrozen: the shut pair gets flattened"

    with_freeze = fx_book._apply_band(positions, target, p, frozen={"EURUSD"})
    assert with_freeze["EURUSD"] == 0.25        # held, untouched
    assert with_freeze["BTCUSD"] == 0.30        # the open pair still rebalances


def test_freezing_does_not_resurrect_a_flat_symbol():
    """Freezing holds the CURRENT weight — it never opens a new position."""
    out = fx_book._apply_band({"BTCUSD": 0.2}, pd.Series({"BTCUSD": 0.2}),
                              profile("balanced"), frozen={"EURUSD"})
    assert "EURUSD" not in out


def test_band_behaviour_is_unchanged_when_nothing_is_frozen():
    """Regression guard: the default path must behave exactly as before."""
    p = profile("balanced")
    positions = {"EURUSD": 0.25}
    target = pd.Series({"EURUSD": 0.2551})      # inside the no-churn band
    assert fx_book._apply_band(positions, target, p) == \
           fx_book._apply_band(positions, target, p, frozen=set())


# ---------------------------------------------------------------------------
# End to end through run_once — the behaviour the audit actually flagged
# ---------------------------------------------------------------------------
@pytest.fixture
def saturday_book(tmp_path, monkeypatch):
    """A mixed FX+crypto book whose newest bar falls on a Saturday.

    Real weekend panels are exactly what the live books saw: `fx_data._align`
    forward-fills the shut FX pairs, so their close is frozen at Friday's while
    crypto keeps printing.
    """
    from trading_algo.forex import fx_data
    from trading_algo.forex.pairs import get_pair

    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    universe = ["EURUSD", "GBPUSD", "BTCUSD", "ETHUSD"]

    def fake_panel(symbols, synthetic, interval="1d", source="yahoo",
                   exchange=None, min_bars=None):
        out = {}
        for i, sym in enumerate(universe):
            df = fx_data.synthetic_pair(get_pair(sym), start="2025-01-01",
                                        end="2026-07-03", seed=i, freq="B")
            # Extend onto a Saturday bar: crypto prints, FX is forward-filled.
            sat = pd.Timestamp("2026-07-04")
            row = df.iloc[-1].copy()
            if sym in ("BTCUSD", "ETHUSD"):
                row = row * 1.03                     # crypto moved over the weekend
            df.loc[sat] = row                        # FX: identical to Friday's close
            out[sym] = df
        return out

    monkeypatch.setattr(fx_book, "_panel", fake_panel)
    fx_book.init_account("sat", 10_000, "balanced", symbols=universe)
    return "sat"


def test_run_once_does_not_trade_shut_venues_but_still_trades_crypto(saturday_book):
    fx_book.run_once(saturday_book, synthetic=True)
    state = fx_book.load_state(saturday_book)

    traded = {t["pair"] for t in state["trades"]}
    assert not (traded & {"EURUSD", "GBPUSD"}), (
        f"booked fills on a closed FX venue: {sorted(traded)}")
    assert traded & {"BTCUSD", "ETHUSD"}, (
        "crypto is 24/7 and must keep trading through the weekend")


def test_an_open_fx_position_is_held_through_the_weekend_not_sold(saturday_book):
    """The freeze must not read as 'target 0' and flatten on a shut venue."""
    state = fx_book.load_state(saturday_book)
    state["positions"] = {"EURUSD": 0.25}
    state["last_close"] = {"EURUSD": 1.10}
    fx_book.save_state(saturday_book, state)

    fx_book.run_once(saturday_book, synthetic=True)
    after = fx_book.load_state(saturday_book)

    assert after["positions"].get("EURUSD") == pytest.approx(0.25), (
        "the shut pair was re-weighted or flattened instead of held")
    assert not any(t["pair"] == "EURUSD" for t in after["trades"])
