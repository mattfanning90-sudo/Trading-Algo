# Live-book audit — what `python -m trading_algo.verify` checks, and what it found

## Why this exists

`pytest -q` passes 572 tests. It passed while a third of the main portfolio sat
in cash for six weeks and while four books were filling orders on a market that
was shut. That is not a criticism of the suite — it is the point. The tests prove
the *maths* is right on a clean price matrix. Nothing was proving the *books*
were right.

`verify.py` closes that gap. It reads the state the schedulers actually wrote and
re-derives it from the trade ledger alone, the way you would reconcile a broker
statement. It never touches the network: the persisted state *is* the record of
what the system did, so the audit is reproducible offline, runs in CI, and can
never be papered over by a lucky re-download.

```bash
python -m trading_algo.verify                 # every book, human report
python -m trading_algo.verify --json          # machine-readable
python -m trading_algo.verify --strict        # exit 1 on any ERROR
```

It runs automatically after every scheduled paper run (`paper-trade`, `fx-paper`,
`day-paper`) and writes to the job step summary. It is report-only there: an
audit must not block the trading run it is auditing.

## What it checks

| Family | Question | Codes |
|---|---|---|
| **RECONCILE** | Does the trade ledger reproduce the stored cash, positions and weights? | `position-mismatch`, `cash-drift`, `weight-mismatch`, `missing-position` |
| **REALISM** | Would a real broker have accepted this fill? | `closed-market-trade`, `dead-price-turnover`, `fractional-shares`, `uncosted-trade` |
| **LIVENESS** | Is anything silently doing nothing? | `never-traded`, `pinned-flat`, `idle-sleeve`, `stale-book` |
| **HORIZON** | Do positions live long enough for their own signal to resolve? | `hold-shorter-than-signal`, `short-holds-lose` |
| **COST** | Is turnover eating the book? | `turnover` |

## Findings as of 2026-07-25

### 1. The ASX sleeve has never traded (ERROR)

`paper_state_full.json` funds three sleeves at a third each. FTSE has 22 trades,
US has 13, **ASX has zero** — since the book opened on 2026-06-11. Its cash is
still exactly `33333.33` AUD, `realized_pnl` is `0.0`, and it holds nothing.

A third of the headline portfolio has been uninvested for six weeks. The book's
reported return is really a two-thirds-invested book, so every performance number
on that account understates the strategy's true exposure and overstates its
capital efficiency.

Compounding it, `last_rebalance_month` is stamped `"2026-07"`, so
`_should_rebalance()` returns `False` for the rest of July regardless of signal —
the sleeve cannot re-enter before 1 August (`pinned-flat`). The `small` book's US
sleeve is in the same pinned state.

Root cause is not yet established: Yahoo is blocked from this sandbox (403), so
the ASX universe could not be re-fetched to see whether `compute_targets` returns
empty. The two candidates are (a) `latest_region_data` failing for `.AX` tickers,
or (b) `compute_targets` legitimately returning empty every month (regime filter
off, or no eligible names). `_empty_target_reason` already records which — but
`last_status` was only added on 2026-07-24, so there is no history to read. **The
next scheduled run will record the reason; check `sleeves.ASX.last_status.status`
on the `full` book.**

### 2. All four FX books trade on a closed market (ERROR)

104 fills across the four books are stamped on a bar when the venue for that
instrument was shut — weekends, and after the ~22:00 UTC Friday FX close. Crypto
is exempt and excluded from the count; these are FX majors and cash equities.

The fills are not merely mistimed, they are against a **frozen price**.
`fx_data._align` forward-fills any symbol that did not print, so a shut market
keeps serving its last close. On Saturday 2026-07-04 the `daytrader` book took
EURUSD from `+0.25` to `-0.25` and back to `+0.01` — a full long-to-short
round-trip and back — every leg filled at `1.14403`, the Friday close, unchanged
all weekend. AUDUSD traded six times across that weekend, every time at `0.6943`.

The weights move because the panel is mixed: crypto keeps printing new bars, so
the ensemble and the vol/correlation risk layer keep re-deriving the whole book,
dragging the frozen FX legs around with them. Each leg pays half the dealing
spread (`fx_book.py`) for exposure that cannot capture a price move, because
there is no price move to capture.

Two gaps let it through:

* **`fx_market_open()` is only wired into `run_loop`, not `run_once`.** The
  daemon path checks the FX session; the `--once` path — which is what the
  GitHub Actions cron actually runs — does not check at all.
* **The staleness gate tolerates exactly the window that matters.**
  `fx_data_quality.STALE_BARS = 6` is deliberately generous so "a quiet FX
  weekend never trips it". On a 60m book that permits the first six hours after
  the Friday close to trade at a dead price before the gate engages. That is why
  the weekend fills cluster in the hours right after the close.

The `--once`/`run_loop` split is the substantive one: the gate exists and is
correct, it is simply not on the path that runs.

### 3. Positions are closed long before their signal can resolve (WARN)

This is the direct answer to "are we giving our agents enough time?". **No, not
on the daily books.**

| Book | Bar | Median hold | Signal horizon | Fraction |
|---|---|---|---|---|
| `matt` | 1d | 4 bars | ~57 bars | 7% |
| `partner` | 1d | 4 bars | ~57 bars | 7% |
| `multiasset` | 1d | 5 bars | ~57 bars | 9% |
| `daytrader` | 60m | 11 bars | ~22 bars | 50% |

The horizon is the mean of the two windows that *imply* a holding period: the
Donchian breakout channel (`donchian_window`, 55 on the daily profiles) and the
momentum ROC window (`roc_window`, 60). `ema_slow` is deliberately excluded — it
is a smoothing constant, not a horizon, and including it would overstate the gap.

A 55-bar turtle-style channel break is a claim about the following several weeks.
Closing after four days does not test that claim; it pays the spread and leaves.
A 60-day momentum reading cannot meaningfully change in four days, so the exit is
not being driven by the momentum agent changing its mind — it is being driven by
the faster agents (mean-reversion, RSI) and by the risk layer re-normalising gross
exposure as correlations move.

`daytrader` is the well-matched book: the `intraday` profile shortens the windows
to 20/24 bars, and an 11-bar median sits reasonably inside that.

**A caveat, stated plainly.** The audit also reports that round-trips cut short
lost money while those given room made it (`short-holds-lose`) — on `daytrader`,
−2.20% of equity across 87 short trips against +0.14% across 115 longer ones.
That is *suggestive, not causal*, and the tool says so in its own output. A
position survives longer precisely when its signal keeps confirming, so the long
bucket is selected for winners by construction. It is evidence worth acting on
only in combination with the horizon mismatch above, which is structural and does
not depend on outcome data.

### 4. Turnover is on the order of the entire loss (INFO)

`daytrader`: gross turnover of **85× equity over 159 bars** (0.54× per bar).
Applying each pair's own `spread_fraction` at half-spread per weight change gives
an implied cost of ~2.57%, against a net return of −2.57%.

The two numbers matching that closely is a coincidence of one book over one
month, and the cost figure is a modelled estimate, not a booked total — do not
read it as "costs are exactly the loss". Read it as: **turnover on this book is
large enough to plausibly account for its entire drawdown**, which makes the
holding-period question above a P&L question, not a stylistic one.

## What this audit does not tell you

* Nothing here says the *strategy* is bad. Every finding is about plumbing —
  capital that never deployed, fills that could not have happened, positions cut
  short. The signal logic itself is untouched by this audit.
* One month of live paper data across four books is a small sample. The
  structural findings (1 and 2) do not depend on sample size; the holding-period
  and cost findings (3 and 4) do.
* Synthetic-mode results remain pipeline tests only (invariant #5). The audit
  reads whatever state is present and does not distinguish a synthetic run from a
  real one.

## Suggested order of work

1. **Gate `run_once` on market hours** — the one unambiguous correctness fix.
   Ideally per-symbol rather than per-run, so a mixed FX+crypto book keeps
   trading crypto through the weekend while the FX legs freeze.
2. **Diagnose ASX** from `last_status` after the next scheduled run.
3. **Then** decide the holding-period question. It is a strategy change, not a
   bug fix — a minimum-hold rule, a wider `rebalance_min_delta` band, or slower
   agent windows are all defensible, and they should be chosen with a
   walk-forward sweep behind them rather than from one month of live data.
