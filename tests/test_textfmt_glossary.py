from datetime import datetime

from trading_algo.dashboard import glossary, textfmt as t


def test_money_and_moves():
    assert t.money(-47.62) == "−A$47.62"
    assert t.money(1234.5, signed=True) == "+A$1,234.50"
    assert t.money(5, "GBP") == "£5.00"
    assert t.money_move(-47.62) == "▼ −A$47.62"
    assert t.money_move(0.0) == "▶ A$0.00"


def test_percent_points_and_days():
    assert t.pct(0.0019) == "+0.2%"
    assert t.pct(-0.000476, 2) == "−0.05%"
    assert t.pct(0.71, 0, signed=False) == "71%"
    assert t.points(-0.038) == "3.8 points"
    assert t.day("2026-10-13 03:00") == "13 Oct 2026"
    assert t.day(datetime(2026, 6, 11)) == "11 Jun 2026"
    assert t.direction(None) == "flat" and t.direction(-1) == "down"


def test_a_value_that_rounds_to_zero_carries_no_sign():
    """The sign is chosen from the value as displayed, so nothing reads "−0.0%"."""
    assert t.pct(-0.0004) == "0.0%"
    assert t.pct(0.0004) == "0.0%"
    assert t.pct(-0.004, 0) == "0%"
    assert t.pct(-0.0004, signed=False) == "0.0%"
    assert t.money(-0.004) == "A$0.00"
    assert t.money(-0.004, signed=True) == "A$0.00"
    assert t.money(0.004, signed=True) == "A$0.00"
    assert t.money(-0.005) == "\u2212A$0.01"             # rounds away from zero: keeps its sign
    assert t.money_move(-0.004) == "\u25bc A$0.00"       # direction stays the caller's


def test_first_use_is_glossed_once_and_only_once():
    out = glossary.gloss(["Backtest Sharpe 0.32", "Sharpe again", "no terms"])
    assert out[0].startswith("Backtest (")                    # backtest glossed
    assert out[0].count("(") == 2                             # …and Sharpe
    assert out[1] == "Sharpe again"                           # second use: plain
    assert out[2] == "no terms"


def test_sharpe_inside_deflated_sharpe_is_not_glossed_separately():
    out = glossary.gloss(["no Deflated-Sharpe test passes"])
    assert out[0].count("(") == 1


def test_a_term_inside_a_gloss_is_never_glossed():
    gl = dict((term, g) for term, _, g in glossary.TERMS)
    out = glossary.gloss(["Deflated-Sharpe"])
    assert out[0] == f"Deflated-Sharpe ({gl['Deflated-Sharpe']})"


def test_every_term_has_a_plain_gloss():
    for term, pattern, g in glossary.TERMS:
        assert g and g[0].islower() and not g.endswith("."), term


def test_plural_forms_are_glossed_too():
    out = glossary.gloss(["two backtests and normal spreads"])
    assert out[0].count("(") == 2
