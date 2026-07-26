"""Pluggable market-data sources (yahoo / crypto / oanda / alpaca / openbb)
plus the per-symbol `auto` router.

Everything here runs offline on synthetic data or injected fake providers, so it
never needs the optional live-feed dependencies (ccxt / oandapyV20 / alpaca-py /
openbb) or a network.
"""
import pandas as pd
import pytest

from trading_algo.forex import (alpaca_data, feeds, fx_book, oanda_data,
                                 openbb_data, run_backtest)
from trading_algo.forex import pairs
from trading_algo.forex.agents import AgentPool


# --- source resolution ------------------------------------------------------
def test_sources_listed():
    assert feeds.SOURCES == ["yahoo", "crypto", "oanda", "alpaca", "openbb",
                             "frankfurter", "auto"]
    # `auto` is a routing instruction, not an adapter you can dispatch a fetch to
    assert feeds.is_provider("yahoo") and not feeds.is_provider("auto")
    assert not feeds.is_provider("nosuchfeed")


def test_resolve_source_exchange_implies_crypto():
    assert feeds.resolve_source(None, None) == "yahoo"
    assert feeds.resolve_source(None, "binance") == "crypto"   # back-compat
    assert feeds.resolve_source("oanda", None) == "oanda"


def test_resolve_source_auto():
    assert feeds.resolve_source("auto", None) == "auto"
    # an explicit --source auto is NOT overridden by --exchange (which only
    # upgrades the *default* yahoo to crypto).
    assert feeds.resolve_source("auto", "binance") == "auto"


def test_resolve_unknown_source_raises():
    with pytest.raises(SystemExit):
        feeds.resolve_source("bloomberg", None)


def test_default_universe_per_source():
    assert feeds.default_universe("oanda") == list(pairs.PAIRS)
    assert feeds.default_universe("alpaca") == pairs.EQUITY_UNIVERSE
    assert feeds.default_universe("openbb") == pairs.EQUITY_UNIVERSE
    assert "BTCUSD" in feeds.default_universe("crypto")
    assert feeds.default_universe("yahoo") == pairs.DEFAULT_UNIVERSE
    # the hf_crypto profile forces the crypto universe even under yahoo
    assert "BTCUSD" in feeds.default_universe("yahoo", "hf_crypto")
    # `auto` exists precisely for the mixed FX+crypto universe
    assert feeds.default_universe("auto") == pairs.DEFAULT_UNIVERSE
    # frankfurter is fiat-only (ECB reference currencies): FX majors, no crypto
    assert feeds.default_universe("frankfurter") == list(pairs.PAIRS)


# --- equity registry --------------------------------------------------------
def test_equities_registered_but_not_in_default_universe():
    assert pairs.EQUITY_UNIVERSE and "AAPL" in pairs.EQUITY_UNIVERSE
    aapl = pairs.get_pair("AAPL")
    assert aapl.quote == "USD" and aapl.pip == 0.01
    assert aapl.swap_long_pips == 0.0          # equity carry not modelled
    assert aapl.spread_fraction(195.0) > 0
    # equities stay OUT of the default FX+crypto universe
    assert "AAPL" not in pairs.DEFAULT_UNIVERSE


# --- OANDA symbol mapping ---------------------------------------------------
def test_oanda_instrument_mapping():
    assert oanda_data.instrument("EURUSD") == "EUR_USD"
    assert oanda_data.instrument("USDJPY") == "USD_JPY"
    assert oanda_data.OANDA_UNIVERSE == list(pairs.PAIRS)


# --- synthetic panels per source (offline; no optional deps needed) ---------
@pytest.mark.parametrize("mod,syms,tf", [
    (oanda_data, ["EURUSD", "GBPUSD"], "1h"),
    (alpaca_data, ["AAPL", "MSFT"], "1h"),
    (openbb_data, ["AAPL", "SPY"], "1d"),
])
def test_source_synthetic_panel_shape(mod, syms, tf):
    panel = mod.synthetic_panel(syms, timeframe=tf, days=4)
    assert set(panel) == set(syms)
    for df in panel.values():
        assert list(df.columns) == ["open", "high", "low", "close"]
        assert len(df) > 10


def test_feeds_load_dispatches_synthetic():
    # crypto -> minute bars in the thousands; alpaca -> equities; oanda -> FX
    crypto = feeds.load(["BTCUSD"], synthetic=True, interval="1m", source="crypto")
    assert crypto["BTCUSD"]["close"].iloc[0] > 1000
    eq = feeds.load(["AAPL"], synthetic=True, interval="1h", source="alpaca")
    assert 50 < eq["AAPL"]["close"].iloc[0] < 1000
    fx = feeds.load(["EURUSD"], synthetic=True, interval="1d", source="oanda")
    assert 0.5 < fx["EURUSD"]["close"].iloc[0] < 2.0


# --- end-to-end through the book + backtest (synthetic) ---------------------
def test_book_runs_on_alpaca_equities(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.init_account("eq", 5_000, "intraday",
                         symbols=pairs.EQUITY_UNIVERSE, source="alpaca")
    s0 = fx_book.load_state("eq")
    assert s0["source"] == "alpaca"
    fx_book.run_once("eq", synthetic=True, pool=AgentPool(max_workers=1),
                     interval="1h", source="alpaca")
    s = fx_book.load_state("eq")
    assert s["equity_history"]
    # an equity book trades equities only — no FX majors leaked in
    assert set(s["symbols"]) == set(pairs.EQUITY_UNIVERSE)


def test_init_cli_sets_source_and_universe(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.main(["--init", "--account", "fx1", "--source", "oanda", "--profile", "intraday"])
    s = fx_book.load_state("fx1")
    assert s["source"] == "oanda"
    assert set(s["symbols"]) == set(pairs.PAIRS)


def test_run_backtest_alpaca_synthetic(capsys):
    run_backtest.main(["--synthetic", "--source", "alpaca", "--bar", "1h"])
    out = capsys.readouterr().out
    assert "FX backtest [balanced]" in out
    assert "AAPL" in out               # equity universe attribution


def test_daily_yahoo_book_unchanged(tmp_path, monkeypatch):
    """Regression: the default daily path stays yahoo + FX majors, plain date key."""
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.init_account("d", 5_000, "balanced")
    s0 = fx_book.load_state("d")
    assert s0["source"] == "yahoo"
    fx_book.run_once("d", synthetic=True, pool=AgentPool(max_workers=1))
    s = fx_book.load_state("d")
    assert s["last_bar_date"] and " " not in s["last_bar_date"]
    assert "EURUSD" in s["symbols"]


# ===========================================================================
# Per-symbol routing (source="auto")
# ===========================================================================
# Every test below injects a FAKE provider through `load_routed(loader=...)`,
# so nothing here can reach a network even by accident.

def _frame(level: float, index: pd.DatetimeIndex) -> pd.DataFrame:
    """A minimal OHLC frame — routing only cares about shape and index."""
    return pd.DataFrame({"open": level, "high": level * 1.01,
                         "low": level * 0.99, "close": level}, index=index)


def _fake_loader(by_source: dict[str, object], calls: list | None = None):
    """A stand-in for feeds.load: serves whatever `by_source` says it has.

    A source mapped to an exception instance raises it (a dead provider / a
    missing credential); a source mapped to {} answers with no bars at all.
    """
    def loader(symbols, synthetic=False, interval="1d", source="yahoo",
               exchange=None, use_cache=False, min_bars=None):
        if calls is not None:
            calls.append((source, list(symbols), interval, synthetic))
        served = by_source.get(source, {})
        if isinstance(served, BaseException):
            raise served
        return {s: served[s] for s in symbols if s in served}
    return loader


# --- planning (pure, no fetch) ---------------------------------------------
def test_plan_routes_groups_by_asset_class():
    groups, unroutable = feeds.plan_routes(["EURUSD", "BTCUSD", "GBPUSD", "SPY", "TLT"])
    # crypto -> ccxt; everything else -> yahoo (the only source with a true range)
    assert groups == {"yahoo": ["EURUSD", "GBPUSD", "SPY", "TLT"], "crypto": ["BTCUSD"]}
    assert unroutable == {}


def test_plan_routes_uses_the_pairs_registry_not_a_second_mapping():
    # asset_class is the ONLY thing routing keys off — change the class, change
    # the provider. (Guards against a parallel symbol->source table creeping in.)
    assert pairs.get_pair("BTCUSD").asset_class == "crypto"
    assert feeds.routes_for("BTCUSD")[0] == "crypto"
    assert pairs.get_pair("EURUSD").asset_class == "fx"
    assert feeds.routes_for("EURUSD") == ["yahoo"]


def test_unregistered_provider_in_the_table_is_skipped_not_attempted():
    routes = {"fx": ("nosuchfeed", "yahoo")}
    assert feeds.routes_for("EURUSD", routes) == ["yahoo"]


def test_default_fx_route_avoids_the_close_only_source():
    """The default panel feeds compute_targets, whose ADX/Donchian/ATR read
    high/low — so the ECB close-only fixing is NOT the default FX signal source.
    A close-only consumer opts in explicitly (and clears require_true_range)."""
    assert feeds.routes_for("EURUSD") == ["yahoo"]
    assert "frankfurter" in feeds.SOURCES          # reachable, just not default
    opt_in = {**feeds.ROUTES, "fx": ("frankfurter", "yahoo")}
    assert feeds.routes_for("EURUSD", opt_in) == ["frankfurter", "yahoo"]


def test_daily_only_source_is_never_asked_for_intraday():
    """Frankfurter serves one ECB fixing per working day; an intraday request
    falls through to a provider that really has intraday bars."""
    opt_in = {**feeds.ROUTES, "fx": ("frankfurter", "yahoo")}
    assert feeds.routes_for("EURUSD", opt_in, interval="1d") == ["frankfurter", "yahoo"]
    assert feeds.routes_for("EURUSD", opt_in, interval="60m") == ["yahoo"]
    assert feeds.routes_for("BTCUSD", interval="60m") == ["crypto", "yahoo"]


def test_symbol_with_no_configured_provider_is_reported():
    groups, unroutable = feeds.plan_routes(["EURUSD", "BTCUSD"],
                                           routes={"fx": ("yahoo",)})
    assert groups == {"yahoo": ["EURUSD"]}
    assert "crypto" in unroutable["BTCUSD"]        # names the asset class


def test_unknown_symbol_is_reported_not_raised():
    groups, unroutable = feeds.plan_routes(["EURUSD", "DOGEUSD"])
    assert groups == {"yahoo": ["EURUSD"]}
    assert "unknown symbol" in unroutable["DOGEUSD"]


def test_daily_only_source_with_no_intraday_fallback_is_reported():
    _, unroutable = feeds.plan_routes(["EURUSD"], routes={"fx": ("frankfurter",)},
                                      interval="60m")
    assert "60m" in unroutable["EURUSD"] and "frankfurter" in unroutable["EURUSD"]


# --- fetch + merge ----------------------------------------------------------
def test_load_routed_merges_two_providers_into_one_panel():
    """The blocker this exists for: FX majors AND crypto in one book."""
    fx_idx = pd.bdate_range("2025-01-01", "2025-01-20")        # weekdays only
    cr_idx = pd.date_range("2025-01-01", "2025-01-20", freq="D")   # 24/7
    calls: list = []
    loader = _fake_loader({
        "yahoo": {"EURUSD": _frame(1.08, fx_idx), "GBPUSD": _frame(1.27, fx_idx)},
        "crypto": {"BTCUSD": _frame(60000.0, cr_idx)},
    }, calls)

    symbols = ["EURUSD", "BTCUSD", "GBPUSD"]
    panel, report = feeds.load_routed(symbols, loader=loader)

    assert list(panel) == symbols                       # request order preserved
    assert report.ok and not report.missing
    assert report.served == {"EURUSD": "yahoo", "GBPUSD": "yahoo", "BTCUSD": "crypto"}
    # each provider was called ONCE, with only its own symbols
    assert sorted((src, tuple(syms)) for src, syms, _, _ in calls) == [
        ("crypto", ("BTCUSD",)), ("yahoo", ("EURUSD", "GBPUSD"))]
    # merged on ONE calendar (fx_data._align), FX forward-filled over weekends
    idx = panel["EURUSD"].index
    assert all(df.index.equals(idx) for df in panel.values())
    assert len(idx) == len(cr_idx)
    assert list(panel["EURUSD"].columns) == ["open", "high", "low", "close"]


def test_exchange_only_reaches_the_crypto_group():
    """resolve_source upgrades ('yahoo', exchange) to 'crypto' for back-compat,
    so forwarding --exchange to an FX group would silently redirect it."""
    idx = pd.bdate_range("2025-01-01", "2025-01-20")
    loader = _fake_loader({"yahoo": {"EURUSD": _frame(1.08, idx)},
                           "crypto": {"BTCUSD": _frame(60000.0, idx)}})
    seen: dict[str, str | None] = {}

    def spy(symbols, synthetic=False, interval="1d", source="yahoo",
            exchange=None, use_cache=False, min_bars=None):
        seen[source] = exchange
        return loader(symbols, synthetic=synthetic, interval=interval,
                      source=source, exchange=exchange, use_cache=use_cache,
                      min_bars=min_bars)

    feeds.load_routed(["EURUSD", "BTCUSD"], exchange="kraken", loader=spy)
    assert seen == {"yahoo": None, "crypto": "kraken"}


def test_load_routed_normalises_mixed_timezones():
    """ccxt hands back naive UTC, OANDA/Yahoo-intraday tz-aware — pandas will
    not union the two, so the merge would explode without normalisation."""
    aware = pd.date_range("2025-01-01", periods=8, freq="h", tz="UTC")
    naive = pd.date_range("2025-01-01", periods=8, freq="h")
    loader = _fake_loader({"yahoo": {"EURUSD": _frame(1.08, aware)},
                           "crypto": {"BTCUSD": _frame(60000.0, naive)}})
    panel, report = feeds.load_routed(["EURUSD", "BTCUSD"], interval="60m",
                                      loader=loader)
    assert report.ok
    assert panel["EURUSD"].index.tz is None
    assert panel["EURUSD"].index.equals(panel["BTCUSD"].index)


def test_one_provider_down_does_not_take_out_the_others():
    """A dead crypto exchange costs you crypto, not FX."""
    idx = pd.bdate_range("2025-01-01", "2025-01-20")
    loader = _fake_loader({
        "yahoo": {"EURUSD": _frame(1.08, idx)},
        "crypto": RuntimeError("binance 503"),
    })
    panel, report = feeds.load_routed(["EURUSD", "BTCUSD"],
                                      routes={"fx": ("yahoo",), "crypto": ("crypto",)},
                                      loader=loader)
    assert list(panel) == ["EURUSD"]                    # FX survived
    assert report.served == {"EURUSD": "yahoo"}
    assert not report.ok
    assert "binance 503" in report.missing["BTCUSD"]
    assert "binance 503" in report.errors["crypto"]


def test_missing_credentials_raise_systemexit_and_fall_back():
    """Adapters signal 'no dependency / no key' with SystemExit, which is NOT an
    Exception — catching only Exception would let a keyless OANDA kill the run."""
    idx = pd.bdate_range("2025-01-01", "2025-01-20")
    loader = _fake_loader({
        "oanda": SystemExit("set OANDA_API_TOKEN ..."),
        "yahoo": {"EURUSD": _frame(1.08, idx)},
    })
    panel, report = feeds.load_routed(["EURUSD"], routes={"fx": ("oanda", "yahoo")},
                                      loader=loader)
    assert report.served == {"EURUSD": "yahoo"}         # fell through to the fallback
    assert report.ok and "EURUSD" in panel              # ...so nothing is missing
    assert "OANDA_API_TOKEN" in report.errors["oanda"]


def test_empty_response_falls_through_to_the_next_provider():
    idx = pd.bdate_range("2025-01-01", "2025-01-20")
    calls: list = []
    loader = _fake_loader({"crypto": {}, "yahoo": {"BTCUSD": _frame(60000.0, idx)}},
                          calls)
    panel, report = feeds.load_routed(["BTCUSD"], loader=loader)
    assert report.served == {"BTCUSD": "yahoo"} and report.ok
    assert [src for src, *_ in calls] == ["crypto", "yahoo"]   # in chain order


def test_exhausted_chain_reports_every_attempt():
    loader = _fake_loader({"crypto": {}, "yahoo": {}})
    panel, report = feeds.load_routed(["BTCUSD"], loader=loader)
    assert panel == {}
    why = report.missing["BTCUSD"]
    assert "crypto returned no bars" in why and "yahoo returned no bars" in why


# --- load(source="auto") wiring --------------------------------------------
def test_load_auto_prints_what_it_could_not_serve(capsys):
    """Requirement: a dropped instrument is reported, never silent."""
    idx = pd.bdate_range("2025-01-01", "2025-01-20")
    loader = _fake_loader({"yahoo": {"EURUSD": _frame(1.08, idx)}})
    panel, report = feeds.load_routed(["EURUSD", "DOGEUSD"], loader=loader)
    assert "DOGEUSD" in report.missing
    # and through the plain `load` wrapper, which has no report to hand back:
    feeds.load(["EURUSD", "DOGEUSD"], synthetic=True, source="auto")
    assert "UNSERVED" in capsys.readouterr().out


def test_load_auto_synthetic_is_one_coherent_panel():
    """Synthetic mode has no providers to route between: fabricating each group
    from its own generator would merge a decade of FX against a 3-day crypto
    stub. One panel, labelled synthetic — never a provider name (invariant #5)."""
    panel = feeds.load(pairs.DEFAULT_UNIVERSE, synthetic=True, source="auto")
    assert set(panel) == set(pairs.DEFAULT_UNIVERSE)
    idx = panel["EURUSD"].index
    assert all(df.index.equals(idx) for df in panel.values())
    assert len(idx) > 1000                       # the full 2015-2026 daily path
    _, report = feeds.load_routed(["EURUSD", "BTCUSD"], synthetic=True)
    assert set(report.served.values()) == {"synthetic"}


def test_load_auto_matches_the_single_source_panel_shape():
    """`auto` must be indistinguishable in shape from a single-source load."""
    syms = ["EURUSD", "BTCUSD"]
    one = feeds.load(syms, synthetic=True, source="yahoo")
    auto = feeds.load(syms, synthetic=True, source="auto")
    assert list(one) == list(auto)
    for s in syms:
        assert one[s].index.equals(auto[s].index)
        assert list(one[s].columns) == list(auto[s].columns)


def test_routing_never_sends_crypto_to_the_fiat_only_source():
    """The blocker in one assertion: ONE universe, two providers, correctly split.
    Frankfurter is fiat-only and ccxt has no EURUSD — neither can take the list."""
    opt_in = {**feeds.ROUTES, "fx": ("frankfurter", "yahoo")}
    groups, unroutable = feeds.plan_routes(pairs.DEFAULT_UNIVERSE, opt_in)
    assert groups["frankfurter"] == list(pairs.PAIRS)
    assert groups["crypto"] == list(pairs.CRYPTO)
    assert unroutable == {}


def test_load_frankfurter_dispatches_and_stays_close_only():
    """feeds.load must reach the adapter — and must not launder its degeneracy."""
    from trading_algo.forex import bar_quality
    panel = feeds.load(["EURUSD", "GBPUSD"], synthetic=True, source="frankfurter")
    assert set(panel) == {"EURUSD", "GBPUSD"}
    assert bar_quality.close_only_symbols(panel) == ["EURUSD", "GBPUSD"]
    df = panel["EURUSD"]
    assert (df["high"] == df["low"]).all() and (df["high"] == df["close"]).all()


def test_load_frankfurter_refuses_intraday_loudly():
    from trading_algo.forex import frankfurter_data
    with pytest.raises(frankfurter_data.FrankfurterError):
        feeds.load(["EURUSD"], synthetic=True, interval="60m", source="frankfurter")


def test_load_single_source_never_routes(monkeypatch):
    """Additive: an explicitly named source still gets the whole symbol list."""
    def boom(*a, **k):
        raise AssertionError("load_routed must not run for an explicit source")
    monkeypatch.setattr(feeds, "load_routed", boom)
    panel = feeds.load(["EURUSD", "BTCUSD"], synthetic=True, source="yahoo")
    assert set(panel) == {"EURUSD", "BTCUSD"}
