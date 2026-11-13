"""All eight real books on the frozen 13 Oct snapshot: the facts the review
established, plus every word pinned (AC-6). Regenerate the golden only on
purpose: UPDATE_GOLDEN=1 pytest tests/test_book_summary_golden.py — then read
the diff before committing."""
import json
import re
import os
from pathlib import Path

import pytest

from book_fixtures import NOW, no_network, use_snapshot
from trading_algo.dashboard import book_inputs, book_rules as R, book_summary as S, registry

GOLDEN = Path(__file__).parent / "fixtures" / "book_summaries_20261013.json"


@pytest.fixture()
def summaries(tmp_path, monkeypatch):
    use_snapshot(tmp_path, monkeypatch)
    no_network(monkeypatch)
    out = {}
    for e in registry.discover_accounts():
        market = "offline" if e["kind"] == "equity" else "n/a"
        out[e["key"]] = S.summarise(book_inputs.load_inputs(e, NOW, market=market))
    return out


def _ids(s, col):
    return [i["id"] for i in s["panel"][col]]


def test_the_facts_the_review_established(summaries):
    full = summaries["FULL"]
    assert full["status"]["level"] == "clear"
    assert full["main_problem"]["id"] == "costs-flip" and full["main_problem"]["backlog"] == "F25"
    assert "mostly-idle" in _ids(full, "wrong")
    assert summaries["ULTRA"]["main_problem"]["id"] == "no-breaker"
    exp = summaries["EXPERIMENTAL"]
    assert "mostly-idle" not in _ids(exp, "wrong") and "isn't compared" in exp["result"]["text"]
    small = summaries["SMALL"]
    assert small["main_problem"]["id"] == "mostly-idle"
    assert "not this book's settings" in next(i for i in small["panel"]["model"] if i["id"] == "backtest")["caveat"]
    for key in ("MATT", "PARTNER", "MULTI"):
        assert summaries[key]["main_problem"]["id"] == "holds-too-short", key
    matt_luck = next(i for i in summaries["MATT"]["panel"]["model"] if i["id"] == "luck-test")
    assert matt_luck["backlog"] == "F22" and "none of the search's finds" in matt_luck["caveat"]
    assert "luck-test" not in _ids(summaries["PARTNER"], "model")
    assert summaries["DAY"]["main_problem"]["id"] == "costs-flip"


def test_every_audit_warning_or_error_appears(summaries):
    # the `summaries` fixture has already pointed every reader at the snapshot
    for e in registry.discover_accounts():
        inp = book_inputs.load_inputs(e, NOW)
        s = summaries[e["key"]]
        shown = set(_ids(s, "wrong")) | {i["id"] for i in s["attention"]}
        for f in inp.findings:
            if f.level in ("WARN", "ERROR"):
                assert (R.COVERED_BY.get(f.code) in shown or "audit-warn" in shown
                        or "audit-error" in shown), (e["key"], f.code)


def test_backlog_ids_exist_and_are_open():
    backlog = json.loads((Path(__file__).parents[1] / "product/backlog/backlog.json").read_text())
    status = {i["id"]: i["status"] for i in backlog["items"]}
    for fid in ("F20", "F21", "F22", "F23", "F25"):
        assert status[fid] not in ("done", "rejected"), fid


def test_pinned_summaries(summaries):
    text = json.dumps(summaries, indent=1, sort_keys=True, ensure_ascii=False)
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.write_text(text + "\n")
    assert GOLDEN.exists(), "run once with UPDATE_GOLDEN=1, read it, then commit"
    assert json.loads(GOLDEN.read_text()) == json.loads(text)


def test_attention_is_glossed_first_because_it_renders_first(summaries):
    ultra = summaries["ULTRA"]
    top = next(i for i in ultra["attention"] if i["id"] == "no-breaker")
    assert "(a rule that pauses trading" in top["text"]


def test_no_nested_or_doubled_brackets_and_no_ugly_numbers(summaries):
    blob = json.dumps(summaries, ensure_ascii=False)
    for bad in ("((", "))", ") (", "−0.0%", "+0.0%"):
        assert bad not in blob, bad
    assert not re.search(r"(?<!\d)[01] days", blob)
