"""Backlog F12 / foundation P0-F: shared notification channel + breaker alerts."""
import pytest

from trading_algo import config as cfg
from trading_algo import notifications as N


def test_breaker_transition_cases():
    assert N.breaker_transition(False, True) == "halt"
    assert N.breaker_transition(True, False) == "resume"
    assert N.breaker_transition(True, True) is None
    assert N.breaker_transition(False, False) is None


def test_notify_dispatches_to_configured_channel(monkeypatch):
    got = []
    N.register_channel("cap", got.append)
    monkeypatch.setattr(cfg, "NOTIFY_CHANNEL", "cap")
    payload = N.notify("evt", "hello", level="alert", account="full")
    assert got and got[0]["event"] == "evt" and got[0]["level"] == "alert"
    assert got[0]["account"] == "full"
    assert payload["message"] == "hello"


def test_notify_never_raises(monkeypatch):
    def boom(_payload):
        raise RuntimeError("channel down")
    N.register_channel("boom", boom)
    monkeypatch.setattr(cfg, "NOTIFY_CHANNEL", "boom")
    # must not propagate — telemetry can't break a trading run
    assert N.notify("evt", "msg")["event"] == "evt"


def test_log_channel_is_always_registered():
    assert "log" in N._CHANNELS
