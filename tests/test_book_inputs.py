"""book_inputs reads only saved data — never a price download — and one bad
input becomes an error entry, not a blank screen."""
import pytest

from book_fixtures import NOW, no_network, use_snapshot
from trading_algo.dashboard import api, book_inputs, registry


def test_fees_by_currency_sums_commission_and_stamp():
    trades = [{"currency": "GBP", "commission": 3.0, "stamp_duty": 5.0},
              {"currency": "USD", "commission": 1.0},
              {"currency": "GBP", "commission": 2.0, "stamp_duty": 0.0}]
    fees, stamp = api.fees_by_currency(trades)
    assert fees == {"GBP": 10.0, "USD": 1.0} and stamp == {"GBP": 5.0}


def test_every_real_book_loads_with_no_network(tmp_path, monkeypatch):
    use_snapshot(tmp_path, monkeypatch)
    no_network(monkeypatch)
    entries = registry.discover_accounts()
    assert {e["key"] for e in entries} >= {"FULL", "ULTRA", "EXPERIMENTAL", "SMALL",
                                           "MATT", "PARTNER", "MULTI", "DAY"}
    for e in entries:
        inp = book_inputs.load_inputs(e, NOW)
        assert inp is not None and inp.errors == {}, (e["key"], inp and inp.errors)


def test_full_reads_its_saved_value_and_excludes_the_forced_closes(tmp_path, monkeypatch):
    use_snapshot(tmp_path, monkeypatch)
    no_network(monkeypatch)
    inp = book_inputs.load_inputs(registry.resolve("FULL"), NOW)
    assert inp.card["equity"] == pytest.approx(99_952.38)
    assert inp.excluded_forced > 0                       # 27 Aug liquidation
    assert inp.costs["total"] > 0 and "GBP" in inp.costs["stamp"]
    assert inp.backtest["available"] is True


def test_matt_has_its_luck_test_and_an_empty_roster(tmp_path, monkeypatch):
    use_snapshot(tmp_path, monkeypatch)
    inp = book_inputs.load_inputs(registry.resolve("MATT"), NOW)
    assert inp.permtest["p_value"] == pytest.approx(0.4726, abs=1e-3)
    assert inp.roster == 0
    assert inp.fx_book is not None and len(inp.positions_closed) > 0


def test_one_broken_input_is_reported_not_raised(tmp_path, monkeypatch):
    use_snapshot(tmp_path, monkeypatch)
    from trading_algo import verify

    def broken(*_a, **_k):
        raise RuntimeError("audit down")

    monkeypatch.setattr(verify, "verify_book", broken)
    inp = book_inputs.load_inputs(registry.resolve("FULL"), NOW)
    assert "audit" in inp.errors and inp.findings == []
