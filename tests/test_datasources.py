"""Alt-data sources: the leakage-safe as-of merge + synthetic generators + wiring."""
import json

import numpy as np
import pandas as pd

from trading_algo import data, datasources as ds, features as feat, mlpipeline as mlp
from trading_algo.regions import get_region


def _prices():
    reg = get_region("US")
    prices, _ = data.synthetic_region(reg)
    return prices


def test_asof_merge_no_lookahead():
    idx = pd.bdate_range("2020-01-01", periods=60)
    # one observation, known (filed) only on day 30
    obs = pd.DataFrame({"known_date": [idx[30]], "ticker": ["A"], "roe": [0.5]})
    panel = ds.asof_panel(obs, idx)
    # invisible before it was known, present from the known date on
    assert (idx[20], "A") not in panel.index or np.isnan(panel.loc[(idx[20], "A"), "roe"])
    assert panel.loc[(idx[40], "A"), "roe"] == 0.5
    # a later trading date still sees only the last-known value (carried forward, not back)
    assert panel.loc[(idx[59], "A"), "roe"] == 0.5


def test_asof_handles_tzaware_known_dates():
    # GDELT stamps carry a UTC "Z"; prices are tz-naive — the merge must not crash
    idx = pd.bdate_range("2018-01-01", periods=40)
    obs = pd.DataFrame({"known_date": ["2018-01-15T00:00:00Z", "2018-02-01T00:00:00Z"],
                        "ticker": ["A", "A"], "sentiment": [0.3, -0.2]})
    panel = ds.asof_panel(obs, idx)
    assert panel.loc[(idx[20], "A"), "sentiment"] in (0.3, -0.2)
    assert panel.index.get_level_values("date").tz is None   # normalised to tz-naive


def test_asof_uses_latest_known():
    idx = pd.bdate_range("2020-01-01", periods=40)
    obs = pd.DataFrame({"known_date": [idx[5], idx[20]], "ticker": ["A", "A"], "roe": [0.1, 0.9]})
    panel = ds.asof_panel(obs, idx)
    assert panel.loc[(idx[10], "A"), "roe"] == 0.1     # only first known yet
    assert panel.loc[(idx[30], "A"), "roe"] == 0.9     # updated after second filing


def test_synthetic_sources_have_expected_columns():
    tickers = ["A", "B", "C"]
    fund = ds.EdgarFundamentals().synthetic(tickers, "2015-01-01", "2020-01-01")
    iv = ds.OptionIV().synthetic(tickers, "2015-01-01", "2020-01-01")
    sent = ds.NewsSentiment().synthetic(tickers, "2015-01-01", "2020-01-01")
    assert {"roe", "net_margin", "asset_growth", "sue"} <= set(fund.columns)
    assert {"iv_level", "iv_skew", "put_call"} <= set(iv.columns)
    # sentiment is emitted as CHANGES (shocks), not levels, + a raw coverage mask
    assert {"sentiment_shock", "buzz_shock", "has_sentiment"} <= set(sent.columns)


def test_extra_panel_adds_feature_columns():
    prices = _prices()
    extra = ds.build_extra_panel(ds.ALL_SOURCES, prices, "2012-01-01", synthetic=True)
    assert not extra.empty
    # panel now carries price features + alt-data columns, still one row per (date,ticker)
    reg = get_region("US")
    _, idx = data.synthetic_region(reg)
    panel = feat.build_feature_panel(prices, idx, extra=extra)
    for c in ("roe", "sue", "iv_level", "sentiment_shock", "has_sentiment"):
        assert c in panel.columns
    assert list(panel.index.names) == ["date", "ticker"]
    # the coverage mask is passed through RAW (0/1), never z-scored into a fake neutral
    assert set(pd.unique(panel["has_sentiment"])) <= {0.0, 1.0}


def test_gdelt_timeline_parser():
    raw = (b'{"timeline":[{"series":"Average Tone","data":['
           b'{"date":"2018-03-01T00:00:00Z","value":1.5},'
           b'{"date":"2018-03-02T00:00:00Z","value":-0.7}]}]}')
    df = ds.NewsSentiment._parse_timeline(raw, "sentiment")
    assert list(df.columns) == ["known_date", "sentiment"]
    assert len(df) == 2 and df["sentiment"].iloc[0] == 1.5
    # malformed input degrades to empty, never raises
    assert ds.NewsSentiment._parse_timeline(b"not json", "sentiment").empty


def test_sparse_altdata_does_not_shrink_panel():
    prices = _prices()
    reg = get_region("US")
    _, idx = data.synthetic_region(reg)
    core = feat.build_feature_panel(prices, idx)                       # price-only
    # a single sentiment observation for one name on one date
    d = prices.index[400]
    sparse = pd.DataFrame({"known_date": [d], "ticker": [prices.columns[0]], "sentiment": [0.9]})
    extra = ds.asof_panel(sparse, prices.index)
    full = feat.build_feature_panel(prices, idx, extra=extra)
    assert len(full) == len(core)                                     # sparse feed kept every row
    assert "sentiment" in full.columns
    assert (full["sentiment"].fillna(0) == full["sentiment"]).all()   # missing filled neutral (0)


def test_pipeline_runs_with_altdata():
    reg = get_region("US")
    prices, idx = data.synthetic_region(reg)
    extra = ds.build_extra_panel(ds.ALL_SOURCES, prices, "2012-01-01", synthetic=True)
    res = mlp.run_ml_backtest(prices, idx, extra=extra, n_folds=4, top_n=15)
    assert res["n_periods"] > 0
    # the model now sees more than the 8 price features
    df = mlp.build_dataset(prices, idx, extra=extra)
    assert len(mlp.feature_cols(df)) > len(feat.FEATURES)


# --- event-decay: a filing/tone print must be a decaying impulse, not a stale plateau ---

def test_asof_decay_linear_fades_and_backward_compat():
    idx = pd.bdate_range("2020-01-01", periods=120)
    obs = pd.DataFrame({"known_date": [idx[10]], "ticker": ["A"], "x": [1.0]})
    # no decay = legacy plain ffill: a flat level carried forward forever
    flat = ds.asof_panel(obs, idx)
    assert flat.loc[(idx[10], "A"), "x"] == 1.0 and flat.loc[(idx[110], "A"), "x"] == 1.0
    # linear decay over a 40-calendar-day gate
    dec = ds.asof_panel(obs, idx, decay={"x": ("linear", 40, None)})
    assert dec.loc[(idx[10], "A"), "x"] == 1.0                 # day 0: full weight
    assert 0.3 < dec.loc[(idx[24], "A"), "x"] < 0.9            # ~20 calendar days in: partial
    assert dec.loc[(idx[110], "A"), "x"] == 0.0               # past the gate: exactly 0


def test_asof_decay_no_lookahead():
    # truncation invariance: a future obs must not change any value at or before the cut
    idx = pd.bdate_range("2020-01-01", periods=80)
    obs = pd.DataFrame({"known_date": [idx[20], idx[50]], "ticker": ["A", "A"], "x": [1.0, 2.0]})
    dec = {"x": ("linear", 30, None)}
    full = ds.asof_panel(obs, idx, decay=dec)
    trunc = ds.asof_panel(obs.iloc[:1], idx, decay=dec)        # drop the future (idx[50]) obs
    cut = idx[40]
    for t in idx[idx <= cut]:
        k = (t, "A")
        a = full.loc[k, "x"] if k in full.index else float("nan")
        b = trunc.loc[k, "x"] if k in trunc.index else float("nan")
        assert (np.isnan(a) and np.isnan(b)) or abs(a - b) < 1e-9


# --- SUE / PEAD: the one horizon-matched fundamental surprise ---

def test_seasonal_surprise_sign_scale_and_filing_date():
    ends = pd.date_range("2015-03-31", periods=12, freq="QE")
    filed = ends + pd.Timedelta(days=45)
    # mild quarter-to-quarter noise (so the surprise normaliser has non-zero variance),
    # then a clear positive jump in the final quarter vs its year-ago comparable
    rng = np.random.default_rng(0)
    ni_vals = list(100.0 + rng.normal(0, 5, 11)) + [200.0]
    ni = pd.DataFrame({"known_date": filed, "end": ends,
                       "start": ends - pd.Timedelta(days=90), "val": ni_vals})
    eq = pd.DataFrame({"known_date": filed, "end": ends, "start": pd.NaT, "val": [1000.0] * 12})
    sue = ds.EdgarFundamentals._seasonal_surprise(ni, eq).sort_values("known_date")
    assert not sue.empty
    assert sue["sue"].iloc[-1] > 0                            # positive surprise, pre-signed +
    assert (pd.to_datetime(sue["known_date"]) > ends.min()).all()   # dated by filing, not period-end
    assert ds.EdgarFundamentals._seasonal_surprise(ni.head(3), eq.head(3)).empty  # <5 q → empty


def test_sue_known_date_guards_equity_filing():
    # equity is public LATER than the earnings → the surprise must be dated by the later filing
    ends = pd.date_range("2015-03-31", periods=10, freq="QE")
    ni_filed, eq_filed = ends + pd.Timedelta(days=30), ends + pd.Timedelta(days=60)
    ni = pd.DataFrame({"known_date": ni_filed, "end": ends,
                       "start": ends - pd.Timedelta(days=90), "val": np.arange(1, 11) * 100.0})
    eq = pd.DataFrame({"known_date": eq_filed, "end": ends, "start": pd.NaT, "val": [1000.0] * 10})
    sue = ds.EdgarFundamentals._seasonal_surprise(ni, eq)
    kd = set(pd.to_datetime(sue["known_date"]))
    assert kd <= set(eq_filed) and not (kd & set(ni_filed))   # later (equity) filing, never NI-only


# --- news shocks: differenced from past known prints, no self-leak ---

def _sue_frames(vals, filed_offset=45):
    ends = pd.date_range("2015-03-31", periods=len(vals), freq="QE")
    ni = pd.DataFrame({"known_date": ends + pd.Timedelta(days=filed_offset), "end": ends,
                       "start": ends - pd.Timedelta(days=89), "val": vals})
    eq = pd.DataFrame({"known_date": ends + pd.Timedelta(days=filed_offset), "end": ends,
                       "start": pd.NaT, "val": [1000.0] * len(vals)})
    return ends, ni, eq


def test_announcement_backdates_before_10q():
    rng = np.random.default_rng(0)
    ends, ni, eq = _sue_frames(list(100 + rng.normal(0, 5, 11)) + [200.0])   # filed at end+45
    ann = pd.DataFrame({"announce_date": ends + pd.Timedelta(days=30)})       # 8-K at end+30
    base = ds.EdgarFundamentals._seasonal_surprise(ni, eq)                    # no announce
    moved = ds.EdgarFundamentals._seasonal_surprise(ni, eq, announce=ann)
    assert (pd.to_datetime(moved["known_date"]).to_numpy()
            < pd.to_datetime(base["known_date"]).to_numpy()).all()           # moved earlier
    assert np.allclose(moved["sue"].to_numpy(), base["sue"].to_numpy())      # only timing moved


def test_announcement_fallback_no_8k():
    rng = np.random.default_rng(1)
    ends, ni, eq = _sue_frames(list(100 + rng.normal(0, 5, 11)) + [180.0])
    base = ds.EdgarFundamentals._seasonal_surprise(ni, eq)
    empty = ds.EdgarFundamentals._seasonal_surprise(ni, eq, announce=pd.DataFrame(columns=["announce_date"]))
    assert np.array_equal(pd.to_datetime(base["known_date"]).to_numpy(),
                          pd.to_datetime(empty["known_date"]).to_numpy())    # fallback preserved


def test_sue_uses_first_reported_value_not_restatement():
    # THE magnitude guard: back-dating must attach the FIRST-reported number, never a later
    # restatement — otherwise a value not public at the announcement leaks in.
    rng = np.random.default_rng(5)
    ends, ni, eq = _sue_frames(list(100 + rng.normal(0, 5, 10)))
    ann = pd.DataFrame({"announce_date": ends + pd.Timedelta(days=30)})
    clean = ds.EdgarFundamentals._seasonal_surprise(ni, eq, announce=ann)
    restate = pd.DataFrame({"known_date": [ends[5] + pd.Timedelta(days=400)], "end": [ends[5]],
                            "start": [ends[5] - pd.Timedelta(days=89)], "val": [ni["val"].iloc[5] * 5]})
    withr = ds.EdgarFundamentals._seasonal_surprise(pd.concat([ni, restate], ignore_index=True), eq, announce=ann)
    assert np.allclose(clean["sue"].to_numpy(), withr["sue"].to_numpy())     # restatement ignored
    assert np.array_equal(pd.to_datetime(clean["known_date"]).to_numpy(),
                          pd.to_datetime(withr["known_date"]).to_numpy())


def test_late_first_filing_not_backdated():
    # a quarter whose first filing lands > MAX_10Q_LAG after the 8-K is treated as a restatement
    rng = np.random.default_rng(2)
    ends = pd.date_range("2015-03-31", periods=12, freq="QE")
    filed = [e + pd.Timedelta(days=45) for e in ends[:-1]] + [ends[-1] + pd.Timedelta(days=90)]
    ni = pd.DataFrame({"known_date": filed, "end": ends, "start": ends - pd.Timedelta(days=89),
                       "val": list(100 + rng.normal(0, 5, 11)) + [200.0]})
    eq = pd.DataFrame({"known_date": filed, "end": ends, "start": pd.NaT, "val": [1000.0] * 12})
    ann = pd.DataFrame({"announce_date": ends + pd.Timedelta(days=10)})       # 8-K at end+10
    moved = ds.EdgarFundamentals._seasonal_surprise(ni, eq, announce=ann)
    kd = pd.to_datetime(moved["known_date"]).to_numpy()
    assert kd[-1] == np.datetime64(ends[-1] + pd.Timedelta(days=90))          # late one NOT back-dated
    assert kd[-2] == np.datetime64(ends[-2] + pd.Timedelta(days=10))          # normal one back-dated


def test_no_out_of_window_8k_matched():
    rng = np.random.default_rng(4)
    ends, ni, eq = _sue_frames(list(100 + rng.normal(0, 5, 8)))
    # all announcements out of window: too early (end+2 < ANN_MIN) and too late (end+200 > filed)
    ann = pd.DataFrame({"announce_date": list(ends + pd.Timedelta(days=2)) + list(ends + pd.Timedelta(days=200))})
    base = ds.EdgarFundamentals._seasonal_surprise(ni, eq)
    moved = ds.EdgarFundamentals._seasonal_surprise(ni, eq, announce=ann)
    assert np.array_equal(pd.to_datetime(base["known_date"]).to_numpy(),
                          pd.to_datetime(moved["known_date"]).to_numpy())     # nothing matched


def test_seasonal_surprise_causal_prefix():
    # no-lookahead: SUE on a prefix of quarters == SUE on the full series for those quarters
    rng = np.random.default_rng(3)
    ends, ni, eq = _sue_frames(list(100 + rng.normal(0, 6, 16)))
    ann = pd.DataFrame({"announce_date": ends + pd.Timedelta(days=30)})
    full = ds.EdgarFundamentals._seasonal_surprise(ni, eq, announce=ann).set_index("known_date")["sue"]
    pref = ds.EdgarFundamentals._seasonal_surprise(ni.iloc[:15], eq.iloc[:15],
                                                   announce=ann.iloc[:15]).set_index("known_date")["sue"]
    common = pref.index.intersection(full.index)   # SUE warms up after ~8 quarters
    assert len(common) >= 5 and np.allclose(pref.loc[common].to_numpy(), full.loc[common].to_numpy())


def test_earnings_announcements_parses_paginates_and_filters(monkeypatch):
    recent = {"filings": {"recent": {
        "form": ["8-K", "10-Q", "8-K"],
        "filingDate": ["2020-01-30", "2020-02-15", "2020-03-01"],
        "reportDate": ["2020-01-29", "2019-12-31", "2020-02-28"],
        "items": ["2.02,9.01", "", "5.02"], "accessionNumber": ["a", "b", "d"]},
        "files": [{"name": "CIK0000000001-older.json"}]}}
    older = {"form": ["8-K"], "filingDate": ["2018-04-29"], "reportDate": ["2018-04-28"],
             "items": ["2.02"], "accessionNumber": ["z"]}
    monkeypatch.setattr(ds, "_http_get",
                        lambda url, timeout=30: json.dumps(older if "older" in url else recent).encode())
    ann = ds.EdgarFundamentals()._earnings_announcements("0000000001")
    assert list(ann.columns) == ["announce_date", "report_date"]
    assert set(ann["announce_date"].dt.strftime("%Y-%m-%d")) == {"2020-01-30", "2018-04-29"}  # both 2.02s
    assert ann["announce_date"].is_monotonic_increasing                       # sorted, paginated
    monkeypatch.setattr(ds, "_http_get", lambda u, timeout=30: b"not json")
    assert ds.EdgarFundamentals()._earnings_announcements("0000000001").empty  # malformed → empty


def test_coverable_slice_spends_budget_on_mappable():
    tickers = ["DEAD1", "DEAD2", "AAPL", "DEAD3", "MSFT"]
    titles = {"AAPL": "Apple Inc", "MSFT": "Microsoft Corp"}
    assert ds.NewsSentiment._coverable(tickers, titles) == ["AAPL", "MSFT"]   # only mappable, in order


def test_edgar_observations_real_path_merges(monkeypatch):
    # drives the REAL observations() parse/merge path with a mocked companyfacts payload
    # (no network) — the path synthetic() bypasses, where a str-vs-datetime 'end' merge
    # crashed on real data. Must parse, merge, and fold into a leakage-safe panel cleanly.
    ends = pd.date_range("2016-03-31", periods=16, freq="QE")

    def flow(base, noise):                       # 10-Q style flow rows (have 'start')
        rng = np.random.default_rng(1)
        return [{"end": e.strftime("%Y-%m-%d"),
                 "start": (e - pd.Timedelta(days=89)).strftime("%Y-%m-%d"),
                 "filed": (e + pd.Timedelta(days=40)).strftime("%Y-%m-%d"),
                 "val": base + rng.normal(0, noise)} for e in ends]

    def stock(vals):                             # balance-sheet stock rows (no 'start')
        return [{"end": e.strftime("%Y-%m-%d"),
                 "filed": (e + pd.Timedelta(days=40)).strftime("%Y-%m-%d"),
                 "val": v} for e, v in zip(ends, vals)]

    facts = {"facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": flow(100.0, 8.0)}},
        "Revenues": {"units": {"USD": flow(500.0, 5.0)}},
        "StockholdersEquity": {"units": {"USD": stock([1000.0 + 10 * i for i in range(16)])}},
        "Assets": {"units": {"USD": stock([2000.0 + 30 * i for i in range(16)])}},
    }}}
    monkeypatch.setattr(ds.EdgarFundamentals, "_ticker_cik", lambda self: {"AAA": "0000000001"})
    monkeypatch.setattr(ds, "_http_get", lambda url, timeout=30: json.dumps(facts).encode())

    obs = ds.EdgarFundamentals().observations(["AAA"], "2016-01-01", "2020-06-30")
    assert not obs.empty
    assert {"roe", "net_margin", "asset_growth", "sue"} <= set(obs.columns)
    # the SUE impulses fold into a decaying, leakage-safe panel without raising
    idx = pd.bdate_range("2016-01-01", "2020-06-30")
    panel = ds.asof_panel(obs, idx, decay=ds.EdgarFundamentals.decay)
    assert "sue" in panel.columns and panel["sue"].notna().any()


def test_news_shock_uses_only_past_no_self_leak():
    s = pd.Series([1.0, 1.0, 1.0, 1.0, 1.0, 5.0])
    shock = ds.NewsSentiment._shock(s, window=20)
    assert shock.iloc[:5].isna().all()                       # warmup (min_periods=5, shift(1))
    assert abs(shock.iloc[5] - 4.0) < 1e-9                    # spike vs trailing mean (~1) of PAST
    trunc = ds.NewsSentiment._shock(s.iloc[:5], window=20)    # truncation invariance
    for i in range(5):
        a, b = shock.iloc[i], trunc.iloc[i]
        assert (np.isnan(a) and np.isnan(b)) or abs(a - b) < 1e-9
