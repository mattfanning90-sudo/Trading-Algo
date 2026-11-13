from datetime import datetime, timedelta, timezone

import pytest

from book_fixtures import make_inp
from trading_algo.dashboard import book_rules as R
from trading_algo.verify import ERROR, Finding


def ids(items):
    return [i["id"] for i in items]


def test_halted_is_act_and_says_days_left():
    inp = make_inp(card={"halted": True}, state={"halt_cooldown": 4})
    out = R.attention(inp)
    assert out[0]["id"] == "halted" and out[0]["level"] == "act" and "4 trading days" in out[0]["text"]


def test_an_audit_error_is_shown_in_plain_words():
    f = Finding(ERROR, "t", "position-mismatch", "sleeve US: ledger replay …", {"subject": "US"})
    out = R.attention(make_inp(findings=[f]))
    assert ids(out) == ["audit-error"] and "US" in out[0]["text"] and "ledger replay" not in out[0]["text"]


def test_near_breaker_follows_the_home_screen_flag():
    near = R.attention(make_inp(book={"near_breaker": True}, card={"off_peak": -0.16}))
    assert ids(near) == ["near-breaker"] and near[0]["text"].startswith("16.0% below its best")
    assert R.attention(make_inp()) == []


@pytest.mark.parametrize("fresh,fires", [("fresh", False), ("late", True), ("stale", True)])
def test_late_or_stale_data_needs_attention(fresh, fires):
    assert ("stale" in ids(R.attention(make_inp(book={"freshness": fresh})))) is fires


def test_no_breaker_only_on_a_geared_book():
    geared = make_inp(card={"breaker": None}, state={"param_overrides": {"max_gross": 3.0}})
    plain = make_inp(card={"breaker": None})
    assert ids(R.attention(geared)) == ["no-breaker"] and R.attention(plain) == []


def test_a_brand_new_book_is_too_new_to_judge():
    inp = make_inp(book={"_marks": [(datetime(2026, 10, 13, tzinfo=timezone.utc), 1000.0)]})
    assert "too new to judge" in R.result(inp)["text"]


def test_result_reads_the_saved_value_with_its_date():
    r = R.result(make_inp(card={"equity": 99_952.38}, market="offline"))
    assert r["text"].startswith("Down A$47.62 (−0.05%) since 11 Jun 2026, as of 13 Oct 2026")
    assert r["dir"] == "down" and "offline" in r["text"]


def test_long_short_and_fx_books_are_not_compared_with_a_market():
    ls = make_inp(state={"param_overrides": {"long_short": True}}, market="ready", live={})
    assert "isn't compared" in R.result(ls)["text"]
    assert "isn't compared" in R.result(make_inp("fx"))["text"]


def _with_market(book_vals, bench_vals):
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    marks = [(start + timedelta(days=i), v) for i, v in enumerate(book_vals)]
    bench = [{"date": (start + timedelta(days=i)).strftime("%Y-%m-%d"), "value": v}
             for i, v in enumerate(bench_vals)]
    return make_inp(card={"equity": book_vals[-1], "initial": book_vals[0]},
                    book={"_marks": marks, "as_of": marks[-1][0].isoformat()},
                    live={"benchmark_curve": bench}, market="ready")


def test_a_steady_gap_is_beyond_normal_and_counts_as_behind():
    inp = _with_market([100_000.0] * 60, [100_000.0 * (1.001 ** i) for i in range(60)])
    m = R.market(inp)
    assert m["beyond"] and m["gap"] < 0
    assert "more than its normal" in R.result(inp)["text"]
    assert "dividends" in R.result(inp)["caveat"]


def test_a_small_gap_in_a_noisy_market_is_within_normal():
    bench = [100_000.0 * (1.01 if i % 2 else 0.99) for i in range(59)] + [100_500.0]
    inp = _with_market([100_000.0] * 60, bench)
    assert not R.market(inp)["beyond"] and "within its normal" in R.result(inp)["text"]
    assert R.going_right(inp) == []


def test_winners_pay_needs_a_positive_closed_result():
    pos = [{"symbol": "A", "opened": "2026-07-01", "closed": "2026-08-01", "side": "LONG", "net": n, "lots": 1}
           for n in (100.0, -40.0, 10.0)]
    out = R.going_right(make_inp(positions_closed=pos))
    assert ids(out) == ["winners-pay"] and "a profit factor of 2.8" in out[0]["text"]
    assert "small sample" in out[0]["caveat"].lower()
    lose = [dict(p, net=n) for p, n in zip(pos, (10.0, -40.0, 5.0))]
    assert R.going_right(make_inp(positions_closed=lose)) == []


def test_winners_pay_says_what_it_left_out():
    pos = [{"symbol": "A", "opened": "2026-07-01", "closed": "2026-08-01", "side": "LONG", "net": 5.0, "lots": 1}]
    out = R.going_right(make_inp(positions_closed=pos, excluded=16))
    assert "16" in out[0]["caveat"] and "since-fixed bug" in out[0]["caveat"]


def test_an_empty_date_never_crashes_the_rules():
    inp = make_inp(card={"since": ""})
    assert "an unknown date" in R.result(inp)["text"]
    pos = [{"symbol": "A", "opened": "2026-07-01", "closed": "2026-08-01", "side": "LONG", "net": 5.0, "lots": 1}]
    R.going_right(make_inp(card={"since": ""}, positions_closed=pos))


def test_no_market_verdict_before_a_month_of_values():
    inp = _with_market([100_000.0] * 10, [100_000.0 * (1.001 ** i) for i in range(10)])
    assert R.market(inp)["too_short"] is True
    assert "too early to compare with the market" in R.result(inp)["text"]
    assert R.result(inp)["caveat"] is None
    assert R.going_right(inp) == []
