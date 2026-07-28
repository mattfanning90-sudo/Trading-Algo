"""Close-only bars: detection, refusal, per-book policy and per-symbol routing.

The condition under test is a *real* data source that publishes one price per
bar and no intrabar range — the ECB daily reference fixing behind
`frankfurter_data`. On such data the range indicators do not fail; they compute a
different statistic under the same name (ATR ~half, ADX a close-efficiency ratio,
Donchian a channel of closes), which moves the traded book while leaving every
risk/cost number looking normal.

So these tests pin three things:
  1. the *structural* detection (metadata may help, it may never be required);
  2. that a range indicator REFUSES such bars — including through the real
     agent → `compute_targets` path — unless someone explicitly opted in;
  3. that each live book's configured source can actually serve its universe,
     per symbol, and that the money layer is untouched by all of it.
"""
import numpy as np
import pandas as pd
import pytest

from trading_algo.forex import (
    bar_quality as bq,
    explain,
    feeds,
    frankfurter_data as fd,
    fx_book,
    fx_config as cfg,
    fx_data,
    fx_data_quality as dq,
    indicators as ind,
    marks,
)
from trading_algo.forex.agents import AgentPool, BreakoutAgent, PairContext, TrendAgent
from trading_algo.forex.fx_config import profile
from trading_algo.forex.fx_strategy import compute_targets
from trading_algo.forex.pairs import DEFAULT_UNIVERSE, get_pair

FX_LEGS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"]
CRYPTO_LEGS = ["BTCUSD", "ETHUSD", "SOLUSD"]


# ---------------------------------------------------------------------------
# Fixtures: a true-OHLC panel and its flattened twin (identical closes)
# ---------------------------------------------------------------------------
def _flatten(df: pd.DataFrame, keep_attrs: bool = True) -> pd.DataFrame:
    """Same closes, no range — exactly what one daily fixing per bar looks like."""
    out = pd.DataFrame({f: df["close"] for f in ("open", "high", "low", "close")})
    if keep_attrs:
        out.attrs.update(fd.PANEL_ATTRS)
    return out


@pytest.fixture
def real_panel():
    return fx_data.synthetic_panel(["EURUSD", "GBPUSD", "BTCUSD"])


@pytest.fixture
def flat_panel(real_panel):
    return {s: _flatten(df) for s, df in real_panel.items()}


@pytest.fixture
def mixed_panel(real_panel):
    """The panel this whole feature exists for: close-only FX + real crypto bars."""
    return {"EURUSD": _flatten(real_panel["EURUSD"]),
            "GBPUSD": _flatten(real_panel["GBPUSD"]),
            "BTCUSD": real_panel["BTCUSD"]}


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def pool():
    return AgentPool(max_workers=1)


# ---------------------------------------------------------------------------
# 1. Detection — structural first, metadata only corroborating
# ---------------------------------------------------------------------------
def test_detection_is_structural_not_metadata(real_panel, flat_panel):
    assert bq.has_true_range(real_panel["EURUSD"]) is True
    assert bq.has_true_range(flat_panel["EURUSD"]) is False
    # ...and it survives a round-trip that loses `.attrs` (parquet, a dict
    # literal, a reconstructed frame): the answer must not depend on the tag.
    stripped = _flatten(real_panel["EURUSD"], keep_attrs=False)
    assert not stripped.attrs
    assert bq.has_true_range(stripped) is False


def test_metadata_alone_is_enough_when_structure_cannot_tell(real_panel):
    """A source that declares itself close-only is believed even if some bar
    happens to differ — the two signals may only ADD the verdict, never remove
    it."""
    tagged = real_panel["EURUSD"].copy()
    tagged.attrs.update(fd.PANEL_ATTRS)
    assert bq.has_true_range(tagged) is False


def test_a_single_flat_bar_is_not_close_only(real_panel):
    """A quiet/halted bar, or a forward-filled row on a mixed FX+crypto calendar
    (which copies a REAL high/low forward), must never be mistaken for a
    close-only source — that would freeze healthy instruments out of the book."""
    df = real_panel["EURUSD"].copy()
    for col in ("open", "high", "low"):
        df.iloc[-40:, df.columns.get_loc(col)] = df["close"].iloc[-40:]
    assert bq.has_true_range(df) is True


def test_warmup_nans_are_not_a_refusal():
    """All-NaN is 'no data', not 'no range' — this gate must not turn an empty
    warm-up window into a refusal."""
    idx = pd.bdate_range("2020-01-01", periods=10)
    nan = pd.Series(np.nan, index=idx)
    assert bq.series_has_true_range(nan, nan) is True


def test_mixed_panel_is_flagged_per_symbol(mixed_panel):
    """Detection is PER SYMBOL: a whole-panel boolean built with `all()` would
    answer 'not close-only' for exactly the mixed panel that needs handling."""
    assert bq.close_only_symbols(mixed_panel) == ["EURUSD", "GBPUSD"]


# ---------------------------------------------------------------------------
# 2. Refusal — at the indicators, so no consumer can forget to ask
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("call", [
    lambda d: ind.true_range(d["high"], d["low"], d["close"]),
    lambda d: ind.atr(d["high"], d["low"], d["close"], 14),
    lambda d: ind.adx(d["high"], d["low"], d["close"], 14),
    lambda d: ind.donchian(d["high"], d["low"], 55),
])
def test_range_indicators_refuse_close_only_bars(flat_panel, call):
    with pytest.raises(bq.CloseOnlyBarsError) as e:
        call(flat_panel["EURUSD"])
    msg = str(e.value)
    assert "close-only" in msg and "docs/DATA_FEEDS.md" in msg   # names the fix


def test_close_only_indicators_on_real_bars_are_untouched(real_panel):
    """The guard must be inert on real data — it is a data check, not a filter."""
    d = real_panel["EURUSD"]
    assert float(ind.atr(d["high"], d["low"], d["close"], 14).iloc[-1]) > 0
    assert 0 <= float(ind.adx(d["high"], d["low"], d["close"], 14).iloc[-1]) <= 100
    upper, _ = ind.donchian(d["high"], d["low"], 55)
    assert upper.notna().any()


def test_close_derived_indicators_are_unaffected(real_panel, flat_panel):
    """The honest residual: everything that reads only closes is bit-identical,
    which is why a close-only source is fine for marking and useless for range."""
    r, f = real_panel["EURUSD"], flat_panel["EURUSD"]
    for fn in (lambda c: ind.ema(c, 20), lambda c: ind.rsi(c, 14),
               lambda c: ind.roc(c, 60), lambda c: ind.bollinger_z(c, 20),
               lambda c: ind.realized_vol(c, 30)):
        assert fn(r["close"]).equals(fn(f["close"]))


def test_agents_and_the_single_weight_function_refuse(flat_panel, pool):
    """The guard reaches the real decision path, not just the primitives."""
    ctx = PairContext(get_pair("EURUSD"))
    p = profile("balanced")
    for agent in (TrendAgent(), BreakoutAgent()):
        with pytest.raises(bq.CloseOnlyBarsError):
            agent.generate(flat_panel["EURUSD"], ctx, p)
    with pytest.raises(bq.CloseOnlyBarsError):
        compute_targets(flat_panel, p, pool=pool)
    with pytest.raises(bq.CloseOnlyBarsError):
        explain.decide_and_explain(flat_panel, p, pool=pool)


def test_opt_in_is_explicit_and_scoped(flat_panel):
    d = flat_panel["EURUSD"]
    assert bq.close_only_acknowledged() is False
    with bq.allow_close_only():
        assert bq.close_only_acknowledged() is True
        assert float(ind.atr(d["high"], d["low"], d["close"], 14).iloc[-1]) > 0
    assert bq.close_only_acknowledged() is False          # never leaks out
    with pytest.raises(bq.CloseOnlyBarsError):
        ind.atr(d["high"], d["low"], d["close"], 14)


def test_the_degeneracy_itself_is_pinned(real_panel, flat_panel):
    """Document the failure numerically, in code: ATR roughly halves and ADX does
    NOT collapse to zero — with high == low the directional moves become the two
    signs of Δclose, so DI+ + DI− is identically 100 and ADX is 100x the smoothed
    efficiency ratio of closes. A plausible-looking number, quietly different."""
    r, f = real_panel["EURUSD"], flat_panel["EURUSD"]
    with bq.allow_close_only():
        atr_flat = ind.atr(f["high"], f["low"], f["close"], 14)
        tr_flat = ind.true_range(f["high"], f["low"], f["close"])
        adx_flat = ind.adx(f["high"], f["low"], f["close"], 14)
        # true range collapses to |Δclose| exactly
        np.testing.assert_allclose(tr_flat.iloc[1:],
                                   f["close"].diff().abs().iloc[1:], rtol=1e-12)
        # DI+ + DI- == 100 exactly (the reason ADX stays plausible)
        up, down = f["high"].diff(), -f["low"].diff()
        plus = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=f.index)
        minus = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=f.index)
        atr_w = tr_flat.ewm(alpha=1 / 14, adjust=False).mean()
        di_sum = (100 * plus.ewm(alpha=1 / 14, adjust=False).mean() / atr_w
                  + 100 * minus.ewm(alpha=1 / 14, adjust=False).mean() / atr_w)
        np.testing.assert_allclose(di_sum.iloc[20:], 100.0, rtol=1e-9)
    atr_real = ind.atr(r["high"], r["low"], r["close"], 14)
    ratio = float(atr_flat.iloc[100:].mean() / atr_real.iloc[100:].mean())
    assert 0.35 < ratio < 0.75                    # ~half, not zero and not equal
    assert float(adx_flat.iloc[100:].mean()) > 5   # emphatically NOT zero


# ---------------------------------------------------------------------------
# 3. The money layer is untouched (why "exclude" and "allow" are even options)
# ---------------------------------------------------------------------------
def test_marking_and_costs_are_exact_on_close_only_bars(real_panel, flat_panel):
    """A fixing is a perfectly good MARK; it is only a bad SIGNAL input."""
    assert fx_data.closes(real_panel).equals(fx_data.closes(flat_panel))
    px = fx_data.closes(flat_panel).iloc[-1]
    pair = get_pair("EURUSD")
    assert (marks.cost_fraction(0.1, pair, float(px["EURUSD"]))
            == marks.cost_fraction(0.1, pair,
                                   float(fx_data.closes(real_panel).iloc[-1]["EURUSD"])))


# ---------------------------------------------------------------------------
# 4. The per-book policy at the pre-signal gate
# ---------------------------------------------------------------------------
def test_every_profile_refuses_by_default():
    for name in cfg.profile_names():
        assert profile(name).close_only_signals == bq.REFUSE


def test_a_bad_policy_name_fails_at_construction():
    with pytest.raises(ValueError):
        cfg.FXParams(close_only_signals="ignore")


def test_a_bad_policy_name_fails_even_on_healthy_data(real_panel):
    """A typo must fail on every run, not only on the days a close-only
    instrument happens to be in the panel."""
    with pytest.raises(ValueError):
        dq.assess_panel(real_panel, policy="ignore")


def test_assess_panel_refuse_names_the_instruments(mixed_panel):
    with pytest.raises(bq.CloseOnlyBarsError) as e:
        dq.assess_panel(mixed_panel, policy=bq.REFUSE)
    assert "EURUSD" in str(e.value) and "GBPUSD" in str(e.value)


def test_assess_panel_exclude_flags_exactly_the_flat_symbols(mixed_panel):
    report = dq.assess_panel(mixed_panel, policy=bq.EXCLUDE)
    assert report.excluded == {"EURUSD", "GBPUSD"}
    assert report.close_only == {"EURUSD", "GBPUSD"}
    assert bq.CLOSE_ONLY_REASON in report.reasons["EURUSD"]


def test_assess_panel_allow_records_but_does_not_exclude(mixed_panel):
    report = dq.assess_panel(mixed_panel, policy=bq.ALLOW)
    assert report.excluded == set()
    assert report.close_only == {"EURUSD", "GBPUSD"}


def test_assess_panel_still_does_the_close_checks(real_panel):
    """The bar-capability check is ADDED to the existing gate, not instead of it."""
    panel = {s: df.copy() for s, df in real_panel.items()}
    stuck = float(panel["GBPUSD"]["close"].iloc[-(dq.STALE_BARS + 6)])
    panel["GBPUSD"].iloc[-(dq.STALE_BARS + 5):,
                         panel["GBPUSD"].columns.get_loc("close")] = stuck
    report = dq.assess_panel(panel, policy=bq.REFUSE)   # no close-only symbols
    assert "GBPUSD" in report.excluded and report.close_only == set()


def test_closes_alone_cannot_see_the_problem(mixed_panel):
    """Why `assess_panel` exists at all: a close-only source's CLOSES are
    healthy, so the closes-matrix gate correctly reports nothing wrong."""
    assert dq.assess(fx_data.closes(mixed_panel)).excluded == set()


# ---------------------------------------------------------------------------
# 5. The live book path
# ---------------------------------------------------------------------------
def _serve(monkeypatch, panel):
    monkeypatch.setattr(fx_book, "_panel", lambda *a, **k: panel)


def test_book_refuses_and_does_not_trade(isolated_state, pool, monkeypatch,
                                         mixed_panel):
    fx_book.init_account("matt", 5_000, "balanced")
    _serve(monkeypatch, mixed_panel)
    with pytest.raises(bq.CloseOnlyBarsError):
        fx_book.run_once("matt", synthetic=True, pool=pool)
    state = fx_book.load_state("matt")
    assert state["last_bar_date"] is None      # nothing marked, nothing persisted
    assert state["trades"] == [] and state["positions"] == {}
    assert state["equity"] == 5_000


def test_book_exclude_freezes_the_flat_legs_and_keeps_trading(
        isolated_state, pool, monkeypatch, mixed_panel):
    fx_book.init_account("matt", 5_000, "balanced",
                         close_only_signals=bq.EXCLUDE)
    _serve(monkeypatch, mixed_panel)
    fx_book.run_once("matt", synthetic=True, pool=pool)
    state = fx_book.load_state("matt")
    assert state["last_bar_date"] is not None                    # the book ran
    assert set(state["data_quality"]["excluded"]) == {"EURUSD", "GBPUSD"}
    assert state["data_quality"]["close_only"] == ["EURUSD", "GBPUSD"]
    assert bq.CLOSE_ONLY_REASON in state["data_quality"]["reasons"]["EURUSD"]
    # the close-only legs are never scored, so they can never be held
    assert not ({"EURUSD", "GBPUSD"} & set(state["positions"]))
    assert "BTCUSD" in state.get("decisions", {})
    # ...but a fixing is still a good MARK: excluded from scoring, not from
    # marking (unlike a stale/dead price, whose mark IS the thing to distrust)
    assert {"EURUSD", "GBPUSD"} <= set(state["last_close"])


def test_book_allow_trades_them_and_says_so(isolated_state, pool, monkeypatch,
                                            mixed_panel, capsys):
    fx_book.init_account("matt", 5_000, "balanced", close_only_signals=bq.ALLOW)
    _serve(monkeypatch, mixed_panel)
    fx_book.run_once("matt", synthetic=True, pool=pool)
    state = fx_book.load_state("matt")
    assert state["data_quality"]["excluded"] == []
    assert state["data_quality"]["close_only"] == ["EURUSD", "GBPUSD"]
    assert state["data_quality"]["close_only_policy"] == bq.ALLOW
    assert "EURUSD" in state["decisions"]           # scored on degraded bars
    assert "CLOSE-ONLY" in capsys.readouterr().out  # and it is on the console
    assert bq.close_only_acknowledged() is False    # the opt-in did not leak
    # the decision book must not print a close-derived ADX under the name ADX
    why = state["decisions"]["EURUSD"]
    assert why["bar_quality"] == "close_only"
    assert "close-to-close" in why["indicator_basis"]["adx"]
    assert "close-derived" in why["text"]
    assert "bar_quality" not in state["decisions"]["BTCUSD"]   # real bars: clean


def test_a_healthy_book_is_bit_for_bit_unchanged(isolated_state, pool,
                                                 monkeypatch, real_panel):
    """The whole mechanism must be inert on a normal OHLC book."""
    fx_book.init_account("matt", 5_000, "balanced")
    _serve(monkeypatch, real_panel)
    fx_book.run_once("matt", synthetic=True, pool=pool)
    state = fx_book.load_state("matt")
    assert state["positions"] and state["trades"]
    assert state["data_quality"]["close_only"] == []
    assert state["data_quality"]["close_only_policy"] == bq.REFUSE


def test_one_refusing_book_does_not_stop_the_others(isolated_state, pool,
                                                    monkeypatch, mixed_panel,
                                                    real_panel):
    fx_book.init_account("bad", 5_000, "balanced")            # refuses
    fx_book.init_account("good", 5_000, "balanced",
                         close_only_signals=bq.EXCLUDE)
    panels = {"bad": mixed_panel, "good": real_panel}
    # serve each book its own panel (run_all iterates accounts alphabetically)
    holder = {"acct": None}
    real_run = fx_book._run_once_locked

    def spy(account, *a, **k):
        holder["acct"] = account
        return real_run(account, *a, **k)

    monkeypatch.setattr(fx_book, "_run_once_locked", spy)
    monkeypatch.setattr(fx_book, "_panel",
                        lambda *a, **k: panels[holder["acct"]])
    fx_book.run_all(synthetic=True, pool=pool)
    assert fx_book.load_state("bad")["last_bar_date"] is None      # never traded
    assert fx_book.load_state("good")["last_bar_date"] is not None  # still ran


def test_book_policy_overrides_the_profile_default(isolated_state):
    fx_book.init_account("x", 5_000, "balanced", close_only_signals="allow")
    assert fx_book.load_state("x")["close_only_signals"] == "allow"
    fx_book.init_account("y", 5_000, "balanced")
    assert "close_only_signals" not in fx_book.load_state("y")   # inherits refuse


# ---------------------------------------------------------------------------
# 6. Per-symbol routing for the REAL mixed universe
# ---------------------------------------------------------------------------
def test_configured_book_sources():
    """The decision, pinned: matt/partner route per symbol; daytrader cannot
    (frankfurter is daily-only) and stays on a source that serves 60m bars."""
    assert cfg.ACCOUNTS["matt"]["source"] == feeds.AUTO
    assert cfg.ACCOUNTS["partner"]["source"] == feeds.AUTO
    assert cfg.ACCOUNTS["daytrader"]["source"] == "yahoo"
    assert cfg.ACCOUNTS["multiasset"]["source"] == "yahoo"


def test_an_existing_book_keeps_its_stored_source(isolated_state, pool,
                                                  monkeypatch, real_panel):
    """`cfg.ACCOUNTS` sets the default for a NEW book; it must never reach in and
    re-source a book that already exists. Switching one is a human passing
    `--source` once — visible, and reversible the same way."""
    fx_book.init_account("matt", 5_000, "balanced", source="yahoo")
    _serve(monkeypatch, real_panel)
    fx_book.run_once("matt", synthetic=True, pool=pool)
    assert fx_book.load_state("matt")["source"] == "yahoo"
    fx_book.run_once("matt", synthetic=True, pool=pool, source=feeds.AUTO)
    assert fx_book.load_state("matt")["source"] == feeds.AUTO      # and it sticks


def test_the_mixed_universe_is_routed_per_symbol():
    groups, unroutable = feeds.plan_routes(DEFAULT_UNIVERSE)
    assert not unroutable                       # nothing quietly dropped
    assert groups["yahoo"] == FX_LEGS           # bar source for FX signals
    assert groups["crypto"] == CRYPTO_LEGS      # real exchange data via ccxt


def test_crypto_is_never_asked_of_a_fiat_only_source():
    for sym in CRYPTO_LEGS:
        assert "frankfurter" not in feeds.routes_for(sym)
        assert fd.supported(sym) is False


def test_routed_load_asks_each_provider_only_for_its_own_symbols():
    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_loader(symbols, synthetic=False, interval="1d", source="yahoo",
                    exchange=None, use_cache=False, min_bars=None):
        calls.append((source, tuple(symbols)))
        panel = fx_data.synthetic_panel(list(symbols))
        if source == "frankfurter":              # would be close-only if used
            panel = {s: _flatten(df) for s, df in panel.items()}
        return panel

    panel, report = feeds.load_routed(DEFAULT_UNIVERSE, loader=fake_loader)
    # exactly two requests — one per provider, each with only its own symbols
    assert sorted(calls) == sorted([("yahoo", tuple(FX_LEGS)),
                                    ("crypto", tuple(CRYPTO_LEGS))])
    assert list(panel) == DEFAULT_UNIVERSE and report.ok
    assert report.served["BTCUSD"] == "crypto" and report.served["EURUSD"] == "yahoo"
    # ...and what comes back is signal-grade: no leg lost its range
    assert bq.close_only_symbols(panel) == []


def test_a_routed_book_runs_end_to_end(isolated_state, pool):
    """The configured default, exercised through feeds (offline): a book opened
    on `auto` fetches, decides and trades without any special-casing."""
    fx_book.init_defaults(synthetic=True)
    assert fx_book.load_state("matt")["source"] == feeds.AUTO
    fx_book.run_once("matt", synthetic=True, pool=pool)
    state = fx_book.load_state("matt")
    assert state["source"] == feeds.AUTO and state["universe_locked"] is False
    assert state["symbols"] == list(DEFAULT_UNIVERSE)
    assert state["positions"] and state["trades"]
    assert state["data_quality"]["close_only"] == []


def test_routed_book_keeps_picking_up_new_default_instruments(
        isolated_state, pool, monkeypatch, real_panel):
    """A book stored as `auto` must merge newly-added DEFAULT_UNIVERSE names the
    same way a yahoo book does — `auto` trades exactly that universe."""
    fx_book.init_account("matt", 5_000, "balanced", source=feeds.AUTO)
    state = fx_book.load_state("matt")
    state["symbols"] = ["EURUSD"]                    # an older, shorter universe
    fx_book.save_state("matt", state)
    _serve(monkeypatch, real_panel)
    fx_book.run_once("matt", synthetic=True, pool=pool)
    assert fx_book.load_state("matt")["symbols"] == list(DEFAULT_UNIVERSE)


# ---------------------------------------------------------------------------
# 7. Frankfurter cannot serve the intraday book — loudly
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bar", ["60m", "1h", "15m", "1m"])
def test_the_daily_only_source_refuses_intraday(bar):
    with pytest.raises(fd.FrankfurterError) as e:
        feeds.load(["EURUSD"], source="frankfurter", interval=bar)
    assert "daily" in str(e.value)
    # routing never even offers it for an intraday request
    assert "frankfurter" not in feeds.routes_for(
        "EURUSD", routes={"fx": ("frankfurter", "yahoo")}, interval=bar)


def test_daily_only_source_is_still_offered_for_daily():
    assert feeds.routes_for("EURUSD", routes={"fx": ("frankfurter",)},
                            interval="1d") == ["frankfurter"]


def test_the_ecb_source_is_close_only_end_to_end():
    """Everything above assumes the adapter really does return range-less bars —
    check that against the adapter itself, offline."""
    panel = feeds.load(["EURUSD", "GBPUSD"], source="frankfurter", synthetic=True)
    assert bq.close_only_symbols(panel) == ["EURUSD", "GBPUSD"]
    for df in panel.values():
        assert (df["high"] == df["close"]).all() and (df["low"] == df["close"]).all()
