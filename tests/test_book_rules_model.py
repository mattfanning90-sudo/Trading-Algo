from book_fixtures import make_inp
from trading_algo.dashboard import book_rules as R
from trading_algo.forex import fx_config

EQ_BT = {"available": True, "synthetic": False, "start": "2012-01-01", "end": "2026-09-18",
         "generated_at": "2026-09-19T08:08:26Z", "allocations": {"ASX": 0.3333, "US": 0.3333, "FTSE": 0.3333},
         "metrics": {"cagr": 0.0609, "sharpe": 0.32},
         "benchmark_metrics": {"cagr": 0.0958, "sharpe": 0.5}}
THIRDS = {"allocations": {"ASX": 1 / 3, "US": 1 / 3, "FTSE": 1 / 3}}


def test_equity_backtest_line_and_its_caveats():
    out = R.backtest_line(make_inp(backtest=EQ_BT, state=THIRDS))[0]
    assert out["text"] == ("Backtest (2012–2026): +6.1% a year, Sharpe 0.32, against the "
                           "benchmark at +9.6% and 0.50")
    c = out["caveat"]
    assert c.startswith("Saved 19 Sep 2026, 24 days ago; may not reflect changes since.")
    assert "survivorship-biased" in c and "dividends" in c
    assert "not this book's settings" not in c                 # FULL's thirds match


def test_a_book_with_other_settings_says_so():
    small = make_inp(backtest=EQ_BT, state={"allocations": {"US": 1.0}},
                     entry={"key": "SMALL", "account": "small", "kind": "equity", "micro": True})
    assert R.backtest_line(small)[0]["caveat"].startswith(
        "This is the standard strategy, not this book's settings.")


def test_missing_and_synthetic_backtests_are_never_performance():
    assert "No saved backtest" in R.backtest_line(make_inp())[0]["text"]
    syn = R.backtest_line(make_inp(backtest=dict(EQ_BT, synthetic=True)))[0]
    assert "made-up test data" in syn["text"] and "%" not in syn["text"]


def test_fx_backtest_carries_the_review_artefact_sentence_only():
    bt = {"available": True, "synthetic": False, "start": "2003-12-02", "end": "2026-10-09",
          "generated_at": "2026-10-13T03:30:08Z", "metrics": {"cagr": -0.0947, "sharpe": -2.14}}
    out = R.backtest_line(make_inp("fx", backtest=bt))[0]
    assert R.FX_ARTEFACTS in fx_config.REVIEW_NOTICE
    assert out["caveat"].startswith(R.FX_ARTEFACTS) and "−2.06" not in out["caveat"]
    assert R.FX_ARTEFACTS
    assert "Sharpe:" not in R.FX_ARTEFACTS


PERM = {"p_value": 0.472636815920398, "n_permutations": 200, "created": "2026-09-19T21:26:21",
        "window": {"start": "2020-04-10", "end": "2026-09-19"}}


def test_luck_test_describes_the_search_not_the_book():
    out = R.luck_test(make_inp("fx", permtest=PERM, roster=0))[0]
    assert "about 1 in 2 times" in out["text"] and "the p-value is 0.47" in out["text"]
    assert "look like luck" in out["text"]
    assert "none of the search's finds" in out["caveat"] and "200 bar shuffles" in out["caveat"]
    assert out["backlog"] == "F22"


def test_a_small_p_reads_rarely_never_zero_in_ten():
    out = R.luck_test(make_inp("fx", permtest=dict(PERM, p_value=0.04), roster=2))[0]
    assert "rarely" in out["text"] and "0 in" not in out["text"]
    assert "unlikely to be luck alone" in out["text"]


def test_an_old_luck_test_says_so_and_equity_has_none():
    old = R.luck_test(make_inp("fx", permtest=dict(PERM, created="2026-08-01T00:00:00"), roster=0))[0]
    assert "Over 30 days old" in old["caveat"]
    assert R.luck_test(make_inp(permtest=PERM)) == []


def test_not_measured_names_what_is_missing():
    assert "decision record" in R.not_measured(make_inp())[0]["text"]
    assert "agent scorecard" in R.not_measured(make_inp("fx"))[0]["text"]


def test_negative_sharpe_uses_the_typographic_minus():
    bt = {"available": True, "synthetic": False, "start": "2003", "end": "2026",
          "generated_at": "2026-10-13T00:00:00Z", "metrics": {"cagr": -0.002, "sharpe": -6.56}}
    out = R.model(make_inp(backtest=bt))[0]
    assert "Sharpe \u22126.56" in out["text"] and "-6" not in out["text"]


def test_a_middling_p_reads_only_occasionally_and_still_looks_like_luck():
    out = R.luck_test(make_inp("fx", permtest=dict(PERM, p_value=0.07), roster=2))[0]
    assert "only occasionally" in out["text"] and "look like luck" in out["text"]
    assert "the p-value is 0.07" in out["text"]
