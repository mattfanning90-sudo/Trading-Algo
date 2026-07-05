"""Backlog F12: the drawdown breaker fires exactly one alert per transition."""
import pytest

from trading_algo import config as cfg
from trading_algo import notifications, paper_trade


@pytest.fixture
def captured(monkeypatch):
    got = []
    notifications.register_channel("cap", got.append)
    monkeypatch.setattr(cfg, "NOTIFY_CHANNEL", "cap")
    return got


def test_run_daily_alerts_once_on_halt_transition(monkeypatch, tmp_path, captured):
    monkeypatch.setattr(paper_trade, "STATE_DIR", str(tmp_path))
    # Force the breaker to report a halt transition regardless of the (benign)
    # synthetic drawdown, so we exercise the alert wiring deterministically.
    monkeypatch.setattr(notifications, "breaker_transition", lambda prev, now: "halt")

    paper_trade.init_account("t", 100_000, synthetic=True, allocations={"US": 1.0})
    paper_trade.run_daily("t", synthetic=True)

    halts = [p for p in captured if p["event"] == "breaker_halt"]
    assert len(halts) == 1
    assert halts[0]["level"] == "alert" and halts[0]["account"] == "t"


def test_no_alert_when_no_transition(monkeypatch, tmp_path, captured):
    monkeypatch.setattr(paper_trade, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(notifications, "breaker_transition", lambda prev, now: None)
    paper_trade.init_account("t", 100_000, synthetic=True, allocations={"US": 1.0})
    paper_trade.run_daily("t", synthetic=True)
    assert not [p for p in captured if p["event"].startswith("breaker_")]
