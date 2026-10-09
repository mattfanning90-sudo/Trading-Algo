"""/api/home: every number traced to an existing source, period maths
hand-checked, and the failure cases a person would hit."""
import json
from datetime import datetime, timezone

import pytest

import trading_algo.paper_trade as pt
from trading_algo.dashboard import home, overview
from trading_algo.forex import fx_book

NOW = datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc)          # a Saturday
SESSION = NOW.isoformat()

DAY_STATE = {
    "account": "daytrader", "currency": "AUD", "profile": "intraday", "bar": "60m",
    "source": "yahoo", "symbols": ["EURUSD"], "initial_capital": 10_000.0,
    "equity": 9_930.0, "peak_equity": 10_100.0, "positions": {}, "last_close": {},
    "last_bar_date": "2026-10-09 02:00", "risk_halted": False, "halt_cooldown": 0,
    "trades": [], "decisions": {},
    "equity_history": [["2026-10-08 15:00", 9_900.0], ["2026-10-08 21:00", 9_950.0],
                       ["2026-10-09 02:00", 9_930.0]],
}


@pytest.fixture()
def books(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    monkeypatch.setenv("MOMENTUM_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("FX_STATE_DIR", str(tmp_path))
    pt.init_account("full", capital=100_000, synthetic=True)
    pt.run_daily("full", synthetic=True)          # a real, evaluated book
    path = tmp_path / "paper_state_full.json"
    state = json.loads(path.read_text())
    state["equity_history"] = [["2026-09-01", 100_000.0], ["2026-09-25", 101_000.0],
                               ["2026-10-01", 102_000.0], ["2026-10-08", 101_500.0],
                               ["2026-10-09", 102_500.0]]
    state["peak_equity_base"] = 103_000.0
    path.write_text(json.dumps(state))
    (tmp_path / "fx_state_daytrader.json").write_text(json.dumps(DAY_STATE))
    return tmp_path


def build(tmp_path, jobs=()):
    return home.build_home(jobs=list(jobs), seen_path=tmp_path / "seen.json",
                           session_start=SESSION, first_run=True, last_opened=None,
                           now=NOW, snapshot={"sha": "abc", "offline": False})


def test_group_totals_equal_overview(books):
    out = build(books)
    ov = overview.build_overview()
    assert [(g["name"], g["aum"]) for g in out["groups"]] == \
           [(g["name"], g["aum"]) for g in ov["groups"]]
    # Independent cross-check: the home screen's own period sum ("since you
    # started") must equal what overview reports as worth minus put in.
    core = next(g for g in out["groups"] if g["name"] == "CORE")
    ov_core = next(g for g in ov["groups"] if g["name"] == "CORE")
    assert core["periods"]["ALL"]["change"] == round(ov_core["aum"] - ov_core["initial"], 2)
    assert core["periods"]["ALL"]["change"] == 2430.0     # 2,500 - 70


def test_daily_book_period_changes(books):
    full = next(b for b in build(books)["books"] if b["key"] == "FULL")
    p = full["periods"]
    assert (p["1D"]["change"], p["1D"]["pct"]) == (1000.0, 0.009852)   # 102.5k vs 101.5k
    assert (p["1W"]["change"], p["1W"]["pct"]) == (500.0, 0.004902)    # vs 1 Oct
    assert (p["1M"]["change"], p["1M"]["pct"]) == (2500.0, 0.025)      # vs 1 Sep
    assert (p["ALL"]["change"], p["ALL"]["pct"]) == (2500.0, 0.025)    # vs capital


def test_hourly_book_1d_is_its_last_session(books):
    day = next(b for b in build(books)["books"] if b["key"] == "DAY")
    assert day["hourly"] is True
    assert (day["periods"]["1D"]["change"], day["periods"]["1D"]["pct"]) == (-20.0, -0.00201)
    assert day["periods"]["1W"] == {"measurable": False}               # history too short
    assert day["periods"]["ALL"]["change"] == -70.0


def test_group_period_counts_only_measured_books(books):
    core = next(g for g in build(books)["groups"] if g["name"] == "CORE")
    assert core["periods"]["1D"] == {"measurable": True, "change": 980.0,
                                     "pct": 0.008793, "measured": 2, "of": 2}
    one_week = core["periods"]["1W"]
    assert (one_week["change"], one_week["measured"], one_week["of"]) == (500.0, 1, 2)


def test_books_carry_as_of_and_freshness(books):
    by = {b["key"]: b for b in build(books)["books"]}
    assert by["FULL"]["as_of"].startswith("2026-10-09")
    assert by["FULL"]["freshness"] == "fresh" and by["DAY"]["freshness"] == "fresh"


def test_near_breaker_uses_the_breaker_figure(books):
    full = next(b for b in build(books)["books"] if b["key"] == "FULL")
    assert full["off_peak"] == round(102_500 / 103_000 - 1, 6)
    assert full["near_breaker"] is False


def test_damaged_book_file_is_act_now(books):
    before = build(books)
    assert not any("could not be parsed" in i["text"] for i in before["attention"])
    (books / "paper_state_broken.json").write_text("{not json")
    out = build(books)
    assert out["verdict"] == "act"
    assert any(i["level"] == "act" and "could not be parsed" in i["text"]
               for i in out["attention"])


def test_no_books_is_nodata_not_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    monkeypatch.setenv("MOMENTUM_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("FX_STATE_DIR", str(tmp_path))
    out = build(tmp_path)
    assert out["verdict"] == "nodata" and out["books"] == []


def test_unvaluable_marks_are_skipped():
    t = lambda d: datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
    marks = [(t("2026-10-07"), 100.0), (t("2026-10-09"), 110.0)]
    assert home.period_change(marks, 100.0, "1D", False)["change"] == 10.0
    assert home._marks({"equity_history": [["2026-10-08", None],
                                           ["2026-10-09", float("nan")],
                                           ["2026-10-10", 5.0]]})[-1][1] == 5.0
    assert len(home._marks({"equity_history": [["2026-10-08", None]]})) == 0


@pytest.mark.parametrize("as_of,now,hourly,expect", [
    ("2026-10-09 00:00", "2026-10-10 08:00", False, "fresh"),   # Fri mark, Sat
    ("2026-10-09 00:00", "2026-10-12 08:00", False, "fresh"),   # 1 weekday
    ("2026-10-09 00:00", "2026-10-13 08:00", False, "late"),    # 2 weekdays
    ("2026-10-09 00:00", "2026-10-14 08:00", False, "stale"),   # 3 weekdays
    ("2026-10-09 22:07", "2026-10-12 03:00", True, "fresh"),    # Monday before 1st run
    ("2026-10-09 22:07", "2026-10-12 15:00", True, "stale"),    # 15 h since midnight
    ("2026-10-09 22:00", "2026-10-10 12:00", True, "fresh"),    # weekend, Friday mark
    ("2026-10-08 22:00", "2026-10-10 12:00", True, "stale"),    # weekend, Thursday mark
])
def test_freshness(as_of, now, hourly, expect):
    t = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    assert home.freshness(t(as_of), t(now), hourly) == expect


def test_mark_dated_ahead_of_now_is_fresh():
    t = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    assert home.freshness(t("2026-10-13 00:00"), t("2026-10-12 22:00"), False) == "fresh"
