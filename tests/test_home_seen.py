"""'New' means first observed in this app session; losing the file never
floods the screen with amber."""
import json
from datetime import datetime, timedelta, timezone

from trading_algo.home import seen

T0 = datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc)


def f(code, level="WARN", subject=None, message="m", book="FULL"):
    return {"book": book, "code": code, "level": level, "subject": subject,
            "message": message}


def session(path, now, findings):
    previous = seen.begin_session(path, now)
    return seen.classify(findings, path=path, session_start=now.isoformat(),
                         first_run=previous is None, now=now)


def news(out):
    return {x["code"] for x in out if x["new"]}


def test_first_run_records_everything_as_known(tmp_path):
    out = session(tmp_path / "seen.json", T0, [f("idle-sleeve", subject="ASX")])
    assert news(out) == set()


def test_new_finding_is_new_in_its_session_and_known_after(tmp_path):
    p = tmp_path / "seen.json"
    session(p, T0, [f("idle-sleeve", subject="ASX")])
    s2 = T0 + timedelta(days=1)
    out = session(p, s2, [f("idle-sleeve", subject="ASX"), f("cash-drift", subject="US")])
    assert news(out) == {"cash-drift"}
    # a second refresh in the same session: still new
    again = seen.classify([f("cash-drift", subject="US")], path=p,
                          session_start=s2.isoformat(), first_run=False,
                          now=s2 + timedelta(minutes=5))
    assert news(again) == {"cash-drift"}
    # next session: known
    out3 = session(p, s2 + timedelta(days=1), [f("cash-drift", subject="US")])
    assert news(out3) == set()


def test_changed_wording_stays_known(tmp_path):
    p = tmp_path / "seen.json"
    session(p, T0, [f("idle-sleeve", subject="ASX", message="in cash 77 days")])
    out = session(p, T0 + timedelta(days=1),
                  [f("idle-sleeve", subject="ASX", message="in cash 78 days")])
    assert news(out) == set()


def test_same_code_other_subject_is_new(tmp_path):
    p = tmp_path / "seen.json"
    session(p, T0, [f("idle-sleeve", subject="ASX")])
    out = session(p, T0 + timedelta(days=1), [f("idle-sleeve", subject="FTSE")])
    assert news(out) == {"idle-sleeve"}


def test_escalation_makes_a_known_finding_new(tmp_path):
    p = tmp_path / "seen.json"
    session(p, T0, [f("never-traded", level="WARN", subject="ASX")])
    out = session(p, T0 + timedelta(days=1),
                  [f("never-traded", level="ERROR", subject="ASX")])
    assert news(out) == {"never-traded"}


def test_info_is_never_new(tmp_path):
    p = tmp_path / "seen.json"
    session(p, T0, [])
    out = session(p, T0 + timedelta(days=1), [f("turnover", level="INFO")])
    assert news(out) == set()


def test_unreadable_file_is_set_aside_and_floods_nothing(tmp_path):
    p = tmp_path / "seen.json"
    session(p, T0, [f("idle-sleeve", subject="ASX")])
    p.write_text("{not json")
    out = session(p, T0 + timedelta(days=1), [f("cash-drift", subject="US")])
    assert news(out) == set()
    assert (tmp_path / "seen.json.unreadable").exists()
    assert json.loads(p.read_text())["v"] == 1
