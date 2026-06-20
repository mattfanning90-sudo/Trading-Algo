"""Latency benchmark harness."""
from trading_algo.bench import time_us


def test_time_us_reports_microseconds():
    s = time_us(lambda: sum(range(1000)), iters=50)
    assert set(s) >= {"min", "median", "mean", "p95", "n"}
    assert s["n"] == 50
    assert s["min"] > 0          # microseconds, positive
    assert s["median"] >= s["min"]
    assert s["p95"] >= s["median"]


def test_time_us_warms_up_once():
    calls = {"n": 0}

    def f():
        calls["n"] += 1
    time_us(f, iters=10)
    assert calls["n"] == 11      # 1 warm-up + 10 measured
