"""Alternative-data feature sources for the predictive model — fundamentals, options
implied vol, and news/social sentiment — merged into the feature panel WITHOUT lookahead.

The whole risk with alt-data is timing: a fundamental is only usable *after it was
filed* (not as of the period it covers); IV/sentiment only as of the day observed.
So every source reports a **`known_date`** (when the information became public), and
`asof_panel` carries values forward from `known_date` only — never backward. Get this
wrong and the backtest is fiction.

Availability (honest):
- **Fundamentals** — REAL and free: SEC EDGAR `companyfacts` gives XBRL facts *with
  filing dates*. `EdgarFundamentals` fetches them (US only; needs network → runs in CI).
- **Options IV** — no free history. `OptionIV` is an adapter: wire a paid feed
  (Polygon options / ORATS / CBOE) into `.observations`; a synthetic generator lets the
  pipeline run offline until then.
- **Sentiment** — no free clean history. `NewsSentiment` is an adapter: wire GDELT /
  Alpha-Vantage-news / RavenPack; synthetic generator meanwhile.

Each source yields columns; `build_extra_panel` merges them all as-of into a
`[(date, ticker), feature]` panel that `features.build_feature_panel(..., extra=...)`
folds in as extra columns. See `docs/research/PREDICTIVE_MODEL.md`.
"""
from __future__ import annotations

import io
import json
import os
import time
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd

_UA = {"User-Agent": "trading-algo research mattfanning90@gmail.com"}


def _http_get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------------------
# Leakage-safe as-of merge (the piece that matters)
# ---------------------------------------------------------------------------

def asof_panel(obs: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    """Carry each source observation forward from its `known_date` onto the trading
    calendar — the no-lookahead join.

    `obs`: columns [known_date, ticker, <feature...>]. Returns a long panel indexed by
    (date, ticker) where each feature's value at date t is the most recent observation
    with `known_date` ≤ t (ffill of past-known values only). A value with known_date > t
    is invisible at t — that is the whole guarantee."""
    if obs is None or obs.empty:
        return pd.DataFrame()
    feats = [c for c in obs.columns if c not in ("known_date", "ticker")]
    obs = obs.copy()
    # normalise to tz-naive: GDELT stamps are UTC ("...Z"), price dates are tz-naive —
    # mixing them breaks the sort/union. Coerce everything to naive UTC wall-time.
    obs["known_date"] = pd.to_datetime(obs["known_date"], utc=True).dt.tz_localize(None)
    out = {}
    for f in feats:
        wide = obs.pivot_table(index="known_date", columns="ticker", values=f, aggfunc="last")
        wide = (wide.reindex(wide.index.union(index)).sort_index()
                    .ffill().reindex(index))          # ffill = past→future only, never back
        out[f] = wide.stack()
    panel = pd.concat(out, axis=1)
    panel.index.names = ["date", "ticker"]
    return panel.dropna(how="all")


class FeatureSource:
    """A named alt-data source that yields point-in-time observations."""
    name = "base"

    def observations(self, tickers: list[str], start: str, end: str | None) -> pd.DataFrame:
        """Return columns [known_date, ticker, <feature...>]. Empty frame if unavailable."""
        raise NotImplementedError

    def synthetic(self, tickers: list[str], start: str, end: str) -> pd.DataFrame:
        """Offline placeholder observations (meaningless values; plumbing only)."""
        raise NotImplementedError


def build_extra_panel(sources: list[FeatureSource], prices: pd.DataFrame,
                      start: str, end: str | None = None,
                      synthetic: bool = False) -> pd.DataFrame:
    """As-of-merge every source into one extra-feature panel aligned to `prices`."""
    tickers = list(prices.columns)
    end = end or str(prices.index[-1].date())
    panels = []
    for s in sources:
        obs = (s.synthetic(tickers, start, end) if synthetic
               else s.observations(tickers, start, end))
        p = asof_panel(obs, prices.index)
        if not p.empty:
            panels.append(p)
    return pd.concat(panels, axis=1) if panels else pd.DataFrame()


# ---------------------------------------------------------------------------
# Fundamentals — SEC EDGAR (REAL, free, point-in-time via filing dates)
# ---------------------------------------------------------------------------

class EdgarFundamentals(FeatureSource):
    """Point-in-time fundamentals from SEC EDGAR XBRL `companyfacts`.

    Uses the **filing date** (`filed`) as `known_date`, so a metric is only visible
    after it was actually reported. Features (denominator-safe ratios, no share count
    needed): return on equity, net margin, and year-on-year asset growth."""
    name = "edgar_fundamentals"
    _CIK_URL = "https://www.sec.gov/files/company_tickers.json"

    def _ticker_cik(self) -> dict[str, str]:
        try:
            data = json.loads(_http_get(self._CIK_URL))
            return {row["ticker"].upper(): f"{int(row['cik_str']):010d}"
                    for row in data.values()}
        except Exception:
            return {}

    @staticmethod
    def _series(facts: dict, concept: str) -> pd.DataFrame:
        """(known_date=filed, end, val) rows for one us-gaap concept, USD units."""
        try:
            units = facts["facts"]["us-gaap"][concept]["units"]
        except Exception:
            return pd.DataFrame()
        key = next((k for k in units if k.upper().startswith("USD")), None)
        if key is None:
            return pd.DataFrame()
        rows = [{"known_date": r["filed"], "end": r["end"], "val": r["val"]}
                for r in units[key] if r.get("filed") and r.get("val") is not None]
        return pd.DataFrame(rows)

    def observations(self, tickers, start, end):
        cik = self._ticker_cik()
        if not cik:
            return pd.DataFrame()
        out = []
        for t in tickers:
            c = cik.get(t.upper())
            if not c:
                continue
            try:
                facts = json.loads(_http_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{c}.json"))
                time.sleep(0.12)                       # SEC fair-access (<10 req/s)
            except Exception:
                continue
            ni = self._series(facts, "NetIncomeLoss")
            eq = self._series(facts, "StockholdersEquity")
            rev = self._series(facts, "Revenues")
            if rev.empty:
                rev = self._series(facts, "RevenueFromContractWithCustomerExcludingAssessedTax")
            assets = self._series(facts, "Assets")
            if ni.empty or eq.empty:
                continue
            # align on period-end, keep the filing date as known_date
            m = ni.merge(eq, on="end", suffixes=("_ni", "_eq"))
            m["known_date"] = m[["known_date_ni", "known_date_eq"]].max(axis=1)
            m["roe"] = m["val_ni"] / m["val_eq"].replace(0, np.nan)
            if not rev.empty:
                m = m.merge(rev.rename(columns={"val": "val_rev"})[["end", "val_rev"]], on="end", how="left")
                m["net_margin"] = m["val_ni"] / m["val_rev"].replace(0, np.nan)
            if not assets.empty:
                a = assets.sort_values("end")
                a["asset_growth"] = a["val"].pct_change(4)   # ~YoY on quarterly
                m = m.merge(a[["end", "asset_growth"]], on="end", how="left")
            m["ticker"] = t
            keep = ["known_date", "ticker"] + [c for c in ("roe", "net_margin", "asset_growth") if c in m]
            out.append(m[keep])
        return pd.concat(out, ignore_index=True) if out else pd.DataFrame()

    def synthetic(self, tickers, start, end):
        rng = np.random.default_rng(11)
        qtrs = pd.date_range(start, end, freq="QE")
        rows = []
        for t in tickers:
            base_roe, base_m = rng.normal(0.12, 0.05), rng.normal(0.08, 0.04)
            for q in qtrs:
                rows.append({"known_date": q + pd.Timedelta(days=45),   # filed ~45d after quarter
                             "ticker": t, "roe": base_roe + rng.normal(0, 0.02),
                             "net_margin": base_m + rng.normal(0, 0.01),
                             "asset_growth": rng.normal(0.05, 0.03)})
        return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Options implied vol — ADAPTER (wire a paid feed; synthetic meanwhile)
# ---------------------------------------------------------------------------

class OptionIV(FeatureSource):
    """Options-implied features: ATM IV level, IV skew (put−call), IV rank, put/call.
    No free history — wire Polygon options / ORATS / CBOE into `observations`
    (set the feed's key and fill the fetch); until then use `synthetic`."""
    name = "option_iv"

    def observations(self, tickers, start, end):
        # Wire-in point: e.g. Polygon options snapshots or ORATS historical IV.
        # Requires a paid feed + key; return empty so the pipeline degrades gracefully.
        return pd.DataFrame()

    def synthetic(self, tickers, start, end):
        rng = np.random.default_rng(22)
        days = pd.bdate_range(start, end, freq="5B")   # weekly obs
        rows = []
        for t in tickers:
            lvl = abs(rng.normal(0.30, 0.08))
            for d in days:
                rows.append({"known_date": d, "ticker": t,
                             "iv_level": lvl + rng.normal(0, 0.03),
                             "iv_skew": rng.normal(0.02, 0.01),
                             "put_call": abs(rng.normal(1.0, 0.2))})
        return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# News / social sentiment — ADAPTER (wire GDELT / Alpha-Vantage / RavenPack)
# ---------------------------------------------------------------------------

class NewsSentiment(FeatureSource):
    """News sentiment from **GDELT** (free, no key): per-company daily average news TONE
    and coverage VOLUME (buzz), via the DOC 2.0 timeline API.

    Honest limits: GDELT DOC 2.0 starts ~2017 (short history), matches companies by NAME
    (from SEC `company_tickers`, so current filers only — delisted names get no sentiment,
    filled neutral downstream), and the API is rate-limited (so `max_names` is capped). It
    is a genuine free differentiated-data test, not a full survivorship-clean feed."""
    name = "news_sentiment"
    _TITLES_URL = "https://www.sec.gov/files/company_tickers.json"
    _API = "https://api.gdeltproject.org/api/v2/doc/doc"

    def _titles(self) -> dict[str, str]:
        try:
            data = json.loads(_http_get(self._TITLES_URL))
            return {row["ticker"].upper(): row["title"] for row in data.values()}
        except Exception:
            return {}

    @staticmethod
    def _parse_timeline(raw: bytes, col: str) -> pd.DataFrame:
        """Parse a GDELT DOC-2.0 timeline JSON into [known_date, <col>]."""
        try:
            series = json.loads(raw)["timeline"][0]["data"]
        except Exception:
            return pd.DataFrame(columns=["known_date", col])
        rows = [{"known_date": d["date"], col: d["value"]} for d in series if "date" in d]
        return pd.DataFrame(rows)

    def _timeline(self, name: str, mode: str, start: str, end: str, col: str) -> pd.DataFrame:
        sd = pd.to_datetime(max(start, "2017-01-01")).strftime("%Y%m%d000000")
        ed = pd.to_datetime(end).strftime("%Y%m%d000000")
        q = urllib.parse.quote(f'"{name}"')
        url = f"{self._API}?query={q}&mode={mode}&format=json&startdatetime={sd}&enddatetime={ed}"
        try:
            df = self._parse_timeline(_http_get(url, timeout=8), col)  # short timeout: GDELT throttles
            time.sleep(0.2)
            return df
        except Exception:
            return pd.DataFrame(columns=["known_date", col])

    def observations(self, tickers, start, end, max_names: int = 40, max_seconds: int = 150):
        """GDELT per-name tone (+ buzz). GDELT's DOC API is rate-limited and slow, so this
        is capped hard: at most `max_names` names and a `max_seconds` wall-clock budget —
        a demo-scale, honest test of differentiated data, not a full production feed (that
        needs GDELT's bulk GKG files or a paid sentiment vendor)."""
        titles = self._titles()
        if not titles:
            return pd.DataFrame()
        out, deadline = [], time.time() + max_seconds
        for t in tickers[:max_names]:
            if time.time() > deadline:
                break                             # stay inside the time budget
            nm = titles.get(t.upper())
            if not nm:
                continue
            tone = self._timeline(nm, "timelinetone", start, end, "sentiment")
            if tone.empty:
                continue
            vol = self._timeline(nm, "timelinevol", start, end, "buzz")
            m = tone.merge(vol, on="known_date", how="left") if not vol.empty else tone
            m["ticker"] = t
            out.append(m)
        return pd.concat(out, ignore_index=True) if out else pd.DataFrame()

    def synthetic(self, tickers, start, end):
        rng = np.random.default_rng(33)
        days = pd.bdate_range(start, end)
        rows = []
        for t in tickers:
            walk = np.cumsum(rng.normal(0, 0.05, len(days)))
            for d, s in zip(days[::3], walk[::3]):     # every 3rd day
                rows.append({"known_date": d, "ticker": t,
                             "sentiment": np.tanh(s),
                             "buzz": abs(rng.normal(1.0, 0.4))})
        return pd.DataFrame(rows)


# Convenience: all three families.
ALL_SOURCES = [EdgarFundamentals(), OptionIV(), NewsSentiment()]
