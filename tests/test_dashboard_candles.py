"""The `candles.json` producer — the missing half of a contract app.js already kept.

`synthCandles()` in app.js has always preferred `S.candles[pair]` and only fallen
back to its seeded generator; the chart caption already switches between
``LIVE OHLC (candles.json)`` and ``SYNTHETIC BARS ANCHORED TO REAL LAST CLOSE``.
Nothing in the repo ever wrote that file, so every FX chart drew invented bars.

These tests pin the two things that make the file safe to publish: it is exactly
the shape the terminal parses, and it never presents an unobserved range as a
measured one.
"""
import json

import pandas as pd
import pytest

from trading_algo.dashboard import candles
from trading_algo.forex import fx_book


def _panel(n=10, flat=False):
    """A tiny OHLC panel. `flat` makes it close-only (high == low == close),
    which is what an ECB-fixing source yields."""
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    out = {}
    for i, sym in enumerate(("EURUSD", "USDJPY")):
        c = pd.Series([1.10 + i * 100 + j * 0.01 for j in range(n)], index=idx)
        out[sym] = pd.DataFrame(
            {"open": c, "close": c} if flat else
            {"open": c, "high": c * 1.004, "low": c * 0.996, "close": c},
            index=idx)
        if flat:
            out[sym]["high"] = c
            out[sym]["low"] = c
    return out


@pytest.fixture()
def routed(monkeypatch):
    """Swap the network for a panel we control. Nothing here may fetch."""
    box = {"panel": _panel()}

    def fake(symbols, **kw):
        p = {s: df for s, df in box["panel"].items() if s in symbols}
        return p, type("R", (), {"served": {s: "fake" for s in p}, "missing": {}})()

    monkeypatch.setattr(candles.feeds, "load_routed", fake)
    return box


# ---------------------------------------------------------------------------
# the contract app.js already parses
# ---------------------------------------------------------------------------
def test_rows_are_the_array_shape_app_js_reads(routed):
    """app.js does `{o:+b[1], h:+b[2], l:+b[3], c:+b[4]}` on each row, so the
    order is load-bearing — a transposed high/low would render every candle
    inverted and still 'work'."""
    out = candles.build(["EURUSD"], bars=5)
    rows = out["EURUSD"]
    assert len(rows) == 5
    ts, o, h, l, c = rows[-1]
    assert isinstance(ts, int) and ts > 1_000_000_000_000     # epoch MILLIseconds
    assert h >= max(o, c) >= min(o, c) >= l                   # a coherent bar
    assert all(isinstance(v, float) for v in (o, h, l, c))


def test_only_the_requested_tail_is_published(routed):
    assert len(candles.build(["EURUSD"], bars=3)["EURUSD"]) == 3


def test_a_row_with_an_unvaluable_field_is_dropped_not_patched(routed):
    """A gap in a chart is honest; an invented bar is not. The alternative —
    forward-filling or zero-filling — would draw a bar that never traded."""
    p = _panel()
    p["EURUSD"].iloc[3, p["EURUSD"].columns.get_loc("high")] = float("nan")
    routed["panel"] = p
    rows = candles.build(["EURUSD"], bars=10)["EURUSD"]
    assert len(rows) == 9                                     # the NaN bar is gone
    assert all(all(v == v for v in r) for r in rows)          # nothing NaN survived


def test_payload_is_valid_json_with_no_bare_nan(routed):
    p = _panel()
    p["USDJPY"].iloc[0, 1] = float("nan")
    routed["panel"] = p
    json.dumps(candles.build(["EURUSD", "USDJPY"], bars=10), allow_nan=False)


# ---------------------------------------------------------------------------
# the honesty guarantee
# ---------------------------------------------------------------------------
def test_range_less_bars_are_flagged_not_drawn_as_measured(routed):
    """A close-only source gives open == high == low == close. Rendered as
    candlesticks those look like a real but very quiet market — flat ticks that
    a reader would take as an observed range. They must be reported so the
    terminal can label them."""
    routed["panel"] = _panel(flat=True)
    meta = candles.build(["EURUSD", "USDJPY"], bars=5)["_meta"]
    assert set(meta["range_less"]) == {"EURUSD", "USDJPY"}


def test_a_real_range_is_not_flagged(routed):
    assert candles.build(["EURUSD"], bars=5)["_meta"]["range_less"] == []


def test_range_less_is_per_symbol_because_panels_are_mixed(routed):
    """The live books hold ECB-fixing FX beside real ccxt crypto bars. A
    whole-panel boolean would answer 'no' for exactly the mixed case that needs
    handling."""
    p = _panel()
    c = p["USDJPY"]["close"]
    p["USDJPY"]["high"] = c
    p["USDJPY"]["low"] = c
    routed["panel"] = p
    meta = candles.build(["EURUSD", "USDJPY"], bars=5)["_meta"]
    assert meta["range_less"] == ["USDJPY"]


def test_meta_says_which_provider_and_whether_it_is_synthetic(routed):
    """Invariant #5: fabricated bars never wear a provider's name, and a
    consumer must be able to tell how old the file is."""
    meta = candles.build(["EURUSD"], bars=5, synthetic=True)["_meta"]
    assert meta["synthetic"] is True
    assert meta["bar"] == "1d"
    assert meta["generated_at"].endswith("Z")
    assert meta["served"]["EURUSD"] == "fake"


def test_a_symbol_nobody_could_serve_is_reported_not_silently_absent(monkeypatch):
    def fake(symbols, **kw):
        p = _panel()
        return {"EURUSD": p["EURUSD"]}, type("R", (), {
            "served": {"EURUSD": "yahoo"},
            "missing": {"BTCUSD": "no provider for BTCUSD at 1d"}})()
    monkeypatch.setattr(candles.feeds, "load_routed", fake)
    meta = candles.build(["EURUSD", "BTCUSD"], bars=5)["_meta"]
    assert "BTCUSD" not in meta["symbols"]
    assert "BTCUSD" in meta["missing"]


# ---------------------------------------------------------------------------
# writing, per bar cadence
# ---------------------------------------------------------------------------
def test_each_bar_cadence_gets_its_own_file(tmp_path, monkeypatch, routed):
    """The daytrader book charts 60m and the rest daily. One merged daily file
    would draw the wrong bars on that page."""
    monkeypatch.setattr(candles, "book_symbols",
                        lambda accounts=None: {"1d": ["EURUSD"], "60m": ["EURUSD"]})
    written = candles.write(str(tmp_path))
    assert sorted(p.split("/")[-1] for p in written) == ["candles-60m.json", "candles.json"]


def test_one_cadence_failing_does_not_cost_the_others_their_charts(tmp_path, monkeypatch):
    monkeypatch.setattr(candles, "book_symbols",
                        lambda accounts=None: {"1d": ["EURUSD"], "60m": ["EURUSD"]})

    def flaky(symbols, **kw):
        if kw.get("interval") == "60m":
            raise RuntimeError("provider down")
        p = _panel()
        return {"EURUSD": p["EURUSD"]}, type("R", (), {"served": {}, "missing": {}})()
    monkeypatch.setattr(candles.feeds, "load_routed", flaky)
    written = candles.write(str(tmp_path))
    assert [p.split("/")[-1] for p in written] == ["candles.json"]


def test_book_symbols_reads_the_live_books(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.init_account("d", 10_000, "balanced", symbols=["EURUSD"], bar="60m")
    fx_book.init_account("n", 5_000, "balanced", symbols=["USDJPY"], bar="1d")
    assert candles.book_symbols() == {"60m": ["EURUSD"], "1d": ["USDJPY"]}


# ---------------------------------------------------------------------------
# the terminal's side of the contract (source-pinned; there is no JS runner here)
# ---------------------------------------------------------------------------
def _app_js() -> str:
    import pathlib
    return (pathlib.Path(__file__).resolve().parents[1]
            / "trading_algo/dashboard/static/app.js").read_text()


def test_a_synthetic_build_never_wears_the_live_label():
    """Invariant #5. A `--synthetic` export routes GENERATED bars through the
    same candles file, so `real` must be gated on `_meta.synthetic` — otherwise
    the caption reads "LIVE OHLC · SYNTHETIC", which is a contradiction the
    screenshot actually showed before this was fixed."""
    js = _app_js()
    assert "real: meta.synthetic !== true" in js
    assert "SYNTHETIC PANEL — PIPELINE TEST BUILD, NOT MARKET DATA" in js


def test_range_less_bars_are_labelled_rather_than_drawn_as_measured():
    js = _app_js()
    assert "meta.range_less" in js
    assert "RANGE UNOBSERVED" in js


def test_the_export_bakes_candles_because_file_urls_cannot_fetch_them():
    """A relative fetch('candles.json') is a CORS error over file://, so a
    shared export would silently fall back to invented bars."""
    import pathlib
    exp = (pathlib.Path(__file__).resolve().parents[1]
           / "trading_algo/dashboard/export.py").read_text()
    assert "window.__CANDLES__" in exp
    assert "window.__CANDLES__" in _app_js()
