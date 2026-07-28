"""The terminal's SWARM tab: payload, route and export baking.

The bug this pins: the swarm was bred, logged and gated for months while the
terminal — the landing page — had no SWARM tab at all, and the one screen that
did show it rendered an empty roster with no hint that the gate had rejected
everything on purpose. So the assertions here are as much about the VERDICT
being reported as about the numbers being present.
"""
import json

import pytest

from trading_algo.dashboard import export, swarm
from trading_algo.forex import champions, evolve, fx_book, swarm_view
from trading_algo.forex.fx_config import profile
from trading_algo.forex.fx_data import synthetic_panel
from trading_algo.forex.pairs import DEFAULT_UNIVERSE

STATIC = "trading_algo/dashboard/static/app.js"


@pytest.fixture
def bred(tmp_path, monkeypatch):
    """A real (tiny) bred swarm for account 'matt' in an isolated state dir."""
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(evolve, "STATE_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(champions, "STATE_DIR", str(tmp_path), raising=False)
    fx_book.init_account("matt", 5_000, "balanced")
    panel = synthetic_panel(DEFAULT_UNIVERSE[:4], start="2016-01-01", end="2023-01-01")
    log, _, final = evolve.breed(panel, profile("balanced"), generations=2,
                                 pop_size=8, seed=1)
    evolve.write_log("matt", log)
    return tmp_path, log, final


def test_no_log_reports_nothing_bred(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(evolve, "STATE_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(champions, "STATE_DIR", str(tmp_path), raising=False)
    s = swarm_view.summary("ghost")
    assert s["available"] is False
    assert s["gate"]["code"] == swarm_view.NO_LOG
    assert s["gate"]["reason"]


def test_bred_but_ungated_is_available_and_says_so(bred):
    """A bred swarm with no champions file is NOT 'unavailable' — the whole
    point is to show the population even before the gate has judged it."""
    s = swarm_view.summary("matt")
    assert s["available"] is True
    assert s["n_trials"] > 0 and len(s["generations"]) == 2
    assert s["gate"]["code"] == swarm_view.GATE_NOT_RUN
    assert s["roster"] == [] and s["gate"]["promoted"] == 0
    assert s["top_finalists"] and s["archetypes"]
    assert s["lineage"]["nodes"] and s["lineage"]["total_nodes"] == s["n_trials"]


def test_empty_roster_under_the_pbo_ceiling_reads_as_none_cleared(bred):
    """The live books' actual state: gate ran, PBO fine, nobody cleared DSR."""
    champions.save_roster("matt", [], meta={"pbo": 0.38, "n_trials": 424, "dsr": {}})
    gate = swarm_view.summary("matt")["gate"]
    assert gate["code"] == swarm_view.NONE_CLEARED
    assert gate["pbo"] == 0.38 and gate["passed"] == 0
    assert str(champions.DSR_MIN) in gate["reason"] or "0.95" in gate["reason"]


def test_empty_roster_over_the_pbo_ceiling_blames_the_cohort(bred):
    """multiasset's actual state — a different verdict, and it must not be
    reported with the DSR sentence, which was not the binding constraint."""
    champions.save_roster("matt", [], meta={"pbo": 0.66, "n_trials": 424, "dsr": {}})
    gate = swarm_view.summary("matt")["gate"]
    assert gate["code"] == swarm_view.COHORT_OVERFIT
    assert "overfit" in gate["reason"].lower()


def test_promoted_roster_marks_its_genomes_alive_in_the_lineage(bred):
    _tmp, _log, final = bred
    promoted = [g for g, _ in final[:2]]
    champions.save_roster("matt", promoted,
                          meta={"pbo": 0.3, "n_trials": 99,
                                "dsr": {g.gid: 0.97 for g in promoted}})
    s = swarm_view.summary("matt")
    assert s["gate"]["code"] == swarm_view.PROMOTED
    assert s["gate"]["promoted"] == 2 and s["gate"]["passed"] == 2
    assert [r["dsr"] for r in s["roster"]] == [0.97, 0.97]
    alive = {n["gid"] for n in s["lineage"]["nodes"] if n["alive"]}
    assert alive == {g.gid for g in promoted}
    assert any(f["promoted"] for f in s["top_finalists"])


def test_lineage_trim_keeps_every_live_champion(bred, monkeypatch):
    """Over the node cap the trim ranks by fitness — a champion must never be
    dropped, or the roster would point at a genome the picture denies exists."""
    _tmp, _log, final = bred
    worst = final[-1][0]                       # deliberately a low-fitness genome
    champions.save_roster("matt", [worst],
                          meta={"pbo": 0.3, "n_trials": 9, "dsr": {worst.gid: 0.96}})
    monkeypatch.setattr(swarm_view, "MAX_LINEAGE_NODES", 3)
    lin = swarm_view.summary("matt")["lineage"]
    assert lin["truncated"] is True and len(lin["nodes"]) == 3
    assert worst.gid in {n["gid"] for n in lin["nodes"] if n["alive"]}
    kept = {n["gid"] for n in lin["nodes"]}
    assert all(p in kept and c in kept for p, c in lin["edges"])


def test_champions_live_is_false_when_only_core_agents_voted(bred):
    """A roster on disk is not evidence it is trading: the engine's champion
    pool is opt-in. The persisted decision book is the evidence."""
    state = fx_book.load_state("matt")
    state["decisions"] = {"EURUSD": {"agents": {"trend": 0.5, "carry": 0.1}}}
    fx_book.save_state("matt", state)
    assert swarm_view.summary("matt")["champions_live"] is False

    state["decisions"] = {"EURUSD": {"agents": {"trend": 0.5, "champ:abc123": -1.0}}}
    fx_book.save_state("matt", state)
    assert swarm_view.summary("matt")["champions_live"] is True


def test_equity_books_get_an_explicit_not_applicable(bred):
    s = swarm.build_swarm("equity", "full")
    assert s["applicable"] is False and s["available"] is False
    assert s["gate"]["code"] == "not_applicable" and s["gate"]["reason"]
    assert swarm.build_swarm("fx", "matt")["applicable"] is True


def test_gate_thresholds_come_from_champions_not_a_copy(bred):
    """The screen reports the bars the gate actually applies."""
    gate = swarm_view.summary("matt")["gate"]
    assert gate["dsr_min"] == champions.DSR_MIN
    assert gate["pbo_max"] == champions.PBO_MAX


def test_payload_is_json_serialisable(bred):
    """It is baked into a static export — a stray numpy float would kill the
    whole page, not just this tab."""
    json.dumps(swarm.build_swarm("fx", "matt"))


def test_export_bakes_the_swarm_route(bred):
    payloads, key = export.build_payloads("matt", synthetic=True)
    assert f"/api/swarm/{key}" in payloads, "SWARM tab would 404 in the static export"
    assert payloads[f"/api/swarm/{key}"]["available"] is True


def test_frontend_wires_the_tab():
    """The tab, its loader and its route must all exist in the SPA — the
    original bug was a fully-working payload with no way to reach it."""
    js = open(STATIC, encoding="utf-8").read()
    assert "tabKeys.push('SWARM')" in js          # the tab appears for FX books
    assert "function swarmHTML(" in js            # the screen exists
    assert "'/api/swarm/' + key" in js            # it is fetched
    assert "if (S.tab === 'SWARM') return swarmHTML(page)" in js   # and dispatched
