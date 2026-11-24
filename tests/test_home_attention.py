"""The one verdict: act now / look soon / clear — quiet unless broken or new."""
from trading_algo.dashboard import home


def book(key="FULL", halted=False, near=False, off_peak=-0.01, breaker=0.25):
    return {"key": key, "name": key, "halted": halted, "near_breaker": near,
            "off_peak": off_peak, "breaker": breaker}


def finding(level, new=False, book="FULL", message="m"):
    return {"book": book, "code": "c", "level": level, "subject": None,
            "message": message, "new": new}


def job(state, summary="s"):
    return {"name": "n", "label": "l", "state": state, "summary": summary,
            "url": "https://example/run"}


def verdict(books=(), findings=(), jobs=()):
    return home.attention(list(books), list(findings), list(jobs))


def test_quiet_day_is_clear():
    out = verdict([book()], [finding("WARN"), finding("INFO")], [job("ok")])
    assert out["verdict"] == "clear"
    assert len(out["known"]) == 1 and len(out["notes"]) == 1 and out["items"] == []


def test_halted_book_is_act_now():
    assert verdict([book(halted=True)])["verdict"] == "act"


def test_known_error_is_still_act_now():
    assert verdict([book()], [finding("ERROR", new=False)])["verdict"] == "act"


def test_new_warning_is_look_soon():
    assert verdict([book()], [finding("WARN", new=True)])["verdict"] == "look"


def test_near_breaker_is_look_soon():
    out = verdict([book(near=True, off_peak=-0.16)])
    assert out["verdict"] == "look"
    assert "16.0%" in out["items"][0]["text"] and "25%" in out["items"][0]["text"]


def test_failed_or_missed_job_is_act_now_with_a_link():
    out = verdict([book()], [], [job("missed", "Shares (daily) didn't run")])
    assert out["verdict"] == "act"
    assert out["items"][0]["url"] == "https://example/run"


def test_unknown_jobs_are_flagged_not_green_and_not_alarming():
    out = verdict([book()], [], [job("unknown")])
    assert out["verdict"] == "clear" and out["jobs_unknown"] is True


def test_no_books_and_no_findings_is_nodata():
    assert verdict([], [], [job("ok")])["verdict"] == "nodata"


def test_a_book_behind_its_schedule_is_look_soon():
    """A book 3+ trading days without a new value must not hide behind "All
    clear" (found live 2026-10-10: EXPERIMENTAL last updated Tue 6 Oct)."""
    stale = {**book("EXPERIMENTAL"), "freshness": "stale", "as_of": "2026-10-06T00:00:00+00:00"}
    out = verdict([stale])
    assert out["verdict"] == "look"
    assert "EXPERIMENTAL" in out["items"][0]["text"] and "2026-10-06" in out["items"][0]["text"]


def test_a_book_one_session_late_stays_quiet():
    late = {**book("SMALL"), "freshness": "late", "as_of": "2026-10-07T00:00:00+00:00"}
    assert verdict([late])["verdict"] == "clear"
