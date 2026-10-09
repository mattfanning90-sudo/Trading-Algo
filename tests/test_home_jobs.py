"""Job health against grace periods measured from GitHub's run history."""
import urllib.error
from datetime import datetime, timezone

import pytest

from trading_algo.home import jobs

SHARES, FX, DAY = ("Paper Trade & Publish Dashboard", "FX Paper Trading (live)",
                   "Day Trading Paper (hourly)")


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def run(name, started, conclusion="success", status="completed"):
    return {"name": name, "run_started_at": started.isoformat().replace("+00:00", "Z"),
            "status": status, "conclusion": conclusion,
            "html_url": f"https://github.com/x/runs/{name[:3]}"}


def states(runs, now):
    return {s.name: s.state for s in jobs.classify(runs, now)}


def test_daily_job_that_ran_after_its_slot_is_ok():
    # Fri 9 Oct 21:30 slot, started 22:51; viewed Sat morning.
    got = states([run(SHARES, utc(2026, 10, 9, 22, 51))], utc(2026, 10, 10, 8, 0))
    assert got[SHARES] == "ok"


def test_daily_job_within_grace_is_late_not_missed():
    # Mon 12 Oct 21:30 slot, nothing since Friday, viewed 23:00 (1.5 h later).
    got = states([run(SHARES, utc(2026, 10, 9, 22, 51))], utc(2026, 10, 12, 23, 0))
    assert got[SHARES] == "late"


def test_daily_job_past_grace_is_missed():
    # Monday's slot, viewed Tue 06:30 UTC = 9 h later (> 8 h 30 m).
    got = states([run(SHARES, utc(2026, 10, 9, 22, 51))], utc(2026, 10, 13, 6, 30))
    assert got[SHARES] == "missed"


def test_latest_failed_run_is_failed():
    got = states([run(FX, utc(2026, 10, 9, 23, 40), conclusion="failure")],
                 utc(2026, 10, 10, 8, 0))
    assert got[FX] == "failed"


def test_cancelled_latest_run_is_looked_past():
    runs = [run(SHARES, utc(2026, 10, 9, 22, 51)),
            run(SHARES, utc(2026, 10, 9, 23, 30), conclusion="cancelled")]
    assert states(runs, utc(2026, 10, 10, 8, 0))[SHARES] == "ok"


def test_in_progress_run_is_running():
    got = states([run(FX, utc(2026, 10, 9, 23, 40), conclusion=None,
                      status="in_progress")], utc(2026, 10, 9, 23, 45))
    assert got[FX] == "running"


def test_hourly_job_missed_after_14_hours_on_a_weekday():
    # Mon 12 Oct 15:00, last run Friday: 15 h since UTC midnight.
    got = states([run(DAY, utc(2026, 10, 9, 22, 7))], utc(2026, 10, 12, 15, 0))
    assert got[DAY] == "missed"


def test_hourly_job_with_a_run_today_is_ok():
    got = states([run(DAY, utc(2026, 10, 12, 2, 10))], utc(2026, 10, 12, 15, 0))
    assert got[DAY] == "ok"


def test_hourly_job_is_ok_at_weekends():
    got = states([run(DAY, utc(2026, 10, 9, 20, 7))], utc(2026, 10, 11, 12, 0))
    assert got[DAY] == "ok"


@pytest.fixture()
def fresh_cache(monkeypatch):
    monkeypatch.setattr(jobs, "_cache", {"at": 0.0, "runs": None, "ok_at": None})


def test_unreachable_github_is_unknown_never_ok(fresh_cache):
    def boom():
        raise urllib.error.URLError("offline")
    out = jobs.check(now=utc(2026, 10, 10, 8, 0), fetch=boom)
    assert {j["state"] for j in out} == {"unknown"}
    assert all("unknown since" in j["summary"] for j in out)


def test_http_403_is_unknown_not_ok(fresh_cache):
    def limited():
        raise urllib.error.HTTPError("u", 403, "rate limited", {}, None)
    out = jobs.check(now=utc(2026, 10, 10, 8, 0), fetch=limited)
    assert {j["state"] for j in out} == {"unknown"}


def test_check_caches_for_five_minutes(fresh_cache):
    calls = []
    def fetch():
        calls.append(1)
        return [run(SHARES, utc(2026, 10, 9, 22, 51))]
    # call 1: no cache -> fetch, stamp 1000; call 2: age 100 s -> cached;
    # call 3: age 400 s -> refetch, stamp 1400
    ticks = iter([1000.0, 1100.0, 1400.0, 1400.0])
    clock = lambda: next(ticks)
    now = utc(2026, 10, 10, 8, 0)
    jobs.check(now=now, fetch=fetch, clock=clock)
    jobs.check(now=now, fetch=fetch, clock=clock)
    assert len(calls) == 1
    jobs.check(now=now, fetch=fetch, clock=clock)
    assert len(calls) == 2


def test_fetch_runs_verifies_https_with_a_real_certificate_list(monkeypatch):
    """python.org Python on macOS ships with no CA list (cafile None), so a bare
    urlopen fails CERTIFICATE_VERIFY_FAILED and every job reads "unknown"
    forever. fetch_runs must pass an SSL context that can verify GitHub."""
    import io
    import json as _json
    import ssl
    seen = {}

    def fake_urlopen(req, timeout=None, context=None):
        seen["context"] = context
        return io.BytesIO(_json.dumps({"workflow_runs": []}).encode())
    monkeypatch.setattr(jobs.urllib.request, "urlopen", fake_urlopen)
    assert jobs.fetch_runs() == []
    assert isinstance(seen["context"], ssl.SSLContext)
    assert seen["context"].verify_mode == ssl.CERT_REQUIRED
    assert seen["context"].cert_store_stats()["x509_ca"] > 0


def test_truncated_github_response_is_unknown_not_a_crash(fresh_cache):
    """IncompleteRead/BadStatusLine are HTTPException, not OSError: unhandled,
    they turned /api/home into a 500 and blanked the whole screen."""
    import http.client
    def truncated():
        raise http.client.IncompleteRead(b"partial")
    out = jobs.check(now=utc(2026, 10, 10, 8, 0), fetch=truncated)
    assert {j["state"] for j in out} == {"unknown"}


def test_a_run_stuck_in_the_queue_does_not_mask_a_missed_job():
    """Only a run actually in progress counts as running; a queued run that
    never starts must not hide Monday's missed slot forever."""
    runs = [run(SHARES, utc(2026, 10, 9, 22, 51)),
            run(SHARES, utc(2026, 10, 12, 21, 40), conclusion=None, status="queued")]
    assert states(runs, utc(2026, 10, 13, 6, 30))[SHARES] == "missed"
