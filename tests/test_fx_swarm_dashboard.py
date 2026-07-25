from trading_algo.forex import champions, dashboard, evolve, fx_book
from trading_algo.forex.fx_config import profile
from trading_algo.forex.fx_data import synthetic_panel
from trading_algo.forex.pairs import DEFAULT_UNIVERSE


def _seed_swarm(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(evolve, "STATE_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(champions, "STATE_DIR", str(tmp_path), raising=False)
    fx_book.init_account("matt", 5_000, "balanced")           # build_payload needs a real book
    panel = synthetic_panel(DEFAULT_UNIVERSE[:4], start="2016-01-01", end="2023-01-01")
    log, _, final = evolve.breed(panel, profile("balanced"), generations=2, pop_size=8, seed=1)
    evolve.write_log("matt", log)                             # breed() already set log.finalists
    champions.save_roster("matt", [g for g, _ in final[:2]],
                          meta={"pbo": 0.3, "n_trials": log.n_trials, "dsr": {}})


def test_swarm_data_has_expected_keys(tmp_path, monkeypatch):
    _seed_swarm(tmp_path, monkeypatch)
    d = dashboard._swarm_data("matt")
    assert {"generations", "lineage", "roster", "pbo", "n_trials", "diversity"} <= set(d)
    assert len(d["generations"]) == 2
    assert all({"gen", "best", "median"} <= set(g) for g in d["generations"])
    assert len(d["roster"]) == 2
    assert all("label" in r for r in d["roster"])


def test_swarm_data_empty_when_no_log(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(evolve, "STATE_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(champions, "STATE_DIR", str(tmp_path), raising=False)
    d = dashboard._swarm_data("ghost")
    assert d["generations"] == [] and d["roster"] == []


def test_render_includes_swarm_tab_and_canvases(tmp_path, monkeypatch):
    _seed_swarm(tmp_path, monkeypatch)
    payload = dashboard.build_payload("matt", synthetic=True, bars=120)
    html = dashboard.render(payload)
    assert 'id="swarm"' in html                        # the section exists
    assert 'id="swarmField"' in html                   # murmuration canvas
    assert 'id="swarmTree"' in html                    # lineage canvas
    assert '["Swarm","swarm"]' in html                 # SECTIONS entry
    assert 'href="#swarm"' in html                     # subnav link
