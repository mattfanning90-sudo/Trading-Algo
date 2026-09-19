"""Backlog F12 / foundation P0-F: shared notification channel + breaker alerts."""
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


# ---------------------------------------------------------------------------
# Webhook channel — the delivery path that leaves the box
# ---------------------------------------------------------------------------
# The registry has existed since F12 with exactly one channel, which prints.
# `verify.py`'s own docstring records the cost: "never-traded: sleeve ASX fired
# every day for 52 days" into a CI log nobody opens.
import json as _json


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture_urlopen(sent, fail=False):
    def _urlopen(req, timeout=None):
        if fail:
            raise OSError("endpoint down")
        sent["url"] = req.full_url
        sent["body"] = _json.loads(req.data.decode())
        sent["headers"] = {k.lower(): v for k, v in req.header_items()}
        sent["timeout"] = timeout
        return _FakeResponse()
    return _urlopen


def test_webhook_channel_posts_json_to_the_configured_url(monkeypatch):
    sent = {}
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://example.test/hook")
    monkeypatch.setattr(N.urllib.request, "urlopen", _capture_urlopen(sent))
    N.notify("audit_errors", "2 books broken", level="alert", channel="webhook")
    assert sent["url"] == "https://example.test/hook"
    assert sent["body"]["event"] == "audit_errors"
    assert sent["body"]["level"] == "alert"
    assert sent["headers"]["content-type"] == "application/json"
    assert sent["timeout"] == N.WEBHOOK_TIMEOUT_SECONDS


def test_webhook_payload_carries_a_human_readable_line(monkeypatch):
    """Slack/Discord/ntfy all render a `text` field, so the alert is readable
    without anyone parsing JSON on their phone."""
    sent = {}
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://example.test/hook")
    monkeypatch.setattr(N.urllib.request, "urlopen", _capture_urlopen(sent))
    N.notify("audit_errors", "sleeve ASX is parked", level="alert",
             channel="webhook")
    assert sent["body"]["text"] == "[ALERT] audit_errors: sleeve ASX is parked"


def test_webhook_without_a_url_is_a_silent_noop(monkeypatch):
    """A book must still trade on a laptop with no webhook configured."""
    sent = {}
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(N.urllib.request, "urlopen", _capture_urlopen(sent))
    N.notify("evt", "msg", channel="webhook")
    assert sent == {}


def test_webhook_logs_before_posting_so_a_dead_endpoint_loses_nothing(
        monkeypatch, capsys):
    """`notify` swallows channel exceptions, so a POST that raises would take
    the local trace down with it unless the log happens FIRST."""
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://example.test/hook")
    monkeypatch.setattr(N.urllib.request, "urlopen",
                        _capture_urlopen({}, fail=True))
    N.notify("audit_errors", "still recorded", level="alert", channel="webhook")
    assert "still recorded" in capsys.readouterr().out
