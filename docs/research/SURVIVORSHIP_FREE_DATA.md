# Free / Open-Source Survivorship-Bias-Free Data

How to feed the backtester the two things that kill survivorship bias —
**point-in-time index membership (incl. delisted names)** and **delisting-inclusive
prices** — without a paid data subscription. Cited research; mapped to the code.

## The honest headline

A genuinely free, survivorship-bias-free build is realistic **for the US sleeve
only**. The blocker everywhere is the same: **delisted price history is the one
thing that is almost never free**, and what exists is overwhelmingly US-centric.

| Market | PIT members (free?) | Delisted prices (free?) | Verdict |
|---|---|---|---|
| **US** | ✅ fja05680 / hanshof (MIT CSV, graveyard, 1996→) | ✅ Tiingo free tier (keeps delisted) | **free build viable** |
| **FTSE 100** | ✅ LSEG Constituent-History PDF (to 1984) | ❌ EODHD (~€30/mo) only | members free, prices paid |
| **ASX 200** | ⚠️ iShares IOZ holdings + Wayback only | ❌ none free | **must pay** (Norgate A$630/yr) |

## US — the free recipe (implemented)

1. **Point-in-time constituents** — `fja05680/sp500` ships
   `S&P 500 Historical Components & Changes.csv`: dated snapshots from 1996→now
   that *include the delisted graveyard* (removed names drop out of later rows).
   MIT-licensed; reliable from ~2001. `hanshof/sp500_constituents` is an
   equivalent Wikipedia-generated mirror. The DIY equivalent is the Wikipedia
   **MediaWiki Revisions API** on "List of S&P 500 companies" (read old revisions,
   parse each table), but the curated repos already clean up Wikipedia's
   incomplete "Selected changes" table.
   → `constituents.download_constituents("US")` fetches it into the cache;
   `MembershipTable.from_wide_frame` parses the "date, comma-list" format and
   normalises class shares (BRK.B → BRK-B).
2. **Delisted prices** — Yahoo/yfinance **purges delisted tickers** (structurally
   survivorship-biased); **Tiingo** retains them on a free key (~50 symbols/hr).
   → `providers.TiingoProvider` (set `TIINGO_API_KEY`); appended to the chain so
   it prices the delisted names yfinance/stooq drop.
3. **Delisting return** — a name that stops trading must book a terminal loss, not
   exit at its last quote. Shumway (1997) uses **−55%** for missing
   performance-related delistings (correcting it erased the apparent Nasdaq size
   effect); practitioner convention is **−100% bankruptcy, −30%/−55% other
   performance-related, merger value for neutral** (Alpha Architect).
   → `data.apply_delisting_returns(prices, still_listed, default_return=-0.30)`.
4. **Run it** — `validate --point-in-time --region US` now uses real PIT
   membership + delisted prices + delisting returns → survivorship-**corrected**
   numbers.

### More-accurate variant (also free)
Scrape archived **iShares IVV month-end holdings** (`etf-scraper` / `talsan/ishares`,
~2006/2010→) backfilled with the **Wayback Machine CDX API** for older gaps — real
point-in-time membership including names later removed (Teddy Koker's recipe).
Identify delistings/dates for free via **SEC EDGAR Form 25-NSE**
(`efts.sec.gov` / `data.sec.gov`, no key) and resolve renamed/defunct tickers with
**OpenFIGI** (free, permanent identifiers).

## FTSE 100 / ASX 200
- **FTSE members** are free from the official **LSEG "FTSE 100 Constituent History"
  PDF** (every add/delete since 1984) — parse it to a `date,ticker` CSV and point
  `Region("FTSE", constituents_file=...)` at it. But **delisted UK prices are
  paid** (EODHD ~€30/mo; Norgate has no UK).
- **ASX** is the real gap: no maintained free constituents dataset and no free
  delisted prices. Options: snapshot **iShares IOZ / SPDR STW** holdings forward
  (+ Wayback for the past), or pay **Norgate Platinum (A$630/yr)** — the retail
  standard — or **EODHD** (~€30/mo, shallow non-US delisted history).

## The cost ladder (if paying is acceptable)
| Option | Cost | Covers | Notes |
|---|---|---|---|
| Tiingo free | $0 | US delisted prices | 50 sym/hr; this build uses it |
| **Sharadar SEP** | ~US$50/mo | US, survivorship-free | clean retail gold standard |
| **EODHD** | ~€30/mo | US + ASX + LSE delisted | only affordable all-markets feed |
| **Norgate Platinum** | A$/US$630/yr | US + ASX, incl. delisted + PIT members | ASX standard |
| CRSP | institutional | US gold standard | WRDS/academic only — not retail |

## Caveats
- Free tiers are **personal-use, no-redistribution**, and rate-limited — fine for
  a personal backtest, not for republishing data.
- Yahoo "Adj Close" has documented split/dividend defects; prefer raw Close +
  actions for delisted names.
- `fja05680` is unreliable 1996–2000 (use 2001+); the index isn't always exactly
  500 names. Wayback ETF-holdings coverage is patchy.
- The delisting-return default (−30%) is a blanket figure; classifying each event
  (bankruptcy vs merger) from EDGAR would refine it.

### Key sources
- fja05680 S&P 500 history — https://github.com/fja05680/sp500
- hanshof S&P 500 constituents — https://github.com/hanshof/sp500_constituents
- Teddy Koker, survivorship-free SPY — https://teddykoker.com/2019/05/creating-a-survivorship-bias-free-sp-500-dataset-with-python/
- etf-scraper — https://pypi.org/project/etf-scraper/ ; talsan/ishares — https://github.com/talsan/ishares
- Tiingo — https://www.tiingo.com/ ; Sharadar SEP — https://data.nasdaq.com/databases/SEP
- EODHD delisted — https://eodhd.com/financial-apis/delisted-stock-companies-data
- Norgate packages — https://norgatedata.com/stockmarketpackages.php
- LSEG FTSE 100 Constituent History (PDF) — https://www.lseg.com/content/dam/ftse-russell/en_us/documents/policy-documents/ftse-100-constituent-history.pdf
- Shumway, Delisting Bias in CRSP — https://www.tylergshumway.org/Shumway-DelistingBiasCRSP-1997.pdf
- Alpha Architect, dealing with delistings — https://alphaarchitect.com/dealing-with-delistings-a-critical-aspect-for-stock-selection-research/
- SEC EDGAR APIs — https://www.sec.gov/search-filings/edgar-application-programming-interfaces ; OpenFIGI — https://www.openfigi.com/api/documentation
- Wikipedia MediaWiki Revisions API — https://www.mediawiki.org/wiki/API:Revisions
