"""AUD currency conversion: a pair's quote-currency P&L is translated to AUD.

The books/backtest are AUD-denominated; an AUD trader converts to the quote
currency to hold a pair, so AUD/quote moves (esp. AUD/USD) are part of real P&L.
"""
import pandas as pd
import pytest

from trading_algo.forex import fx_book, fxconv
from trading_algo.forex.agents import AgentPool


# --- the conversion maths --------------------------------------------------
def test_aud_per_quote_from_majors():
    px = {"AUDUSD": 0.66, "USDJPY": 150.0, "USDCAD": 1.36}
    assert fxconv.aud_per_quote("AUD", px) == 1.0
    assert fxconv.aud_per_quote("USD", px) == pytest.approx(1 / 0.66)
    assert fxconv.aud_per_quote("JPY", px) == pytest.approx((1 / 150.0) / 0.66)
    assert fxconv.aud_per_quote("CAD", px) == pytest.approx((1 / 1.36) / 0.66)


def test_aud_per_quote_missing_rate_is_none():
    assert fxconv.aud_per_quote("USD", {}) is None          # no AUDUSD
    assert fxconv.aud_per_quote("JPY", {"AUDUSD": 0.66}) is None  # no USDJPY


def test_conversion_factor_direction():
    # AUD strengthens vs USD (AUDUSD 0.66 -> 0.70): AUD-per-USD falls, so a
    # USD-quoted position is worth LESS in AUD => factor < 1.
    f = fxconv.conversion_factor("USD", {"AUDUSD": 0.66}, {"AUDUSD": 0.70})
    assert f == pytest.approx(0.66 / 0.70)
    assert f < 1.0
    # flat rate => no translation
    assert fxconv.conversion_factor("USD", {"AUDUSD": 0.66}, {"AUDUSD": 0.66}) == 1.0
    # AUD-quoted leg is never translated
    assert fxconv.conversion_factor("AUD", {"AUDUSD": 0.66}, {"AUDUSD": 0.70}) == 1.0
    # underivable => safe fallback
    assert fxconv.conversion_factor("USD", {}, {}) == 1.0


def test_aud_per_quote_frame():
    px = pd.DataFrame({"AUDUSD": [0.66, 0.70], "USDJPY": [150.0, 150.0]})
    f = fxconv.aud_per_quote_frame(px, ["USD", "JPY", "AUD"])
    assert list(f["AUD"]) == [1.0, 1.0]
    assert f["USD"].iloc[0] == pytest.approx(1 / 0.66)
    assert f["JPY"].iloc[1] == pytest.approx((1 / 150.0) / 0.70)
    # a quote with no rate in the panel -> NaN column (caller falls back to 1.0)
    f2 = fxconv.aud_per_quote_frame(px[["USDJPY"]], ["USD"])
    assert f2["USD"].isna().all()


# --- the book applies it to mark-to-market ---------------------------------
def _one_bar_panel(eurusd, audusd, date="2025-01-02"):
    idx = pd.to_datetime([date])
    mk = lambda v: pd.DataFrame({"open": [v], "high": [v], "low": [v], "close": [v]}, index=idx)
    return {"EURUSD": mk(eurusd), "AUDUSD": mk(audusd)}


def _seed_halted_long(tmp_path, monkeypatch, audusd_now):
    """A halted book holding 1.0 EURUSD; marks one flat-price bar where AUDUSD
    moves. Halted => no rebalance, so we isolate the FX translation of the mark."""
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.init_account("c", 5_000, "balanced")
    st = fx_book.load_state("c")
    st.update({"positions": {"EURUSD": 1.0}, "symbols": ["EURUSD", "AUDUSD"],
               "last_close": {"EURUSD": 1.08, "AUDUSD": 0.66},
               "last_bar_date": "2025-01-01", "equity": 5_000.0,
               "peak_equity": 5_000.0, "risk_halted": True, "halt_cooldown": 5})
    fx_book.save_state("c", st)
    monkeypatch.setattr(fx_book, "_panel",
                        lambda *a, **k: _one_bar_panel(1.08, audusd_now))
    fx_book.run_once("c", pool=AgentPool(max_workers=1))
    return fx_book.load_state("c")["equity"]


def test_book_marks_pnl_in_aud(tmp_path, monkeypatch):
    # EURUSD flat; AUD strengthens 0.66->0.70 => a long USD-quoted book loses ~5.7%
    # in AUD even though the pair didn't move.
    eq_fx = _seed_halted_long(tmp_path, monkeypatch, audusd_now=0.70)
    assert eq_fx == pytest.approx(5_000 * (0.66 / 0.70), rel=2e-3)
    assert eq_fx < 4_900


def test_book_flat_fx_is_unchanged(tmp_path, monkeypatch):
    # Same setup but AUDUSD unchanged => equity ~flat (pair didn't move either).
    eq_flat = _seed_halted_long(tmp_path, monkeypatch, audusd_now=0.66)
    assert eq_flat == pytest.approx(5_000, rel=2e-3)


def test_daily_snapshot_attributes_pnl(tmp_path, monkeypatch):
    """run_once records a daily P&L snapshot attributing the move to each position."""
    _seed_halted_long(tmp_path, monkeypatch, audusd_now=0.70)   # AUD up: long USD-quoted loses
    dy = fx_book.load_state("c")["daily"]
    assert dy["date"] == "2025-01-02"
    assert dy["net_aud"] < 0 and dy["net_pct"] < 0
    eur = next(c for c in dy["by_pair"] if c["pair"] == "EURUSD")
    # pair was flat; the loss is the AUD/USD translation on the held long
    assert eur["move"] == pytest.approx(0.0, abs=1e-6)
    assert eur["contrib"] == pytest.approx(0.66 / 0.70 - 1, rel=1e-3)
