"""Overview cards expose the breaker's own numbers, so the home screen can say
"near the breaker" without computing a second drawdown."""
import json

import trading_algo.paper_trade as pt
from trading_algo import config as cfg
from trading_algo.dashboard import overview
from trading_algo.forex import fx_book
from trading_algo.forex import fx_config


def _fx(tmp_path, **over):
    state = {"account": "matt", "currency": "AUD", "profile": "balanced",
             "bar": "1d", "symbols": ["EURUSD"], "initial_capital": 5000.0,
             "equity": 4900.0, "peak_equity": 5070.0, "positions": {},
             "last_close": {}, "last_bar_date": "2026-10-09",
             "risk_halted": False, "halt_cooldown": 0, "trades": [],
             "decisions": {},
             "equity_history": [["2026-10-08", 4950.0], ["2026-10-09", 4900.0]]}
    state.update(over)
    (tmp_path / "fx_state_matt.json").write_text(json.dumps(state))


def _cards(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    return {c["key"]: c for c in overview.build_overview()["accounts"]}


def test_fx_card_has_off_peak_and_profile_breaker(tmp_path, monkeypatch):
    _fx(tmp_path)
    card = _cards(tmp_path, monkeypatch)["MATT"]
    assert card["off_peak"] == round(4900.0 / 5070.0 - 1.0, 6)     # -0.033531
    assert card["breaker"] == fx_config.profile("balanced").max_drawdown_stop


def test_equity_card_uses_the_engine_breaker(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    pt.init_account("full", capital=100_000, synthetic=True)
    card = _cards(tmp_path, monkeypatch)["FULL"]
    assert card["breaker"] == cfg.MAX_DRAWDOWN_STOP
    assert card["off_peak"] <= 0.0


def test_book_without_a_breaker_reports_none(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    pt.init_account("full", capital=100_000, synthetic=True)
    path = tmp_path / "paper_state_full.json"
    state = json.loads(path.read_text())
    state["max_drawdown_stop"] = None          # the `ultra` profile does this
    path.write_text(json.dumps(state))
    assert _cards(tmp_path, monkeypatch)["FULL"]["breaker"] is None
