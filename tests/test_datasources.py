"""Alt-data sources: the leakage-safe as-of merge + synthetic generators + wiring."""
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

def test_news_shock_uses_only_past_no_self_leak():
    s = pd.Series([1.0, 1.0, 1.0, 1.0, 1.0, 5.0])
    shock = ds.NewsSentiment._shock(s, window=20)
    assert shock.iloc[:5].isna().all()                       # warmup (min_periods=5, shift(1))
    assert abs(shock.iloc[5] - 4.0) < 1e-9                    # spike vs trailing mean (~1) of PAST
    trunc = ds.NewsSentiment._shock(s.iloc[:5], window=20)    # truncation invariance
    for i in range(5):
        a, b = shock.iloc[i], trunc.iloc[i]
        assert (np.isnan(a) and np.isnan(b)) or abs(a - b) < 1e-9
