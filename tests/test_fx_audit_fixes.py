"""Regression tests for the adversarial audit of P&L-by-pair / trade-quality.

Each test pins a numerically-demonstrated finding from the audit workflow:
1. blotter P&L-since dropped the AUD/quote translation the book applies
2. blotter crashed when a recorded trade's pair is missing from today's panel
3. flip trades (position crossing zero) were attributed 100% to the new side
4. per-'day' labels were wrong for the hourly (daytrader) book
"""
import pandas as pd
import pytest

from trading_algo.forex import dashboard


def _panel(eur=(1.08, 1.08), aud=(0.66, 0.70)):
    idx = pd.to_datetime(["2025-01-02", "2025-01-03"])
    mk = lambda a, b: pd.DataFrame({"open": [a, b], "high": [a, b],
                                    "low": [a, b], "close": [a, b]}, index=idx)
    return {"EURUSD": mk(*eur), "AUDUSD": mk(*aud)}


def _state(trades):
    return {"equity_history": [["2025-01-02", 5_000.0]], "equity": 5_000.0,
            "initial_capital": 5_000.0, "trades": trades}


def test_blotter_pnl_includes_aud_translation():
    """EURUSD flat, AUD strengthens 0.66->0.70: a long USD-quoted position LOSES
    in AUD — the blotter must show it (it used to show 0.00)."""
    st = _state([{"date": "2025-01-02", "pair": "EURUSD", "side": "BUY",
                  "delta_weight": 1.0, "target_weight": 1.0, "price": 1.08}])
    txn = dashboard._transactions(st, _panel())
    pnl = txn["rows"][0]["pnl"]
    assert pnl == pytest.approx(5_000 * (0.66 / 0.70 - 1.0), rel=1e-3)   # ≈ -285.71
    assert pnl < -280


def test_blotter_audusd_trade_matches_book_convention():
    """A long AUDUSD position marks to ~0 in AUD terms when only AUDUSD moves
    (the pair gain IS the currency move) — book convention, blotter must agree."""
    st = _state([{"date": "2025-01-02", "pair": "AUDUSD", "side": "BUY",
                  "delta_weight": 1.0, "target_weight": 1.0, "price": 0.66}])
    txn = dashboard._transactions(st, _panel())
    assert txn["rows"][0]["pnl"] == pytest.approx(0.0, abs=0.02)


def test_blotter_out_of_window_trade_uses_hub_closes():
    """A trade OLDER than the bounded display panel must keep its real AUD
    translation via the injected hub closes — not the old fxf=1.0 fallback
    (which produced pnl 0.0 here)."""
    st = _state([{"date": "2024-06-01", "pair": "EURUSD", "side": "BUY",
                  "delta_weight": 1.0, "target_weight": 1.0, "price": 1.08}])
    hub = pd.DataFrame({"AUDUSD": [0.66, 0.70]},
                       index=pd.to_datetime(["2024-06-01", "2025-01-03"]))
    txn = dashboard._transactions(st, _panel(), hub_closes=hub)
    pnl = txn["rows"][0]["pnl"]
    assert pnl == pytest.approx(5_000 * (0.66 / 0.70 - 1.0), rel=1e-3)   # ≈ -285.71
    assert pnl < -280


def test_blotter_rejects_corrupt_negative_rate():
    """fx_factor now delegates to fxconv.conversion_factor, so fxconv._val's
    v>0 rule applies uniformly: a corrupt NEGATIVE AUDUSD rate on the trade
    date yields factor 1.0 (the old hand-rolled check produced a negative
    factor and a spurious P&L)."""
    st = _state([{"date": "2025-01-02", "pair": "EURUSD", "side": "BUY",
                  "delta_weight": 1.0, "target_weight": 1.0, "price": 1.08}])
    txn = dashboard._transactions(st, _panel(aud=(-0.66, 0.70)))
    assert txn["rows"][0]["pnl"] == pytest.approx(0.0, abs=0.02)


def test_blotter_survives_pair_missing_from_panel():
    """A trade whose pair has left the panel must not crash the page build."""
    st = _state([{"date": "2025-01-02", "pair": "USDJPY", "side": "BUY",
                  "delta_weight": 0.2, "target_weight": 0.2, "price": 150.0}])
    txn = dashboard._transactions(st, _panel())          # no USDJPY in panel
    row = txn["rows"][0]
    assert row["last"] is None and row["pnl"] is None    # graceful, not TypeError


def test_flip_trade_split_between_sides():
    """+0.2 -> -0.1 in one trade: 2/3 of the P&L belongs to the closed LONG leg."""
    txn = {"rows": [
        {"pair": "EURUSD", "pnl": 0.0, "cost": 0.0, "target": 0.2, "dweight": 0.2,
         "regime": "trending"},
        {"pair": "EURUSD", "pnl": 2727.27, "cost": 0.0, "target": -0.1,
         "dweight": -0.3, "regime": "trending"}]}
    at = dashboard._attribution_rollup(txn)
    assert at["by_side"]["long"] == pytest.approx(2727.27 * (0.2 / 0.3), rel=1e-6)
    assert at["by_side"]["short"] == pytest.approx(2727.27 * (0.1 / 0.3), rel=1e-6)


def test_trade_stats_unit_and_no_losses():
    hourly = {"equity_history": [["2026-07-01 09:00", 100], ["2026-07-01 10:00", 101],
                                 ["2026-07-01 11:00", 102], ["2026-07-01 12:00", 103]],
              "trades": []}
    ts = dashboard._trade_stats(hourly)
    assert ts["unit"] == "hour"                      # labels adapt for the day book
    assert ts["no_losses"] is True                   # ∞ profit factor, not '–'
    daily = {"equity_history": [["2026-07-01", 100], ["2026-07-02", 99],
                                ["2026-07-03", 101]], "trades": []}
    ts2 = dashboard._trade_stats(daily)
    assert ts2["unit"] == "day" and ts2["no_losses"] is False
