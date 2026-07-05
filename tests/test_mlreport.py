"""Predictive-model report: the forward-monitor metrics emission (plumbing only)."""
import json

from trading_algo import mlreport


def test_emit_metrics_appends_jsonl(tmp_path, monkeypatch):
    # monkeypatch the heavy report so we test only the emit plumbing: the sink is filled,
    # a run timestamp is stamped, and one JSON row is appended per invocation.
    def fake_build(*a, metrics_sink=None, **k):
        if metrics_sink is not None:
            metrics_sink.append({"universe": "synthetic", "delta_ir": 0.1, "passes": False})
        return "report body"

    monkeypatch.setattr(mlreport, "build_report", fake_build)
    out = tmp_path / "altdata_monitor.jsonl"
    mlreport.main(["--synthetic", "--with-altdata", "--emit-metrics", str(out)])
    mlreport.main(["--synthetic", "--with-altdata", "--emit-metrics", str(out)])

    lines = out.read_text().strip().splitlines()
    assert len(lines) == 2                      # one appended row per run (a growing log)
    rec = json.loads(lines[0])
    assert rec["passes"] is False and rec["universe"] == "synthetic"
    assert "run_utc" in rec and rec["run_utc"].endswith("Z")


def test_edge_gate_requires_all_and_ignores_sentiment():
    import inspect
    d1 = {"ci_low": 0.2, "ci_high": 0.9, "delta_ic": 0.01}
    assert mlreport._edge_pass(d1, {"dsr": 0.97}, 0.2, True)                  # all gates clear
    assert not mlreport._edge_pass({"ci_low": -0.1, "ci_high": 0.9, "delta_ic": 0.01},
                                   {"dsr": 0.97}, 0.2, True)                  # CI lower ≤ 0
    assert not mlreport._edge_pass(d1, {"dsr": 0.80}, 0.2, True)             # DSR < 95%
    assert not mlreport._edge_pass(d1, {"dsr": 0.97}, 0.7, True)             # PBO > 50%
    assert not mlreport._edge_pass(d1, {"dsr": 0.97}, None, True)            # PBO n/a
    assert not mlreport._edge_pass(d1, {"dsr": 0.97}, 0.2, False)            # shuffle-null non-zero
    # STRUCTURAL guard: the gate takes no sentiment/covered argument, so a survivor-conditioned
    # covered-sub-universe IC can never contribute to a pass.
    assert not any("sent" in p or "cover" in p for p in inspect.signature(mlreport._edge_pass).parameters)


def test_no_metrics_file_without_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(mlreport, "build_report", lambda *a, **k: "report")
    # without --emit-metrics nothing is written (and build_report gets no sink)
    mlreport.main(["--synthetic"])
    assert not list(tmp_path.iterdir())
