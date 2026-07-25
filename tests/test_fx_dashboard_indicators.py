"""What the FX terminal screens are allowed to claim about a bar.

Two honesty contracts on the `fx_api` payload:

  * the agents' OWN indicator readings reach the frontend, so the terminal can
    label RSI/ADX/momentum as LIVE instead of re-deriving them from bars it
    invented — including the NaN-sanitising that keeps the page's JSON valid;
  * the data-quality gate is reported as `null` when it was never measured and
    as the actual exclusion set when it was — "nothing flagged" and "never
    checked" must never render the same.
"""
import json
import os

import pytest

from trading_algo.dashboard import fx_api
from trading_algo.forex import fx_book


def _state(**extra):
    """A minimal one-decision FX book on disk (same shape as fx_state_*.json)."""
    return {
        "account": "matt", "currency": "AUD", "profile": "balanced", "bar": "1d",
        "symbols": ["EURUSD"], "initial_capital": 1000.0, "equity": 1000.0,
        "positions": {"EURUSD": 0.10}, "last_close": {"EURUSD": 1.06},
        "last_bar_date": "2026-01-05", "peak_equity": 1000.0,
        "risk_halted": False, "halt_cooldown": 0, "trades": [],
        "equity_history": [["2026-01-05", 1000.0]],
        "decisions": {"EURUSD": {
            "weight": 0.10, "tilt": 0.4, "regime": "trending",
            "agents": {"trend": 0.5}, "text": "LONG EURUSD",
            "indicators": {"price": 1.06, "ema_fast": 1.055, "ema_slow": 1.04,
                           "adx": 24.5, "rsi": 61.2, "roc": 0.012,
                           "bb_z": 1.4, "donchian_hi": 1.07,
                           "donchian_lo": 1.01, "ann_vol": 0.08}}},
        **extra,
    }


@pytest.fixture
def book(tmp_path, monkeypatch):
    def write(state):
        monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
        with open(os.path.join(str(tmp_path), "fx_state_matt.json"), "w") as f:
            json.dump(state, f)
        return fx_api.build_fx_snapshot("matt")
    return write


# ---------------------------------------------------------------------------
# indicators
# ---------------------------------------------------------------------------
def test_every_persisted_indicator_reaches_the_row(book):
    snap = book(_state())
    row = next(r for r in snap["rows"] if r["pair"] == "EURUSD")
    assert row["indicators"] == {
        "price": 1.06, "ema_fast": 1.055, "ema_slow": 1.04, "adx": 24.5,
        "rsi": 61.2, "roc": 0.012, "bb_z": 1.4, "donchian_hi": 1.07,
        "donchian_lo": 1.01, "ann_vol": 0.08}
    # the pre-existing scalar fields still read the same way
    assert row["price"] == pytest.approx(1.06)
    assert row["ann_vol"] == pytest.approx(0.08)


def test_a_nan_indicator_is_nulled_and_the_payload_stays_valid_json(book):
    """ann_vol is NaN on a short history; NaN in json.dumps is invalid JSON and
    would kill the whole page, so it must arrive as null."""
    state = _state()
    state["decisions"]["EURUSD"]["indicators"]["ann_vol"] = float("nan")
    state["decisions"]["EURUSD"]["indicators"]["adx"] = None
    snap = book(state)
    row = next(r for r in snap["rows"] if r["pair"] == "EURUSD")
    assert row["indicators"]["ann_vol"] is None
    assert row["indicators"]["adx"] is None
    assert row["indicators"]["rsi"] == pytest.approx(61.2)
    assert "NaN" not in json.dumps(snap, allow_nan=False)


def test_a_held_leg_with_no_decision_has_an_empty_indicator_dict(book):
    """USDJPY is held but was never decided on — an empty dict, so the UI shows
    '—' rather than a stale reading from another pair."""
    state = _state()
    state["positions"]["USDJPY"] = 0.10
    snap = book(state)
    row = next(r for r in snap["rows"] if r["pair"] == "USDJPY")
    assert row["indicators"] == {}


# ---------------------------------------------------------------------------
# data-quality exclusions
# ---------------------------------------------------------------------------
def test_data_quality_is_null_when_the_book_never_measured_it(book):
    """Every book on disk predates the persisted gate — null, NOT {} or [],
    either of which would render as 'all clear'."""
    snap = book(_state())
    assert snap["data_quality"] is None


def test_data_quality_reports_the_dropped_legs_with_reasons(book):
    snap = book(_state(data_quality={
        "excluded": ["GBPUSD", "USDCHF"], "date": "2026-01-05",
        "reasons": {"GBPUSD": "stale (4 unchanged closes)",
                    "USDCHF": "dead price (0.0)"}}))
    dq = snap["data_quality"]
    assert dq["excluded"] == ["GBPUSD", "USDCHF"]
    assert dq["date"] == "2026-01-05"
    assert dq["reasons"]["USDCHF"] == "dead price (0.0)"


def test_a_clean_bar_reports_an_empty_exclusion_set_not_null(book):
    """Measured and nothing flagged is a real, different statement from
    never measured — the UI can say 'all symbols clean'."""
    snap = book(_state(data_quality={"excluded": [], "reasons": {},
                                     "date": "2026-01-05"}))
    assert snap["data_quality"] == {"excluded": [], "reasons": {},
                                    "date": "2026-01-05"}


def test_a_malformed_data_quality_block_degrades_instead_of_raising(book):
    """The dashboard must never 500 on a state written by an older/newer engine."""
    assert book(_state(data_quality=["GBPUSD"]))["data_quality"] is None
    assert book(_state(data_quality={}))["data_quality"] == {
        "excluded": [], "reasons": {}, "date": ""}
