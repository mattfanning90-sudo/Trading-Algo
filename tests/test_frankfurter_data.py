"""Frankfurter adapter — ECB reference rates, keyless, daily, CLOSE-ONLY.

Everything here runs offline. The fixtures below are hand-written in the
documented Frankfurter response shape and the HTTP transport is injected, so no
test ever makes a real call; an autouse guard turns any accidental socket use
into a hard failure.

What these tests pin down, in order of how expensive a silent break would be:
  1. **Direction.** EURUSD is USD-per-EUR, USDJPY is JPY-per-USD. An inversion
     here would negate every P&L downstream and nothing else would notice.
  2. **Crosses** derived from two ECB rates stay exactly consistent with the
     majors they are built from.
  3. **Refusals are loud** — crypto, non-ECB currencies, intraday timeframes and
     malformed payloads raise instead of returning an empty/ffilled panel.
  4. **Close-only is visible** — the degenerate bar is flagged, detectable, and
     refusable, never disguised as a candle.
"""
import json

import pandas as pd
import pytest

from trading_algo.forex import bar_quality
from trading_algo.forex import frankfurter_data as fd
from trading_algo.forex import pairs
from trading_algo.forex.pairs import Pair

# ---------------------------------------------------------------------------
# Fixtures: real ECB reference rates (EUR base), early January 2024.
# The calendar is deliberately realistic — 1 Jan is a TARGET holiday and 6/7 Jan
# are a weekend, so the series jumps 05 -> 08 with nothing in between.
# ---------------------------------------------------------------------------
RATES = {
    "2024-01-02": {"USD": 1.0956, "JPY": 155.15, "GBP": 0.86790, "AUD": 1.6216,
                   "CAD": 1.4612, "CHF": 0.9318, "NZD": 1.7513},
    "2024-01-03": {"USD": 1.0919, "JPY": 156.02, "GBP": 0.86465, "AUD": 1.6293,
                   "CAD": 1.4581, "CHF": 0.9337, "NZD": 1.7563},
    "2024-01-04": {"USD": 1.0953, "JPY": 158.32, "GBP": 0.86213, "AUD": 1.6335,
                   "CAD": 1.4614, "CHF": 0.9345, "NZD": 1.7595},
    "2024-01-05": {"USD": 1.0921, "JPY": 157.55, "GBP": 0.86118, "AUD": 1.6301,
                   "CAD": 1.4590, "CHF": 0.9302, "NZD": 1.7548},
    "2024-01-08": {"USD": 1.0947, "JPY": 158.44, "GBP": 0.85955, "AUD": 1.6338,
                   "CAD": 1.4623, "CHF": 0.9288, "NZD": 1.7581},
}

TIMESERIES_BODY = json.dumps({
    "amount": 1.0, "base": "EUR",
    "start_date": "2024-01-02", "end_date": "2024-01-08",
    "rates": RATES,
})

LATEST_BODY = json.dumps({
    "amount": 1.0, "base": "EUR", "date": "2024-01-08",
    "rates": RATES["2024-01-08"],
})


class Transport:
    """Injectable `fetch`: records the URLs asked for, replays a fixture."""

    def __init__(self, body: str = TIMESERIES_BODY) -> None:
        self.body, self.urls = body, []

    def __call__(self, url: str) -> str:
        self.urls.append(url)
        return self.body


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Any real socket use is a test bug, not a flaky network."""
    def boom(*a, **k):                      # pragma: no cover - only on failure
        raise AssertionError("test attempted a real HTTP call")
    monkeypatch.setattr(fd.urllib.request, "urlopen", boom)


def load(symbols, **kw):
    t = Transport(kw.pop("body", TIMESERIES_BODY))
    panel = fd.load_ohlcv(symbols, fetch=t, **kw)
    return panel, t


# ---------------------------------------------------------------------------
# 1. Direction — the expensive silent bug
# ---------------------------------------------------------------------------
def test_pair_direction_worked_example():
    """EURUSD = USD per EUR; USDJPY = JPY per USD; AUDUSD = USD per AUD."""
    panel, _ = load(["EURUSD", "USDJPY", "AUDUSD", "USDCAD"])
    day = pd.Timestamp("2024-01-02")

    # EUR is the request base, so EURUSD is the raw ECB quote, untouched.
    assert panel["EURUSD"]["close"].loc[day] == pytest.approx(1.0956, abs=1e-12)
    # A cross through EUR: 155.15 JPY/EUR ÷ 1.0956 USD/EUR = 141.61 JPY/USD.
    assert panel["USDJPY"]["close"].loc[day] == pytest.approx(155.15 / 1.0956, rel=1e-12)
    assert panel["USDJPY"]["close"].loc[day] == pytest.approx(141.61, abs=0.01)
    # 1.0956 USD/EUR ÷ 1.6216 AUD/EUR = 0.6756 USD/AUD.
    assert panel["AUDUSD"]["close"].loc[day] == pytest.approx(1.0956 / 1.6216, rel=1e-12)
    assert panel["USDCAD"]["close"].loc[day] == pytest.approx(1.4612 / 1.0956, rel=1e-12)


def test_every_major_lands_in_a_plausible_band():
    """An inverted quote would still be a valid float — the band catches it."""
    band = {"EURUSD": (0.9, 1.6), "GBPUSD": (1.0, 1.8), "USDJPY": (80.0, 200.0),
            "AUDUSD": (0.5, 0.95), "USDCAD": (1.1, 1.6), "USDCHF": (0.7, 1.2),
            "NZDUSD": (0.5, 0.9)}
    panel, _ = load(list(band))
    for sym, (lo, hi) in band.items():
        px = float(panel[sym]["close"].iloc[-1])
        assert lo < px < hi, f"{sym} = {px} looks inverted or mis-scaled"


def test_reciprocal_pairs_are_exact_inverses(monkeypatch):
    """USDCHF derived here must be 1/CHFUSD — a scale slip would break this."""
    monkeypatch.setitem(pairs.ALL_PAIRS, "CHFUSD",
                        Pair("CHFUSD", "CHF", "USD", "CHFUSD=X", 0.0001, 1.0, 0.0, 0.0))
    panel, _ = load(["USDCHF", "CHFUSD"])
    prod = panel["USDCHF"]["close"] * panel["CHFUSD"]["close"]
    assert (prod - 1.0).abs().max() < 1e-12


# ---------------------------------------------------------------------------
# 2. Crosses computed from two ECB rates
# ---------------------------------------------------------------------------
def test_cross_is_consistent_with_its_legs():
    """EURJPY == EURUSD x USDJPY exactly (all three come from one EUR base)."""
    panel, _ = load(["EURUSD", "USDJPY", "EURJPY", "GBPJPY", "EURGBP"])
    chained = panel["EURUSD"]["close"] * panel["USDJPY"]["close"]
    assert (panel["EURJPY"]["close"] - chained).abs().max() < 1e-9
    day = pd.Timestamp("2024-01-03")
    # GBPJPY = (JPY per EUR) / (GBP per EUR) = 156.02 / 0.86465
    assert panel["GBPJPY"]["close"].loc[day] == pytest.approx(156.02 / 0.86465, rel=1e-12)
    # EURGBP is the raw quote (base leg is the request base).
    assert panel["EURGBP"]["close"].loc[day] == pytest.approx(0.86465, abs=1e-12)


def test_one_request_serves_the_whole_universe():
    """Crosses are derived locally, so N symbols cost ONE call — and that is why
    the cross and its legs cannot disagree."""
    _, t = load(["EURUSD", "USDJPY", "EURJPY", "AUDUSD", "GBPUSD"])
    assert len(t.urls) == 1
    assert "base=EUR" in t.urls[0]


# ---------------------------------------------------------------------------
# 3. Refusals — loud, never an empty panel
# ---------------------------------------------------------------------------
def test_crypto_symbols_are_refused_with_a_route_hint():
    with pytest.raises(fd.FrankfurterError) as e:
        load(["EURUSD", "BTCUSD", "SOLUSD"])
    msg = str(e.value)
    assert "BTCUSD" in msg and "SOLUSD" in msg
    assert "ccxt" in msg                       # tells the caller where to route it
    assert not fd.supported("BTCUSD")


def test_equities_are_refused():
    assert not fd.supported("AAPL") and not fd.supported("TLT")
    assert "not fiat FX" in fd.unsupported_reason("AAPL")
    with pytest.raises(fd.FrankfurterError):
        load(["AAPL"])


def test_currency_outside_the_ecb_set_is_refused(monkeypatch):
    # RUB (suspended 2022) and HRK (withdrawn 2023) are deliberately absent.
    assert not fd.is_ecb_currency("RUB") and not fd.is_ecb_currency("HRK")
    assert fd.is_ecb_currency("usd") and fd.is_ecb_currency("TRY")
    monkeypatch.setitem(pairs.ALL_PAIRS, "USDRUB",
                        Pair("USDRUB", "USD", "RUB", "USDRUB=X", 0.0001, 10.0, 0.0, 0.0))
    assert "RUB" in fd.unsupported_reason("USDRUB")
    with pytest.raises(fd.FrankfurterError, match="RUB"):
        load(["USDRUB"])


@pytest.mark.parametrize("tf", ["60m", "1h", "15m", "1m", "1wk"])
def test_intraday_timeframe_is_refused(tf):
    """The 60m daytrader book cannot be fed from a daily fixing — say so."""
    with pytest.raises(fd.FrankfurterError, match="daily"):
        load(["EURUSD"], timeframe=tf)
    with pytest.raises(fd.FrankfurterError, match="daily"):
        fd.synthetic_panel(["EURUSD"], timeframe=tf)


def test_unknown_symbol_is_refused():
    assert not fd.supported("XYZABC")
    with pytest.raises(fd.FrankfurterError):
        load(["XYZABC"])


def test_empty_request_is_refused():
    with pytest.raises(fd.FrankfurterError):
        load([])


# ---------------------------------------------------------------------------
# 3b. Malformed / empty responses
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("body,needle", [
    ("<html>502 Bad Gateway</html>", "non-JSON"),
    ('{"error": "bad currency"}', "'rates'"),
    ('{"amount":1,"base":"EUR","rates":{}}', "no rates"),
    ('[1,2,3]', "'rates'"),
    ('{"amount":1,"base":"EUR","rates":{"2024-01-02":[1,2]}}', "unexpected"),
    ('{"amount":1,"base":"EUR","rates":{"2024-01-02":{"USD":1.09},"x":[1]}}',
     "expected an object"),
    ('{"amount":1,"base":"EUR","rates":{"2024-01-02":{"USD":"n/a"}}}', "not a number"),
    ('{"amount":1,"base":"EUR","rates":{"2024-01-02":{"USD":true}}}', "not a number"),
    ('{"amount":1,"base":"EUR","rates":{"2024-01-02":{"USD":null}}}', "not a number"),
    # a flat ccy -> rate body is the /latest envelope: the server ignored the
    # date range we asked for, so one bar must not pass as a warm-up window
    (LATEST_BODY, "ignored the requested date range"),
])
def test_malformed_payload_raises_clearly(body, needle):
    with pytest.raises(fd.FrankfurterError) as e:
        fd._parse_rates(body)
    assert needle in str(e.value)


def test_missing_currency_column_raises_not_nan():
    """If the server omits a leg we asked for, fail — don't emit a NaN column."""
    body = json.dumps({"amount": 1.0, "base": "EUR",
                       "rates": {"2024-01-02": {"USD": 1.0956}}})
    with pytest.raises(fd.FrankfurterError, match="no JPY column"):
        load(["USDJPY"], body=body)


def test_server_side_rebase_is_refused():
    """A base we didn't ask for would invert/scale everything — refuse it."""
    body = json.dumps({"amount": 1.0, "base": "USD",
                       "rates": {"2024-01-02": {"JPY": 141.6, "EUR": 0.9127}}})
    with pytest.raises(fd.FrankfurterError, match="base="):
        load(["USDJPY"], body=body)


def test_amount_is_honoured():
    """`amount=100` quotes per 100 units; ignoring it scales every price by 100."""
    body = json.dumps({"amount": 100.0, "base": "EUR",
                       "rates": {"2024-01-02": {"USD": 109.56}}})
    _, parsed = fd._parse_rates(body)
    assert parsed["2024-01-02"]["USD"] == pytest.approx(1.0956)


def test_http_failure_is_loud_and_actionable(monkeypatch):
    """A dead endpoint must name the URL and the v1 fallback, not return {}."""
    import urllib.error

    def raise_404(*a, **k):
        raise urllib.error.HTTPError("http://x", 404, "Not Found", {}, None)
    monkeypatch.setattr(fd.urllib.request, "urlopen", raise_404)
    with pytest.raises(fd.FrankfurterError) as e:
        fd._http_get("https://api.frankfurter.dev/v2/rates?base=EUR")
    assert "404" in str(e.value) and "FRANKFURTER_URL" in str(e.value)

    def raise_dns(*a, **k):
        raise urllib.error.URLError("Name or service not known")
    monkeypatch.setattr(fd.urllib.request, "urlopen", raise_dns)
    with pytest.raises(fd.FrankfurterError, match="request failed"):
        fd._http_get("https://api.frankfurter.dev/v2/rates")


def test_non_https_url_is_refused():
    """Market data comes off the wire — a stray file:// URL must not be opened."""
    with pytest.raises(fd.FrankfurterError, match="non-https"):
        fd._http_get("file:///etc/passwd")
    with pytest.raises(fd.FrankfurterError, match="non-https"):
        fd._http_get("http://api.frankfurter.dev/v2/rates")


# ---------------------------------------------------------------------------
# 4. Close-only honesty
# ---------------------------------------------------------------------------
def test_close_only_is_flagged_and_detectable():
    """The adapter's own contract. The detector itself (structural, source-
    agnostic) is `bar_quality`'s — see tests/test_close_only_bars.py."""
    panel, _ = load(["EURUSD", "USDJPY"])
    for sym, df in panel.items():
        assert list(df.columns) == ["open", "high", "low", "close"]
        assert df.attrs["close_only"] is True
        assert "ECB" in df.attrs["provenance"]
        # the honest degenerate bar: one observed price, zero range
        assert (df["high"] - df["low"]).abs().max() == 0.0
        assert df["open"].equals(df["close"]), sym
    assert bar_quality.close_only_symbols(panel) == ["EURUSD", "USDJPY"]
    # ...and the verdict survives a round-trip that drops `.attrs`
    stripped = pd.DataFrame(panel["EURUSD"].to_dict())
    assert stripped.attrs == {}
    assert not bar_quality.has_true_range(stripped)


# ---------------------------------------------------------------------------
# 5. Calendar, history window and URL shape
# ---------------------------------------------------------------------------
def test_ecb_gaps_are_left_as_gaps():
    """1 Jan is a TARGET holiday and 6/7 Jan a weekend — no invented rows, no
    forward-filled fixings on days the ECB never published."""
    panel, _ = load(["EURUSD"])
    idx = panel["EURUSD"].index
    assert list(idx.strftime("%Y-%m-%d")) == [
        "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
    assert pd.Timestamp("2024-01-01") not in idx
    assert pd.Timestamp("2024-01-06") not in idx and pd.Timestamp("2024-01-07") not in idx
    assert (idx[-1] - idx[-2]).days == 3          # Fri -> Mon, nothing between
    assert isinstance(idx, pd.DatetimeIndex) and idx.tz is None
    assert idx.is_monotonic_increasing


def test_history_range_and_limit():
    """A date range for the warm-up window; `limit` bounds the bars returned."""
    _, t = load(["EURUSD"], start="2024-01-01", end="2024-01-08")
    assert "start_date=2024-01-01" in t.urls[0] and "end_date=2024-01-08" in t.urls[0]

    panel, _ = load(["EURUSD"], limit=2)
    assert len(panel["EURUSD"]) == 2                 # warm-up window is capped
    assert panel["EURUSD"].index[-1] == pd.Timestamp("2024-01-08")

    # ...but an explicit range is a deliberate history request: never truncated.
    panel, _ = load(["EURUSD"], start="2024-01-01", end="2024-01-08", limit=2)
    assert len(panel["EURUSD"]) == 5

    with pytest.raises(fd.FrankfurterError, match="after"):
        load(["EURUSD"], start="2024-06-01", end="2024-01-01")


def test_default_window_covers_the_warmup_and_ends_today():
    start, end = fd._date_range(None, None, limit=250)
    assert end == pd.Timestamp.now("UTC").tz_localize(None).strftime("%Y-%m-%d")
    assert (pd.Timestamp(end) - pd.Timestamp(start)).days >= 250   # ≥ limit bars


def test_url_shapes_for_v2_and_v1():
    """v2 /rates takes the range as query params; v1 puts it in the path."""
    v2 = fd._build_url(fd.DEFAULT_URL, "EUR", ["EUR", "USD", "JPY"],
                       "2024-01-01", "2024-01-08")
    assert v2.startswith("https://api.frankfurter.dev/v2/rates?")
    assert "symbols=JPY%2CUSD" in v2 and "start_date=2024-01-01" in v2
    assert "EUR" not in v2.split("symbols=")[1]      # base isn't re-requested

    v1 = fd._build_url("https://api.frankfurter.dev/v1", "EUR", ["EUR", "USD"],
                       "2024-01-01", "2024-01-08")
    assert v1.startswith("https://api.frankfurter.dev/v1/2024-01-01..2024-01-08?")


def test_base_url_is_overridable_by_env(monkeypatch):
    monkeypatch.setenv("FRANKFURTER_URL", "https://api.frankfurter.dev/v1")
    _, t = load(["EURUSD"], start="2024-01-02", end="2024-01-08")
    assert "/v1/2024-01-02..2024-01-08" in t.urls[0]


# ---------------------------------------------------------------------------
# 6. Offline twin
# ---------------------------------------------------------------------------
def test_synthetic_twin_reproduces_the_close_only_shape():
    """The offline twin must not hand back a true range the live feed can never
    provide — otherwise offline tests pass and live silently changes regime."""
    panel = fd.synthetic_panel(["EURUSD", "USDJPY"])
    assert set(panel) == {"EURUSD", "USDJPY"}
    for df in panel.values():
        assert list(df.columns) == ["open", "high", "low", "close"]
        assert len(df) > 100
        assert (df["high"] - df["low"]).abs().max() == 0.0
    assert bar_quality.close_only_symbols(panel) == ["EURUSD", "USDJPY"]
    assert panel["EURUSD"].attrs["source"] == "frankfurter"
    assert 0.5 < float(panel["EURUSD"]["close"].iloc[0]) < 2.0


def test_synthetic_twin_does_not_claim_ecb_provenance():
    """Faithful in SHAPE, honest about ORIGIN.

    The twin used to inherit `PANEL_ATTRS` wholesale, so a fabricated frame
    carried `provenance="ECB daily reference rates (~16:00 CET fixing)"` and
    `bar="ecb_daily_reference_rate"` — a synthetic price asserting a fixing the
    ECB never published. Latent while nothing rendered `.attrs`, but this string
    exists to be DISPLAYED (docs/DATA_FEEDS.md "Charts" has the FX dashboard
    printing it beside the line it draws), so the twin must label itself.
    Invariant #5: synthetic data is a pipeline test, never presented as real.
    """
    syn = fd.synthetic_panel(["EURUSD"])["EURUSD"].attrs
    assert syn["synthetic"] is True
    assert "SYNTHETIC" in syn["provenance"]
    assert "ECB" not in syn["provenance"].replace("NOT ECB data", "")
    assert syn["bar"] == "synthetic_close_only"
    # ...while the shape-bearing flags stay identical, so every close-only
    # consumer treats the twin exactly like the live feed
    live = fd.PANEL_ATTRS
    assert syn["close_only"] == live["close_only"] is True
    assert syn["source"] == live["source"] == "frankfurter"
    # and the live constant is NOT mutated by building the synthetic one
    assert "synthetic" not in live
    assert live["bar"] == "ecb_daily_reference_rate"


def test_synthetic_twin_applies_the_same_refusals():
    with pytest.raises(fd.FrankfurterError, match="BTCUSD"):
        fd.synthetic_panel(["EURUSD", "BTCUSD"])


def test_universe_is_the_fx_majors():
    assert fd.FRANKFURTER_UNIVERSE == list(pairs.PAIRS)
    assert all(fd.supported(s) for s in fd.FRANKFURTER_UNIVERSE)
    # every registered FX cross is serviceable too (all legs are ECB currencies)
    assert all(fd.supported(s) for s in pairs.CROSSES)
