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


def test_init_refuses_to_overwrite_a_live_book(account, paper_cycle):
    """--init builds a FRESH book with an empty trade ledger, and every P&L
    number is derived from that ledger — so re-running the documented
    `--init` line on a funded account would destroy the only record of it.
    forex.fx_book.init_account has always guarded this; the equity side must too.
    """
    pt.init_account(account, capital=100_000, synthetic=True)
    paper_cycle(account)                     # decide, then fill
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


def test_daily_run_marks_and_persists(account, paper_cycle):
    pt.init_account(account, capital=300_000, synthetic=True)
    paper_cycle(account)
    state = pt.load_state(account)

    assert state["equity_history"], "equity history should be recorded"
    date, equity = state["equity_history"][-1]
    assert equity > 0

    # positions are whole-share integers
    for s in state["sleeves"].values():
        for shares in s["positions"].values():
            assert isinstance(shares, int)


def test_state_is_json_roundtrippable(account, paper_cycle):
    pt.init_account(account, capital=100_000, synthetic=True)
    paper_cycle(account)
    raw = json.loads(open(pt._state_file(account)).read())
    assert raw["base_currency"] == cfg.BASE_CURRENCY


def test_force_rebalance_resets_months(account, paper_cycle):
    pt.init_account(account, capital=300_000, synthetic=True)
    paper_cycle(account)
    state = pt.load_state(account)
    for s in state["sleeves"].values():
        s["last_rebalance_month"] = None
    pt.save_state(account, state)
    # second run should not raise
    pt.run_daily(account, synthetic=True)


def test_cost_basis_derived_from_fills(account, paper_cycle):
    pt.init_account(account, capital=300_000, synthetic=True)
    paper_cycle(account)
    state = pt.load_state(account)
    # The stored fields stay at their empty defaults — they are not the source of
    # truth. Basis and realised P&L are derived from the fills ledger.
    for sleeve in state["sleeves"].values():
        assert sleeve.get("cost_basis") == {} and sleeve.get("realized_pnl") == 0.0
    basis = pnl.open_basis(pnl.build_lots(state["trades"])[0])
    for k, sleeve in state["sleeves"].items():
        for t in sleeve["positions"]:          # every holding traces to a real buy
            assert basis.get((k, t), 0) > 0


def test_single_region_account(account, paper_cycle):
    """A US-only account holds just one sleeve and runs cleanly."""
    pt.init_account(account, capital=1_000, synthetic=True, allocations={"US": 1.0})
    state = pt.load_state(account)
    assert list(state["sleeves"]) == ["US"]
    assert list(state["allocations"]) == ["US"]
    assert abs(state["allocations"]["US"] - 1.0) < 1e-9
    paper_cycle(account)
    state = pt.load_state(account)
    assert state["equity_history"][-1][1] > 0
    # only USD trades, never ASX/FTSE
    assert all(t["region"] == "US" for t in state["trades"])


def test_init_rejects_unknown_region(account):
    with pytest.raises(SystemExit):
        pt.init_account(account, capital=1000, synthetic=True, allocations={"XYZ": 1.0})


def test_min_size_gate_holds_cash(account, paper_cycle):
    # split three ways a tiny account sits below the viability floor → no trades
    pt.init_account(account, capital=300, synthetic=True)
    paper_cycle(account)
    state = pt.load_state(account)
    assert len(state["trades"]) == 0
    assert all(not s["positions"] for s in state["sleeves"].values())


def test_drawdown_halt_liquidates(account, paper_cycle):
    """A halted book stages a liquidation and fills it at the NEXT close — a
    breaker decides at a close like anything else and cannot fill at that one."""
    pt.init_account(account, capital=300_000, synthetic=True)
    paper_cycle(account)                             # opens positions
    state = pt.load_state(account)
    state["risk_halted"] = True                      # force the breaker on
    state["halt_cooldown"] = 5
    pt.save_state(account, state)
    state = paper_cycle(account)                     # stage, then fill the exit
    assert all(not s["positions"] for s in state["sleeves"].values())  # all cash
    assert state["risk_halted"] is True              # still halted (cooldown remains)


def test_cooldown_counts_market_days_not_runs(account, paper_cycle):
    """The engine fires several times a day; the drawdown cooldown must decrement
    per distinct report date, not per run."""
    pt.init_account(account, capital=300_000, synthetic=True)
    paper_cycle(account)
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


def test_micro_account_does_not_crash(account, paper_cycle):
    """A tiny account can't afford the full book — must handle gracefully."""
    pt.init_account(account, capital=100, synthetic=True)
    paper_cycle(account)
    state = pt.load_state(account)
    assert state["equity_history"][-1][1] >= 0


def _micro_sleeve(cash):
    return {"currency": "USD", "cash": float(cash), "positions": {},
            "cost_basis": {}, "realized_pnl": 0.0,
            "last_rebalance_month": None, "last_rebalance_date": None}


def test_micro_mode_concentrates_until_every_pick_buys_a_share():
    """The live `small` book's 2026-10-01 rebalance, on its real targets and
    closes. Micro mode used to call a name affordable if ONE share fit in the
    whole sleeve (<= 647.21) but then gave each of three picks a third of it
    (219.74), so AMD/AMAT/SOXX all rounded to 0 shares and the book bought
    nothing three months running. Like `_fit_leg`, it must shrink the pick count
    in rank order — never choosing by price — until every pick buys a share:
    3 picks -> AMD gets 0, 2 picks -> 0, 1 pick at 659.18 -> 1 share of AMD."""
    ranked = ["MU", "AMD", "AMAT", "SOXX", "MRK", "SMH", "CAT", "XBI", "CSCO", "AMGN"]
    closes = [1097.39, 615.73, 529.30, 576.33, 143.81, 617.81, 826.35, 154.51,
              108.34, 407.27]
    targets = pd.Series(0.037, index=ranked)
    px = pd.Series(closes, index=ranked)
    sleeve = _micro_sleeve(679.57)
    trades: list = []

    pt.rebalance_sleeve(get_region("US"), sleeve, targets, px, "2026-10-01", trades)

    assert sleeve["positions"] == {"AMD": 1}
    assert [(t["side"], t["ticker"], t["shares"]) for t in trades] == [("BUY", "AMD", 1)]


def test_micro_mode_keeps_three_picks_when_each_can_buy_a_share():
    """Guard: when the top three affordable names each get >= 1 share at a third
    of the sleeve, micro mode still spreads across three (970.00 each here)."""
    targets = pd.Series(0.1, index=["A", "B", "C", "D"])
    px = pd.Series([400.0, 300.0, 200.0, 50.0], index=["A", "B", "C", "D"])
    sleeve = _micro_sleeve(3000.0)
    trades: list = []

    pt.rebalance_sleeve(get_region("US"), sleeve, targets, px, "2026-10-01", trades)

    assert sleeve["positions"] == {"A": 2, "B": 3, "C": 4}


_KNOWN_STATUSES = {
    "rebalanced", "held", "cash:idle", "cash:halted", "cash:below-min",
    "cash:regime-off", "cash:no-eligible-names", "cash:data-quality",
    "cash:insufficient-names",
}


def test_every_sleeve_records_a_status(account, paper_cycle):
    """Each run stamps every sleeve with a machine-readable reason for its state,
    so an idle sleeve (e.g. ASX sitting in cash) is diagnosable, not silent."""
    pt.init_account(account, capital=300_000, synthetic=True)
    paper_cycle(account)
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


def test_failed_fx_rate_never_poisons_equity(account, monkeypatch, paper_cycle):
    """End-to-end: a transient USD-pair failure must never write a NaN FX rate
    or NaN equity into state — the regression behind the `full` book's NaN AUM."""
    pt.init_account(account, capital=300_000, synthetic=True)
    paper_cycle(account)                 # establishes a good snapshot
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


def test_unfunded_region_paper_init_succeeds(account, paper_cycle):
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
    paper_cycle(account)                 # a full daily cycle runs
    state = pt.load_state(account)
    assert state["equity_history"][-1][1] > 0


def test_failed_pair_no_prior_holds_cash_and_persists_no_nan(account, monkeypatch, paper_cycle):
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
    paper_cycle(account)
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


def test_post_halt_re_entry_follows_cooldown_not_calendar(account, paper_cycle):
    """When the drawdown cooldown clears, the sleeve must be able to re-enter on
    the next run — NOT be stuck flat until the next calendar month. The halted
    branch must not stamp last_rebalance_month=this_month (which would calendar-
    block re-entry for the rest of the month)."""
    pt.init_account(account, capital=300_000, synthetic=True)
    paper_cycle(account)
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


def test_unpriced_holdings_never_reach_the_breaker(account, monkeypatch, paper_cycle):
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
    paper_cycle(account)             # open positions
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
    paper_cycle(account)

    after = pt.load_state(account)
    assert after["risk_halted"] is False, "breaker must not trip on an unpriced book"
    assert len(after["equity_history"]) == equity_rows, "no phantom equity row"
    assert len(after["trades"]) == trades, "must not trade off unpriced holdings"


def test_one_dead_holding_does_not_freeze_the_sleeve(account, monkeypatch, paper_cycle):
    """A single permanently-unpriced name must not sit the sleeve out forever.

    Guards the fix above from over-reaching: a delisted holding never prices
    again, so skipping the sleeve whenever ANY holding is unpriced would freeze
    it for good. Only a TOTAL blackout — nothing we hold has a price — is an
    outage worth sitting out.
    """
    pt.init_account(account, capital=300_000, synthetic=True)
    paper_cycle(account)
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
    paper_cycle(account)

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



def test_diagnosis_asks_at_the_decision_bar_and_marks_at_the_fill(
        account, paper_cycle, monkeypatch, capsys):
    """A paper book decides at one close and fills at the NEXT, so the two
    halves of the diagnosis live on different bars. The target must be asked
    where the live rebalance asked it — the DECISION bar — and the positions
    marked where they were executed — the FILL bar. Asking at the fill bar
    sizes a target the book never chose; on real panels that put a 0.35-0.79pp
    median gap into the printed exposure, and 5-10 regime flips per sleeve
    printed a 0% target against a 50-89% invested book."""
    from trading_algo import data, data_quality, strategy

    pt.init_account(account, capital=300_000, synthetic=True,
                    allocations={"US": 1.0})
    state = paper_cycle(account)                      # decide, then fill
    (decided, _), (filled, _) = state["equity_history"]
    decided, filled = pd.Timestamp(decided), pd.Timestamp(filled)
    sleeve = state["sleeves"]["US"]

    region = get_region("US")
    px, ix = data.synthetic_region(region)            # the bars the book traded on
    monkeypatch.setattr(data, "load_region", lambda *a, **k: (px, ix))
    calls = {}
    real_elig, real_targets = data_quality.eligible, strategy.compute_targets

    def elig_spy(prices, reg, asof, base=None):
        calls["eligible_asof"] = asof
        return real_elig(prices, reg, asof, base)

    def targets_spy(prices, index_prices, p, asof=None, eligible=None, capacity=None):
        calls["target_asof"] = asof
        return real_targets(prices, index_prices, p, asof=asof, eligible=eligible,
                            capacity=capacity)

    monkeypatch.setattr(data_quality, "eligible", elig_spy)
    monkeypatch.setattr(strategy, "compute_targets", targets_spy)

    def held_at(bar):
        invested = sum(n * float(px[t].loc[bar])
                       for t, n in sleeve["positions"].items())
        return f"{invested / (invested + sleeve['cash']):6.1%}"

    assert held_at(decided) != held_at(filled), "the fixture cannot tell the bars apart"

    capsys.readouterr()
    pt._print_tracking_diagnosis(account, state)
    out = capsys.readouterr().out
    assert "diagnosis unavailable" not in out, out
    assert calls.get("target_asof") == decided, "target asked at the fill bar"
    assert calls.get("eligible_asof") == decided, "quality gate read at the fill bar"
    line = next(ln for ln in out.splitlines() if "[US]" in ln)
    assert f"held gross {held_at(filled)}" in line, (
        f"positions not marked at the fill bar {filled.date()}: {line}")


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


def test_reconcile_status_hands_the_bridge_the_backtests_holdings(monkeypatch):
    """The holdings lines need the backtest's per-sleeve weights and returns,
    so --reconcile must pass the result it already computed."""
    from trading_algo import attribution
    state = _profiled_book(regions=("ASX", "US", "FTSE"))
    monkeypatch.setattr(pt, "load_state", lambda account: state)
    _capture_backtest(monkeypatch)
    got = {}
    real = attribution.reconcile

    def spy(st, pred, **kw):
        got.update(kw)
        return real(st, pred, **kw)

    monkeypatch.setattr(attribution, "reconcile", spy)
    pt.reconcile_status("t", synthetic=True)
    assert "equity" in (got.get("backtest") or {})


def test_the_comparison_opens_with_the_book_at_the_books_size(monkeypatch):
    """Since the per-order commission floor (Task 13) the backtest's cost
    depends on its size. Funded at `cfg.START`, the comparison had compounded to
    1.3-5.3x the book's capital by the day the book opened, so the floor was
    charged at the wrong size and `_predicted_curve` only re-levelled the
    result. The comparison must instead trade FROM the book's first equity date
    at the book's own capital: every sleeve's NAV on that date is the book's
    funded sleeve capital to the cent, and the re-level factor is 1.0.

    The FX rate drifts across the sample, so a sleeve funded at any bar other
    than the open bar holds the wrong LOCAL amount — the amount the floor is
    charged against — even where its base-currency value happens to agree."""
    from trading_algo import fx

    def drifting_fx(currencies, start="2012-01-01", end="2026-01-01",
                    base="AUD", seed=7):
        idx = pd.bdate_range(start, end)
        ramp = np.linspace(1.0, 2.0, len(idx))
        return pd.DataFrame({c: 1.0 if c == base else ramp * {"USD": 1.5}.get(c, 2.0)
                             for c in currencies}, index=idx)

    monkeypatch.setattr(fx, "synthetic_fx", drifting_fx)
    opened, last = "2020-03-11", "2020-06-30"       # a Wednesday, mid-sample
    state = {"initial_capital_base": 10_000.0, "base_currency": "AUD",
             "fx_snapshot": {"AUD": 1.0}, "trades": [],
             "allocations": {"US": 0.5, "FTSE": 0.5}, "param_overrides": {},
             "equity_history": [[opened, 10_000.0], [last, 10_100.0]]}
    res = pt._backtest_for_book(state, synthetic=True)
    open_d = pd.Timestamp(opened)
    rate = drifting_fx(["USD", "GBP"], end="2020-07-01").loc[open_d]

    for k, share in state["allocations"].items():
        nav = float(res["sleeve_equity"].loc[open_d, k])
        assert nav == pytest.approx(10_000.0 * share, abs=0.005), k
        sleeve = res["sleeves"][k]
        local = float(sleeve["equity"].loc[open_d])
        ccy = get_region(k).currency
        assert local == pytest.approx(10_000.0 * share / rate[ccy], abs=0.005), k
        # funded and decided on the open bar, filled on the next — Task 9
        assert sleeve["turnover"].index[0] > open_d, k
        assert (sleeve["equity"].loc[:open_d] == sleeve["equity"].iloc[0]).all(), k

    eq = res["equity"]
    window = eq[(eq.index >= open_d) & (eq.index <= pd.Timestamp(last))]
    factor = state["initial_capital_base"] / float(window.iloc[0])
    assert factor == pytest.approx(1.0, abs=1e-12)
    pd.testing.assert_series_equal(pt._predicted_curve(state, res), window,
                                   check_exact=False, atol=0.005, rtol=0.0)


# --- next-day-close fills: a decision is staged, then filled a session later --
def test_a_decision_fills_on_the_next_session_not_its_own(account, paper_cycle):
    """The book decides at the latest close and fills at the NEXT one.

    Filling at the close that produced the signal is not executable by any real
    broker, and it is what made the paper book's fills disagree with the
    backtest's staged targets.
    """
    pt.init_account(account, capital=300_000, synthetic=True,
                    allocations={"US": 1.0})

    state = paper_cycle(account, sessions=1)          # session 1: decide only
    sleeve = state["sleeves"]["US"]
    assert state["trades"] == [], "a target must not fill at the close that produced it"
    assert not sleeve["positions"]
    pending = sleeve.get("pending_target")
    assert pending and pending["weights"], "the target must be staged for the next close"
    decided_on = pending["date"]

    state = paper_cycle(account, sessions=1)          # session 2: fill
    sleeve = state["sleeves"]["US"]
    assert state["trades"], "the staged target must fill on the next session"
    assert "pending_target" not in sleeve
    assert sleeve["positions"]
    for t in state["trades"]:
        assert t["date"] > decided_on, (
            f"{t['ticker']} filled on {t['date']}, the bar its own signal used")
    assert sleeve["last_rebalance_date"] > decided_on


def test_a_second_run_on_the_same_bar_does_not_fill(account, paper_cycle):
    """The engine fires up to three times a day, once per regional close. A
    pending target waits for a new SESSION, not merely for the next run."""
    pt.init_account(account, capital=300_000, synthetic=True,
                    allocations={"US": 1.0})
    paper_cycle(account, sessions=1)
    pt.run_daily(account, synthetic=True)             # same bar, second pass
    state = pt.load_state(account)
    assert state["trades"] == []
    assert state["sleeves"]["US"].get("pending_target")


def test_a_breaker_trip_liquidates_at_the_next_close(account, paper_cycle):
    """The breaker decides at a close like a rebalance does, so its exit fills
    at the NEXT session's close — `backtest.py` stages CASH the same way. It
    must not fill at the close that tripped it, and must not wait an extra
    session (or for the cooldown) either."""
    pt.init_account(account, capital=300_000, synthetic=True,
                    allocations={"US": 1.0})
    state = paper_cycle(account)                      # open the book
    assert state["sleeves"]["US"]["positions"]
    state["peak_equity_base"] = 10 * state["initial_capital_base"]   # a -90% book
    pt.save_state(account, state)

    state = paper_cycle(account, sessions=1)          # this close trips the breaker
    assert state["risk_halted"] is True
    tripped_on = state["equity_history"][-1][0]
    assert state["sleeves"]["US"]["positions"], "no exit at the tripping close"
    # The trip itself stages the exit, dated the bar that tripped. Left to the
    # next run's halted branch it would be dated that run's bar instead, and
    # fill a session later than backtest.py's.
    for k, sl in state["sleeves"].items():
        assert sl.get("pending_target") == {
            "date": tripped_on, "weights": {}, "frozen": []}, k
    opened = len(state["trades"])

    pt.run_daily(account, synthetic=True)             # same bar again: still no exit
    assert len(pt.load_state(account)["trades"]) == opened

    state = paper_cycle(account, sessions=1)          # the next close: exit
    assert not state["sleeves"]["US"]["positions"]
    exits = state["trades"][opened:]
    assert exits and all(t["side"] == "SELL" and t["date"] > tripped_on
                         for t in exits)
    assert "pending_target" not in state["sleeves"]["US"]
    assert state["risk_halted"] is True               # the cooldown is still running


def test_a_book_saved_before_staging_existed_loads_and_runs(account, paper_cycle):
    """Every live book on disk predates `pending_target`. A missing key means
    nothing is pending: the book loads, validates and runs as it always did."""
    us = get_region("US")
    a, b = us.universe[:2]
    legacy = {                                        # the shape d490a15 wrote
        "account": account, "schema_version": 2, "base_currency": "AUD",
        "initial_capital_base": 10_000.0, "allocations": {"US": 1.0},
        "sleeves": {"US": {
            "currency": "USD", "cash": 50_000.0, "positions": {a: 10, b: 20},
            "cost_basis": {}, "realized_pnl": 0.0,
            "last_rebalance_month": "2025-12", "last_rebalance_date": "2025-12-01",
            "last_status": {"date": "2025-12-19", "status": "held",
                            "positions": 2, "flat_since": None}}},
        "trades": [], "equity_history": [["2025-12-19", 10_000.0]],
        "sleeve_history": [], "fx_snapshot": {"USD": 1.5},
        "group": "CORE", "param_overrides": {}, "peak_equity_base": 10_000.0,
        "risk_halted": False}
    with open(pt._state_file(account), "w") as f:
        json.dump(legacy, f)

    sleeve = pt.load_state(account)["sleeves"]["US"]
    assert pt._fill_pending(us, sleeve, pd.Series(dtype=float), "2025-12-22",
                            []) is False

    state = paper_cycle(account, sessions=1)          # same month: nothing to do
    sleeve = state["sleeves"]["US"]
    assert sleeve["positions"] == {a: 10, b: 20}
    assert state["trades"] == []
    assert "pending_target" not in sleeve


def test_the_bridge_rebuilds_a_staged_book_from_its_fills(account, paper_cycle):
    """The reconciliation bridge rebuilds each paper close from the ledger
    (`attribution._sleeve_close`). A staged target moves no cash and no shares,
    so the decision close must come back as the funded, all-cash sleeve — and
    its mark must be that cash — with the build-out landing on the fill bar."""
    from trading_algo import attribution
    pt.init_account(account, capital=300_000, synthetic=True,
                    allocations={"US": 1.0})
    funded = pt.load_state(account)["sleeves"]["US"]["cash"]
    state = paper_cycle(account)
    (decided, _), (filled, _) = state["equity_history"]
    assert {t["date"] for t in state["trades"]} == {filled}

    cash, pos = attribution._sleeve_close(state, "US", pd.Timestamp(decided))
    assert cash == pytest.approx(funded) and not any(pos.values())
    rate = state["fx_snapshot"]["USD"]
    assert state["sleeve_history"][0]["US"] == pytest.approx(funded * rate, abs=0.01)

    cash, pos = attribution._sleeve_close(state, "US", pd.Timestamp(filled))
    sleeve = state["sleeves"]["US"]
    assert cash == pytest.approx(sleeve["cash"])
    assert pos == {t: float(n) for t, n in sleeve["positions"].items()}


# --- dividends: credited to cash, ledgered as DIV rows ----------------------
def test_dividends_are_credited_to_cash_and_ledgered(monkeypatch):
    """The paper book marks at the UNADJUSTED close, so an ex-dividend drop is
    booked as a loss while the cash never arrives — the backtest, running on
    yfinance's adjusted series, is a total-return number. This credit is what
    makes the two engines measure the same thing."""
    from trading_algo.regions import get_region

    region = get_region("US")
    sleeve = {"currency": "USD", "cash": 1_000.0,
              "positions": {"AAA": 100, "BBB": -50},
              "last_dividend_date": "2026-06-01"}
    div = pd.DataFrame({"AAA": [0.5], "BBB": [0.2]},
                       index=pd.DatetimeIndex(["2026-06-10"]))
    monkeypatch.setattr(pt.data, "dividends", lambda t, s, e: div)

    log = []
    got = pt._credit_dividends(region, sleeve, "2026-06-15", log, synthetic=False)

    # long 100 @ 0.5 = +50; a SHORT owes the dividend: -50 @ 0.2 = -10
    assert got == pytest.approx(40.0)
    assert sleeve["cash"] == pytest.approx(1_040.0)
    assert sleeve["last_dividend_date"] == "2026-06-15"
    rows = {r["ticker"]: r for r in log}
    assert rows["AAA"]["side"] == "DIV" and rows["AAA"]["date"] == "2026-06-10"
    assert rows["AAA"]["commission"] == 0.0
    assert "decision" not in rows["AAA"], "a dividend is not a fill; TCA must skip it"
    assert rows["BBB"]["shares"] == -50


def test_first_run_credits_nothing_and_arms_the_window(monkeypatch):
    """With no prior mark there is no window to credit over, and back-crediting
    a book's whole history from a feed is not something to do silently."""
    from trading_algo.regions import get_region

    sleeve = {"currency": "USD", "cash": 1_000.0, "positions": {"AAA": 100}}
    monkeypatch.setattr(pt.data, "dividends",
                        lambda t, s, e: (_ for _ in ()).throw(AssertionError("no fetch")))
    log = []
    assert pt._credit_dividends(get_region("US"), sleeve, "2026-06-15", log,
                                synthetic=False) == 0.0
    assert sleeve["last_dividend_date"] == "2026-06-15"
    assert log == []


def test_a_dividend_going_ex_on_this_bar_is_credited(monkeypatch):
    """yfinance's `end` is EXCLUSIVE. Measured 2026-10-10: MRK's 0.85 ex
    2026-09-15 is absent from a download ending 2026-09-15 and present in one
    ending 2026-09-16. Asking for (last, today) would put a dividend going ex
    on today's own bar in no window at all: this run cannot see it, and the
    next run's window starts after it."""
    from trading_algo.regions import get_region

    sleeve = {"currency": "USD", "cash": 0.0, "positions": {"MRK": 10},
              "last_dividend_date": "2026-09-14"}
    paid = pd.DataFrame({"MRK": [0.85]}, index=pd.DatetimeIndex(["2026-09-15"]))

    def yahoo(tickers, start, end):             # end exclusive, as yfinance
        return paid.loc[(paid.index >= start) & (paid.index < end)]

    monkeypatch.setattr(pt.data, "dividends", yahoo)
    log = []
    got = pt._credit_dividends(get_region("US"), sleeve, "2026-09-15", log,
                               synthetic=False)
    assert got == pytest.approx(8.5)
    assert [r["date"] for r in log] == ["2026-09-15"]


def test_an_lse_dividend_is_scaled_like_its_price(monkeypatch):
    """Yahoo quotes an LSE dividend in pence, as it quotes the price. Measured
    2026-10-10: HSBA.L paid 7.4134 ex 2026-08-13 against a 1,525.8 close. The
    sleeve keeps pounds, so 100 shares earn GBP 7.41, not GBP 741."""
    from trading_algo.regions import get_region

    sleeve = {"currency": "GBP", "cash": 0.0, "positions": {"HSBA.L": 100},
              "last_dividend_date": "2026-08-12"}
    paid = pd.DataFrame({"HSBA.L": [7.4134]},
                        index=pd.DatetimeIndex(["2026-08-13"]))
    monkeypatch.setattr(pt.data, "dividends", lambda t, s, e: paid)
    log = []
    got = pt._credit_dividends(get_region("FTSE"), sleeve, "2026-08-13", log,
                               synthetic=False)
    assert got == pytest.approx(7.4134)
    assert log[0]["fill"] == pytest.approx(0.074134)
    assert log[0]["currency"] == "GBP"


def test_a_feed_failure_keeps_the_window_for_the_next_run(monkeypatch):
    """Advancing the window past a failed fetch loses every dividend in it for
    good, because the next run's window starts after it. Keep the window, and
    the next run credits it."""
    from trading_algo.regions import get_region

    sleeve = {"currency": "USD", "cash": 0.0, "positions": {"AAA": 10},
              "last_dividend_date": "2026-06-01"}

    def down(tickers, start, end):
        raise RuntimeError("Yahoo 429")

    monkeypatch.setattr(pt.data, "dividends", down)
    assert pt._credit_dividends(get_region("US"), sleeve, "2026-06-15", [],
                                synthetic=False) == 0.0
    assert sleeve["last_dividend_date"] == "2026-06-01"

    paid = pd.DataFrame({"AAA": [0.5]}, index=pd.DatetimeIndex(["2026-06-10"]))
    monkeypatch.setattr(pt.data, "dividends", lambda t, s, e: paid)
    assert pt._credit_dividends(get_region("US"), sleeve, "2026-06-16", [],
                                synthetic=False) == pytest.approx(5.0)
    assert sleeve["last_dividend_date"] == "2026-06-16"


def test_an_earlier_or_repeated_bar_never_reopens_a_credited_window(monkeypatch):
    """The engine runs up to three times on one bar, and a feed can briefly
    serve an older last bar. Neither may fetch again or move the window back,
    or the next run reopens an ex-date already paid. The feed ANSWERS here:
    a sentinel that raises is swallowed by the credit's own failure handling,
    so a test built on one passes with the guard deleted."""
    sleeve = {"currency": "USD", "cash": 0.0, "positions": {"AAA": 10},
              "last_dividend_date": "2026-06-01"}
    paid = pd.DataFrame({"AAA": [0.5]}, index=pd.DatetimeIndex(["2026-06-14"]))
    calls, log = [], []
    monkeypatch.setattr(pt.data, "dividends", _yahoo(paid, calls))
    region = get_region("US")

    assert pt._credit_dividends(region, sleeve, "2026-06-15", log,
                                synthetic=False) == pytest.approx(5.0)
    for bar in ("2026-06-15", "2026-06-12"):
        assert pt._credit_dividends(region, sleeve, bar, log, synthetic=False) == 0.0
        assert sleeve["last_dividend_date"] == "2026-06-15"
    assert len(calls) == 1, "a bar at or before the window's end must not refetch"

    assert pt._credit_dividends(region, sleeve, "2026-06-16", log,
                                synthetic=False) == 0.0
    assert [(r["ticker"], r["date"]) for r in log] == [("AAA", "2026-06-14")]
    assert sleeve["cash"] == pytest.approx(5.0)



def test_a_dividend_accrues_to_the_book_before_this_runs_fill(
        account, paper_cycle, monkeypatch):
    """A dividend that went ex since the last run belongs to the shares held
    over that period, not to what this run's fill buys at its close — so the
    credit runs before the staged target fills."""
    pt.init_account(account, capital=300_000, synthetic=True,
                    allocations={"US": 1.0})
    seen = []
    real = pt._credit_dividends

    def spy(region, sleeve, today, trade_log, synthetic, **kw):
        seen.append((dict(sleeve["positions"]), len(trade_log)))
        return real(region, sleeve, today, trade_log, synthetic, **kw)

    monkeypatch.setattr(pt, "_credit_dividends", spy)
    paper_cycle(account, sessions=1)                  # decide
    state = paper_cycle(account, sessions=1)          # fill
    assert state["sleeves"]["US"]["positions"], "the second session must fill"
    assert seen == [({}, 0), ({}, 0)], "the credit must see the book before the fill"
    assert state["sleeves"]["US"]["last_dividend_date"] == state["equity_history"][-1][0]

# --- the dividend window: exact entitlement, never lost, never doubled ------
def _yahoo(paid: pd.DataFrame, calls: list | None = None):
    """A dividend feed with yfinance's window: start inclusive, end EXCLUSIVE,
    and only the names asked for."""
    def fetch(tickers, start, end):
        if calls is not None:
            calls.append((tuple(tickers), start, end))
        rows = paid.loc[(paid.index >= start) & (paid.index < end)]
        return rows[[t for t in tickers if t in paid.columns]]
    return fetch

def _fill(date, ticker, side, shares, fill=100.0):
    return {"date": date, "region": "US", "ticker": ticker, "side": side,
            "shares": shares, "decision": fill, "fill": fill, "commission": 1.0,
            "stamp_duty": 0.0, "currency": "USD"}

def test_a_window_held_open_across_a_fill_pays_the_shares_held_at_the_ex_date(
        monkeypatch):
    """Entitlement is the position at the ex-date, read off the ledger: today's
    position less every fill dated on or after it. The review's case: AAA 10
    held; the 06-15 run could not fetch, kept the window open, and its fill
    bought AAA to 30 and opened BBB 60 — both ex 06-15. Crediting today's
    position paid AAA 30 and BBB 60; the shares that earned the dividend were
    AAA 10 and BBB 0 (bought at that close, after the name went ex)."""
    sleeve = {"currency": "USD", "cash": 0.0, "positions": {"AAA": 30, "BBB": 60},
              "last_dividend_date": "2026-06-12"}
    log = [_fill("2026-06-01", "AAA", "BUY", 10), _fill("2026-06-15", "AAA", "BUY", 20),
           _fill("2026-06-15", "BBB", "BUY", 60)]
    paid = pd.DataFrame({"AAA": [0.5], "BBB": [0.2]},
                        index=pd.DatetimeIndex(["2026-06-15"]))
    monkeypatch.setattr(pt.data, "dividends", _yahoo(paid))
    got = pt._credit_dividends(get_region("US"), sleeve, "2026-06-16", log,
                               synthetic=False)
    assert got == pytest.approx(5.0)
    assert [(r["ticker"], r["shares"]) for r in log if r["side"] == "DIV"] == [("AAA", 10)]

def test_a_name_sold_inside_an_open_window_is_still_paid(monkeypatch):
    """Sold at the close of its own ex-date while the window was held open: it
    was held at that day's open, so the dividend is owed even though the book
    no longer holds the name when the window is finally credited."""
    sleeve = {"currency": "USD", "cash": 0.0, "positions": {},
              "last_dividend_date": "2026-06-12"}
    log = [_fill("2026-06-01", "AAA", "BUY", 10), _fill("2026-06-15", "AAA", "SELL", 10)]
    paid = pd.DataFrame({"AAA": [0.5]}, index=pd.DatetimeIndex(["2026-06-15"]))
    monkeypatch.setattr(pt.data, "dividends", _yahoo(paid))
    assert pt._credit_dividends(get_region("US"), sleeve, "2026-06-16", log,
                                synthetic=False) == pytest.approx(5.0)

def _alerts(monkeypatch):
    got = []
    monkeypatch.setattr(pt.notifications, "notify",
                        lambda event, message, **kw: got.append((event, message, kw)))
    return got

def test_a_feed_that_returns_nothing_keeps_the_window_and_alerts(monkeypatch):
    """yfinance never raises on a failed download: `multi._download_one`
    catches the error and hands back an empty frame. Read as "no dividends",
    that advanced the window and lost them for good — with only a print."""
    sleeve = {"currency": "USD", "cash": 0.0, "positions": {"AAA": 10},
              "last_dividend_date": "2026-06-01"}
    monkeypatch.setattr(pt.data, "dividends", lambda t, s, e: pd.DataFrame())
    alerts = _alerts(monkeypatch)
    assert pt._credit_dividends(get_region("US"), sleeve, "2026-06-02", [],
                                synthetic=False, account="full") == 0.0
    assert sleeve["last_dividend_date"] == "2026-06-01"
    [(event, message, kw)] = alerts
    assert event == "dividend_feed_gap" and kw["level"] == "alert"
    assert "AAA" in message and kw["missing"] == ["AAA"] and kw["account"] == "full"

def test_a_name_the_feed_omitted_is_paid_later_and_nothing_twice(monkeypatch):
    """Measured: yfinance drops a failed name's column and returns the rest.
    Pay what came back now, keep the window open for the name that did not,
    and when it arrives pay only that — the window re-reads ex-dates already
    paid, so a paid one is recognised from its DIV row, not paid again."""
    sleeve = {"currency": "USD", "cash": 0.0, "positions": {"AAA": 10, "BBB": 20},
              "last_dividend_date": "2026-06-01"}
    paid = pd.DataFrame({"AAA": [0.5], "BBB": [0.2]},
                        index=pd.DatetimeIndex(["2026-06-10"]))
    log, region = [], get_region("US")
    _alerts(monkeypatch)
    monkeypatch.setattr(pt.data, "dividends", _yahoo(paid[["AAA"]]))   # BBB missing
    assert pt._credit_dividends(region, sleeve, "2026-06-15", log,
                                synthetic=False) == pytest.approx(5.0)
    assert sleeve["last_dividend_date"] == "2026-06-01"

    monkeypatch.setattr(pt.data, "dividends", _yahoo(paid))
    assert pt._credit_dividends(region, sleeve, "2026-06-16", log,
                                synthetic=False) == pytest.approx(4.0)
    assert sorted((r["ticker"], r["date"]) for r in log) == [
        ("AAA", "2026-06-10"), ("BBB", "2026-06-10")]
    assert sleeve["cash"] == pytest.approx(9.0)
    assert sleeve["last_dividend_date"] == "2026-06-16"

def test_a_window_stuck_for_more_than_a_few_sessions_says_so(monkeypatch):
    """One missed fetch heals on the next run. A window still open sessions
    later is a feed that is not coming back by itself (a renamed or delisted
    name): every dividend in it waits, and the alert has to say that."""
    monkeypatch.setattr(pt.data, "dividends", lambda t, s, e: pd.DataFrame())
    alerts = _alerts(monkeypatch)
    for today in ("2026-06-02", "2026-06-15"):           # 1 session, then 10
        sleeve = {"currency": "USD", "cash": 0.0, "positions": {"AAA": 10},
                  "last_dividend_date": "2026-06-01"}
        pt._credit_dividends(get_region("US"), sleeve, today, [], synthetic=False)
    (_, fresh, _), (_, stuck, kw) = alerts
    assert "stuck" not in fresh
    assert "stuck" in stuck and kw["stuck_sessions"] == 10


def test_a_name_going_ex_on_its_fill_bar_is_paid_at_the_pre_fill_shares(monkeypatch):
    """Decided 06-12, filled at 06-15's close; AAA and BBB both go ex 06-15.
    Whoever holds at the ex-date's open is paid: AAA's 10 pre-fill shares, and
    nothing for BBB, bought at that close. The run credits BEFORE it fills,
    and the next run's window starts after 06-15, so it is paid once."""
    region = get_region("US")
    sleeve = {"currency": "USD", "cash": 10_000.0, "positions": {"AAA": 10},
              "last_dividend_date": "2026-06-12",
              "pending_target": {"date": "2026-06-12",
                                 "weights": {"AAA": 0.3, "BBB": 0.3}, "frozen": []}}
    log = [_fill("2026-06-01", "AAA", "BUY", 10)]
    paid = pd.DataFrame({"AAA": [0.5], "BBB": [0.2]},
                        index=pd.DatetimeIndex(["2026-06-15"]))
    monkeypatch.setattr(pt.data, "dividends", _yahoo(paid))
    px = pd.Series({"AAA": 100.0, "BBB": 50.0})

    pt._credit_dividends(region, sleeve, "2026-06-15", log, synthetic=False)
    assert pt._fill_pending(region, sleeve, px, "2026-06-15", log)
    assert sleeve["positions"]["AAA"] > 10 and sleeve["positions"]["BBB"] > 0
    pt._credit_dividends(region, sleeve, "2026-06-16", log, synthetic=False)
    assert [(r["ticker"], r["shares"], r["date"]) for r in log
            if r["side"] == "DIV"] == [("AAA", 10, "2026-06-15")]

def test_an_exit_on_an_ex_date_is_paid_across_a_feed_gap_end_to_end(
        account, paper_cycle, monkeypatch):
    """Through run_daily with the credit forced live. The book opens, trips its
    breaker, and liquidates at the next close — the bar every name goes ex.
    That run's feed returns nothing, so the window stays open across the exit.
    The next run must pay the shares sold at that close (held at its open),
    though the book now holds nothing at all."""
    pt.init_account(account, capital=300_000, synthetic=True,
                    allocations={"US": 1.0})
    real = pt._credit_dividends
    monkeypatch.setattr(
        pt, "_credit_dividends",
        lambda region, sleeve, today, log, synthetic, **kw:
            real(region, sleeve, today, log, False, **kw))
    feed = {"up": True, "ex": None}

    def dividends(tickers, start, end):
        if not feed["up"]:
            return pd.DataFrame()
        idx = pd.DatetimeIndex([feed["ex"]] if feed["ex"] else [])
        return pd.DataFrame({t: [1.0] * len(idx) for t in tickers}, index=idx)

    monkeypatch.setattr(pt.data, "dividends", dividends)
    _alerts(monkeypatch)

    state = paper_cycle(account)                      # open the book
    held = dict(state["sleeves"]["US"]["positions"])
    assert held
    state["peak_equity_base"] = 10 * state["initial_capital_base"]
    pt.save_state(account, state)
    paper_cycle(account, sessions=1)                  # this close trips the breaker

    feed["up"] = False
    state = paper_cycle(account, sessions=1)          # the exit fills; feed down
    exit_bar = state["equity_history"][-1][0]
    assert not state["sleeves"]["US"]["positions"]
    assert state["sleeves"]["US"]["last_dividend_date"] < exit_bar

    feed.update(up=True, ex=exit_bar)
    state = paper_cycle(account, sessions=1)
    paid = {r["ticker"]: (r["shares"], r["date"]) for r in state["trades"]
            if r["side"] == "DIV"}
    assert paid == {t: (n, exit_bar) for t, n in held.items()}
    assert state["sleeves"]["US"]["last_dividend_date"] == state["equity_history"][-1][0]

def test_status_and_compare_count_fills_not_dividends(account, paper_cycle, capsys):
    """'Trades to date' and the compare table count executions. A dividend
    row is cash income; it must not read as a trade."""
    pt.init_account(account, capital=300_000, synthetic=True,
                    allocations={"US": 1.0})
    state = paper_cycle(account)
    fills = len(state["trades"])
    ticker, shares = next(iter(state["sleeves"]["US"]["positions"].items()))
    state["trades"].append({"date": state["equity_history"][-1][0], "region": "US",
                            "ticker": ticker, "side": "DIV", "shares": shares,
                            "fill": 0.5, "commission": 0.0, "stamp_duty": 0.0,
                            "currency": "USD"})
    state["sleeves"]["US"]["cash"] += shares * 0.5
    pt.save_state(account, state)
    capsys.readouterr()
    pt.status(account)
    assert f"Trades to date   {fills}\n" in capsys.readouterr().out
    pt.compare([account])
    assert capsys.readouterr().out.rstrip().endswith(f" {fills}")


# --- a window that can never close is released, and says why ---------------
def _sessions(start, end):
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, end)]


def test_a_dead_name_with_no_price_is_abandoned_and_the_window_closes(monkeypatch):
    """The review's probe: the window opened at 01-05; DEAD was sold the next
    day and then its feed died, so it stayed in the window's names (traded
    inside it) and the window stuck at 01-05 through 06-30 with an alert every
    run. Past the gap, a name with no usable close on this bar either is
    abandoned — named — and the window moves on."""
    sleeve = {"currency": "USD", "cash": 0.0, "positions": {"LIVE": 10},
              "last_dividend_date": "2026-01-05"}
    log = [_fill("2026-01-02", "DEAD", "BUY", 5), _fill("2026-01-06", "DEAD", "SELL", 5),
           _fill("2026-01-02", "LIVE", "BUY", 10)]
    paid = pd.DataFrame({"LIVE": [0.5]}, index=pd.DatetimeIndex(["2026-03-02"]))
    monkeypatch.setattr(pt.data, "dividends", _yahoo(paid))       # never has DEAD
    alerts = _alerts(monkeypatch)
    px = pd.Series({"LIVE": 50.0})                                 # DEAD: no close
    for day in _sessions("2026-01-06", "2026-06-30"):
        pt._credit_dividends(get_region("US"), sleeve, day, log, synthetic=False,
                             px=px)
    assert sleeve["last_dividend_date"] == "2026-06-30"
    assert len(alerts) == pt.DIVIDEND_GAP_SESSIONS + 1
    _, message, kw = alerts[-1]
    assert "abandoned" in message and "no price either" in message
    assert kw["abandoned"] == ["DEAD"]
    assert [(r["ticker"], r["date"]) for r in log if r["side"] == "DIV"] == [
        ("LIVE", "2026-03-02")]


def test_a_name_that_still_has_a_price_is_never_abandoned(monkeypatch):
    """A name the market still prices is alive: only its dividend feed is
    failing, and the dividend is owed. It keeps the window open, however long."""
    sleeve = {"currency": "USD", "cash": 0.0, "positions": {"AAA": 10},
              "last_dividend_date": "2026-06-01"}
    monkeypatch.setattr(pt.data, "dividends", lambda t, s, e: pd.DataFrame())
    alerts = _alerts(monkeypatch)
    pt._credit_dividends(get_region("US"), sleeve, "2026-06-30", [], synthetic=False,
                         px=pd.Series({"AAA": 50.0}))
    assert sleeve["last_dividend_date"] == "2026-06-01"
    (_, message, kw), = alerts
    assert "stuck" in message and "abandoned" not in message and not kw["abandoned"]


def test_the_run_hands_the_credit_its_bar_and_its_account(
        account, paper_cycle, monkeypatch):
    """The abandon rule reads this bar's closes and the alert names the book:
    both come from the call in run_daily, so pin that they arrive."""
    pt.init_account(account, capital=300_000, synthetic=True,
                    allocations={"US": 1.0})
    seen = []
    real = pt._credit_dividends

    def spy(region, sleeve, today, log, synthetic, **kw):
        seen.append(kw)
        return real(region, sleeve, today, log, synthetic, **kw)

    monkeypatch.setattr(pt, "_credit_dividends", spy)
    state = paper_cycle(account, sessions=1)
    [kw] = seen
    assert kw["account"] == account
    assert isinstance(kw["px"], pd.Series)
    assert set(kw["px"].index) >= set(state["sleeves"]["US"]["positions"])
    assert kw["px"].notna().any()
