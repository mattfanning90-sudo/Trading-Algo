import threading

from trading_algo.dashboard.live_marks import LiveMarks


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_idle_then_pending_then_ready_then_expired():
    gate, clock = threading.Event(), Clock()
    lm = LiveMarks(lambda acct: (gate.wait(5), {"as_of": "2026-10-13"})[1],
                   online=lambda: True, ttl=60, clock=clock)
    assert lm.status("full") == ("idle", None)
    assert lm.start("full") == "pending" and lm.status("full")[0] == "pending"
    gate.set()
    lm.wait("full", 5)
    assert lm.status("full") == ("ready", {"as_of": "2026-10-13"})
    clock.t = 61
    assert lm.status("full")[0] == "idle"


def test_offline_never_starts_a_fetch():
    called = []
    lm = LiveMarks(lambda a: called.append(a), online=lambda: False)
    assert lm.start("full") == "offline" and lm.status("full")[0] == "offline" and called == []


def test_failed_fetch_reports_error_and_retries_later():
    clock = Clock()
    lm = LiveMarks(lambda a: 1 / 0, online=lambda: True, clock=clock)
    lm.start("full")
    lm.wait("full", 5)
    assert lm.status("full")[0] == "error"
    clock.t = 61
    assert lm.status("full")[0] == "idle"
