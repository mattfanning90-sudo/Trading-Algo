import json

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
    monkeypatch.setattr(metrics, "monthly_returns", lambda e: (calls.__setitem__("m", calls["m"] + 1), real_m(e))[1])
    monkeypatch.setattr(metrics, "drawdown", lambda e: (calls.__setitem__("d", calls["d"] + 1), real_d(e))[1])
    s = S.summarise(make_inp(card={"off_peak": -0.0112}))
    tile = next(t for t in s["tiles"] if t["label"] == "Below its best")
    assert tile["value"] == "−1.1%" and calls == {"m": 1, "d": 1}


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
