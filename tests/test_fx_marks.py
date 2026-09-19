"""marks.py — THE shared cost/mark/annualisation formula module (round-2 items 1/2/5).

Pins three things:
1. The formulas reproduce the legacy fx_book/dashboard numbers exactly, and
   fx_book.run_once routes through marks (source pin — no inline copy left).
2. Every trade written by run_once is stamped with its execution-time
   'aud_per_quote' (item 1) and it JSON round-trips through save/load_state.
3. periods_per_year IS the calendar-time annualisation convention (item 5):
   daily -> 252, hourly -> 24*365.25, minute -> capped at 24*365.25 — and
   fx_book.status prints vol annualised at the book's own bar, not sqrt(252).
"""
import inspect
import json
import math

import numpy as np
import pandas as pd
import pytest

from trading_algo.forex import fx_book, fxconv, marks
from trading_algo.forex.pairs import get_pair as _gp
from trading_algo.forex.agents import AgentPool
from trading_algo.forex.pairs import get_pair


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def pool():
    return AgentPool(max_workers=1)


# ---------------------------------------------------------------------------
# 1. Formula reproduction + source pin (item 2, book half)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sym,price", [
    ("EURUSD", 1.08),        # FX major
    ("USDJPY", 150.0),       # JPY cross (pip 0.01)
    ("BTCUSD", 60_000.0),    # crypto
    ("SPY", 520.0),          # equity
    ("TLT", 95.0),           # bond ETF
])
def test_cost_formulas_match_legacy(sym, price):
    pr = get_pair(sym)
    for dw in (0.25, -0.10, 0.0):
        legacy = abs(dw) * 0.5 * pr.spread_fraction(price)
        assert marks.cost_fraction(dw, pr, price) == legacy
        assert marks.trade_cost(dw, pr, price, 5_000.0) == legacy * 5_000.0


def test_cost_bad_price_guard():
    pr = get_pair("EURUSD")
    assert marks.cost_fraction(0.3, pr, 0.0) == 0.0
    assert marks.cost_fraction(0.3, pr, float("nan")) == 0.0
    assert marks.cost_fraction(0.3, pr, None) == 0.0
    assert marks.trade_cost(0.3, pr, float("nan"), 10_000.0) == 0.0


def test_position_contribution_translates_pnl_not_notional():
    # Margin book: contrib = w * fxf * (now/entry - 1). Only the P&L converts.
    assert marks.position_contribution(0.2, 1.05, 1.08, 0.98) == pytest.approx(
        0.2 * 0.98 * (1.08 / 1.05 - 1.0))
    assert marks.trade_mark(-0.15, 150.0, 148.0, 1.01, 10_000.0) == pytest.approx(
        -0.15 * 1.01 * (148.0 / 150.0 - 1.0) * 10_000.0)
    # trade_mark is exactly position_contribution scaled into account currency
    assert marks.trade_mark(0.3, 1.0, 1.1, 1.0, 2_000.0) == pytest.approx(
        marks.position_contribution(0.3, 1.0, 1.1, 1.0) * 2_000.0)
    # A flat pair earns nothing regardless of the FX move: no notional exposure.
    assert marks.position_contribution(0.4, 1.10, 1.10, 1.07) == pytest.approx(0.0)


def test_audusd_position_earns_when_audusd_moves():
    """The bug this formula replaced: for AUDUSD the quote IS USD, so the AUD
    translation factor is exactly 1/r and the old ``w*(r*f-1)`` cancelled to a
    hard 0.0 on every bar — an AUDUSD leg could never earn while still paying
    the spread. A short AUDUSD must profit when AUD falls against USD."""
    entry, now = 0.6600, 0.6534                 # AUD/USD falls 1%
    r = now / entry
    fxf = 1.0 / r                               # aud_per_USD rises as AUD falls
    contrib = marks.position_contribution(-0.25, entry, now, fxf)
    assert contrib == pytest.approx(-0.25 * (1.0 - 1.0 / r))
    assert contrib > 0.0
    assert contrib == pytest.approx(0.0025, rel=5e-2)   # ≈ +0.25% of equity
    # ...and the long side loses the mirror amount.
    assert marks.position_contribution(0.25, entry, now, fxf) == pytest.approx(-contrib)


def test_aud_return_is_the_vectorised_twin():
    """The panel backtests must not re-fork the scalar formula."""
    rets = pd.Series([0.01, -0.02, 0.0])
    ratio = pd.Series([1.005, 0.995, 1.01])
    got = marks.aud_return(rets, ratio)
    for i in range(len(rets)):
        entry, now = 1.0, 1.0 + rets[i]
        assert got[i] == pytest.approx(
            marks.position_contribution(1.0, entry, now, ratio[i]))


def test_fx_book_routes_through_marks_only():
    """Source pin: marks is the ONE formula site on the book side — the inline
    half-spread and mark formulas must not reappear in run_once."""
    # run_once is now a thin storage.account_lock wrapper delegating to
    # _run_once_locked; the mark/cost formulas live in the locked body, so the
    # source pin must inspect both (the negative asserts still scan the real body).
    src = inspect.getsource(fx_book.run_once) + inspect.getsource(fx_book._run_once_locked)
    assert "marks.cost_fraction" in src
    assert "marks.position_contribution" in src
    assert "0.5 * get_pair(s).spread_fraction" not in src
    assert "0.5 * spread_fraction" not in src


# ---------------------------------------------------------------------------
# 2. aud_per_quote stamped at trade-write time (item 1)
# ---------------------------------------------------------------------------
def test_trades_stamped_with_aud_per_quote(isolated_state, pool):
    fx_book.init_account("matt", 5_000, "balanced")
    fx_book.run_once("matt", synthetic=True, pool=pool)
    st = fx_book.load_state("matt")
    assert st["trades"], "expected the synthetic run to trade"
    # After one run every trade happened at the latest bar, whose closes are
    # exactly state['last_close'] — so the stamp must equal aud_per_quote
    # derived from px_last at that bar (6dp).
    for t in st["trades"]:
        assert "aud_per_quote" in t
        expected = fxconv.aud_per_quote(get_pair(t["pair"]).quote, st["last_close"])
        # default FX book: AUDUSD hub present -> always derivable and positive
        assert isinstance(t["aud_per_quote"], float) and t["aud_per_quote"] > 0
        assert t["aud_per_quote"] == pytest.approx(expected, abs=1e-6)
    # JSON round-trip through the state file (load_state already re-read it,
    # but pin the on-disk representation explicitly too)
    with open(isolated_state / "fx_state_matt.json") as f:
        raw = json.load(f)
    assert raw["trades"][0]["aud_per_quote"] == st["trades"][0]["aud_per_quote"]


def test_aud_per_quote_null_when_underivable(isolated_state, pool):
    """A universe-locked crypto-only book has no AUDUSD hub in px_last: the key
    must still be present, written as JSON null, without crashing the run."""
    fx_book.init_account("cryptoonly", 5_000, "balanced",
                         symbols=["BTCUSD", "ETHUSD"])
    fx_book.run_once("cryptoonly", synthetic=True, pool=pool)
    st = fx_book.load_state("cryptoonly")
    assert st["trades"], "expected the synthetic run to trade"
    for t in st["trades"]:
        assert "aud_per_quote" in t and t["aud_per_quote"] is None


# ---------------------------------------------------------------------------
# 3. Annualisation convention (item 5): calendar-time IS the convention
# ---------------------------------------------------------------------------
def test_periods_per_year_pins_the_convention():
    daily = pd.bdate_range("2024-01-01", periods=40)
    hourly = pd.date_range("2024-01-01", periods=40, freq="h")
    minute = pd.date_range("2024-01-01", periods=40, freq="min")
    assert marks.periods_per_year(daily) == 252
    assert marks.periods_per_year(hourly) == pytest.approx(24 * 365.25)
    assert marks.periods_per_year(minute) == 24 * 365.25      # capped at hourly
    # degenerate/empty index falls back to daily spacing
    assert marks.periods_per_year(pd.DatetimeIndex([])) == 252
    # decision recorded where it lives
    assert "calendar-time" in (marks.__doc__ or "").lower()


def test_status_annualises_at_book_bar(isolated_state, capsys):
    """fx_book.status on an HOURLY book must print vol scaled by
    sqrt(periods_per_year) (~sqrt(8766)), NOT sqrt(252)."""
    fx_book.init_account("daytrader", 10_000, "intraday", bar="60m")
    st = fx_book.load_state("daytrader")
    idx = pd.date_range("2025-01-02 00:00", periods=30, freq="h")
    rng = np.random.default_rng(7)
    eq = 10_000.0 * np.cumprod(1.0 + rng.normal(0.0, 0.002, size=len(idx)))
    st["equity_history"] = [[t.strftime("%Y-%m-%d %H:%M"), round(float(v), 2)]
                            for t, v in zip(idx, eq)]
    st["equity"] = round(float(eq[-1]), 2)
    fx_book.save_state("daytrader", st)

    capsys.readouterr()                       # drop init/save chatter
    fx_book.status("daytrader")
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if "Ann. vol" in l)

    s = pd.Series([e for _, e in st["equity_history"]],
                  index=pd.to_datetime([d for d, _ in st["equity_history"]]))
    rets = s.pct_change(fill_method=None).dropna()
    right = rets.std() * math.sqrt(marks.periods_per_year(s.index))   # ~ x sqrt(8766)
    wrong = rets.std() * math.sqrt(252)
    assert f"{right:.1%}" != f"{wrong:.1%}", "test setup must distinguish the two"
    assert f"{right:.1%}" in line
    assert f"{wrong:.1%}" not in line


def test_status_daily_book_unchanged_at_252(isolated_state, capsys):
    """Daily spacing >= 12h -> the convention still annualises at 252."""
    fx_book.init_account("matt", 5_000, "balanced")
    st = fx_book.load_state("matt")
    idx = pd.bdate_range("2025-01-02", periods=30)
    rng = np.random.default_rng(11)
    eq = 5_000.0 * np.cumprod(1.0 + rng.normal(0.0, 0.002, size=len(idx)))
    st["equity_history"] = [[t.strftime("%Y-%m-%d"), round(float(v), 2)]
                            for t, v in zip(idx, eq)]
    st["equity"] = round(float(eq[-1]), 2)
    fx_book.save_state("matt", st)

    capsys.readouterr()
    fx_book.status("matt")
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if "Ann. vol" in l)
    s = pd.Series([e for _, e in st["equity_history"]],
                  index=pd.to_datetime([d for d, _ in st["equity_history"]]))
    rets = s.pct_change(fill_method=None).dropna()
    assert f"{rets.std() * math.sqrt(252):.1%}" in line


# ---------------------------------------------------------------------------
# Financing: margin interest on the long debit + stock-loan fee on shorts
# ---------------------------------------------------------------------------
# Every equity and bond in MULTI_ASSET_UNIVERSE ships swap_long = swap_short =
# 0.0, deliberately (pairs.py:103) — the FX carry model is swap points, and
# nobody wrote an equity financing model. The consequence: the `multiasset` book
# holds 1.02x long and 0.45x short and pays NOTHING to borrow either the cash or
# the shares. This is that missing charge.
#
# Convention, stated once so it cannot drift: margin interest accrues on the
# LONG DEBIT ONLY — max(0, long exposure - 1) — because a short generates cash
# rather than consuming it. Charging on (gross - 1) would double-count the short
# leg. Shorts pay a separate stock-loan fee on their notional. FX and crypto are
# EXCLUDED: their financing already lives in swap points / perp funding, so
# charging them here would bill the same cost twice.


def _fin(weights, **kw):
    kw.setdefault("margin_rate", 0.055)
    kw.setdefault("borrow_rate", 0.004)
    return marks.financing_fraction(weights, _gp, **kw)


def test_unlevered_long_only_book_pays_no_financing():
    total, by_pair = _fin({"SPY": 0.6, "AGG": 0.4})
    assert total == 0.0 and by_pair == {}


def test_levered_long_book_pays_margin_on_the_debit_only():
    """1.5x long = 0.5x financed, not 1.5x."""
    total, _ = _fin({"SPY": 1.0, "QQQ": 0.5}, elapsed_days=365.25)
    assert total == pytest.approx(0.5 * 0.055, rel=1e-6)


def test_a_short_pays_borrow_but_creates_no_margin_debit():
    """The whole point of the correction: a short is not borrowed cash."""
    total, by_pair = _fin({"SPY": 1.0, "AAPL": -0.4}, elapsed_days=365.25)
    assert total == pytest.approx(0.4 * 0.004, rel=1e-6)   # borrow only, no margin
    assert by_pair["AAPL"] == pytest.approx(0.4 * 0.004, rel=1e-6)


def test_fx_legs_are_excluded_because_swap_points_already_finance_them():
    """AUDUSD carries real swap_long/swap_short. Charging margin here too would
    bill the same financing twice."""
    total, by_pair = _fin({"AUDUSD": 2.0}, elapsed_days=365.25)
    assert total == 0.0 and by_pair == {}


def test_margin_is_attributed_pro_rata_across_the_financed_longs():
    """by_pair must sum to the total, or the dashboard's reconciliation breaks."""
    total, by_pair = _fin({"SPY": 1.0, "QQQ": 0.5, "AAPL": -0.4},
                          elapsed_days=365.25)
    assert sum(by_pair.values()) == pytest.approx(total, rel=1e-9)
    assert by_pair["SPY"] == pytest.approx(2 * by_pair["QQQ"], rel=1e-6)  # 1.0 vs 0.5


def test_financing_scales_with_elapsed_time():
    """A weekend gap costs three days of interest, not one."""
    one, _ = _fin({"SPY": 1.5}, elapsed_days=1.0)
    three, _ = _fin({"SPY": 1.5}, elapsed_days=3.0)
    assert three == pytest.approx(3.0 * one, rel=1e-9)


def test_zero_rates_are_a_perfect_noop():
    total, by_pair = _fin({"SPY": 1.5, "AAPL": -0.4},
                          margin_rate=0.0, borrow_rate=0.0, elapsed_days=10.0)
    assert total == 0.0 and by_pair == {}
