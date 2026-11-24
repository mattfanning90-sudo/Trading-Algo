from trading_algo.dashboard import episodes as E

NET = dict(symbol_of=lambda lot: lot["sym"], net_of=lambda lot: lot["net"])


def test_a_trimmed_position_is_one_position():
    events = [("2026-07-01", "AAPL", 10), ("2026-07-15", "AAPL", 5), ("2026-08-01", "AAPL", 0)]
    lots = [{"date": "2026-07-15", "sym": "AAPL", "net": 20.0},
            {"date": "2026-08-01", "sym": "AAPL", "net": -5.0}]
    out, excluded = E.closed_positions(events, lots, **NET)
    assert excluded == 0
    assert out == [{"symbol": "AAPL", "opened": "2026-07-01", "closed": "2026-08-01",
                    "side": "LONG", "net": 15.0, "lots": 2}]


def test_a_flip_closes_one_position_and_opens_another():
    events = [("2026-07-01", "EURUSD", 0.2), ("2026-07-05", "EURUSD", -0.1),
              ("2026-07-09", "EURUSD", 0.0)]
    lots = [{"date": "2026-07-05", "sym": "EURUSD", "net": 3.0},
            {"date": "2026-07-09", "sym": "EURUSD", "net": -1.0}]
    out, _ = E.closed_positions(events, lots, **NET)
    assert [(p["side"], p["net"]) for p in out] == [("LONG", 3.0), ("SHORT", -1.0)]


def test_an_open_position_is_not_counted():
    out, _ = E.closed_positions([("2026-07-01", "X", 5)], [], **NET)
    assert out == []


def test_forced_close_dates_are_left_out_and_counted():
    events = [("2026-07-01", "A", 1), ("2026-08-27", "A", 0),
              ("2026-07-02", "B", 1), ("2026-09-01", "B", 0)]
    lots = [{"date": "2026-08-27", "sym": "A", "net": 276.0},
            {"date": "2026-09-01", "sym": "B", "net": -3.0}]
    out, excluded = E.closed_positions(events, lots, exclude_dates={"2026-08-27"}, **NET)
    assert excluded == 1 and [p["symbol"] for p in out] == ["B"]


def test_a_lot_with_no_known_result_makes_the_position_unmeasured():
    events = [("2026-07-01", "A", 1), ("2026-07-03", "A", 0)]
    out, _ = E.closed_positions(events, [{"date": "2026-07-03", "sym": "A", "net": None}], **NET)
    assert out[0]["net"] is None


def test_a_closed_position_with_no_matching_lot_is_unmeasured():
    out, _ = E.closed_positions([("2026-07-01", "EURUSD", 0.2), ("2026-07-10", "EURUSD", 0.0)], [], **NET)
    assert out[0]["net"] is None and out[0]["lots"] == 0


def test_equity_events_follow_buys_and_sells_and_skip_cash_rows():
    trades = [{"date": "2026-07-01", "region": "US", "ticker": "AAPL", "side": "BUY", "shares": 10},
              {"date": "2026-07-02", "region": "US", "ticker": "AAPL", "side": "DIV", "amount": 1.0},
              {"date": "2026-07-03", "region": "US", "ticker": "AAPL", "side": "SELL", "shares": 4},
              {"date": "2026-07-04", "region": "US", "ticker": "AAPL", "side": "SELL", "shares": 6}]
    assert [q for _, _, q in E.equity_events(trades)] == [10, 6, 0]


def test_forced_dates_read_the_corrections_block():
    state = {"corrections": [{"rows": [{"date": "2026-08-27"}, {"date": "2026-08-28 00:00"}]}]}
    assert E.forced_dates(state) == {"2026-08-27", "2026-08-28"}
    assert E.forced_dates({}) == set()
