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


def test_a_hung_fetch_times_out_and_a_late_finish_still_fills_the_cache():
    from trading_algo.dashboard.live_marks import HANG_AFTER
    gate, clock = threading.Event(), Clock()
    calls = []

    def build(acct):
        calls.append(acct)
        if len(calls) == 1:
            gate.wait(10)
        return {"as_of": "x", "n": len(calls)}

    lm = LiveMarks(build, online=lambda: True, ttl=10_000, clock=clock)
    first = lm.start("full")
    assert first == "pending"
    clock.t = HANG_AFTER + 1
    assert lm.status("full")[0] == "error"
    assert lm.start("full") == "pending"          # a fresh fetch replaces the hung one
    lm.wait("full", 5)
    assert lm.status("full") == ("ready", {"as_of": "x", "n": 2})
    gate.set()                                    # the old one finishes late
    import time
    for _ in range(100):
        if len(calls) == 1 or lm.status("full")[1]["n"] == 1:
            break
        time.sleep(0.02)
    assert lm.status("full")[0] == "ready"


def test_start_on_a_fresh_cache_does_not_refetch():
    calls = []
    lm = LiveMarks(lambda a: calls.append(a) or {"k": 1}, online=lambda: True)
    lm.start("full")
    lm.wait("full", 5)
    assert lm.start("full") == "ready" and calls == ["full"]
