"""Multi-region paper-trading engine (offline / synthetic)."""
import json

import numpy as np
import pandas as pd
import pytest

from trading_algo import config as cfg
from trading_algo import metrics
from trading_algo import paper_trade as pt
from trading_algo import pnl
from trading_algo.regions import get_region


def test_sell_stamps_actual_realized_pnl():
    """Each sell is stamped with its real realised P&L, computed from the actual
    lots it consumes in the fills ledger — not from a stored tally."""
    trade_log = [{"date": "2026-06-10", "region": "US", "ticker": "AAPL",
                  "side": "BUY", "shares": 10, "fill": 100.0,
                  "commission": 1.0, "stamp_duty": 0.0, "currency": "USD"}]
    sleeve = {"currency": "USD", "cash": 0.0, "positions": {"AAPL": 10},
              "cost_basis": {}, "realized_pnl": 0.0, "last_rebalance_month": None}
    px = pd.Series({"AAPL": 120.0})
    pt.rebalance_sleeve(get_region("US"), sleeve, pd.Series(dtype=float),
                        px, "2026-07-01", trade_log)
    assert sleeve["positions"] == {}                 # fully sold
    sell = trade_log[-1]
    assert sell["side"] == "SELL"
    assert sell["entry"] == pytest.approx(100.0)     # actual FIFO cost basis
    assert sell["realized"] > 150                    # ~ (120−100)·10, minus costs/slippage
    # the vestigial stored fields are NOT used as the source of truth
    assert sleeve["realized_pnl"] == 0.0 and sleeve["cost_basis"] == {}


def test_min_gap_guard_blocks_near_inception_churn():
    """A book rebalanced late in a month is not churned days later on the 1st,
    but does rebalance once the gap clears."""
    sleeve = {"last_rebalance_month": "2026-06", "last_rebalance_date": "2026-06-28"}
    assert pt._should_rebalance(sleeve, "2026-07-01", "2026-07") is False   # +3 days
    assert pt._should_rebalance(sleeve, "2026-07-20", "2026-07") is True    # +22 days
    # a fresh book (never rebalanced) always trades on its first run
    assert pt._should_rebalance({"last_rebalance_month": None}, "2026-07-01", "2026-07") is True


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    return "test"


def test_init_refuses_to_overwrite_a_live_book(account):
    """--init builds a FRESH book with an empty trade ledger, and every P&L
    number is derived from that ledger — so re-running the documented
    `--init` line on a funded account would destroy the only record of it.
    forex.fx_book.init_account has always guarded this; the equity side must too.
    """
    pt.init_account(account, capital=100_000, synthetic=True)
    pt.run_daily(account, synthetic=True)
    before = pt.load_state(account)
    assert before["trades"], "need a book with history to prove it is protected"

    with pytest.raises(SystemExit, match="already exists"):
        pt.init_account(account, capital=100_000, synthetic=True)

    assert pt.load_state(account)["trades"] == before["trades"], \
        "the refused --init must leave the ledger untouched"

    # --force is the deliberate escape hatch, and it DOES reset.
    pt.init_account(account, capital=100_000, synthetic=True, force=True)
    assert pt.load_state(account)["trades"] == []


def test_init_splits_into_sleeves(account):
    pt.init_account(account, capital=100_000, synthetic=True)
    state = pt.load_state(account)
    assert set(state["sleeves"]) == set(cfg.ALLOCATIONS)
    assert state["initial_capital_base"] == 100_000
    # each sleeve funded with positive local cash
    for k, s in state["sleeves"].items():
        assert s["cash"] > 0
        assert s["positions"] == {}


def test_daily_run_marks_and_persists(account):
    pt.init_account(account, capital=300_000, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)

    assert state["equity_history"], "equity history should be recorded"
    date, equity = state["equity_history"][-1]
    assert equity > 0

    # positions are whole-share integers
    for s in state["sleeves"].values():
        for shares in s["positions"].values():
            assert isinstance(shares, int)


def test_state_is_json_roundtrippable(account):
    pt.init_account(account, capital=100_000, synthetic=True)
    pt.run_daily(account, synthetic=True)
    raw = json.loads(open(pt._state_file(account)).read())
    assert raw["base_currency"] == cfg.BASE_CURRENCY


def test_force_rebalance_resets_months(account):
    pt.init_account(account, capital=300_000, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    for s in state["sleeves"].values():
        s["last_rebalance_month"] = None
    pt.save_state(account, state)
    # second run should not raise
    pt.run_daily(account, synthetic=True)


def test_cost_basis_derived_from_fills(account):
    pt.init_account(account, capital=300_000, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    # The stored fields stay at their empty defaults — they are not the source of
    # truth. Basis and realised P&L are derived from the fills ledger.
    for sleeve in state["sleeves"].values():
        assert sleeve.get("cost_basis") == {} and sleeve.get("realized_pnl") == 0.0
    basis = pnl.open_basis(pnl.build_lots(state["trades"])[0])
    for k, sleeve in state["sleeves"].items():
        for t in sleeve["positions"]:          # every holding traces to a real buy
            assert basis.get((k, t), 0) > 0


def test_single_region_account(account):
    """A US-only account holds just one sleeve and runs cleanly."""
    pt.init_account(account, capital=1_000, synthetic=True, allocations={"US": 1.0})
    state = pt.load_state(account)
    assert list(state["sleeves"]) == ["US"]
    assert list(state["allocations"]) == ["US"]
    assert abs(state["allocations"]["US"] - 1.0) < 1e-9
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    assert state["equity_history"][-1][1] > 0
    # only USD trades, never ASX/FTSE
    assert all(t["region"] == "US" for t in state["trades"])


def test_init_rejects_unknown_region(account):
    with pytest.raises(SystemExit):
        pt.init_account(account, capital=1000, synthetic=True, allocations={"XYZ": 1.0})


def test_min_size_gate_holds_cash(account):
    # split three ways a tiny account sits below the viability floor → no trades
    pt.init_account(account, capital=300, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    assert len(state["trades"]) == 0
    assert all(not s["positions"] for s in state["sleeves"].values())


def test_drawdown_halt_liquidates(account):
    pt.init_account(account, capital=300_000, synthetic=True)
    pt.run_daily(account, synthetic=True)            # opens positions
    state = pt.load_state(account)
    state["risk_halted"] = True                      # force the breaker on
    state["halt_cooldown"] = 5
    pt.save_state(account, state)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    assert all(not s["positions"] for s in state["sleeves"].values())  # all cash
    assert state["risk_halted"] is True              # still halted (cooldown remains)


def test_cooldown_counts_market_days_not_runs(account):
    """The engine fires several times a day; the drawdown cooldown must decrement
    per distinct report date, not per run."""
    pt.init_account(account, capital=300_000, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    state["risk_halted"] = True
    state["halt_cooldown"] = 3
    state.pop("halt_last_day", None)
    pt.save_state(account, state)
    # Two runs land on the SAME synthetic report date -> one day of cooldown.
    pt.run_daily(account, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    assert state["halt_cooldown"] == 2               # dropped by ONE, not two
    assert state["risk_halted"] is True


def test_micro_account_does_not_crash(account):
    """A tiny account can't afford the full book — must handle gracefully."""
    pt.init_account(account, capital=100, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    assert state["equity_history"][-1][1] >= 0


_KNOWN_STATUSES = {
    "rebalanced", "held", "cash:idle", "cash:halted", "cash:below-min",
    "cash:regime-off", "cash:no-eligible-names", "cash:data-quality",
    "cash:insufficient-names",
}


def test_every_sleeve_records_a_status(account):
    """Each run stamps every sleeve with a machine-readable reason for its state,
    so an idle sleeve (e.g. ASX sitting in cash) is diagnosable, not silent."""
    pt.init_account(account, capital=300_000, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    for k, s in state["sleeves"].items():
        st = s.get("last_status")
        assert st is not None, f"{k} has no last_status"
        assert st["status"] in _KNOWN_STATUSES, f"{k}: unknown status {st['status']}"
        assert st["date"] == state["equity_history"][-1][0]
        # a sleeve in cash must record since-when; a working sleeve must not
        if st["status"].startswith("cash"):
            assert st["flat_since"], f"{k} flat but no flat_since"
        else:
            assert st["flat_since"] is None


def test_empty_target_reason_distinguishes_regime_off():
    """A risk-off index yields 'regime-off'; a risk-on index with no qualifying
    name yields 'no-eligible-names' — the distinction the old blanket
    'regime RISK-OFF' print could not make."""
    from trading_algo.regions import get_region
    p = get_region("ASX").params
    idx = pd.Series(range(200, 100, -1),
                    index=pd.bdate_range("2025-01-01", periods=100))  # falling → risk-off
    prices = pd.DataFrame({"AAA": range(100)}, index=idx.index)
    assert pt._empty_target_reason(prices, idx, p, eligible=None) == "regime-off"
    # everything frozen by data quality → 'data-quality', regardless of regime
    assert pt._empty_target_reason(prices, idx, p, eligible=set()) == "data-quality"


def _break_one_pair(monkeypatch, currency="USD"):
    """Simulate a transient single-pair FX download failure: the given
    currency's column comes back all-NaN (as when its Yahoo pair 403s while the
    others succeed), which is exactly what poisoned the US sleeve's equity."""
    real = pt.fx.synthetic_fx

    def broken(currencies, *a, **k):
        tbl = real(currencies, *a, **k)
        tbl[currency] = float("nan")
        return tbl

    monkeypatch.setattr(pt.fx, "synthetic_fx", broken)


def test_fx_snapshot_carries_forward_a_failed_rate(monkeypatch):
    """A NaN FX rate (failed pair fetch) must not surface as NaN — the last
    known-good rate is carried forward instead."""
    _break_one_pair(monkeypatch, "USD")
    prev = {"AUD": 1.0, "USD": 1.50, "GBP": 1.90}
    snap = pt.fx_snapshot(synthetic=True, prev=prev)
    assert snap["USD"] == pytest.approx(1.50)     # carried forward, not NaN
    assert snap["AUD"] == 1.0
    assert snap["GBP"] == snap["GBP"]             # the healthy pair is unaffected


def test_failed_fx_rate_never_poisons_equity(account, monkeypatch):
    """End-to-end: a transient USD-pair failure must never write a NaN FX rate
    or NaN equity into state — the regression behind the `full` book's NaN AUM."""
    pt.init_account(account, capital=300_000, synthetic=True)
    pt.run_daily(account, synthetic=True)                 # establishes a good snapshot
    good_usd = pt.load_state(account)["fx_snapshot"]["USD"]
    assert good_usd == good_usd                            # sanity: not NaN

    _break_one_pair(monkeypatch, "USD")
    pt.run_daily(account, synthetic=True)                  # USD pair now fails
    state = pt.load_state(account)

    # The snapshot (overwritten every run) must never contain a NaN rate.
    for ccy, rate in state["fx_snapshot"].items():
        assert rate == rate, f"NaN FX rate persisted for {ccy}"
    assert state["fx_snapshot"]["USD"] == pytest.approx(good_usd)
    # No NaN ever enters the persisted equity/sleeve history.
    for date, eq in state["equity_history"]:
        assert eq == eq, f"NaN equity persisted on {date}"


def test_unfunded_region_paper_init_succeeds(account):
    """An account funded on a registered-but-UNFUNDED region (TSX/CAD — absent
    from cfg.ALLOCATIONS by design) must open cleanly. fx_snapshot has to size
    its currency set off THIS account's regions, not the global ALLOCATIONS,
    or the CAD sleeve KeyErrors on funding."""
    pt.init_account(account, capital=50_000, synthetic=True, allocations={"TSX": 1.0})
    state = pt.load_state(account)
    assert list(state["sleeves"]) == ["TSX"]
    assert state["sleeves"]["TSX"]["currency"] == "CAD"
    assert state["sleeves"]["TSX"]["cash"] > 0             # funded in local CAD
    assert "CAD" in state["fx_snapshot"]                   # its rate was snapshotted
    pt.run_daily(account, synthetic=True)                 # a full daily cycle runs
    state = pt.load_state(account)
    assert state["equity_history"][-1][1] > 0


def test_failed_pair_no_prior_holds_cash_and_persists_no_nan(account, monkeypatch):
    """A failed pair with NO prior good rate must not (a) persist a NaN FX rate,
    nor (b) bypass the hold-cash path via `NaN < MIN_VIABLE` — the sleeve cannot
    be valued, so it holds cash and never trades on a NaN mark."""
    # US-only book so the failing pair IS the book's whole valuation currency.
    pt.init_account(account, capital=100_000, synthetic=True, allocations={"US": 1.0})
    # Simulate a book that has NEVER resolved a USD rate (no prior to carry).
    state = pt.load_state(account)
    state["fx_snapshot"] = {}
    pt.save_state(account, state)

    _break_one_pair(monkeypatch, "USD")                   # USD 403s, nothing to carry
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)

    # (a) no NaN rate is ever persisted
    for ccy, rate in state["fx_snapshot"].items():
        assert rate == rate, f"NaN FX rate persisted for {ccy}"
    # (b) an unvaluable sleeve holds cash — it must NOT fall through the
    # min-viable gate and trade on a NaN valuation
    assert not state["sleeves"]["US"]["positions"], "traded on an unvaluable sleeve"
    assert len(state["trades"]) == 0
    # (c) no NaN in the persisted equity history
    for d, eq in state["equity_history"]:
        assert eq == eq, f"NaN equity persisted on {d}"


def test_post_halt_re_entry_follows_cooldown_not_calendar(account):
    """When the drawdown cooldown clears, the sleeve must be able to re-enter on
    the next run — NOT be stuck flat until the next calendar month. The halted
    branch must not stamp last_rebalance_month=this_month (which would calendar-
    block re-entry for the rest of the month)."""
    pt.init_account(account, capital=300_000, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    month = state["equity_history"][-1][0][:7]

    # Force a halt that will clear on the very next run (cooldown 1, fresh day).
    state["risk_halted"] = True
    state["halt_cooldown"] = 1
    state.pop("halt_last_day", None)
    pt.save_state(account, state)

    pt.run_daily(account, synthetic=True)                 # halted run -> resume
    state = pt.load_state(account)
    assert state["risk_halted"] is False                  # cooldown cleared
    # After a halt->resume the sleeve must not be calendar-pinned to this month:
    # re-entry is governed by the cooldown, so the month stamp is cleared.
    for k, s in state["sleeves"].items():
        assert s.get("last_rebalance_month") != month, (
            f"{k} still calendar-blocked at {month} after the halt lifted")


def test_staleness_gate_flags_old_bars_but_not_weekends():
    """The wall-clock freshness gate refuses to act on a bar that is too old for
    the region's daily cadence, but a normal Fri->Mon (or long-weekend) gap must
    never false-positive."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    region = get_region("US")
    tz = ZoneInfo(region.timezone)
    # Friday close, evaluated the following Monday -> fresh (weekend gap only).
    fri = pd.Timestamp("2026-07-17")                      # Friday
    mon = datetime(2026, 7, 20, 18, 0, tzinfo=tz)         # Monday evening
    assert pt._is_stale(region, fri, now=mon) is False
    # A bar two full weeks old is unambiguously stale.
    old = pd.Timestamp("2026-07-06")
    assert pt._is_stale(region, old, now=mon) is True


def test_unpriced_holdings_never_reach_the_breaker(account, monkeypatch):
    """A sleeve whose holdings have no price must not be valued at cash-only.

    This is what forced the live `full` book into liquidation on 2026-08-27: the
    FTSE price row carried no usable closes, `sleeve_equity_local` silently
    dropped all ten holdings and returned CASH only (£4,175 instead of £17,129),
    and nothing marked the valuation incomplete — `_is_stale` checks the bar's
    DATE, not whether it contains prices. The breaker read a −26% book, tripped,
    and liquidated three sleeves on a loss that never happened.

    Unpriced holdings must be treated exactly like a stale bar: skip the sleeve,
    mark the valuation incomplete, leave the breaker and history untouched.
    """
    pt.init_account(account, capital=300_000, synthetic=True)
    pt.run_daily(account, synthetic=True)             # open positions
    before = pt.load_state(account)
    assert any(s["positions"] for s in before["sleeves"].values()), "need holdings"
    equity_rows = len(before["equity_history"])
    trades = len(before["trades"])

    real = pt.latest_region_data

    def blank_last_row(region, synthetic):
        """Same bar date, but every close on it is missing — the live failure."""
        prices, index_px = real(region, synthetic)
        prices = prices.copy()
        prices.iloc[-1] = float("nan")
        return prices, index_px

    monkeypatch.setattr(pt, "latest_region_data", blank_last_row)
    pt.run_daily(account, synthetic=True)

    after = pt.load_state(account)
    assert after["risk_halted"] is False, "breaker must not trip on an unpriced book"
    assert len(after["equity_history"]) == equity_rows, "no phantom equity row"
    assert len(after["trades"]) == trades, "must not trade off unpriced holdings"


def test_one_dead_holding_does_not_freeze_the_sleeve(account, monkeypatch):
    """A single permanently-unpriced name must not sit the sleeve out forever.

    Guards the fix above from over-reaching: a delisted holding never prices
    again, so skipping the sleeve whenever ANY holding is unpriced would freeze
    it for good. Only a TOTAL blackout — nothing we hold has a price — is an
    outage worth sitting out.
    """
    pt.init_account(account, capital=300_000, synthetic=True)
    pt.run_daily(account, synthetic=True)
    state = pt.load_state(account)
    region = next(k for k, s in state["sleeves"].items() if s["positions"])
    dead = sorted(state["sleeves"][region]["positions"])[0]

    real = pt.latest_region_data

    def one_dead_name(reg, synthetic):
        prices, index_px = real(reg, synthetic)
        prices = prices.copy()
        if dead in prices.columns:
            prices.loc[prices.index[-1], dead] = float("nan")
        return prices, index_px

    monkeypatch.setattr(pt, "latest_region_data", one_dead_name)
    pt.run_daily(account, synthetic=True)

    after = pt.load_state(account)
    assert after["sleeves"][region]["last_status"]["status"] != "unpriced", \
        "one dead name must not sit the whole sleeve out"
    assert after["risk_halted"] is False


# ---- status(): the CLI's vol and drawdown are the measurement layer's -------
def _seed_equity(account, n=22, sigma=0.01, seed=5) -> pd.Series:
    """A book whose equity history is `n` daily marks of known returns."""
    pt.init_account(account, capital=100_000, synthetic=True)
    st = pt.load_state(account)
    idx = pd.bdate_range("2026-01-02", periods=n)
    eq = 100_000 * np.cumprod(1 + np.random.default_rng(seed).normal(0, sigma, n))
    st["equity_history"] = [[d.strftime("%Y-%m-%d"), round(float(v), 2)]
                            for d, v in zip(idx, eq)]
    pt.save_state(account, st)
    return pd.Series([v for _, v in st["equity_history"]], index=idx)


def _status_value(capsys, account, label) -> str:
    capsys.readouterr()
    pt.status(account)
    line = next(ln for ln in capsys.readouterr().out.splitlines() if label in ln)
    return line.split()[-1]


def test_status_prints_the_layers_vol_and_drawdown(account, capsys):
    """'Ann. vol' and 'Max drawdown' equal the primitives at the series' own
    annualisation (daily marks -> 252) with the sample (ddof=1) sd."""
    s = _seed_equity(account)
    rets = s.pct_change().dropna()
    vol = metrics.annualised_vol(rets, periods_per_year=metrics.periods_per_year(s.index))
    population = rets.std(ddof=0) * np.sqrt(252)
    assert f"{vol:.1%}" != f"{population:.1%}", "the pin must tell ddof apart"
    assert _status_value(capsys, account, "Ann. vol") == f"{vol:.1%}"
    assert _status_value(capsys, account, "Max drawdown") == \
        f"{metrics.max_drawdown(s).value:.2%}"


def test_status_routes_through_the_metrics_layer(account, capsys, monkeypatch):
    """A local formula with the same value passes the pin above; this passes
    only if status() CALLS the layer."""
    _seed_equity(account)
    monkeypatch.setattr(metrics, "annualised_vol",
                        lambda r, *, periods_per_year: 0.4321)
    monkeypatch.setattr(metrics, "max_drawdown",
                        lambda e: metrics.Measure(-0.1234, "MaxDrawdown", float("nan")))
    assert _status_value(capsys, account, "Ann. vol") == "43.2%"
    assert _status_value(capsys, account, "Max drawdown") == "-12.34%"


def test_diagnosis_uses_the_same_gate_and_date_as_the_live_path(monkeypatch, capsys):
    """The tracking diagnosis must ask `compute_targets` the same question the
    live rebalance asks (paper_trade.py:849-854): the data-quality gate's
    eligible set, evaluated as of the sleeve's last rebalance date. Without
    both, the 'exposure gap' it prints is an artefact of comparing a gated book
    built in June to an ungated target computed in September."""
    from trading_algo import data, data_quality, strategy

    region = get_region("US")
    px, ix = data.synthetic_region(region, start="2024-01-01", end="2026-01-01")
    monkeypatch.setattr(data, "load_region", lambda *a, **k: (px, ix))

    gated = set(px.columns) - {px.columns[0]}     # the gate froze one name
    calls = {}

    def fake_eligible(prices, reg, asof, base=None):
        calls["eligible_asof"] = asof
        return gated, data_quality.QualityReport()

    monkeypatch.setattr(data_quality, "eligible", fake_eligible)

    real = strategy.compute_targets

    def spy(prices, index_prices, p, asof=None, eligible=None, capacity=None):
        calls["asof"] = asof
        calls["eligible"] = eligible
        return real(prices, index_prices, p, asof=asof, eligible=eligible,
                    capacity=capacity)

    monkeypatch.setattr(strategy, "compute_targets", spy)

    rebal = px.index[-40]
    state = {"sleeves": {"US": {"positions": {px.columns[1]: 10}, "cash": 5_000.0,
                                "last_rebalance_date": rebal.strftime("%Y-%m-%d")}},
             "equity_history": [["2025-01-02", 100_000.0]]}

    pt._print_tracking_diagnosis("test", state)

    # the diagnosis swallows exceptions, so prove it did not silently bail
    out = capsys.readouterr().out
    assert "diagnosis unavailable" not in out, out
    assert calls.get("eligible") == gated, "target computed outside the quality gate"
    assert calls.get("asof") == rebal, "target computed at today's bar, not the rebalance"
    assert calls.get("eligible_asof") == rebal, "quality gate assessed at the wrong date"


def test_reconcile_flag_dispatches_to_the_bridge(monkeypatch):
    """`--reconcile` must reach reconcile_status, alongside --tca/--attribution."""
    seen = {}
    monkeypatch.setattr(pt, "reconcile_status",
                        lambda account, synthetic: seen.update(
                            account=account, synthetic=synthetic))
    pt.main(["--account", "full", "--reconcile", "--synthetic"])
    assert seen == {"account": "full", "synthetic": True}


def test_reconcile_status_prints_the_books_actual_money(monkeypatch, capsys):
    """The money column is the book's real base-currency P&L: the paper-return
    row must read end - start equity (9,000), not the return scaled by
    initial_capital_base (10,000) — the build-out's costs sit between the two."""
    from trading_algo import portfolio_backtest

    state = {"initial_capital_base": 100_000.0, "base_currency": "AUD",
             "fx_snapshot": {"AUD": 1.0}, "trades": [], "allocations": None,
             "equity_history": [["2026-01-05", 90_000.0], ["2026-06-30", 99_000.0]]}
    monkeypatch.setattr(pt, "load_state", lambda account: state)
    curve = pd.Series([1.0, 1.1, 1.2],
                      index=pd.to_datetime(["2025-12-31", "2026-01-05", "2026-06-30"]))
    monkeypatch.setattr(portfolio_backtest, "run_portfolio_backtest",
                        lambda **kw: {"equity": curve})
    pt.reconcile_status("t", synthetic=True)
    out = capsys.readouterr().out
    assert "bridge unavailable" not in out, out
    row = next(ln for ln in out.splitlines() if ln.startswith("paper return"))
    assert row.split()[-1] == "9,000", row


# --- the backtest a book is compared against must be like for like ----------
def _profiled_book(regions=("US",), overrides=None, last="2026-06-30") -> dict:
    """A book as `--init --profile` leaves it: its own regions and allocations,
    its profile's StrategyParams overrides, and two equity points."""
    return {"initial_capital_base": 10_000.0, "base_currency": "AUD",
            "fx_snapshot": {"AUD": 1.0}, "trades": [],
            "allocations": {k: 1.0 / len(regions) for k in regions},
            "param_overrides": dict(overrides or {}),
            "equity_history": [["2026-01-05", 10_000.0], [last, 10_100.0]]}


def _capture_backtest(monkeypatch, end_is_exclusive=False) -> dict:
    """Stand in for run_portfolio_backtest and record what it was asked for.

    With `end_is_exclusive` the stand-in honours yfinance's convention, which
    the real-data path passes `end` straight through to: bars strictly BEFORE
    `end` come back, never the bar dated `end` itself."""
    from trading_algo import portfolio_backtest
    seen: dict = {}

    def fake(**kw):
        seen.update(kw)
        end = pd.Timestamp(kw["end"])
        idx = pd.bdate_range("2025-12-01", end, inclusive=(
            "left" if end_is_exclusive else "both"))
        return {"equity": pd.Series(np.linspace(1.0, 1.1, len(idx)), index=idx)}

    monkeypatch.setattr(portfolio_backtest, "run_portfolio_backtest", fake)
    return seen


@pytest.mark.parametrize("report", ["attribution_status", "reconcile_status"])
def test_a_profiled_book_is_bridged_against_its_own_strategy(monkeypatch, report):
    """`ultra` trades 3x leverage and `experimental` long/short, through the
    same compute_targets as every book, because paper layers the profile's
    overrides on each region's defaults (`_account_params`). Bridging them
    against a backtest built WITHOUT those overrides compares a leveraged or
    market-neutral book with the default 1x long-only strategy — so both
    reports must ask the backtest for the book's own per-region params."""
    overrides = {"max_gross": 3.0, "long_short": True, "top_n": 6}
    state = _profiled_book(overrides=overrides)
    monkeypatch.setattr(pt, "load_state", lambda account: state)
    seen = _capture_backtest(monkeypatch)

    getattr(pt, report)("t", synthetic=True)

    us = get_region("US")
    assert seen.get("params") == us.params.with_overrides(**overrides)
    assert seen.get("params") == pt._account_params(state, us)


def test_a_plain_book_keeps_each_regions_own_params(monkeypatch):
    """run_portfolio_backtest's `params` REPLACES every region's params
    wholesale. A book with no profile must therefore pass none, so each
    region keeps its own defaults exactly as `_account_params` leaves them."""
    state = _profiled_book(regions=("ASX", "US", "FTSE"))
    seen = _capture_backtest(monkeypatch)
    pt._backtest_for_book(state, synthetic=True)
    assert seen.get("params") is None


def test_a_profile_over_regions_with_different_defaults_is_refused(monkeypatch):
    """Wholesale replacement can carry ONE param set. If the book's regions
    would end up with different params once the profile is layered on, no
    single `params` reproduces the paper path — refuse rather than bridge
    against a strategy the book never ran."""
    from dataclasses import replace

    from trading_algo import regions
    us = regions.REGIONS["US"]
    monkeypatch.setitem(regions.REGIONS, "US", replace(
        us, params=us.params.with_overrides(top_n=3)))
    state = _profiled_book(regions=("ASX", "US"), overrides={"max_gross": 3.0})
    _capture_backtest(monkeypatch)
    with pytest.raises(ValueError, match="different params"):
        pt._backtest_for_book(state, synthetic=True)


def test_the_backtest_runs_the_books_own_regions_and_allocations(monkeypatch):
    """config.ALLOCATIONS funds four sleeves since TSX was added; `full`
    holds three at a third each. The comparison is the book's own split."""
    state = _profiled_book(regions=("ASX", "US", "FTSE"))
    seen = _capture_backtest(monkeypatch)
    pt._backtest_for_book(state, synthetic=True)
    assert seen.get("regions") == ["ASX", "US", "FTSE"]
    assert seen.get("allocations") == state["allocations"]
    assert list(seen["allocations"]) != list(cfg.ALLOCATIONS)


def test_the_predicted_curve_reaches_the_books_last_equity_date(monkeypatch, capsys):
    """`end` reaches yfinance, whose `end` is EXCLUSIVE: asking for the book's
    last date returns bars up to the day before, so the bridge window stopped
    one day short (observed 2026-09-22 against the book's 2026-09-23). Both
    reports must cover the book's last equity date."""
    from trading_algo import attribution
    state = _profiled_book(regions=("ASX", "US", "FTSE"), last="2026-06-30")
    monkeypatch.setattr(pt, "load_state", lambda account: state)
    _capture_backtest(monkeypatch, end_is_exclusive=True)

    pt.reconcile_status("t", synthetic=True)
    out = capsys.readouterr().out
    assert "window 2026-01-05 -> 2026-06-30" in out, out

    got = {}
    real = attribution.attribution_report
    monkeypatch.setattr(attribution, "attribution_report",
                        lambda st, pred=None, *a, **k: (got.update(pred=pred),
                                                        real(st, pred, *a, **k))[1])
    pt.attribution_status("t", synthetic=True)
    assert got["pred"].index[-1] == pd.Timestamp("2026-06-30")
    assert got["pred"].index[0] == pd.Timestamp("2026-01-05")
