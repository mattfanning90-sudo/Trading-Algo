"""Quant-research agent: candidate search + deflated/PBO validation."""
import pytest

from trading_algo.forex import research
from trading_algo.forex.fx_config import profile
from trading_algo.forex.fx_data import closes, synthetic_panel
from trading_algo.forex.pairs import DEFAULT_UNIVERSE


@pytest.fixture
def panel():
    return synthetic_panel(DEFAULT_UNIVERSE, start="2019-01-01", end="2023-01-01")


def test_candidates_are_signals_in_range(panel):
    cands = research.candidates(closes(panel))
    assert len(cands) >= 10
    assert any(n.startswith("statarb_") for n in cands)        # stat-arb present
    assert any(n.startswith("ou_meanrev_") for n in cands)     # OU mean-reversion present
    for name, sig in cands.items():
        assert sig.abs().max().max() <= 1.0 + 1e-9, name


def test_statarb_is_dollar_neutral_legs(panel):
    sig = research._statarb(closes(panel), "AUDUSD", "NZDUSD", 30)
    # the two legs move opposite, everything else flat
    last = sig.dropna().iloc[-1]
    assert abs(last["AUDUSD"] + last["NZDUSD"]) < 1e-9
    assert (last.drop(["AUDUSD", "NZDUSD"]) == 0).all()


def test_run_research_scores_and_pbo(panel):
    res = research.run_research(panel, profile("balanced"), n_bars=500)
    assert res["n_trials"] >= 10
    assert 0.0 <= res["pbo"] <= 1.0
    for name, m in res["metrics"].items():
        assert {"sharpe", "psr", "dsr", "total"} <= set(m)
        assert 0.0 <= m["dsr"] <= 1.0
    report = research.format_report(res)
    assert "VERDICT" in report and "Deflated-Sharpe" in report
