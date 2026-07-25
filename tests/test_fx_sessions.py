"""The market-hours trading gate — the ONE thing that stopped 104 bad fills.

`fx_data._align` forward-fills, so a weekend bar carries the last real print. The
book used to trade it: `fx_market_open()` gated only `engine.run_loop`, while every
workflow (and every cron) invokes `--once`, which reaches `fx_book.run_once`
directly. The result was fills — and paid spread — at prices no venue was quoting.

These tests pin the fix at the level the bug lived at:

* a closed-market bar must produce NO trades, while still marking to it;
* an open-market bar with the same data must still trade;
* crypto is exempt (genuinely 24/7);
* the gate and `verify.check_market_hours` must share one definition, so a fill
  the gate allows can never be one the audit flags.
"""
import pandas as pd
import pytest

from trading_algo import verify
from trading_algo.forex import fx_book, sessions
from trading_algo.forex.agents import AgentPool
from trading_algo.forex.pairs import DEFAULT_UNIVERSE


# --- the boundaries themselves ------------------------------------------------
@pytest.mark.parametrize("stamp,tradable", [
    ("2024-01-03", True),        # Wednesday
    ("2024-01-06", False),       # Saturday
    ("2024-01-07", False),       # Sunday 00:00 — week has not restarted
    ("2024-01-07 23:00", True),  # Sunday 23:00 UTC — FX week open
    ("2024-01-05", True),        # Friday daily bar (no hour -> 00:00, inside)
    ("2024-01-05 23:00", False), # Friday 23:00 UTC — after the FX close
])
def test_fx_week_boundaries(stamp, tradable):
    assert sessions.bar_is_tradable("EURUSD", sessions.parse_bar(stamp)) is tradable


def test_crypto_is_never_gated():
    """Crypto trades through the weekend, so the gate must not touch it."""
    saturday = sessions.parse_bar("2024-01-06 12:00")
    assert sessions.bar_is_tradable("BTCUSD", saturday) is True
    assert sessions.bar_is_tradable("EURUSD", saturday) is False


def test_parse_bar_handles_both_key_shapes():
    assert sessions.parse_bar("2024-01-03").hour == 0
    assert sessions.parse_bar("2024-01-03 14:30").hour == 14
    with pytest.raises(ValueError):
        sessions.parse_bar("not-a-bar")


# --- the gate in the book ------------------------------------------------------
def _panel_ending(last_day: str, symbols):
    """A real multi-bar synthetic panel re-dated so its FINAL bar lands on
    `last_day`. Enough history for a genuine decision, so the gate is what
    changes the outcome — not a warm-up shortfall."""
    from trading_algo.forex.fx_data import synthetic_panel

    panel = synthetic_panel(list(symbols))
    n = len(next(iter(panel.values())))
    idx = pd.bdate_range(end=pd.Timestamp(last_day), periods=n)
    # bdate_range never lands on a weekend, so pin the last label explicitly.
    idx = idx[:-1].append(pd.DatetimeIndex([pd.Timestamp(last_day)]))
    return {s: df.set_axis(idx) for s, df in panel.items()}


def _run_on(monkeypatch, tmp_path, last_day: str, account="g"):
    """Seed a funded book and advance it NON-synthetically on a crafted bar."""
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.init_account(account, 50_000, "balanced")
    syms = list(DEFAULT_UNIVERSE)
    monkeypatch.setattr(fx_book, "_panel",
                        lambda *a, **k: _panel_ending(last_day, syms))
    fx_book.run_once(account, synthetic=False, pool=AgentPool(max_workers=1))
    return fx_book.load_state(account)


def _fx_trades(state):
    return [t for t in state["trades"] if not sessions.is_crypto(t["pair"])]


def _crypto_trades(state):
    return [t for t in state["trades"] if sessions.is_crypto(t["pair"])]


def test_closed_market_bar_writes_no_fx_fills(tmp_path, monkeypatch):
    """A Saturday bar must not fill any FX pair — the price is a forward-filled
    ghost. Crypto in the same book keeps trading, which is the point of gating
    per SYMBOL rather than per run."""
    state = _run_on(monkeypatch, tmp_path, "2024-01-06")     # Saturday
    assert _fx_trades(state) == []
    assert _crypto_trades(state), "crypto is 24/7 and must not be gated"
    assert not any(not sessions.is_crypto(s) for s in state["positions"]), \
        "a closed FX market must not open FX exposure"
    # ...and the book still advanced: it marked and recorded the bar.
    assert state["last_bar_date"] == "2024-01-06"


def test_open_market_bar_does_trade_fx(tmp_path, monkeypatch):
    """Control: identical data on a Wednesday must fill FX too, or the test above
    would pass for the wrong reason (e.g. no signal at all)."""
    state = _run_on(monkeypatch, tmp_path, "2024-01-03")     # Wednesday
    assert _fx_trades(state), "an open FX market with a live signal must fill"
    assert state["last_bar_date"] == "2024-01-03"


def test_closed_market_holds_an_existing_position(tmp_path, monkeypatch):
    """Holding is not flattening: a shut market must leave exposure untouched
    rather than 'selling' it at a price nobody was quoting."""
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.init_account("h", 50_000, "balanced")
    st = fx_book.load_state("h")
    st.update({"positions": {"EURUSD": 0.25}, "last_bar_date": "2024-01-04",
               "last_close": {s: 1.0 for s in DEFAULT_UNIVERSE}})
    fx_book.save_state("h", st)
    syms = list(DEFAULT_UNIVERSE)
    monkeypatch.setattr(fx_book, "_panel",
                        lambda *a, **k: _panel_ending("2024-01-06", syms))
    fx_book.run_once("h", synthetic=False, pool=AgentPool(max_workers=1))
    after = fx_book.load_state("h")
    assert [t for t in after["trades"] if t["pair"] == "EURUSD"] == []
    assert after["positions"].get("EURUSD") == pytest.approx(0.25)


# --- gate and audit must agree ------------------------------------------------
def test_gate_and_verifier_share_one_definition(tmp_path, monkeypatch):
    """Whatever the gate permits, the verifier must not flag — otherwise the
    audit reports an error no code path can avoid producing."""
    state = _run_on(monkeypatch, tmp_path, "2024-01-03")     # open: trades exist
    assert state["trades"]
    findings = verify.check_market_hours("g", state, "fx")
    assert findings == [], f"verifier flagged fills the gate allowed: {findings}"


def test_weekend_crypto_fills_are_not_flagged(tmp_path, monkeypatch):
    """The same agreement on the harder case: the gate lets weekend CRYPTO
    through, so the verifier must not report those either."""
    state = _run_on(monkeypatch, tmp_path, "2024-01-06")     # Saturday
    assert _crypto_trades(state)
    assert verify.check_market_hours("g", state, "fx") == []


def test_verifier_still_catches_legacy_closed_market_fills():
    """The gate stops NEW bad fills; the verifier must keep reporting the ones
    already committed to history (that is how the 104 were found)."""
    state = {"trades": [{"date": "2024-01-06", "pair": "EURUSD", "side": "BUY",
                         "price": 1.09, "delta_weight": 0.1}]}
    findings = verify.check_market_hours("legacy", state, "fx")
    assert len(findings) == 1 and findings[0].code == "closed-market-trade"
