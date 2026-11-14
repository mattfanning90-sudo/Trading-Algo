import json
from datetime import datetime, timedelta, timezone

import pytest

from book_fixtures import NOW, make_inp, no_network, use_snapshot
from trading_algo import metrics
from trading_algo.dashboard import book_inputs, book_rules as R, book_summary as S, glossary, registry
from trading_algo.verify import WARN, Finding

EQ_COSTS = {"total": 449.17, "by_ccy": {"GBP": 396.16}, "stamp": {"GBP": 292.51},
            "includes": "commission and stamp duty"}


def test_halted_outranks_everything_and_leads_the_summary():
    s = S.summarise(make_inp(card={"halted": True, "equity": 99_952.38}, costs=EQ_COSTS))
    assert s["main_problem"]["id"] == "halted" and s["status"]["level"] == "act"
    assert s["summary"][1]["text"].startswith("Main problem: Paused by its safety breaker")


def test_summary_order_and_never_more_good_news_than_problems():
    good = [{"symbol": "A", "opened": "2026-07-01", "closed": "2026-08-01", "side": "LONG",
             "net": 50.0, "lots": 1}]
    with_problem = S.summarise(make_inp(card={"equity": 99_952.38}, costs=EQ_COSTS,
                                        positions_closed=good))
    kinds = [x["item_id"].split("#")[0] for x in with_problem["summary"]]
    assert kinds == ["result", "costs-flip", "winners-pay", "backtest"]
    no_problem = S.summarise(make_inp(positions_closed=good))
    kinds = [x["item_id"].split("#")[0] for x in no_problem["summary"]]
    assert kinds == ["result", "no-problem", "backtest"] and "winners-pay" not in kinds


def test_every_summary_sentence_maps_to_a_shown_item_and_carries_its_caveat():
    s = S.summarise(make_inp(card={"equity": 99_952.38}, costs=EQ_COSTS))
    shown = {s["result"]["uid"]: s["result"], s["main_problem"]["uid"]: s["main_problem"]}
    for col in s["panel"].values():
        shown.update({i["uid"]: i for i in col})
    for sent in s["summary"]:
        assert sent["item_id"] in shown
        cav = shown[sent["item_id"]].get("caveat")
        if cav:
            assert cav.split(". ")[0].rstrip(".") in sent["text"]


def test_uids_are_unique_when_a_rule_fires_twice():
    fs = [Finding(WARN, "t", "cash-drift", "…", {"subject": "US"}),
          Finding(WARN, "t", "cash-drift", "…", {"subject": "FTSE"})]
    s = S.summarise(make_inp(findings=fs))
    uids = [i["uid"] for i in s["panel"]["wrong"]]
    assert len(uids) == len(set(uids)) == 2


def test_a_rule_that_throws_becomes_one_couldnt_measure_line(monkeypatch):
    monkeypatch.setattr(R, "going_right", lambda inp: 1 / 0)
    s = S.summarise(make_inp(card={"equity": 99_952.38}, costs=EQ_COSTS))
    assert [i["id"] for i in s["panel"]["right"]] == ["couldnt-measure"]
    assert s["main_problem"]["id"] == "costs-flip" and "going right" in s["errors"][0]


def test_glossary_explains_each_term_once_on_the_screen():
    s = S.summarise(make_inp(card={"equity": 99_952.38}, costs=EQ_COSTS,
                             backtest={"available": True, "synthetic": False, "start": "2012",
                                       "end": "2026", "generated_at": "2026-10-10T00:00:00Z",
                                       "metrics": {"cagr": 0.06, "sharpe": 0.3}}))
    blob = json.dumps(s, ensure_ascii=False)
    for term, _, g in glossary.TERMS:
        assert blob.count(f"({g})") <= 1, term
    assert blob.count("(a replay of the strategy on past prices, costs included)") == 1


def test_six_tiles_and_fx_without_closed_positions():
    assert len(S.summarise(make_inp())["tiles"]) == 6
    fx = S.summarise(make_inp("fx", fx_book={"net_pnl": 0.0, "residual": 0.0, "costs": 0.0,
                                             "turnover": 0.0}))
    win = next(t for t in fx["tiles"] if t["label"] == "Win rate")
    assert win["value"] == "—" and "No closed positions yet" in win["caption"]


def test_one_valuation_point_across_result_tiles_and_chart():
    s = S.summarise(make_inp(card={"equity": 99_952.38}))
    since = next(t for t in s["tiles"] if t["label"] == "Since start")
    assert since["value"] == "▼ −A$47.62" and "A$47.62" in s["result"]["text"]
    assert s["chart"]["value"][-1][1] == pytest.approx(-47.62)


def test_tile_below_best_is_the_breaker_figure_and_months_come_from_metrics(monkeypatch):
    calls = {"m": 0, "d": 0}
    real_m, real_d = metrics.monthly_returns, metrics.drawdown
    monkeypatch.setattr(metrics, "monthly_returns", lambda e, **kw: (calls.__setitem__("m", calls["m"] + 1), real_m(e, **kw))[1])
    monkeypatch.setattr(metrics, "drawdown", lambda e: (calls.__setitem__("d", calls["d"] + 1), real_d(e))[1])
    s = S.summarise(make_inp(card={"off_peak": -0.0112}))
    tile = next(t for t in s["tiles"] if t["label"] == "Below its best")
    assert tile["value"] == "−1.1%" and calls == {"m": 1, "d": 1}


def test_months_compound_to_the_since_start_return_on_the_real_book(tmp_path, monkeypatch):
    """FULL's first day (capital put in -> first mark) was dropped: its months
    compounded to +0.12% while Since start read -0.05%."""
    use_snapshot(tmp_path, monkeypatch)
    no_network(monkeypatch)
    seen, real = [], metrics.monthly_returns
    monkeypatch.setattr(metrics, "monthly_returns",
                        lambda e, **kw: (seen.append(real(e, **kw)), seen[-1])[1])
    inp = book_inputs.load_inputs(registry.resolve("FULL"), NOW, market="offline")
    S.summarise(inp)
    since = inp.card["equity"] / inp.card["initial"] - 1
    assert float((1 + seen[0]).prod() - 1) == pytest.approx(since, abs=1e-6)


def test_summary_survives_an_unvaluable_mark(tmp_path, monkeypatch):
    dst = use_snapshot(tmp_path, monkeypatch)
    no_network(monkeypatch)
    p = dst / "paper_state_full.json"
    state = json.loads(p.read_text())
    state["equity_history"].insert(-1, ["2026-10-10", None])
    p.write_text(json.dumps(state))
    s = S.summarise(book_inputs.load_inputs(registry.resolve("FULL"), NOW))
    json.dumps(s, allow_nan=False)                      # valid JSON, no NaN
    assert all(v is not None for _, v in s["chart"]["value"])


def _marks(values, start=datetime(2026, 6, 1, tzinfo=timezone.utc)):
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


def test_a_failed_check_never_reads_as_all_clear(monkeypatch):
    monkeypatch.setattr(R, "holding_back", lambda inp: 1 / 0)
    s = S.summarise(make_inp())
    assert s["main_problem"]["text"].startswith("Couldn't check")
    assert not any("No standout problem" in x["text"] for x in s["summary"])
    assert s["summary"][1]["text"].startswith("Main problem: Couldn't check")


def test_inputs_that_fail_to_load_never_read_as_all_clear(tmp_path, monkeypatch):
    """The audit and the FX money layer both throw: the screen must say so in
    the pill, the main problem and the tiles, never a clean zero."""
    use_snapshot(tmp_path, monkeypatch)
    no_network(monkeypatch)

    def boom(*_a, **_k):
        raise RuntimeError("input failed")

    monkeypatch.setattr(book_inputs.verify, "verify_book", boom)
    monkeypatch.setattr(book_inputs.fx_pnl, "book_pnl", boom)
    s = S.summarise(book_inputs.load_inputs(registry.resolve("MATT"), NOW))
    assert s["status"] == {"level": "look", "shape": "\u25b2", "word": "Couldn't run its audit"}
    assert s["main_problem"]["text"].startswith("Couldn't check")
    assert not any("No standout problem" in x["text"] for x in s["summary"])
    tiles = {t["label"]: t for t in s["tiles"]}
    for label in ("Costs so far", "Win rate", "Turnover"):
        assert tiles[label]["value"] == "\u2014", label
        assert tiles[label]["caption"].startswith("couldn't measure"), label


def test_chart_text_reconciles_the_daily_drop_with_the_breakers_own_figure():
    vals = [100_000.0, 102_000.0, 99_000.0]
    s = S.summarise(make_inp(card={"equity": 99_000.0, "off_peak": -0.0512},
                             book={"_marks": _marks(vals), "as_of": _marks(vals)[-1][0].isoformat()}))
    d = s["chart"]["below_desc"]
    assert "Fall from the highest daily value" in d
    assert "The safety breaker measures from its own recorded peak: −5.1% now." in d


def test_chart_text_on_a_book_without_a_breaker_never_cites_one():
    """ULTRA has no safety breaker (its tile says so); the chart text must not
    say the breaker measures anything."""
    vals = [100_000.0, 102_000.0, 99_000.0]
    s = S.summarise(make_inp(card={"equity": 99_000.0, "off_peak": -0.0512, "breaker": None},
                             state={"param_overrides": {"max_gross": 3.0}},
                             book={"_marks": _marks(vals), "as_of": _marks(vals)[-1][0].isoformat()}))
    d = s["chart"]["below_desc"]
    assert "safety breaker measures" not in d
    assert d.endswith("The Below its best tile measures from the book's own recorded peak: "
                      "\u22125.1% now; this book has no safety breaker.")


def test_a_nan_in_the_benchmark_cannot_make_invalid_json():
    vals = [100_000.0 + i * 10 for i in range(30)]
    bench = [{"date": (datetime(2026, 6, 1) + timedelta(days=i)).strftime("%Y-%m-%d"),
              "value": 100_000.0 + i * 5} for i in range(30)]
    bench[10]["value"] = float("nan")
    inp = make_inp(card={"equity": vals[-1]},
                   book={"_marks": _marks(vals), "as_of": _marks(vals)[-1][0].isoformat()},
                   live={"benchmark_curve": bench}, market="ready")
    json.dumps(S.summarise(inp), allow_nan=False)
    assert R.market(inp)["too_short"] is False


def test_closed_rows_say_one_day_and_same_day():
    rows = [{"symbol": "A", "opened": "2026-08-01", "closed": "2026-08-02", "side": "LONG", "net": 5.0, "lots": 1},
            {"symbol": "B", "opened": "2026-08-03", "closed": "2026-08-03", "side": "LONG", "net": 5.0, "lots": 1},
            {"symbol": "C", "opened": "2026-08-01", "closed": "2026-08-05", "side": "LONG", "net": 5.0, "lots": 1}]
    held = {r["symbol"]: r["held"] for r in S.summarise(make_inp(positions_closed=rows))["closed"]}
    assert held == {"A": "1 day", "B": "the same day", "C": "4 days"}


def test_a_tiny_month_reads_zero_and_flat():
    vals = [100_000.0, 100_000.01]
    s = S.summarise(make_inp(card={"equity": vals[-1]},
                             book={"_marks": _marks(vals), "as_of": _marks(vals)[-1][0].isoformat()}))
    assert s["months"] and all(m["text"] == "0.0%" and m["dir"] == "flat" for m in s["months"])


def test_one_blocks_failure_leaves_the_rest_of_the_screen():
    bad = [{"symbol": "A", "opened": None, "closed": "2026-08-02", "side": "LONG", "net": 5.0, "lots": 1}]
    s = S.summarise(make_inp(positions_closed=bad))
    assert s["closed"] == [] and "the last closed positions" in s["errors"] and len(s["tiles"]) == 6


def test_a_young_ready_market_is_too_short_not_missing():
    vals = [100_000.0, 100_010.0]
    bench = [{"date": "2026-06-01", "value": 100_000.0}, {"date": "2026-06-02", "value": 100_005.0}]
    inp = make_inp(card={"equity": vals[-1]},
                   book={"_marks": _marks(vals), "as_of": _marks(vals)[-1][0].isoformat()},
                   live={"benchmark_curve": bench}, market="ready")
    assert R.market(inp) == {"too_short": True, "days": 2}
    vs = next(t for t in S.summarise(inp)["tiles"] if t["label"] == "vs the market")
    assert "too early" in vs["caption"]


def test_summarise_is_pure(monkeypatch):
    import builtins
    import copy
    import socket
    import time
    from datetime import datetime as real_dt

    inp = make_inp(card={"equity": 99_952.38}, costs=EQ_COSTS)
    S.summarise(copy.deepcopy(inp))          # warm any lazy imports
    before = copy.deepcopy({k: v for k, v in vars(inp).items() if k != "errors"})

    def boom(*a, **k):
        raise AssertionError("I/O or clock read during summarise")

    class NoClock(real_dt):
        now = utcnow = today = classmethod(lambda cls, *a, **k: boom())

    import trading_algo.dashboard.book_rules as brm
    import trading_algo.dashboard.book_summary as bsm
    monkeypatch.setattr(builtins, "open", boom)
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(time, "time", boom)
    monkeypatch.setattr(bsm, "datetime", NoClock)
    monkeypatch.setattr(brm, "datetime", NoClock)
    S.summarise(inp)
    monkeypatch.undo()
    assert {k: v for k, v in vars(inp).items() if k != "errors"} == before


def test_all_chip_and_since_start_tile_equal_the_overview_card_on_the_real_book(tmp_path, monkeypatch):
    use_snapshot(tmp_path, monkeypatch)
    no_network(monkeypatch)
    inp = book_inputs.load_inputs(registry.resolve("FULL"), NOW, market="offline")
    chip = inp.book["periods"]["ALL"]["change"]
    assert chip == pytest.approx(inp.card["equity"] - inp.card["initial"], abs=0.01)
    since = next(t for t in S.summarise(inp)["tiles"] if t["label"] == "Since start")
    amount = float(since["value"].replace("\u2212", "-").replace(",", "").split("A$")[1]) * (-1 if "\u2212" in since["value"] else 1)
    assert amount == pytest.approx(chip, abs=0.01)


def test_market_gap_is_measured_to_the_results_as_of_date():
    from test_book_rules_attention import _with_market
    inp = _with_market([100_000.0] * 60, [100_000.0 * (1.001 ** i) for i in range(60)])
    assert R.market(inp)["last"] == R.as_of(inp)
