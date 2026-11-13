from datetime import datetime, timezone

from book_fixtures import NOW, make_inp, no_network, use_snapshot
from trading_algo.dashboard import book_inputs, book_positions as P, registry


def test_full_groups_regions_with_their_reasons(tmp_path, monkeypatch):
    use_snapshot(tmp_path, monkeypatch)
    no_network(monkeypatch)
    v = P.positions_view(book_inputs.load_inputs(registry.resolve("FULL"), NOW, market="offline"))
    names = [g["key"] for g in v["groups"]]
    assert set(names) == {"ASX", "US", "FTSE"}
    asx = next(g for g in v["groups"] if g["key"] == "ASX")
    assert "since the book began" in asx["why"] and asx["caveat"] and asx["rows"] == []
    us = next(g for g in v["groups"] if g["key"] == "US")
    assert us["rows"] and us["rows"][0]["bought"].startswith("Bought at US$")
    assert v["prices"]["status"] == "offline" and "at cost" in v["prices"]["text"]
    assert v["count"].endswith("holdings")


def _live(price=500.0, priced=True):
    return {"as_of": "2026-10-13", "sleeves": [{"key": "US", "positions": [
        {"ticker": "AAPL", "priced": priced, "price": price, "value_base": 7_000.0,
         "unrealized_base": 350.0, "unrealized_pct": 0.05}]}]}


def _equity_state(shares=10):
    return {"allocations": {"US": 1.0}, "fx_snapshot": {"USD": 1.43},
            "sleeves": {"US": {"currency": "USD", "cash": 100.0, "positions": {"AAPL": shares}}},
            "trades": [{"date": "2026-07-01", "region": "US", "ticker": "AAPL", "currency": "USD",
                        "side": "BUY" if shares > 0 else "SELL", "shares": abs(shares),
                        "fill": 476.19, "commission": 1.0}]}


def test_live_prices_fill_in_worth_and_gain():
    v = P.positions_view(make_inp(state=_equity_state(), live=_live(), market="ready"))
    row = v["groups"][0]["rows"][0]
    assert row["worth"] == "A$7,000.00" and row["gain"] == "▲ +A$350.00" and row["dir"] == "up"
    assert v["prices"]["text"] == "Prices as of 13 Oct 2026."


def test_a_short_reads_sold_at():
    v = P.positions_view(make_inp(state=_equity_state(-10)))
    row = v["groups"][0]["rows"][0]
    assert row["side"] == "SHORT" and row["bought"].startswith("Sold at") and "short" in row["qty"]


def test_an_unpriced_holding_says_held_at_cost():
    v = P.positions_view(make_inp(state=_equity_state(), live=_live(priced=False), market="ready"))
    assert v["groups"][0]["rows"][0]["note"] == "No price today; held at cost."


def test_long_short_cash_is_explained_and_paused_regions_come_first():
    state = _equity_state()
    state["param_overrides"] = {"long_short": True}
    state["allocations"] = {"US": 0.5, "FTSE": 0.5}
    state["sleeves"]["FTSE"] = {"currency": "GBP", "cash": 1.0, "positions": {},
                                "last_flat_reason": "data-quality"}
    v = P.positions_view(make_inp(state=state))
    assert v["groups"][0]["key"] == "FTSE" and v["groups"][0]["level"] == "act"
    assert any("short sales" in n for n in v["notes"])


def test_fx_positions_group_by_asset_class_and_say_when_the_market_is_shut():
    sat = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    fxb = {"open_rows": [
        {"pair": "EURUSD", "weight": 0.2, "avg_entry": 1.1, "price": 1.12, "priced": True,
         "notional": 1_000.0, "unrealized": 20.0, "entry_cost": 1.0},
        {"pair": "BTCUSD", "weight": -0.05, "avg_entry": 60_000.0, "price": 59_000.0,
         "priced": True, "notional": 250.0, "unrealized": 4.0, "entry_cost": 0.5}]}
    v = P.positions_view(make_inp("fx", fx_book=fxb, state={"bar": "1d"}, now=sat))
    by = {g["key"]: g for g in v["groups"]}
    assert set(by) == {"fx", "crypto"}
    assert "Market shut" in by["fx"]["why"] and "Market shut" not in by["crypto"]["why"]
    assert by["crypto"]["rows"][0]["side"] == "SHORT"
    assert by["fx"]["rows"][0]["gain"] == "▲ +A$19.00"            # after the entry spread


def test_a_non_finite_live_value_reads_as_unpriced():
    for bad in (float("nan"), float("inf")):
        live = _live(price=bad)
        live["sleeves"][0]["positions"][0]["value_base"] = bad
        v = P.positions_view(make_inp(state=_equity_state(), live=live, market="ready"))
        row = v["groups"][0]["rows"][0]
        assert row["note"] == "No price today; held at cost." and row["worth"] is None
        assert "nan" not in str(v).lower() and "inf" not in str(v).lower()


def test_missing_live_date_and_single_holding_wording():
    live = _live()
    live["as_of"] = None
    v = P.positions_view(make_inp(state=_equity_state(), live=live, market="ready"))
    assert v["prices"]["text"] == "Prices as of an unknown date."
    assert v["count"] == "1 holding"


def test_fx_single_position_and_non_finite_price():
    fxb = {"open_rows": [{"pair": "EURUSD", "weight": 0.2, "avg_entry": 1.1,
                          "price": float("nan"), "priced": True, "notional": 1_000.0,
                          "unrealized": 20.0, "entry_cost": 1.0}]}
    v = P.positions_view(make_inp("fx", fx_book=fxb, state={"bar": "1d"}))
    g = v["groups"][0]
    assert "1 open position." in g["why"] or "Market shut" in g["why"]
    assert g["rows"][0]["now"] == "no price today" and "nan" not in str(v).lower()


def test_one_share_is_singular_and_thousands_have_a_comma():
    for n, want in ((1, "1 share"), (1302, "1,302 shares")):
        v = P.positions_view(make_inp(state=_equity_state(n)))
        assert v["groups"][0]["rows"][0]["qty"] == want
