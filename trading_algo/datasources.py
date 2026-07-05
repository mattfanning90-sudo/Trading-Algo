"""Alternative-data feature sources for the predictive model — fundamentals, options
implied vol, and news/social sentiment — merged into the feature panel WITHOUT lookahead.

The whole risk with alt-data is timing: a fundamental is only usable *after it was
filed* (not as of the period it covers); IV/sentiment only as of the day observed.
So every source reports a **`known_date`** (when the information became public), and
`asof_panel` carries values forward from `known_date` only — never backward. Get this
wrong and the backtest is fiction.

The alt-data columns only start to *matter* once they encode the CHANGE/surprise the
anomaly actually rewards, not a stale level (a cross-sectionally z-scored quality level
carries almost no forward-ordering information). So this module emits:
- **`sue`** — a seasonal earnings SURPRISE (PEAD; Ball-Brown, Bernard-Thomas), the one
  candidate whose ~1-quarter drift horizon matches the 21-day label, built from
  duration-filtered quarterly NetIncomeLoss and decayed over the drift window.
- **`sentiment_shock` / `buzz_shock`** — tone/attention CHANGES vs a trailing baseline
  (Tetlock; Barber-Odean), differenced from the raw dated prints, decayed over ~2 weeks.
- **`has_sentiment`** — a raw 0/1 coverage mask used ONLY to sub-select the covered
  cross-section for evaluation; it is excluded from the model (see mlpipeline.RAW_MASK)
  because GDELT coverage is a survivorship/recency proxy, not a tradeable signal.

Availability (honest):
- **Fundamentals** — REAL and free: SEC EDGAR `companyfacts` gives XBRL facts *with
  filing dates*. `EdgarFundamentals` fetches them (US only; needs network → runs in CI).
- **Options IV** — no free history. `OptionIV` is an adapter (synthetic-only); its columns
  are DEFERRED and never scored for a pass (see mlreport) until a paid feed is wired.
- **Sentiment** — no free clean history. `NewsSentiment` wires GDELT DOC 2.0 (real but
  ~2017+, name-matched, rate-limited); synthetic generator meanwhile.

Each source yields columns; `build_extra_panel` merges them all as-of into a
`[(date, ticker), feature]` panel that `features.build_feature_panel(..., extra=...)`
folds in as extra columns. See `docs/research/PREDICTIVE_MODEL.md`.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd

_UA = {"User-Agent": "trading-algo research mattfanning90@gmail.com"}

# Pre-registered event-decay windows — FIXED from the literature, never swept (the
# chief-engineer guard: a swept gate is unpaid multiplicity). PEAD drift runs ~one
# quarter (Bernard-Thomas 1989); news tone/attention shocks decay over ~2 weeks
# (Tetlock 2007; Barber-Odean 2008).
SUE_DRIFT_DAYS = 63        # ~one quarter of trading days: PEAD drift window
NEWS_SHOCK_DAYS = 10       # ~2 trading weeks: news-shock decay window
NEWS_SHOCK_WINDOW = 20     # trailing known-day window for the tone/buzz baseline

# Pre-registered PEAD announcement-timing windows (fixed from the SEC filing calendar, never
# swept). Earnings break in the 8-K Item 2.02 press release; we move the SUE impulse from the
# (days-to-weeks later) 10-Q filing to that announcement so the drift window aligns with the
# actual drift start (Bernard-Thomas). Only the TIMING moves — the surprise magnitude is the
# FIRST-reported value, so no restated/final number is ever back-dated onto the announcement.
ANN_MIN_DAYS = 5      # an earnings 8-K lands at least a few days after quarter-end
ANN_MAX_DAYS = 75     # ...and (even for accelerated filers) within ~one quarter, capped <= 10-Q
MAX_10Q_LAG = 60      # a first 10-Q follows the release within weeks; a value whose kept filing
                      # lands > this after the 8-K is a restatement → NOT back-dated


def _http_get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------------------
# Leakage-safe as-of merge (the piece that matters)
# ---------------------------------------------------------------------------

def asof_panel(obs: pd.DataFrame, index: pd.DatetimeIndex,
               decay: dict | None = None) -> pd.DataFrame:
    """Carry each source observation forward from its `known_date` onto the trading
    calendar — the no-lookahead join.

    `obs`: columns [known_date, ticker, <feature...>]. Returns a long panel indexed by
    (date, ticker) where each feature's value at date t is the most recent observation
    with `known_date` ≤ t (ffill of past-known values only). A value with known_date > t
    is invisible at t — that is the whole guarantee.

    `decay`: optional per-COLUMN event decay ``{col: (mode, gate_days, tau)}``. For a
    column in the map, the ffilled value is multiplied by a weight that fades with the
    age of the underlying event — ``linear`` = clip(1 − days_since/gate, 0, 1), ``exp`` =
    exp(−days_since/tau) zeroed past ``gate_days`` — so a filing/tone print is a decaying
    IMPULSE, not a stale plateau. `days_since` derives only from a ffilled PAST known_date
    (>= 0 by construction), so no future information enters at t. Columns absent from the
    map get plain ffill (level behaviour); `decay=None` reproduces the legacy path exactly.
    """
    if obs is None or obs.empty:
        return pd.DataFrame()
    decay = decay or {}
    feats = [c for c in obs.columns if c not in ("known_date", "ticker")]
    obs = obs.copy()
    # normalise to tz-naive: GDELT stamps are UTC ("...Z"), price dates are tz-naive —
    # mixing them breaks the sort/union. Coerce everything to naive UTC wall-time.
    obs["known_date"] = pd.to_datetime(obs["known_date"], utc=True).dt.tz_localize(None)
    # pin to ns before the int cast: pandas 2.x may carry µs/ms resolution, and the naive
    # `.astype("int64")` would then be in the wrong unit (days_since off by 1000×).
    idx_ns = index.values.astype("datetime64[ns]").astype("int64")
    out = {}
    for f in feats:
        sub = obs[["known_date", "ticker", f]]
        wide = sub.pivot_table(index="known_date", columns="ticker", values=f, aggfunc="last")
        wide = (wide.reindex(wide.index.union(index)).sort_index()
                    .ffill().reindex(index))          # ffill = past→future only, never back
        if f in decay:
            # per-column last-known-date, ffilled, to age the event (only past known_dates)
            nn = sub.dropna(subset=[f]).copy()
            nn["_kd"] = nn["known_date"].values.astype("datetime64[ns]").astype("int64")
            kd = nn.pivot_table(index="known_date", columns="ticker", values="_kd", aggfunc="last")
            kd = (kd.reindex(kd.index.union(index)).sort_index().ffill()
                    .reindex(index).reindex(columns=wide.columns))
            days = (idx_ns[:, None] - kd.to_numpy()) / 86_400e9      # days since the event, >= 0
            mode, gate, tau = decay[f]
            with np.errstate(invalid="ignore", over="ignore"):
                if mode == "linear":
                    w = np.clip(1.0 - days / gate, 0.0, 1.0)
                else:                                                # "exp"
                    w = np.exp(-days / tau)
                    if gate is not None:
                        w = np.where(days > gate, 0.0, w)
            w = np.where(np.isnan(days), np.nan, w)                  # never-known cell → neutral
            wide = wide * w
        out[f] = wide.stack()
    panel = pd.concat(out, axis=1)
    panel.index.names = ["date", "ticker"]
    return panel.dropna(how="all")


class FeatureSource:
    """A named alt-data source that yields point-in-time observations."""
    name = "base"
    # Per-column event decay applied by build_extra_panel/asof_panel (see asof_panel).
    decay: dict = {}

    def observations(self, tickers: list[str], start: str, end: str | None) -> pd.DataFrame:
        """Return columns [known_date, ticker, <feature...>]. Empty frame if unavailable."""
        raise NotImplementedError

    def synthetic(self, tickers: list[str], start: str, end: str) -> pd.DataFrame:
        """Offline placeholder observations (meaningless values; plumbing only). Must emit
        the SAME columns and route through the SAME transforms as `observations` so the
        synthetic run is a real negative control for the new-feature code path."""
        raise NotImplementedError


def build_extra_panel(sources: list[FeatureSource], prices: pd.DataFrame,
                      start: str, end: str | None = None,
                      synthetic: bool = False) -> pd.DataFrame:
    """As-of-merge every source into one extra-feature panel aligned to `prices`.
    Each source's per-column `decay` config is threaded through so surprise/shock
    features fade as decaying impulses while level/mask columns plain-ffill."""
    tickers = list(prices.columns)
    end = end or str(prices.index[-1].date())
    panels = []
    for s in sources:
        obs = (s.synthetic(tickers, start, end) if synthetic
               else s.observations(tickers, start, end))
        p = asof_panel(obs, prices.index, decay=getattr(s, "decay", {}))
        if not p.empty:
            panels.append(p)
    return pd.concat(panels, axis=1) if panels else pd.DataFrame()


# ---------------------------------------------------------------------------
# Fundamentals — SEC EDGAR (REAL, free, point-in-time via filing dates)
# ---------------------------------------------------------------------------

class EdgarFundamentals(FeatureSource):
    """Point-in-time fundamentals from SEC EDGAR XBRL `companyfacts`.

    Uses the **filing date** (`filed`) as `known_date`, so a metric is only visible
    after it was actually reported. Features: return on equity, net margin, YoY asset
    growth (pre-signed negative per Cooper-Gulen-Schill), and — the genuinely new,
    horizon-matched signal — a seasonal earnings surprise `sue` (PEAD)."""
    name = "edgar_fundamentals"
    _CIK_URL = "https://www.sec.gov/files/company_tickers.json"
    # SUE decays over the ~1-quarter PEAD drift window; roe/net_margin/asset_growth are
    # slow levels (plain ffill), has no decay entry.
    decay = {"sue": ("linear", SUE_DRIFT_DAYS, None)}

    def _ticker_cik(self) -> dict[str, str]:
        try:
            data = json.loads(_http_get(self._CIK_URL))
            return {row["ticker"].upper(): f"{int(row['cik_str']):010d}"
                    for row in data.values()}
        except Exception:
            return {}

    @staticmethod
    def _parse_submission_arrays(block: dict) -> pd.DataFrame:
        """Zip one EDGAR submissions parallel-array block (filings.recent, or an older
        filings.files JSON — same flat shape) into rows. Empty on a missing/short block."""
        cols = ("form", "filingDate", "reportDate", "items", "accessionNumber")
        if not block or "form" not in block:
            return pd.DataFrame(columns=cols)
        n = len(block["form"])
        return pd.DataFrame({c: (block.get(c) or [None] * n) for c in cols})

    def _submissions(self, cik: str) -> pd.DataFrame:
        """Full filing history for a CIK (recent + paginated older files back to ~2007) as a
        flat frame [form, filingDate, reportDate, items, accessionNumber]. Empty on any
        network/parse failure — a graceful offline fallback, exactly like _ticker_cik."""
        base = "https://data.sec.gov/submissions"
        try:
            data = json.loads(_http_get(f"{base}/CIK{cik}.json"))
            time.sleep(0.12)                       # SEC fair-access
        except Exception:
            return pd.DataFrame()
        filings = data.get("filings") or {}
        frames = [self._parse_submission_arrays(filings.get("recent") or {})]
        for f in filings.get("files") or []:       # older filings, paginated by EDGAR
            name = f.get("name")
            if not name:
                continue
            try:
                frames.append(self._parse_submission_arrays(json.loads(_http_get(f"{base}/{name}"))))
                time.sleep(0.12)
            except Exception:
                continue
        frames = [f for f in frames if not f.empty]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def _earnings_announcements(self, cik: str) -> pd.DataFrame:
        """8-K Item 2.02 (Results of Operations) filings for a CIK → [announce_date,
        report_date] sorted. The 8-K filingDate is when the earnings press release became
        public on EDGAR (>= the item event date, so the conservative announcement stamp)."""
        subs = self._submissions(cik)
        cols = ["announce_date", "report_date"]
        if subs.empty:
            return pd.DataFrame(columns=cols)
        m = subs[(subs["form"] == "8-K") & subs["items"].fillna("").str.contains("2.02")]
        if m.empty:
            return pd.DataFrame(columns=cols)
        return (pd.DataFrame({"announce_date": pd.to_datetime(m["filingDate"], errors="coerce"),
                              "report_date": pd.to_datetime(m["reportDate"], errors="coerce")})
                .dropna(subset=["announce_date"]).sort_values("announce_date")
                .reset_index(drop=True))

    @staticmethod
    def _series(facts: dict, concept: str) -> pd.DataFrame:
        """(known_date=filed, end, start, val) rows for one us-gaap concept, USD units.
        `start` (present for flow concepts, absent for balance-sheet stocks) lets us
        filter a flow series to true ~quarterly periods before differencing."""
        try:
            units = facts["facts"]["us-gaap"][concept]["units"]
        except Exception:
            return pd.DataFrame()
        key = next((k for k in units if k.upper().startswith("USD")), None)
        if key is None:
            return pd.DataFrame()
        rows = [{"known_date": r["filed"], "end": r["end"], "start": r.get("start"),
                 "val": r["val"]}
                for r in units[key] if r.get("filed") and r.get("val") is not None]
        df = pd.DataFrame(rows)
        if not df.empty:
            # Normalise date columns up front so every downstream merge on 'end' and
            # comparison on 'known_date' is datetime-vs-datetime. Real filings arrive as
            # ISO strings; converting on only SOME paths (e.g. _quarterly) caused a
            # str-vs-datetime merge-key mismatch that only surfaced on real data.
            df["end"] = pd.to_datetime(df["end"])
            df["known_date"] = pd.to_datetime(df["known_date"])
            df["start"] = pd.to_datetime(df["start"])
        return df

    @staticmethod
    def _quarterly(s: pd.DataFrame, keep: str = "last") -> pd.DataFrame:
        """Filter a us-gaap series to ~quarterly (≈90-day) periods and dedupe by period-
        end. Flow concepts like NetIncomeLoss mix 10-Q quarterly and 10-K cumulative-YTD/
        annual values under one tag; a positional seasonal diff over that mix is noise, so
        keep only true quarters. Balance-sheet stocks (no `start`) skip the duration filter.

        `keep`: which filing to keep per period-end. ``"last"`` = the latest restatement (best
        current view, for level features). ``"first"`` = the FIRST-reported value — required
        for the announcement-dated SUE, so a later restatement is never back-dated onto the
        earnings announcement (the value that was public then is the first-reported one)."""
        if s.empty:
            return s
        s = s.copy()
        s["end"] = pd.to_datetime(s["end"])
        s["known_date"] = pd.to_datetime(s["known_date"])
        if "start" in s.columns and s["start"].notna().any():
            dur = (s["end"] - pd.to_datetime(s["start"])).dt.days
            s = s[(dur >= 60) & (dur <= 120)]                    # ~one quarter only
        s = (s.sort_values("known_date").drop_duplicates("end", keep=keep)
               .sort_values("end").reset_index(drop=True))
        return s

    @staticmethod
    def _attach_announcement(rows: pd.DataFrame, announce: pd.DataFrame) -> pd.Series:
        """Remap each SUE row's known_date from its (first) filing date to the EARLIEST 8-K
        Item 2.02 announcement inside the pre-registered window
        [end+ANN_MIN_DAYS, min(filing, end+ANN_MAX_DAYS)], with a MAX_10Q_LAG restatement
        guard. Rows with no qualifying announcement keep their filing date (today's behaviour).
        Only ever moves known_date EARLIER onto another real, already-public date — so it can
        never introduce lookahead; it fixes a timing mis-specification (PEAD drift starts at the
        announcement, not the later 10-Q)."""
        ann = np.sort(pd.to_datetime(announce["announce_date"]).to_numpy())
        end = pd.to_datetime(rows["end"]).to_numpy()
        filed = pd.to_datetime(rows["known_date"]).to_numpy()
        out = filed.copy()
        day = np.timedelta64(1, "D")
        for i in range(len(rows)):
            lo = end[i] + ANN_MIN_DAYS * day
            hi = min(filed[i], end[i] + ANN_MAX_DAYS * day)
            if hi < lo or not len(ann):
                continue
            j = np.searchsorted(ann, lo, side="left")            # earliest announce >= lo
            if j < len(ann) and ann[j] <= hi and (filed[i] - ann[j]) <= MAX_10Q_LAG * day:
                out[i] = ann[j]
        return pd.Series(out, index=rows.index)

    @staticmethod
    def _seasonal_surprise(ni: pd.DataFrame, eq: pd.DataFrame, lookback: int = 8,
                           announce: pd.DataFrame | None = None) -> pd.DataFrame:
        """SUE (PEAD): seasonal earnings surprise per filing, [known_date, sue].

        SUE_q = (NI_q − NI_{q−4}) / std(past seasonal diffs), scaled by contemporaneous
        StockholdersEquity for scale-free comparability. Uses FIRST-reported values
        (`keep='first'`) so a later restatement is never back-dated. known_date starts as the
        later of the NI and equity first-filing dates (no not-yet-public denominator); if an
        `announce` frame (8-K Item 2.02 dates) is given, it is moved EARLIER to the earnings-
        announcement date so the drift window aligns with the actual PEAD start. Pre-signed +."""
        ni = EdgarFundamentals._quarterly(ni, keep="first")
        if len(ni) < 5:
            return pd.DataFrame(columns=["known_date", "sue"])
        ni = ni.sort_values("end").reset_index(drop=True)
        d = ni["val"] - ni["val"].shift(4)                       # YoY seasonal diff
        sd = d.shift(1).rolling(lookback, min_periods=4).std()   # std of PAST diffs only
        rows = ni[["end", "known_date"]].copy()
        rows["sue"] = (d / sd.replace(0.0, np.nan)).to_numpy()
        if eq is not None and not eq.empty:
            eqq = EdgarFundamentals._quarterly(eq, keep="first")[["end", "val", "known_date"]]
            m = rows.merge(eqq.rename(columns={"val": "eqv", "known_date": "eq_kd"}),
                           on="end", how="left")
            m["known_date"] = m[["known_date", "eq_kd"]].max(axis=1)
            m["sue"] = m["sue"] / m["eqv"].abs().replace(0.0, np.nan)
            rows = m[["end", "known_date", "sue"]]
        rows = rows.replace([np.inf, -np.inf], np.nan).dropna(subset=["sue"]).reset_index(drop=True)
        if announce is not None and not announce.empty and not rows.empty:
            rows["known_date"] = EdgarFundamentals._attach_announcement(rows, announce)
        return rows[["known_date", "sue"]]

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
            # roe / net_margin / asset_growth (slow levels) — align on period-end, keep the
            # LATER filing date as known_date (denominator must be public too)
            m = ni.merge(eq, on="end", suffixes=("_ni", "_eq"))
            m["known_date"] = m[["known_date_ni", "known_date_eq"]].max(axis=1)
            m["roe"] = m["val_ni"] / m["val_eq"].replace(0, np.nan)
            if not rev.empty:
                m = m.merge(rev.rename(columns={"val": "val_rev"})[["end", "val_rev"]], on="end", how="left")
                m["net_margin"] = m["val_ni"] / m["val_rev"].replace(0, np.nan)
            if not assets.empty:
                a = self._quarterly(assets).sort_values("end")
                # Cooper-Gulen-Schill: high YoY asset growth → LOW returns. Pre-signed
                # (negated) for interpretability; a linear ridge would learn the sign anyway.
                a["asset_growth"] = -a["val"].pct_change(4)
                m = m.merge(a[["end", "asset_growth"]], on="end", how="left")
            m["ticker"] = t
            keep = ["known_date", "ticker"] + [k for k in ("roe", "net_margin", "asset_growth") if k in m]
            parts = [m[keep]]
            # sue: earnings-surprise impulses dated to the 8-K Item 2.02 announcement (PEAD
            # drift start), falling back to the 10-Q filing date where no 8-K is found.
            ann = self._earnings_announcements(c)
            sue = self._seasonal_surprise(ni, eq, announce=ann)
            if not sue.empty:
                parts.append(sue.assign(ticker=t)[["known_date", "ticker", "sue"]])
            out.append(pd.concat(parts, ignore_index=True))
        return pd.concat(out, ignore_index=True) if out else pd.DataFrame()

    def synthetic(self, tickers, start, end):
        # Route synthetic data through the SAME _seasonal_surprise/_quarterly path so the
        # negative control genuinely exercises the surprise code. NI is independent of
        # prices (seed 11) → sue must carry ~0 incremental IC.
        rng = np.random.default_rng(11)
        qtrs = pd.date_range(start, end, freq="QE")
        filed = qtrs + pd.Timedelta(days=45)                     # filed ~45d after quarter
        starts = qtrs - pd.Timedelta(days=90)
        out = []
        for t in tickers:
            base_roe, base_m = rng.normal(0.12, 0.05), rng.normal(0.08, 0.04)
            eqv = abs(rng.normal(1000.0, 200.0))
            ni_vals = eqv * (base_roe + rng.normal(0, 0.02, len(qtrs)))
            rows = pd.DataFrame({
                "known_date": filed, "ticker": t,
                "roe": base_roe + rng.normal(0, 0.02, len(qtrs)),
                "net_margin": base_m + rng.normal(0, 0.01, len(qtrs)),
                "asset_growth": -rng.normal(0.05, 0.03, len(qtrs)),   # pre-signed like real
            })
            out.append(rows)
            ni = pd.DataFrame({"known_date": filed, "end": qtrs, "start": starts, "val": ni_vals})
            eq = pd.DataFrame({"known_date": filed, "end": qtrs, "start": pd.NaT,
                               "val": eqv + rng.normal(0, 10, len(qtrs))})
            # synthetic 8-K announcement ~15d before the 10-Q → exercises the back-dating path
            announce = pd.DataFrame({"announce_date": qtrs + pd.Timedelta(days=30),
                                     "report_date": qtrs})
            sue = self._seasonal_surprise(ni, eq, announce=announce)
            if not sue.empty:
                out.append(sue.assign(ticker=t)[["known_date", "ticker", "sue"]])
        return pd.concat(out, ignore_index=True)


# ---------------------------------------------------------------------------
# Options implied vol — ADAPTER (DEFERRED: no real free history; synthetic only)
# ---------------------------------------------------------------------------

class OptionIV(FeatureSource):
    """Options-implied features: ATM IV level, IV skew (put−call), put/call. No free
    history — wire Polygon options / ORATS / CBOE into `observations` (option-observation
    date as known_date); until then only `synthetic` runs. DEFERRED: iv_* columns are
    never scored for a pass (mlreport excludes them), because synthetic IV is independent
    of synthetic prices and any 'edge' would be a leakage artifact, not a result."""
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
# News / social sentiment — GDELT (real, free, ~2017+); shocks, not levels
# ---------------------------------------------------------------------------

class NewsSentiment(FeatureSource):
    """News sentiment from **GDELT** (free, no key): per-company daily average news TONE
    and coverage VOLUME (buzz), via the DOC 2.0 timeline API. The tradeable content is the
    CHANGE, so this emits `sentiment_shock` (tone vs trailing baseline) and `buzz_shock`
    (log attention ratio) rather than the standing level, plus a raw `has_sentiment` 0/1
    coverage mask (excluded from the model; used only to sub-select covered names).

    Honest limits: GDELT DOC 2.0 starts ~2017 (short history), matches companies by NAME
    (from SEC `company_tickers`, so current filers only — delisted names get no sentiment,
    a survivorship/recency proxy), and the API is rate-limited (so `max_names` is capped).
    A genuine free differentiated-data test, not a full survivorship-clean feed."""
    name = "news_sentiment"
    _TITLES_URL = "https://www.sec.gov/files/company_tickers.json"
    _API = "https://api.gdeltproject.org/api/v2/doc/doc"
    # tone/attention shocks decay over ~2 weeks; has_sentiment is a coverage flag (ffill).
    decay = {"sentiment_shock": ("linear", NEWS_SHOCK_DAYS, None),
             "buzz_shock": ("linear", NEWS_SHOCK_DAYS, None)}

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

    @staticmethod
    def _shock(s: pd.Series, window: int = NEWS_SHOCK_WINDOW, log_ratio: bool = False) -> pd.Series:
        """Change/attention shock from RAW dated observations (already sorted by
        known_date). Baseline = trailing mean of PRIOR known values (shift(1) → the
        current obs is never in its own baseline, so no self-leak). Level shock = value −
        baseline; attention shock = log(value / baseline)."""
        base = s.shift(1).rolling(window, min_periods=5).mean()
        if log_ratio:
            eps = 1e-6
            out = np.log((s + eps) / (base + eps))
            return out.where(base > 0)
        return s - base

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

    @staticmethod
    def _to_shocks(m: pd.DataFrame, ticker: str) -> pd.DataFrame:
        """From raw [known_date, sentiment, buzz?] rows → shock columns + coverage mask."""
        m = m.sort_values("known_date").reset_index(drop=True)
        m["sentiment_shock"] = NewsSentiment._shock(m["sentiment"])
        cols = ["known_date", "sentiment_shock"]
        if "buzz" in m.columns:
            m["buzz_shock"] = NewsSentiment._shock(m["buzz"], log_ratio=True)
            cols.append("buzz_shock")
        m["has_sentiment"] = 1.0
        m["ticker"] = ticker
        return m[cols + ["has_sentiment", "ticker"]]

    @staticmethod
    def _coverable(tickers, titles) -> list:
        """Order-preserving subset of `tickers` GDELT can name-match (have a title). The PIT
        run passes ~1000 membership names, many delisted/obscure with no current title; the
        old `tickers[:max_names]` spent the whole budget on those leading un-mappable names and
        returned 0 covered dates. Slicing AFTER coverability lands the budget on real matches."""
        return [t for t in tickers if titles.get(t.upper())]

    def observations(self, tickers, start, end, max_names: int = 150, max_seconds: int = 600):
        """GDELT per-name tone (+ buzz) as shocks. GDELT's DOC API is rate-limited and slow,
        so this is capped: at most `max_names` COVERABLE names and a `max_seconds` wall-clock
        budget. Still name-matched to CURRENT filers (company_tickers.json), so the covered set
        is explicitly survivor-conditioned — its IC is corroboration only, never a pass gate.
        A survivorship-COMPLETE feed needs GDELT's bulk GKG files or a paid vendor."""
        titles = self._titles()
        if not titles:
            return pd.DataFrame()
        out, deadline = [], time.time() + max_seconds
        for t in self._coverable(tickers, titles)[:max_names]:
            if time.time() > deadline:
                break                             # stay inside the time budget
            nm = titles.get(t.upper())            # guaranteed present by _coverable
            tone = self._timeline(nm, "timelinetone", start, end, "sentiment")
            if tone.empty:
                continue
            vol = self._timeline(nm, "timelinevol", start, end, "buzz")
            m = tone.merge(vol, on="known_date", how="left") if not vol.empty else tone
            out.append(self._to_shocks(m, t))
        return pd.concat(out, ignore_index=True) if out else pd.DataFrame()

    def synthetic(self, tickers, start, end):
        # Same _shock path as real data (negative control). Noise walk is independent of
        # prices (seed 33) → the shocks must carry ~0 incremental IC.
        rng = np.random.default_rng(33)
        days = pd.bdate_range(start, end)
        out = []
        for t in tickers:
            walk = np.tanh(np.cumsum(rng.normal(0, 0.05, len(days))))
            buzz = np.abs(rng.normal(1.0, 0.4, len(days)))
            m = pd.DataFrame({"known_date": days, "sentiment": walk, "buzz": buzz}).iloc[::3]
            out.append(self._to_shocks(m, t))
        return pd.concat(out, ignore_index=True)


# Convenience: all three families.
ALL_SOURCES = [EdgarFundamentals(), OptionIV(), NewsSentiment()]

# Coverage/indicator columns that must NEVER be fed to the model (survivorship/recency
# proxies) — mlpipeline drops these from the feature matrix; used only for sub-universe
# evaluation. Kept here so features and mlpipeline agree on the one list.
MASK_COLS = ("has_sentiment",)

# Options-IV columns are DEFERRED (synthetic-only, no real feed) and excluded from any
# pass claim; mlreport reads this to keep them out of the scored alt columns.
DEFERRED_COLS = ("iv_level", "iv_skew", "put_call")
