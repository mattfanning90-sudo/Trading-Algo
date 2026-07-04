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
    assert {"roe", "net_margin", "asset_growth"} <= set(fund.columns)
    assert {"iv_level", "iv_skew", "put_call"} <= set(iv.columns)
    assert {"sentiment", "buzz"} <= set(sent.columns)


def test_extra_panel_adds_feature_columns():
    prices = _prices()
    extra = ds.build_extra_panel(ds.ALL_SOURCES, prices, "2012-01-01", synthetic=True)
    assert not extra.empty
    # panel now carries price features + alt-data columns, still one row per (date,ticker)
    reg = get_region("US")
    _, idx = data.synthetic_region(reg)
    panel = feat.build_feature_panel(prices, idx, extra=extra)
    for c in ("roe", "iv_level", "sentiment"):
        assert c in panel.columns
    assert list(panel.index.names) == ["date", "ticker"]


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
