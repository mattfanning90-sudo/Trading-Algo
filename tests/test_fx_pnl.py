"""FX money-terms P&L: the weight-lot ledger and the exact bar accumulator.

Covers the two independent answers to "what did this book make, in dollars":

  * ``forex.fx_pnl`` — FIFO round-trips over signed weight, marked in the account
    currency, reconstructible from the persisted trades log alone;
  * ``fx_book``'s ``state["cumulative"]`` — the exact per-bar price / carry /
    spread decomposition, which is the ONLY place carry can come from.

Plus the contract the terminal dashboard's FX screens consume.
"""
import json
import os

import pytest

from trading_algo.dashboard import fx_api, registry
from trading_algo.forex import fx_book, fx_data, fx_pnl, marks
from trading_algo.forex.agents import AgentPool
from trading_algo.forex.pairs import get_pair


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _state(trades, equity=1000.0, initial=1000.0, positions=None,
           last_close=None, history=None, **extra):
    return {
        "account": "t", "currency": "AUD", "profile": "balanced", "bar": "1d",
        "symbols": ["EURUSD"], "initial_capital": initial, "equity": equity,
        "positions": positions or {}, "last_close": last_close or {},
        "last_bar_date": "2026-01-10", "peak_equity": max(equity, initial),
        "risk_halted": False, "halt_cooldown": 0, "trades": trades,
        "equity_history": history if history is not None else [["2026-01-10", equity]],
        **extra,
    }


def _t(date, pair, dw, price, apq=1.0, target=None):
    return {"date": date, "pair": pair, "side": "BUY" if dw > 0 else "SELL",
            "delta_weight": dw, "target_weight": target if target is not None else dw,
            "price": price, "aud_per_quote": apq}


@pytest.fixture
def pool():
    return AgentPool(max_workers=1)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# FIFO over signed weight
# ---------------------------------------------------------------------------
def test_long_round_trip_nets_price_move_less_both_spreads():
    """Open +10% at 1.00, close it at 1.10 on a 1,000 book: notional 100,
    gross +10, net = gross less the spread charged on BOTH legs."""
    trades = [_t("2026-01-01", "EURUSD", 0.10, 1.00),
              _t("2026-01-05", "EURUSD", -0.10, 1.10, target=0.0)]
    state = _state(trades, history=[["2026-01-01", 1000.0], ["2026-01-05", 1000.0]])
    equity_on, _ = fx_pnl.equity_lookup(state)
    open_lots, realized = fx_pnl.build_lots(trades, equity_on)

    assert open_lots == {}                        # flat again
    assert len(realized) == 1
    r = realized[0]
    assert r["side"] == "LONG"
    assert r["filled"] == pytest.approx(0.10)
    assert r["entry"] == pytest.approx(1.00)
    assert r["exit"] == pytest.approx(1.10)
    assert r["entry_notional"] == pytest.approx(100.0)
    assert r["gross"] == pytest.approx(10.0)      # 0.10 × (1.10/1.00 − 1) × 1000
    pair = get_pair("EURUSD")
    assert r["entry_cost"] == pytest.approx(marks.trade_cost(0.10, pair, 1.00, 1000.0))
    assert r["exit_cost"] == pytest.approx(marks.trade_cost(-0.10, pair, 1.10, 1000.0))
    assert r["net"] == pytest.approx(r["gross"] - r["entry_cost"] - r["exit_cost"])
    assert r["held_days"] == 4


def test_short_round_trip_gains_when_price_falls():
    trades = [_t("2026-01-01", "EURUSD", -0.20, 1.00),
              _t("2026-01-02", "EURUSD", 0.20, 0.90, target=0.0)]
    state = _state(trades, history=[["2026-01-01", 1000.0], ["2026-01-02", 1000.0]])
    equity_on, _ = fx_pnl.equity_lookup(state)
    _, realized = fx_pnl.build_lots(trades, equity_on)
    r = realized[0]
    assert r["side"] == "SHORT"
    assert r["gross"] == pytest.approx(20.0)      # −0.20 × (0.90/1.00 − 1) × 1000
    assert r["net"] > 0


def test_fifo_consumes_oldest_lot_first():
    """Two adds then a partial close: the FIRST lot's entry price is the basis,
    and the remainder stays open at the second lot's price."""
    trades = [_t("2026-01-01", "EURUSD", 0.10, 1.00),
              _t("2026-01-02", "EURUSD", 0.10, 1.20, target=0.20),
              _t("2026-01-03", "EURUSD", -0.10, 1.30, target=0.10)]
    state = _state(trades, history=[["2026-01-01", 1000.0]])
    equity_on, _ = fx_pnl.equity_lookup(state)
    open_lots, realized = fx_pnl.build_lots(trades, equity_on)
    assert len(realized) == 1
    assert realized[0]["entry"] == pytest.approx(1.00)      # oldest lot, not the avg
    assert realized[0]["entry_date"] == "2026-01-01"
    marked = fx_pnl.mark_open(open_lots, {"EURUSD": 1.30})
    assert marked[0]["avg_entry"] == pytest.approx(1.20)    # the survivor
    assert marked[0]["weight"] == pytest.approx(0.10)


def test_flip_through_zero_splits_the_close_and_reopens():
    """+20% flipped straight to −10% in one record: the closing leg belongs to
    the long, the overshoot opens a short, and the spread splits pro-rata."""
    trades = [_t("2026-01-01", "EURUSD", 0.20, 1.00),
              _t("2026-01-02", "EURUSD", -0.30, 1.10, target=-0.10)]
    state = _state(trades, history=[["2026-01-01", 1000.0]])
    equity_on, _ = fx_pnl.equity_lookup(state)
    open_lots, realized = fx_pnl.build_lots(trades, equity_on)
    assert len(realized) == 1
    r = realized[0]
    assert r["side"] == "LONG"
    assert r["filled"] == pytest.approx(0.20)
    full = marks.trade_cost(-0.30, get_pair("EURUSD"), 1.10, 1000.0)
    assert r["exit_cost"] == pytest.approx(full * 2 / 3)     # 0.20 of 0.30
    lots = open_lots["EURUSD"]
    assert sum(l[0] for l in lots) == pytest.approx(-0.10)   # short overshoot open
    marked = fx_pnl.mark_open(open_lots, {"EURUSD": 1.10})
    assert marked[0]["entry_cost"] == pytest.approx(full / 3)


def test_aud_translation_uses_the_stored_execution_stamp():
    """Price flat but AUD/quote up 10% → the AUD book still made money, because
    the position was held in the quote currency (fxconv's convention)."""
    trades = [_t("2026-01-01", "EURUSD", 0.10, 1.00, apq=1.00),
              _t("2026-01-02", "EURUSD", -0.10, 1.00, apq=1.10, target=0.0)]
    state = _state(trades, history=[["2026-01-01", 1000.0]])
    equity_on, _ = fx_pnl.equity_lookup(state)
    _, realized = fx_pnl.build_lots(trades, equity_on)
    assert realized[0]["gross"] == pytest.approx(10.0)
    assert realized[0]["fx_known"] is True


def test_unstamped_trade_falls_back_to_price_move_only_and_is_flagged():
    """A fill from before fx_book stamped aud_per_quote must not invent a rate —
    it translates at 1.0 and the book reports how many such fills there are."""
    trades = [{"date": "2026-01-01", "pair": "EURUSD", "side": "BUY",
               "delta_weight": 0.10, "target_weight": 0.10, "price": 1.00},
              {"date": "2026-01-02", "pair": "EURUSD", "side": "SELL",
               "delta_weight": -0.10, "target_weight": 0.0, "price": 1.10}]
    state = _state(trades, history=[["2026-01-01", 1000.0]])
    equity_on, _ = fx_pnl.equity_lookup(state)
    _, realized = fx_pnl.build_lots(trades, equity_on)
    assert realized[0]["gross"] == pytest.approx(10.0)   # price move only
    assert realized[0]["fx_known"] is False
    assert fx_pnl.book_pnl(state)["fx_unstamped"] == 2


def test_unknown_or_unpriced_trades_are_skipped():
    trades = [_t("2026-01-01", "NOTAPAIR", 0.10, 1.00),
              {"date": "2026-01-02", "pair": "EURUSD", "side": "BUY",
               "delta_weight": 0.1, "price": None}]
    equity_on, _ = fx_pnl.equity_lookup(_state(trades))
    open_lots, realized = fx_pnl.build_lots(trades, equity_on)
    assert (open_lots, realized) == ({}, [])


# ---------------------------------------------------------------------------
# whole-book accounting
# ---------------------------------------------------------------------------
def test_every_spread_is_allocated_exactly_once():
    """costs == Σ(round-trip entry+exit costs) + Σ(open-lot entry costs).
    The invariant that lets the closed-trades footer and the NET P&L tile be
    derived from the same numbers without drifting."""
    trades = [_t("2026-01-01", "EURUSD", 0.20, 1.00),
              _t("2026-01-02", "USDJPY", -0.15, 150.0),
              _t("2026-01-03", "EURUSD", -0.30, 1.05, target=-0.10),
              _t("2026-01-04", "USDJPY", 0.05, 148.0, target=-0.10)]
    state = _state(trades, positions={"EURUSD": -0.10, "USDJPY": -0.10},
                   last_close={"EURUSD": 1.04, "USDJPY": 149.0, "AUDUSD": 0.66},
                   history=[["2026-01-01", 1000.0], ["2026-01-04", 1010.0]])
    equity_on, _ = fx_pnl.equity_lookup(state)
    open_lots, realized = fx_pnl.build_lots(trades, equity_on)
    per_trade = sum(fx_pnl.trade_cost(t, equity_on(t["date"])) for t in trades)
    allocated = (sum(r["entry_cost"] + r["exit_cost"] for r in realized)
                 + sum(r["entry_cost"] for r in fx_pnl.mark_open(
                     open_lots, state["last_close"])))
    assert allocated == pytest.approx(per_trade)
    assert fx_pnl.book_pnl(state)["costs"] == pytest.approx(round(per_trade, 2))


def test_book_pnl_decomposition_adds_up_to_net():
    """net_pnl = realized + open + carry + residual, always — the residual is
    reported rather than absorbed anywhere."""
    trades = [_t("2026-01-01", "EURUSD", 0.20, 1.00),
              _t("2026-01-03", "EURUSD", -0.10, 1.05, target=0.10)]
    state = _state(trades, equity=1012.0,
                   positions={"EURUSD": 0.10},
                   last_close={"EURUSD": 1.06, "AUDUSD": 0.66},
                   history=[["2026-01-01", 1000.0], ["2026-01-03", 1012.0]],
                   cumulative={"from": "2026-01-01", "start_equity": 1000.0,
                               "price_pnl": 13.0, "carry": 0.5, "cost": 1.5,
                               "by_pair": {"EURUSD": {"pnl": 13.0, "carry": 0.5,
                                                      "cost": 1.5}}, "bars": 3})
    p = fx_pnl.book_pnl(state)
    assert p["net_pnl"] == pytest.approx(12.0)
    assert (p["realized"] + p["open"] + p["carry"] + p["residual"]
            == pytest.approx(p["net_pnl"], abs=0.02))
    assert p["carry"] == pytest.approx(0.5)          # only the accumulator has it
    assert p["exact_from"] == "2026-01-01"
    # per-pair rollup carries the exact carry through
    row = next(r for r in p["by_pair"] if r["pair"] == "EURUSD")
    assert row["carry"] == pytest.approx(0.5)
    assert row["round_trips"] == 1 and row["trades"] == 2


def test_carry_is_none_when_the_book_predates_the_accumulator():
    state = _state([_t("2026-01-01", "EURUSD", 0.10, 1.00)],
                   positions={"EURUSD": 0.10},
                   last_close={"EURUSD": 1.01, "AUDUSD": 0.66})
    p = fx_pnl.book_pnl(state)
    assert p["carry"] is None and p["exact_from"] is None


def test_currency_exposure_splits_each_pair_into_its_legs():
    exp = fx_pnl.currency_exposure({"EURUSD": 0.20, "USDJPY": -0.10}, 1000.0)
    by = {e["currency"]: e for e in exp}
    assert by["EUR"]["weight"] == pytest.approx(0.20)
    assert by["USD"]["weight"] == pytest.approx(-0.30)   # short EURUSD leg + short USDJPY base
    assert by["JPY"]["weight"] == pytest.approx(0.10)
    assert by["EUR"]["notional"] == pytest.approx(200.0)


def test_blotter_prices_each_fill_and_hides_why_by_default():
    trades = [_t("2026-01-01", "EURUSD", 0.20, 1.25)]
    trades[0]["why"] = "because"
    state = _state(trades, history=[["2026-01-01", 2000.0]])
    rows = fx_pnl.blotter(state)
    assert len(rows) == 1
    r = rows[0]
    assert "why" not in r
    assert r["notional"] == pytest.approx(400.0)         # 0.20 × 2000
    assert r["cost"] == pytest.approx(
        round(marks.trade_cost(0.20, get_pair("EURUSD"), 1.25, 2000.0), 4))
    assert r["bid"] < r["price"] < r["ask"]
    assert r["spread_bps"] > 0
    assert fx_pnl.blotter(state, include_why=True)[0]["why"] == "because"


def test_mark_open_is_offline_and_survives_a_missing_price():
    """A pair with no stored close still lists (weight, notional, cost basis) —
    it just has no mark. Nothing here touches the network."""
    trades = [_t("2026-01-01", "EURUSD", 0.10, 1.00)]
    state = _state(trades, positions={"EURUSD": 0.10}, last_close={})
    equity_on, _ = fx_pnl.equity_lookup(state)
    open_lots, _ = fx_pnl.build_lots(trades, equity_on)
    row = fx_pnl.mark_open(open_lots, {})[0]
    assert row["priced"] is False
    assert row["unrealized"] == 0.0
    assert row["notional"] == pytest.approx(100.0)


def test_equity_lookup_is_asof_and_shared_with_the_static_dashboard():
    from trading_algo.forex import dashboard as fxdash
    assert fxdash._equity_lookup is fx_pnl.equity_lookup, \
        "one string-asof equity lookup — do not re-implement it"
    state = _state([], history=[["2026-01-05", 100.0], ["2026-01-10", 200.0]])
    equity_on, _ = fx_pnl.equity_lookup(state)
    assert equity_on("2026-01-05") == 100.0
    assert equity_on("2026-01-07") == 100.0      # asof: last bar at or before
    assert equity_on("2026-01-10") == 200.0
    assert equity_on("2025-01-01") == state["equity"]   # predates the curve


def test_formulas_come_from_the_marks_module():
    """fx_pnl must never grow its own cost/mark arithmetic — the book's charge
    and the ledger's must be the same function (see forex/marks.py)."""
    import inspect
    src = inspect.getsource(fx_pnl)
    assert "marks.trade_cost" in src and "marks.trade_mark" in src
    assert "spread_pips * " not in src, "re-derived the spread instead of using marks"


# ---------------------------------------------------------------------------
# the exact per-bar accumulator in fx_book
# ---------------------------------------------------------------------------
def test_cumulative_identity_holds_bar_by_bar(isolated, pool, monkeypatch):
    """equity − start_equity == price_pnl + carry − cost, exactly, and the
    per-instrument legs sum to each total."""
    symbols = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]
    full = fx_data.synthetic_panel(symbols, start="2015-01-01", end="2026-01-01")
    cut = {"n": 0}

    def sliced(*a, **k):
        # advance the panel one bar per call so every run marks a NEW bar
        return {s: df.iloc[:len(df) - 6 + cut["n"]] for s, df in full.items()}

    monkeypatch.setattr(fx_book, "_panel", sliced)
    fx_book.init_account("t", 5_000, "balanced")
    for i in range(6):
        cut["n"] = i
        fx_book.run_once("t", synthetic=True, pool=pool)

    state = fx_book.load_state("t")
    c = state["cumulative"]
    assert c["bars"] == 6 and c["from"] and c["to"]
    assert c["start_equity"] == pytest.approx(5_000.0)      # tracked from inception
    assert (state["equity"] - c["start_equity"]
            == pytest.approx(c["price_pnl"] + c["carry"] - c["cost"], abs=1e-6))
    legs = c["by_pair"].values()
    assert sum(v["pnl"] for v in legs) == pytest.approx(c["price_pnl"], abs=1e-9)
    assert sum(v["carry"] for v in legs) == pytest.approx(c["carry"], abs=1e-9)
    assert sum(v["cost"] for v in legs) == pytest.approx(c["cost"], abs=1e-9)


def test_cumulative_cost_matches_the_ledger_reconstruction(isolated, pool):
    """Two independent derivations of the same spread bill: the accumulator's
    per-bar charge and the blotter's replay of the trades log."""
    fx_book.init_account("t", 5_000, "balanced")
    fx_book.run_once("t", synthetic=True, pool=pool)
    state = fx_book.load_state("t")
    ledger = sum(r["cost"] for r in fx_pnl.blotter(state))
    assert state["cumulative"]["cost"] == pytest.approx(ledger, abs=0.02)


def test_accumulator_is_additive_not_reset(isolated, pool, monkeypatch):
    """A book that predates the accumulator starts tracking at its next bar and
    says so, instead of pretending the earlier history was measured."""
    fx_book.init_account("t", 5_000, "balanced")
    fx_book.run_once("t", synthetic=True, pool=pool)
    state = fx_book.load_state("t")
    state["cumulative"] = {"from": "2020-01-01", "start_equity": 4_000.0,
                           "price_pnl": 1.0, "carry": 0.0, "cost": 0.0,
                           "by_pair": {}, "bars": 1}
    fx_book.save_state("t", state)
    fx_book.run_once("t", synthetic=True, pool=pool)       # same bar: no-op
    again = fx_book.load_state("t")
    assert again["cumulative"]["from"] == "2020-01-01"      # never re-seeded
    assert again["cumulative"]["price_pnl"] == 1.0


# ---------------------------------------------------------------------------
# the dashboard contract
# ---------------------------------------------------------------------------
FX_MONEY_STATE = _state(
    [_t("2026-01-01", "EURUSD", 0.20, 1.00, apq=0.66),
     _t("2026-01-05", "EURUSD", -0.10, 1.05, apq=0.66, target=0.10),
     _t("2026-01-05", "USDJPY", 0.10, 150.0, apq=0.0091)],
    equity=1015.0, positions={"EURUSD": 0.10, "USDJPY": 0.10},
    last_close={"EURUSD": 1.06, "USDJPY": 151.0, "AUDUSD": 0.66},
    history=[["2026-01-01", 1000.0], ["2026-01-05", 1015.0]],
    decisions={"EURUSD": {"weight": 0.10, "tilt": 0.4, "regime": "trending",
                          "agents": {"trend": 0.5}, "indicators": {"price": 1.06},
                          "text": "LONG EURUSD"}},
    daily={"date": "2026-01-05", "pnl_pct": 0.01, "carry_pct": 0.0,
           "cost_pct": -0.0001, "net_pct": 0.0099, "net_aud": 10.0,
           "by_pair": [{"pair": "EURUSD", "weight": 0.2, "move": 0.05,
                        "fx": 0.0, "contrib": 0.01}]},
    cumulative={"from": "2026-01-01", "start_equity": 1000.0, "price_pnl": 16.0,
                "carry": 0.2, "cost": 1.2, "bars": 2,
                "by_pair": {"EURUSD": {"pnl": 16.0, "carry": 0.2, "cost": 1.0},
                            "USDJPY": {"pnl": 0.0, "carry": 0.0, "cost": 0.2}}})


@pytest.fixture
def money_book(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    registry.discover_accounts.cache_clear() if hasattr(
        registry.discover_accounts, "cache_clear") else None
    with open(os.path.join(str(tmp_path), "fx_state_matt.json"), "w") as f:
        json.dump(FX_MONEY_STATE, f)
    return tmp_path


def test_fx_snapshot_carries_the_money_layer(money_book):
    snap = fx_api.build_fx_snapshot("matt")
    for key in ("pnl", "positions_money", "pnl_by_pair", "exposure", "blotter",
                "closed", "drawdown", "cumulative"):
        assert key in snap, f"FX snapshot lost its {key} block"

    p = snap["pnl"]
    assert p["currency"] == "AUD"
    assert p["net_pnl"] == pytest.approx(15.0)
    assert p["realized"] + p["open"] + p["carry"] + p["residual"] \
        == pytest.approx(p["net_pnl"], abs=0.02)
    assert p["gross_notional"] == pytest.approx(0.20 * 1015.0)

    # open positions: both legs, marked, with a cost basis
    pairs = {r["pair"] for r in snap["positions_money"]}
    assert pairs == {"EURUSD", "USDJPY"}
    eur = next(r for r in snap["positions_money"] if r["pair"] == "EURUSD")
    assert eur["avg_entry"] == pytest.approx(1.00)
    assert eur["notional"] == pytest.approx(100.0)
    assert eur["day_move"] == pytest.approx(0.05)      # from the daily attribution
    jpy = next(r for r in snap["positions_money"] if r["pair"] == "USDJPY")
    assert jpy["day_move"] is None                     # not held into that bar → '—'

    # closed ledger totals come from the full ledger, not the displayed rows
    c = snap["closed"]
    assert c["count"] == 1 and c["net"] == pytest.approx(p["realized"])
    assert c["rows"][0]["pair"] == "EURUSD"
    assert c["rows"][0]["side"] == "LONG"
    assert c["rows"][0]["return_pct"] != 0

    # blotter + drawdown + exact decomposition
    assert snap["blotter_count"] == 3
    assert snap["blotter"][0]["notional"] == pytest.approx(200.0)
    assert snap["drawdown"][-1]["dd"] <= 0.0
    assert snap["cumulative"]["from_inception"] is True
    assert snap["cumulative"]["net"] == pytest.approx(15.0)


def test_decision_rows_gain_money_and_never_hide_a_holding(money_book):
    """Every open leg appears in the decision book even with no decision on
    file, and each row reads as a holding as well as a view."""
    snap = fx_api.build_fx_snapshot("matt")
    rows = {r["pair"]: r for r in snap["rows"]}
    assert "USDJPY" in rows, "a held position must never be invisible"
    eur = rows["EURUSD"]
    assert eur["notional"] == pytest.approx(100.0)
    assert eur["avg_entry"] == pytest.approx(1.00)
    assert eur["realized"] != 0.0
    assert eur["total_pnl"] == pytest.approx(
        eur["realized"] + eur["unrealized"] - 0.0, abs=1.0)
    # the decision fields are untouched
    assert eur["tilt"] == pytest.approx(0.4) and eur["regime"] == "TRENDING"
    assert len(eur["agents"]) == 6


def test_ledgers_are_bounded_but_totals_are_not(money_book, monkeypatch):
    """A long-running hourly book ships a bounded table and says so; the footer
    totals still describe every fill."""
    monkeypatch.setattr(fx_api, "LEDGER_MAX", 2)
    snap = fx_api.build_fx_snapshot("matt")
    assert snap["blotter_count"] == 3 and snap["blotter_shown"] == 2
    assert len(snap["blotter"]) == 2
    assert snap["blotter"][-1]["pair"] == "USDJPY"          # newest kept
    assert snap["closed"]["count"] == 1
