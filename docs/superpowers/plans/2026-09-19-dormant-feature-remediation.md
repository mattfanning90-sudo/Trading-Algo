# Dormant-Feature Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn on the shipped-but-unwired features that carry real value, make the
audit trustworthy enough to gate CI, and explicitly retire or document the rest.

**Architecture:** Strictly ordered phases. Phase 0 fixes the audit's false
positives, because every later phase depends on an alarm that only fires on real
problems. Nothing in Phases 0-5 changes a strategy knob, so no phase can alter
what any live book trades. Phase 6 is the only phase that changes backtest
numbers, and it is opt-in behind a config value that stays `None` until measured.

**Tech Stack:** Python 3.11, pandas/numpy, stdlib-only additions (urllib for the
webhook channel — no new dependency), GitHub Actions.

**Spec:** This plan implements the findings of the 2026-09-19 forensic audit
(this conversation) and supersedes the stale items in `docs/FORENSIC_AUDIT_2026-07.md`.

## Global Constraints

- **Invariant 1 — No lookahead.** Signals at t use data ≤ t; trades execute t+1.
- **Invariant 2 — Costs always on.** Never report backtest metrics without
  commission + slippage; UK stamp duty applies to FTSE buys.
- **Invariant 3 — One weight function.** Backtest and paper both route through
  `strategy.compute_targets`. Never add a second copy of the weight logic.
- **Invariant 4 — Whole shares** in paper trading; per-region commission floor respected.
- **Invariant 5 — Synthetic results are pipeline tests only**; never presented as performance.
- **Invariant 6 — Each sleeve trades in its local currency**; only the portfolio
  layer converts to AUD.
- **No new runtime dependencies.** The webhook channel uses `urllib.request`.
- **No strategy-knob changes in Phases 0-5.** `StrategyParams` / `FXParams`
  defaults and every profile stay exactly as they are.

---

## Findings this plan does NOT act on (and why)

Recorded so a future reader does not re-derive them:

| Finding | Decision |
|---|---|
| ASX sleeve "never traded" | **Not a bug.** ASX 200 is 8731.2 vs a 200d MA of 8821.5 — genuinely risk-off. The regime filter is working. Phase 0 reclassifies the audit's ERROR. |
| 63 closed-market FX fills | **Already fixed.** All are June/July 2026; zero since August. Historical ledger residue. Phase 0 stops re-reporting them. |
| `use_value` (value factor) | Strategy change, not a switch. Needs its own walk-forward backtest first. |
| `PAPER_ALLOCATION_REBALANCE` | Treasury-policy choice, not a defect. Leave off. |
| TSX funding | The registry→backtest→fund gate is working as designed. Run the backtest first (Task 7.2), then decide. |
| `execution_ibkr.py` / `crypto_exec.py` | Deliberately dormant is the correct default for live-order code. Document, do not wire. |

---

## Phase 0 — Make the audit trustworthy

**Why first:** `verify` currently reports 5 ERRORs, and all 5 are false positives.
Wiring alerts or `--strict` CI to it today would fail every build and train you to
ignore the alarm. Fix the classification before connecting anything to it.

### Task 0.1: Stop re-reporting historical closed-market fills

**Files:**
- Modify: `trading_algo/verify.py:212-247` (`check_market_hours`)
- Test: `tests/test_verify.py`

**Interfaces:**
- Produces: module constant `HISTORICAL_CUTOFF_DAYS: int`; `check_market_hours(account, state, kind, cutoff_days: int | None = None) -> list[Finding]`
- New finding code: `closed-market-trade-historical` at level `INFO`

- [ ] **Step 1: Write the failing test**

```python
def test_old_closed_market_fills_are_informational_not_errors():
    from trading_algo import verify
    old = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d %H:%M")
    state = {"trades": [{"date": old, "pair": "EURUSD", "side": "BUY",
                         "price": 1.1, "delta_weight": 0.1}]}
    # a Saturday stamp is always outside the FX session
    found = verify.check_market_hours("matt", state, "fx", cutoff_days=30)
    assert [f.level for f in found] == [verify.INFO]
    assert found[0].code == "closed-market-trade-historical"


def test_recent_closed_market_fills_still_error():
    from trading_algo import verify
    recent = _most_recent_saturday().strftime("%Y-%m-%d %H:%M")
    state = {"trades": [{"date": recent, "pair": "EURUSD", "side": "BUY",
                         "price": 1.1, "delta_weight": 0.1}]}
    found = verify.check_market_hours("matt", state, "fx", cutoff_days=30)
    assert [f.level for f in found] == [verify.ERROR]
    assert found[0].code == "closed-market-trade"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_verify.py -k closed_market -v`
Expected: FAIL — `check_market_hours() got an unexpected keyword argument 'cutoff_days'`

- [ ] **Step 3: Implement**

Add next to the existing `IDLE_DAYS` / `STALE_BOOK_DAYS` constants:

```python
# Fills older than this are ledger history, not a live defect: the audit
# re-derives every book from its whole trade ledger, so a bug fixed in July
# would otherwise be re-reported as an ERROR forever and mute the channel.
HISTORICAL_CUTOFF_DAYS = 30
```

Replace the offender loop and return in `check_market_hours`:

```python
def check_market_hours(account: str, state: dict, kind: str,
                       cutoff_days: int | None = None) -> list[Finding]:
    from .forex import sessions

    cutoff_days = HISTORICAL_CUTOFF_DAYS if cutoff_days is None else cutoff_days
    cutoff = datetime.now() - timedelta(days=cutoff_days) if cutoff_days else None

    offenders: dict[str, int] = {}
    historical: dict[str, int] = {}
    for t in state.get("trades") or []:
        symbol = t.get("pair") or t.get("ticker") or "?"
        try:
            stamp = t["date"]
            ts = sessions.parse_bar(stamp)
        except (ValueError, KeyError):
            continue
        if not sessions.bar_is_tradable(symbol, ts, kind,
                                        sessions.bar_interval(stamp)):
            bucket = historical if (cutoff and ts < cutoff) else offenders
            bucket[symbol] = bucket.get(symbol, 0) + 1

    found: list[Finding] = []
    if offenders:
        total = sum(offenders.values())
        found.append(Finding(
            ERROR, account, "closed-market-trade",
            f"{total} trades in the last {cutoff_days} days executed while the "
            f"market for that instrument was closed (weekend/after the FX close) "
            f"across {len(offenders)} symbols — these filled against a "
            "forward-filled price no venue was quoting",
            {"by_symbol": dict(sorted(offenders.items(), key=lambda x: -x[1]))}))
    if historical:
        total = sum(historical.values())
        found.append(Finding(
            INFO, account, "closed-market-trade-historical",
            f"{total} closed-market fills older than {cutoff_days} days across "
            f"{len(historical)} symbols — already-fixed history retained in the "
            "ledger, not a live defect",
            {"by_symbol": dict(sorted(historical.items(), key=lambda x: -x[1]))}))
    return found
```

Add `timedelta` to the datetime import: `from datetime import datetime, timedelta`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_verify.py -v`
Expected: PASS

- [ ] **Step 5: Confirm against the real books**

Run: `python3 -m trading_algo.verify`
Expected: the four `closed-market-trade` ERRORs become `closed-market-trade-historical` INFO notes. Error count drops from 5 to 1.

- [ ] **Step 6: Commit**

```bash
git add trading_algo/verify.py tests/test_verify.py
git commit -m "fix(verify): age out historical closed-market fills to INFO

All 63 daytrader / 16 matt / 14 multiasset / 11 partner closed-market fills
date from June-July 2026 and zero since August: the C1 session gate works.
Re-reporting them as ERROR forever would mute the alert channel the moment
it is connected."
```

### Task 0.2: Classify a regime-driven flat sleeve as correct behaviour

**Files:**
- Modify: `trading_algo/paper_trade.py:713` (persist the rebalance reason)
- Modify: `trading_algo/verify.py:333-343` (inside `check_liveness`, the `never-traded` branch)
- Test: `tests/test_verify.py`

**Interfaces:**
- Consumes: the `_empty_target_reason` vocabulary — `regime-off`, `no-eligible-names`, `data-quality`, `insufficient-names`
- Produces: sleeve key `last_flat_reason: str | None`; finding code `flat-by-design` at `INFO`

**Background:** on a rebalance day `paper_trade` writes `status = f"cash:{reason}"`,
but on every day after it overwrites the daily status with the generic
`cash:idle`. So by the time the audit reads the book, *why* the sleeve is flat has
been erased. ASX rebalanced on 2026-09-15 and was read on 2026-09-18 as
`cash:idle`. Persisting the reason is what makes the distinction auditable.

- [ ] **Step 1: Write the failing test**

```python
def test_sleeve_flat_for_regime_is_not_an_error():
    from trading_algo import verify
    state = {"allocations": {"ASX": 0.33}, "trades": [],
             "sleeves": {"ASX": {"currency": "AUD", "cash": 33333.0,
                                 "positions": {}, "last_flat_reason": "regime-off",
                                 "last_status": {"date": "2026-09-18",
                                                 "status": "cash:idle",
                                                 "positions": 0,
                                                 "flat_since": "2026-07-24"}}}}
    found = verify.check_liveness("full", state, "equity",
                                  datetime(2026, 9, 18))
    codes = {f.code: f.level for f in found}
    assert codes.get("flat-by-design") == verify.INFO
    assert "never-traded" not in codes


def test_sleeve_flat_for_data_quality_is_still_an_error():
    from trading_algo import verify
    state = {"allocations": {"ASX": 0.33}, "trades": [],
             "sleeves": {"ASX": {"currency": "AUD", "cash": 33333.0,
                                 "positions": {}, "last_flat_reason": "data-quality",
                                 "last_status": {"date": "2026-09-18",
                                                 "status": "cash:idle",
                                                 "positions": 0,
                                                 "flat_since": "2026-07-24"}}}}
    found = verify.check_liveness("full", state, "equity",
                                  datetime(2026, 9, 18))
    assert any(f.code == "never-traded" and f.level == verify.ERROR for f in found)
```

Note: the `never-traded` logic lives inside `check_liveness(account, state, kind,
today)` (`verify.py:302`) — there is no separate idle-sleeve function. `kind` is
`"equity"` for `paper_state_*` books and `"fx"` for `fx_state_*` (`verify.py:85`).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_verify.py -k flat -v`
Expected: FAIL — `never-traded` fires as ERROR in both cases.

- [ ] **Step 3: Persist the reason in paper_trade**

At `trading_algo/paper_trade.py`, in the `targets.empty` branch (~line 703):

```python
                if targets.empty:
                    reason = _empty_target_reason(prices, index_px, params, elig)
                    print(f"  [{k}] flat — {reason} (holding cash).")
                    status = f"cash:{reason}"
                    # Persist WHY across the days that follow: the daily status
                    # is overwritten with the generic 'cash:idle' on non-rebalance
                    # days, which erases the difference between "the regime gate
                    # said cash" (correct) and "the feed was broken" (an outage).
                    sleeve["last_flat_reason"] = reason
                else:
                    status = "rebalanced"
                    sleeve["last_flat_reason"] = None
```

- [ ] **Step 4: Reclassify in verify**

Replace the `never-traded` block:

```python
        traded = any(t.get("region") == key for t in state.get("trades") or [])
        funded = (state.get("allocations") or {}).get(key)
        reason = sleeve.get("last_flat_reason")
        if funded and not traded:
            if reason == "regime-off":
                found.append(Finding(
                    INFO, account, "flat-by-design",
                    f"sleeve {key} is funded ({funded:.0%}, "
                    f"{sleeve.get('cash', 0):,.0f} {sleeve.get('currency')}) and "
                    "has never traded because the regime filter is risk-off — "
                    "de-risking to cash is the designed behaviour, not a fault"))
            else:
                found.append(Finding(
                    ERROR, account, "never-traded",
                    f"sleeve {key} is funded ({funded:.0%} of the book, "
                    f"{sleeve.get('cash', 0):,.0f} {sleeve.get('currency')}) but "
                    "has never executed a single trade since the book opened"
                    + (f" (last flat reason: {reason})" if reason else "")))
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_verify.py tests/test_paper_trade.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add trading_algo/verify.py trading_algo/paper_trade.py tests/test_verify.py
git commit -m "fix(verify): distinguish a regime-off sleeve from a broken one

ASX has been flat since 2026-07-24 because ^AXJO (8731.2) is below its 200d
MA (8821.5) — the regime filter doing its job. The audit called this an ERROR
every day for 57 days. Persist the rebalance reason so 'flat by design' and
'flat because the feed died' are separable."
```

### Task 0.3: Make CI fail on a real error

**Files:**
- Modify: `.github/workflows/paper-trade.yml:80`, `.github/workflows/fx-paper.yml:94`, `.github/workflows/day-paper.yml:66`

**Background:** the audit step runs `python -m trading_algo.verify | tee /tmp/audit.txt`.
GitHub's default shell is `bash -e {0}` with **no `pipefail`**, so the exit code
seen is `tee`'s — always 0. Even with `--strict` the job would pass. Both halves
must change together.

- [ ] **Step 1: Confirm Phase 0 leaves zero ERRORs**

Run: `python3 -m trading_algo.verify; echo "exit=$?"`
Expected: `0 errors` in the summary line. **Do not proceed if any ERROR remains** —
investigate it first; it is a real finding.

- [ ] **Step 2: Update all three workflows**

In each of the three files, replace the audit step's `run:` block with:

```yaml
        shell: bash
        run: |
          set -o pipefail
          python -m trading_algo.verify --strict | tee /tmp/audit.txt
          {
            echo "## Live-book audit"
            echo '```'
            cat /tmp/audit.txt
            echo '```'
          } >> "$GITHUB_STEP_SUMMARY"
```

Keep the existing `if: always()` on the step.

- [ ] **Step 3: Keep the trading run un-blocked**

Verify the audit step is the LAST step that can fail, and that the
"commit state back" step still carries `if: always()`. A failed audit must
report loudly but must never discard a completed trading run's state.

Run: `grep -n "if: always()" .github/workflows/paper-trade.yml`
Expected: present on both the audit step and the commit step.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/paper-trade.yml .github/workflows/fx-paper.yml .github/workflows/day-paper.yml
git commit -m "ci: let the live-book audit fail the job

--strict alone was inert: bash -e has no pipefail, so 'verify | tee' always
returned tee's 0. Enabled only now that Phase 0 removed the false positives."
```

---

## Phase 1 — Connect the alarm bell

**Why:** every risk alert the system raises — drawdown halts, crowding, the audit's
ERRORs — is delivered to `NOTIFY_CHANNEL = "log"`, which prints to a CI log nobody
opens. `verify.py:551` records the consequence in its own docstring: *"`never-traded:
sleeve ASX` fired every day for 52 days"*. The abstraction exists precisely so this
cannot happen; only the channel is missing.

### Task 1.1: Add a webhook notification channel

**Files:**
- Modify: `trading_algo/notifications.py`
- Test: `tests/test_notifications.py`

**Interfaces:**
- Produces: `_webhook_channel(payload: dict) -> None`, registered as `"webhook"`
- Reads env: `ALERT_WEBHOOK_URL` (absent → silently no-op, never raises)

- [ ] **Step 1: Write the failing test**

```python
def test_webhook_channel_posts_json(monkeypatch):
    from trading_algo import notifications as N
    sent = {}

    def fake_urlopen(req, timeout=None):
        sent["url"] = req.full_url
        sent["body"] = json.loads(req.data.decode())
        class _R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return _R()

    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://example.test/hook")
    monkeypatch.setattr(N.urllib.request, "urlopen", fake_urlopen)
    N.notify("audit_errors", "2 books broken", level="alert", channel="webhook")
    assert sent["url"] == "https://example.test/hook"
    assert sent["body"]["event"] == "audit_errors"


def test_webhook_channel_without_url_is_a_noop(monkeypatch):
    from trading_algo import notifications as N
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    # must not raise
    N.notify("audit_errors", "x", level="alert", channel="webhook")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_notifications.py -k webhook -v`
Expected: FAIL — no `webhook` channel registered.

- [ ] **Step 3: Implement**

In `trading_algo/notifications.py`, after `register_channel("log", _log_channel)`:

```python
import json
import os
import urllib.request


def _webhook_channel(payload: dict) -> None:
    """POST the payload as JSON to $ALERT_WEBHOOK_URL (Slack/Discord/ntfy all
    accept this shape). Absent URL = no-op: a book must still trade on a laptop
    with no webhook configured. `notify()` already swallows exceptions, so a dead
    endpoint can never break a trading run."""
    url = os.environ.get("ALERT_WEBHOOK_URL")
    if not url:
        return
    body = dict(payload)
    body.setdefault("text", f"[{payload.get('level', 'info').upper()}] "
                            f"{payload.get('event')}: {payload.get('message')}")
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10):
        pass
    _log_channel(payload)      # always keep the local trace too


register_channel("webhook", _webhook_channel)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_notifications.py tests/test_breaker_alert.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_algo/notifications.py tests/test_notifications.py
git commit -m "feat(notifications): add a webhook delivery channel

The registry has existed since F12 with exactly one channel that prints.
Stdlib urllib only - no new dependency."
```

### Task 1.2: Select the channel and pass the secret in CI

**Files:**
- Modify: `trading_algo/config.py:254`
- Modify: `.github/workflows/paper-trade.yml`, `fx-paper.yml`, `day-paper.yml`

- [ ] **Step 1: Point the config knob at the channel**

```python
# Delivery channel for risk alerts (drawdown breaker, crowding, audit ERRORs).
# "log" prints only; "webhook" POSTs to $ALERT_WEBHOOK_URL *and* prints. With no
# URL set the webhook channel is a silent no-op, so this is safe everywhere.
NOTIFY_CHANNEL = "webhook"
```

- [ ] **Step 2: Add the secret to the audit step of all three workflows**

```yaml
        env:
          ALERT_WEBHOOK_URL: ${{ secrets.ALERT_WEBHOOK_URL }}
```

- [ ] **Step 3: Create the secret (MANUAL — needs a human)**

Create an incoming webhook (Slack, Discord, or ntfy.sh — ntfy needs no account),
then:

```bash
gh secret set ALERT_WEBHOOK_URL --body "https://ntfy.sh/<your-private-topic>"
gh secret list
```

Expected: `ALERT_WEBHOOK_URL` listed. Until this is done the channel no-ops and
behaviour is unchanged.

- [ ] **Step 4: Verify end to end**

Run: `ALERT_WEBHOOK_URL=https://ntfy.sh/<topic> python3 -m trading_algo.verify`
Expected: a push notification arrives if (and only if) an ERROR fired.

- [ ] **Step 5: Commit**

```bash
git add trading_algo/config.py .github/workflows/
git commit -m "feat: route risk alerts to the webhook channel"
```

---

## Phase 2 — Stop silent data staleness

**Why:** `load_prices` returns a cached parquet file forever once written — there is
no freshness check anywhere in `trading_algo/data.py:81-88`. Locally this is
already biting: `trading_algo/.cache` holds files from 24 July still being served
today, which is why a local ASX load returns data ending 2026-07-24 while yfinance
returns data through 2026-09-18. CI is unaffected (it has no equity cache), but
every local backtest, sweep and research run silently uses 8-week-old prices.

### Task 2.1: Expire the open-ended price cache

**Files:**
- Modify: `trading_algo/data.py:74-88` (`load_prices`)
- Test: `tests/test_cache_key.py`

**Interfaces:**
- Produces: module constant `CACHE_TTL_HOURS: int = 20`

**Design note:** only caches for an **open-ended** request (`end is None`, meaning
"up to now") can go stale. A request with an explicit `end` is a fixed historical
window and its cache is valid forever — so it must NOT be expired, or every
backtest re-downloads the whole universe.

- [ ] **Step 1: Write the failing test**

```python
def test_open_ended_cache_expires(tmp_path, monkeypatch):
    import os, time
    from trading_algo import data
    monkeypatch.setattr(data, "CACHE_DIR", str(tmp_path))
    calls = []

    def fake_download(tickers, start, end):
        calls.append(1)
        idx = pd.bdate_range("2026-01-01", periods=5)
        return pd.DataFrame({t: 1.0 for t in tickers}, index=idx)

    monkeypatch.setattr(data, "_download_primary", fake_download)
    data.load_prices(["AAA"], "2026-01-01", None, cache_key="k")
    assert len(calls) == 1
    data.load_prices(["AAA"], "2026-01-01", None, cache_key="k")
    assert len(calls) == 1                      # fresh cache reused

    path = data._cache_path("k")
    old = time.time() - (data.CACHE_TTL_HOURS + 1) * 3600
    os.utime(path, (old, old))
    data.load_prices(["AAA"], "2026-01-01", None, cache_key="k")
    assert len(calls) == 2                      # stale cache refetched


def test_closed_window_cache_never_expires(tmp_path, monkeypatch):
    import os, time
    from trading_algo import data
    monkeypatch.setattr(data, "CACHE_DIR", str(tmp_path))
    calls = []

    def fake_download(tickers, start, end):
        calls.append(1)
        idx = pd.bdate_range("2026-01-01", periods=5)
        return pd.DataFrame({t: 1.0 for t in tickers}, index=idx)

    monkeypatch.setattr(data, "_download_primary", fake_download)
    data.load_prices(["AAA"], "2026-01-01", "2026-01-08", cache_key="k2")
    path = data._cache_path("k2")
    old = time.time() - 10_000 * 3600
    os.utime(path, (old, old))
    data.load_prices(["AAA"], "2026-01-01", "2026-01-08", cache_key="k2")
    assert len(calls) == 1                      # a closed window is immutable
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cache_key.py -k expires -v`
Expected: FAIL — `AttributeError: module 'trading_algo.data' has no attribute 'CACHE_TTL_HOURS'`

- [ ] **Step 3: Implement**

Beside `CACHE_DIR` in `trading_algo/data.py`:

```python
# An OPEN-ENDED request (end=None) means "prices up to now", so its cache goes
# stale every trading day. A request with an explicit `end` is a closed window
# and its cache is valid forever. 20h < one calendar day, so a daily scheduled
# run always refetches while a burst of local runs still shares one download.
CACHE_TTL_HOURS = 20
```

Replace the cache-read block in `load_prices`:

```python
    if use_cache and os.path.exists(cache_file):
        fresh = True
        if end is None:
            age_h = (time.time() - os.path.getmtime(cache_file)) / 3600.0
            fresh = age_h < CACHE_TTL_HOURS
        if fresh:
            # Reuse the cache for this key even if a few tickers persistently
            # fail to download (else every call re-fetches the whole universe).
            df = pd.read_parquet(cache_file)
            have = [t for t in tickers if t in df.columns]
            if have:
                return df.loc[start:end, have]
```

Add `import time` at module level (it is currently imported inside `_download_primary`;
leave that one alone or hoist it — either is fine, but the module-level import is required).

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_cache_key.py tests/test_data_quality.py -v`
Expected: PASS

- [ ] **Step 5: Clear the poisoned local cache**

```bash
rm -rf trading_algo/.cache
python3 -c "
import warnings; warnings.filterwarnings('ignore')
from trading_algo import data, regions
px, idx = data.load_region(regions.get_region('ASX'), start='2024-01-01')
print('ASX last price date:', px.index[-1].date())
"
```

Expected: today's (or the last trading day's) date — **not** 2026-07-24.

- [ ] **Step 6: Commit**

```bash
git add trading_algo/data.py tests/test_cache_key.py
git commit -m "fix(data): expire the open-ended price cache after 20h

load_prices returned a cached parquet forever once written. Local caches from
24 July were still being served on 19 September, so every local backtest ran
on 8-week-old prices. Closed windows (explicit end=) stay cached forever."
```

### Task 2.2: Refuse to trade on a stale panel

**Files:**
- Modify: `trading_algo/data.py` (`load_region`)
- Test: `tests/test_data_quality.py`

**Interfaces:**
- Produces: module constant `MAX_PANEL_STALENESS_DAYS: int = 5`; emits
  `notifications.notify("stale_panel", ..., level="alert")`

**Design note:** this is a *warning*, not an exception. `paper_trade` already has a
`cash:stale-data` path and the data-quality gate freezes individual names; the gap
is that a whole region going stale is silent. Raising here would halt a book that
is otherwise healthy in three other regions.

- [ ] **Step 1: Write the failing test**

```python
def test_stale_region_panel_alerts(monkeypatch):
    from trading_algo import data, regions, notifications
    got = []
    notifications.register_channel("cap", got.append)
    monkeypatch.setattr("trading_algo.config.NOTIFY_CHANNEL", "cap")

    idx = pd.bdate_range("2020-01-01", periods=300)
    frame = pd.DataFrame({t: 100.0 for t in
                          [*regions.get_region("ASX").universe, "^AXJO"]}, index=idx)
    monkeypatch.setattr(data, "load_prices", lambda *a, **k: frame)
    data.load_region(regions.get_region("ASX"), start="2020-01-01")
    assert any(p["event"] == "stale_panel" for p in got)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_data_quality.py -k stale_region -v`
Expected: FAIL — no notification emitted.

- [ ] **Step 3: Implement**

At the end of `load_region`, after `prices = prices.dropna(how="all")`:

```python
    # A whole region's feed dying is silent otherwise: the data-quality gate
    # judges names against each other, so if EVERY name stops on the same day
    # nothing looks anomalous and the sleeve simply goes to cash forever.
    if end is None and len(prices.index):
        age = (pd.Timestamp.now().normalize() - prices.index[-1]).days
        if age > MAX_PANEL_STALENESS_DAYS:
            from . import notifications
            notifications.notify(
                "stale_panel",
                f"{region.key} price panel ends {prices.index[-1].date()} "
                f"({age} days old) — the feed or the cache is dead; this sleeve "
                "will de-risk to cash on a price nobody is quoting",
                level="alert", region=region.key, last_bar=str(prices.index[-1].date()),
                age_days=age)
    return prices, index_px
```

Add beside `CACHE_TTL_HOURS`:

```python
# A live panel older than this has a dead feed or a dead cache, not a quiet
# market. Long weekends and public holidays are why this is not 1 or 2.
MAX_PANEL_STALENESS_DAYS = 5
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_data_quality.py tests/test_data_fallback.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_algo/data.py tests/test_data_quality.py
git commit -m "feat(data): alert when a region's price panel goes stale"
```

---

## Phase 3 — Finish the champion/challenger loop

**Why:** this is the self-improving loop — the breeder runs monthly, evaluates 424
genomes, gates them on Deflated Sharpe + PBO, and writes a roster. It contributes
nothing live for two independent reasons: no workflow passes `--champions`, and all
four rosters are empty (`promoted: []`, `dsr: {}`).

**Order matters:** measure before touching the threshold. Do not assume a gate that
promotes nothing is broken — a gate that correctly rejects 424 noisy genomes is
doing its job, and loosening it would promote noise into a live book.

### Task 3.1: Measure the DSR distribution against its null

**Files:**
- Create: `scripts/measure_champion_gate.py`
- Test: none (a measurement tool, not production code — it reads state read-only)

**Interfaces:**
- Consumes: `evolve.read_log(account)`, `validation.deflated_sharpe_ratio(returns, n_trials, sr_variance)`
- Produces: a printed table; no state is written

- [ ] **Step 1: Write the measurement script**

```python
"""Measure the champion gate: what DSR do the finalists actually score, and what
would pure noise score? Read-only — writes no state, promotes nothing.

The question this answers: is DSR_MIN=0.95 correctly rejecting 424 overfit
genomes, or is it miscalibrated and rejecting a real edge? Loosening a threshold
before measuring it is how noise gets promoted into a live book.
"""
import warnings

import numpy as np

warnings.filterwarnings("ignore")

from trading_algo.forex import champions, evolve, fx_config, validation


def main() -> None:
    rng = np.random.default_rng(0)
    for account in fx_config.ACCOUNTS:
        log = evolve.read_log(account)
        if log is None:
            print(f"{account}: no swarm log")
            continue
        panel = evolve._panel_for(account, synthetic=False)
        p = fx_config.profile(fx_config.ACCOUNTS[account].get("profile", "balanced"))
        _, holdout = evolve.split_history(panel, log.holdout_frac)

        scores = []
        for gid in log.finalists:
            g = evolve.genome_from_dna(log.registry[gid]["dna"])
            r = evolve.genome_returns(g, holdout, p).to_numpy()
            scores.append(validation.deflated_sharpe_ratio(r, log.n_trials))

        # The null: same length, same vol, zero edge. If real genomes do not
        # separate from this, the gate is right to reject them.
        n = len(evolve.genome_returns(
            evolve.genome_from_dna(log.registry[log.finalists[0]]["dna"]),
            holdout, p))
        null = [validation.deflated_sharpe_ratio(
            rng.normal(0, 0.01, n), log.n_trials) for _ in range(200)]

        s = np.array(scores)
        print(f"\n=== {account} (n_trials={log.n_trials}, "
              f"holdout={log.holdout_frac:.0%}, {len(s)} finalists)")
        print(f"  real DSR : max={s.max():.4f} p90={np.percentile(s, 90):.4f} "
              f"median={np.median(s):.4f}")
        print(f"  null DSR : max={np.max(null):.4f} p90={np.percentile(null, 90):.4f} "
              f"median={np.median(null):.4f}")
        print(f"  clearing DSR>={champions.DSR_MIN}: {(s >= champions.DSR_MIN).sum()}"
              f" of {len(s)}   (null would clear "
              f"{(np.array(null) >= champions.DSR_MIN).mean():.1%} of the time)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `python3 scripts/measure_champion_gate.py`

**Calibration already verified (2026-09-19) — do not redo:** the DSR function
itself is sound. On 717-point series with `n_trials=424` it scores pure noise at
`0.0000`, a modest edge at `0.9705` and a large edge at `1.0000`. So a DSR of 0
means "indistinguishable from noise", **not** "the metric is broken". The
degenerate-bug branch below is therefore unlikely; keep it only as a backstop.

- [ ] **Step 3: Interpret — this is a decision gate, not a code change**

- **If real max DSR is close to the null max** → the gate is correct. 424 genomes
  produced no edge that survives deflation. **Change nothing.** Record the result
  and move to Task 3.2 (wire the inert flag) and Task 3.3 (report it honestly).
- **If real DSR clearly separates from the null but still lands under 0.95** →
  the bar may be mis-set for this sample size. Do NOT simply lower it; open a
  separate spec (`/spec champion-gate-calibration`) and decide deliberately.
- **If real DSR is degenerate (all identical / all 0.0) AND the null also scores
  non-zero** → only then suspect `sr_variance`. The calibration check above makes
  this unlikely; all-zero real scores next to an all-zero null simply means these
  424 genomes carry no edge that survives deflation.

- [ ] **Step 4: Commit the tool and the measured result**

```bash
git add scripts/measure_champion_gate.py
git commit -m "tools: measure the champion gate's DSR distribution vs its null

Answers whether DSR_MIN=0.95 rejects 424 overfit genomes correctly or is
miscalibrated. Evidence before any threshold change."
```

### Task 3.2: Wire `--champions` into the live cycle (inert until earned)

**Files:**
- Modify: `.github/workflows/fx-paper.yml:84`
- Test: `tests/test_fx_champions.py`

**Safety property:** `champions.champions_agents(account)` returns
`[*default_agents(), *load_roster(account)]`, and every roster is currently empty.
So passing `--champions` today produces **exactly the same five technical agents**
the books already run. It is a no-op that becomes live only when a genome earns
promotion — which is why it is safe to wire now and wrong to wire later.

- [ ] **Step 1: Write the failing test**

```python
def test_empty_roster_yields_exactly_the_core_agents(tmp_path, monkeypatch):
    from trading_algo.forex import champions
    from trading_algo.forex.agents import default_agents
    monkeypatch.setattr(champions, "STATE_DIR", str(tmp_path))
    champions.save_roster("matt", [], {"pbo": 0.1, "n_trials": 424,
                                       "promoted": [], "dsr": {}})
    names = [a.name for a in champions.champions_agents("matt")]
    assert names == [a.name for a in default_agents()]
```

- [ ] **Step 2: Run test**

Run: `pytest tests/test_fx_champions.py -k empty_roster -v`
Expected: PASS immediately (this documents the safety property that makes Step 3 safe).
If it FAILS, stop — do not wire the flag.

- [ ] **Step 3: Wire the flag**

In `.github/workflows/fx-paper.yml`, change the engine line:

```yaml
          python -m trading_algo.forex.engine --once --champions $ML_FLAG $SYNTH
```

Champions take priority over ML for daily unlocked books (`fx_book.py:368`), which
is the intended precedence: a genome promoted on measured hold-out evidence
outranks a freshly retrained net.

- [ ] **Step 4: Verify locally (synthetic, no state written to real books)**

Run: `python3 -m trading_algo.forex.engine --once --champions --synthetic`
Expected: runs clean; the decision book shows the same five technical agents.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/fx-paper.yml tests/test_fx_champions.py
git commit -m "feat(fx): let the live cycle consume promoted champions

The breeder has written a roster monthly since July and nothing read it.
Inert today (every roster is empty = the core five agents), live the moment
a genome clears the DSR/PBO gate on its own hold-out evidence."
```

### Task 3.3: Show the gate's verdict on the dashboard — ALREADY DONE, DO NOT BUILD

**Verified 2026-09-19: `forex/swarm_view.summary()` already does this**, and
better than the design below — it distinguishes `none_cleared` from
`cohort_overfit`, carries the real PBO and `DSR_MIN`, and is covered by
`tests/test_dashboard_swarm.py`. The task below was written on a wrong
assumption. Left visible rather than deleted so nobody re-derives it.

### (superseded) Task 3.3: Show the gate's verdict on the dashboard

**Files:**
- Modify: `trading_algo/dashboard/swarm.py`
- Test: `tests/test_dashboard_swarm.py`

- [ ] **Step 1: Write the failing test**

```python
def test_swarm_view_reports_an_empty_roster_honestly(tmp_path, monkeypatch):
    from trading_algo.dashboard import swarm
    from trading_algo.forex import champions
    monkeypatch.setattr(champions, "STATE_DIR", str(tmp_path))
    champions.save_roster("matt", [], {"pbo": 0.0833, "n_trials": 424,
                                       "promoted": [], "dsr": {}})
    out = swarm.gate_summary("matt")
    assert out["promoted"] == 0
    assert out["n_trials"] == 424
    assert "no genome cleared" in out["verdict"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_swarm.py -k honestly -v`
Expected: FAIL — `gate_summary` does not exist.

- [ ] **Step 3: Implement**

```python
def gate_summary(account: str) -> dict:
    """What the promotion gate concluded, in words. An empty roster is a RESULT
    ("424 genomes tried, none survived deflation"), not a missing feature — say
    so, or the screen reads as broken plumbing."""
    from ..forex import champions
    meta = champions.load_meta(account)          # {} when no gate has ever run
    promoted = len(meta.get("promoted") or [])
    n_trials = meta.get("n_trials", 0)
    pbo = meta.get("pbo")
    if not meta:
        verdict = "the breeder has not run for this book yet"
    elif pbo is not None and pbo > champions.PBO_MAX:
        verdict = (f"cohort binned as overfit — PBO {pbo:.2f} exceeds "
                   f"{champions.PBO_MAX}; nothing promoted")
    elif promoted == 0:
        verdict = (f"no genome cleared DSR>={champions.DSR_MIN} out of "
                   f"{n_trials} tried — the gate found no edge that survives "
                   "deflation, and correctly promoted nothing")
    else:
        verdict = f"{promoted} champion(s) promoted from {n_trials} tried"
    return {"promoted": promoted, "n_trials": n_trials, "pbo": pbo,
            "dsr_min": champions.DSR_MIN, "verdict": verdict}
```

Add the `load_meta` accessor to `trading_algo/forex/champions.py` beside `load_roster`:

```python
def load_meta(account: str) -> dict:
    """The gate's own record of its last verdict ({} if it has never run)."""
    path = champions_path(account)
    if not os.path.exists(path):
        return {}
    with open(path) as fh:
        return (json.load(fh) or {}).get("meta") or {}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_dashboard_swarm.py tests/test_fx_champions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_algo/dashboard/swarm.py trading_algo/forex/champions.py tests/test_dashboard_swarm.py
git commit -m "feat(dashboard): report the promotion gate's verdict in words"
```

---

## Phase 4 — Populate the FX backtest tab

**Why:** the tab has shown `⚠ ILLUSTRATIVE NUMBERS … THE FIGURES ARE PLACEHOLDERS`
since it shipped. The July audit called this unfixable; it no longer is —
`forex/run_backtest.py:194` now writes `state/fx_backtest_{account}.json`. No such
file exists anywhere, because nothing ever runs it.

### Task 4.1: Write the cache in CI and commit it

**Files:**
- Modify: `.github/workflows/fx-paper.yml`
- Test: `tests/test_fx_backtest.py`

- [ ] **Step 1: Confirm the invocation**

There is **no** `--write-cache` flag. Passing `--account <name>` is itself what
runs that book's own configuration and writes
`state/fx_backtest_{account}.json` (`forex/run_backtest.py:194`). `--out PATH`
redirects the cache elsewhere (useful for a dry run).

Run: `python3 -m trading_algo.forex.run_backtest --help`
Expected: `--account` documented as "write the dashboard's FX BACKTEST cache to
state/fx_backtest_{account}.json".

- [ ] **Step 2: Add a step to `fx-paper.yml`, after the engine step**

```yaml
      - name: Refresh the BACKTEST tab cache (real data, weekly)
        # Walk-forward is slow, so only on Mondays; the tab shows its own
        # generated_at date so a week-old cache reads honestly.
        if: github.event_name == 'schedule' && fromJSON(format('{0}', github.run_number)) > 0
        run: |
          for acct in matt partner multiasset daytrader; do
            python -m trading_algo.forex.run_backtest --account "$acct" $SYNTH \
              || echo "skip backtest cache $acct"
          done
```

- [ ] **Step 3: Confirm invariant 5 holds**

Run: `python3 -m trading_algo.forex.run_backtest --account matt --synthetic`
Expected: with `--synthetic`, **no cache file is written** (`tests/test_fx_backtest.py:130`
already asserts `not list(tmp_path.glob("fx_backtest_*.json"))`). Synthetic numbers
must never reach a dashboard that presents them as performance.

- [ ] **Step 4: Run the suite**

Run: `pytest tests/test_fx_backtest.py tests/test_dashboard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/fx-paper.yml
git commit -m "ci: populate the FX BACKTEST tab cache on real-data runs

The writer has existed since the M15 fix; nothing invoked it, so the tab has
been in placeholder mode. Synthetic runs still write nothing (invariant 5)."
```

---

## Phase 5 — Publish the reporting that already exists

**Why:** `tearsheet.py`, `tca.py`, `attribution.py` and `promotion.py` are written,
tested, and reachable — and nothing ever runs them. TCA in particular is now worth
running: 46 of 81 trades on `full` and 18 of 23 on `ultra` carry a decision price,
so it will produce real numbers (the July audit's "0 of 49" is stale).

### Task 5.1: Add a monthly report job

**Files:**
- Create: `.github/workflows/monthly-report.yml`
- Test: manual dispatch

- [ ] **Step 1: Confirm each CLI runs and note its exact flags**

```bash
python3 -m trading_algo.paper_trade --account full --tca
python3 -m trading_algo.paper_trade --account full --attribution
python3 -m trading_algo.paper_trade --account full --promotion
```

Expected: each prints a report and exits 0. If any errors, fix that CLI before
scheduling it.

- [ ] **Step 2: Create the workflow**

```yaml
name: Monthly Book Report

on:
  schedule:
    - cron: "0 3 1 * *"      # 03:00 UTC on the 1st
  workflow_dispatch: {}

permissions:
  contents: read

jobs:
  report:
    runs-on: ubuntu-latest
    env:
      MOMENTUM_STATE_DIR: ${{ github.workspace }}/state
      FX_STATE_DIR: ${{ github.workspace }}/state
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with:
          python-version: "3.11"
          cache: pip
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Per-account reports
        shell: bash
        run: |
          set -o pipefail
          for acct in full small ultra experimental; do
            {
              echo "## ${acct}"
              echo '### Execution TCA (decision price vs fill)'
              echo '```'
              python -m trading_algo.paper_trade --account "$acct" --tca 2>&1 || echo "n/a"
              echo '```'
              echo '### Attribution'
              echo '```'
              python -m trading_algo.paper_trade --account "$acct" --attribution 2>&1 || echo "n/a"
              echo '```'
              echo '### Promotion readiness'
              echo '```'
              python -m trading_algo.paper_trade --account "$acct" --promotion 2>&1 || echo "n/a"
              echo '```'
            } >> "$GITHUB_STEP_SUMMARY"
          done
```

- [ ] **Step 3: Dispatch it once and read the summary**

```bash
gh workflow run monthly-report.yml
gh run watch
```

Expected: a populated summary with real TCA numbers for `full` and `ultra`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/monthly-report.yml
git commit -m "ci: run the tearsheet/TCA/attribution/promotion reports monthly

All four have been written and tested since F5/F10/F11 with no trigger."
```

---

## Phase 6 — Capacity realism (ADV cap + market impact)

**Why this is last and separate:** it is the only change here that alters backtest
numbers, and it is the largest piece of real work. Both features are *double-gated*:
the config values are `None` **and** no caller passes `volume=`, so setting the
config alone does nothing. `data.load_volume` has zero callers anywhere.

**Honest scale note:** on a A$100k book trading megacaps, the ADV cap and impact
cost are close to a rounding error. They matter for (a) telling the truth about
what the strategy would do with 100× the capital, and (b) not fooling yourself with
a backtest that assumes infinite liquidity. Do this when you want the capacity
answer, not because the switch is off.

### Task 6.1: Plumb volume through the backtest path

**Files:**
- Modify: `trading_algo/run_backtest.py`, `trading_algo/portfolio_backtest.py`
- Test: `tests/test_adv_cap.py`, `tests/test_impact_cost.py`

**Interfaces:**
- Consumes: `data.load_volume(tickers, start, end) -> pd.DataFrame`
- Produces: `backtest.run_backtest(prices, index_prices, region, ..., volume=<frame|None>)`
  reached from both CLIs. Note `run_backtest` takes a `Region`, not a `StrategyParams`.

- [ ] **Step 1: Write the failing test**

```python
def test_run_backtest_passes_volume_when_a_capacity_feature_is_on(monkeypatch):
    from trading_algo import backtest, config as cfg, run_backtest as rb
    seen = {}

    def spy(prices, index_px, region, **kw):
        seen["volume"] = kw.get("volume")
        return {"equity": pd.Series([1.0]), "metrics": {}, "turnover": pd.Series(dtype=float),
                "total_cost_fraction": 0.0, "point_in_time": False, "trades": []}

    monkeypatch.setattr(cfg, "ADV_CAP_PCT", 0.05)
    # run_backtest is imported INTO run_backtest.py's namespace
    # (`from .backtest import run_backtest`), so patch it there, not on backtest.
    monkeypatch.setattr(rb, "run_backtest", spy)
    rb.run_single("US", synthetic=True, point_in_time=False)
    assert seen["volume"] is not None
```

`run_single(region_key: str, synthetic: bool, point_in_time: bool)` — all three are
positional-or-keyword and none has a default, so all three must be supplied.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_adv_cap.py -k passes_volume -v`
Expected: FAIL — `seen["volume"] is None`

- [ ] **Step 3: Implement in `run_backtest.run_single`**

```python
    # Volume is only fetched when a capacity feature is actually on: it is a
    # second full download, and both features are a no-op without it.
    volume = None
    if cfg.ADV_CAP_PCT or cfg.IMPACT_COEF:
        volume = (data.synthetic_volume(list(prices.columns), prices.index)
                  if synthetic
                  else data.load_volume(list(prices.columns), cfg.START))
    result = run_backtest(prices, index_px, region, membership=membership,
                          volume=volume)
```

Mirror the same block in `portfolio_backtest.py` for each sleeve.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_adv_cap.py tests/test_impact_cost.py tests/test_backtest.py -v`
Expected: PASS

- [ ] **Step 5: Measure the drag before choosing a value**

```bash
python3 -m trading_algo.run_backtest                                  # baseline
ADV_CAP_PCT=0.05 IMPACT_COEF=0.1 python3 -m trading_algo.run_backtest # capped
```

Record both CAGR/Sharpe figures in the commit message. If the difference is
negligible at your capital, say so — that is a finding, not a failure.

- [ ] **Step 6: Commit**

```bash
git add trading_algo/run_backtest.py trading_algo/portfolio_backtest.py tests/
git commit -m "feat(backtest): plumb volume so the ADV cap and impact cost can bind

Both features shipped fully implemented and structurally unreachable: no
caller ever passed volume=, and data.load_volume had zero callers. Still off
by default (ADV_CAP_PCT/IMPACT_COEF stay None)."
```

### Task 6.2: Correct the false claim in config.py

**Files:**
- Modify: `trading_algo/config.py:272-280`

**Background:** the comment states the cap "is applied inside
`strategy.compute_targets` so backtest and paper size identically". It is not —
it is applied in `backtest.py:92`, and `paper_trade.py:700` never passes
`capacity`. A comment that misdescribes a risk control is worse than no comment.

- [ ] **Step 1: Rewrite the comment truthfully**

```python
# Cap each position at this fraction of the name's trailing average DOLLAR
# volume so the book never targets more than it could realistically trade.
# None = off (a perfect no-op). Needs volume data.
#
# SCOPE: applied in the BACKTEST path only (backtest.py builds the per-name
# `capacity` series and passes it to strategy.targets_at). Paper trading calls
# compute_targets without a capacity argument, so the cap does NOT bind there.
# Sizing therefore differs between backtest and paper whenever this is set —
# see Phase 6 of docs/superpowers/plans/2026-09-19-dormant-feature-remediation.md.
ADV_CAP_PCT: float | None = None
```

- [ ] **Step 2: Commit**

```bash
git add trading_algo/config.py
git commit -m "docs(config): correct the ADV cap's documented scope

It is applied in backtest.py, not compute_targets, and paper never receives
it — the opposite of what the comment claimed."
```

---

## Phase 7 — Housekeeping (small, independent, batchable)

Each is a one-commit change; none depends on another.

### Task 7.1: Resolve the `MetaLabeler` contradiction

**Decision required from the owner.** `MetaLabeler` is trained on every FX paper
run (`train.py:200` writes `meta_label.json`), has **zero call sites**, and nothing
reads the file — while `README.md:209` and `docs/FX_DEEP_RESEARCH.md:143` both state
it "sizes the ensemble's side". That is a documentation claim the code does not honour.

- [ ] **Option A (recommended — delete):** remove `MetaLabeler` from `ml_agent.py`
  and `forex/__init__.py:24,35`, drop the training block in `train.py`, and correct
  both documents. Saves a daily CI training step that feeds nothing.
- [ ] **Option B (wire):** open `/spec meta-labeling` first — meta-labeling changes
  position sizing and therefore needs its own walk-forward evidence before it
  touches a live book.

Do not leave it as-is: a doc that describes a risk control that does not run is the
same class of defect as Task 6.2.

### Task 7.2: Backtest TSX, then decide funding

- [ ] Run: `python3 -m trading_algo.run_backtest --region TSX`
- [ ] Run: `python3 -m trading_algo.sweep --region TSX`
- [ ] If the walk-forward surface is flat and positive net of costs, add `"TSX"` to
      `config.ALLOCATIONS` and rebalance the four sleeves to 25% each. If not,
      record the result in `docs/` and leave it unfunded — that is the gate working.

### Task 7.3: Fix the stale `fx-train.yml` push trigger

- [ ] Remove the `push: branches: ["claude/practical-cray-luepjo"]` block
      (`.github/workflows/fx-train.yml:36-40`) — a long-merged dev branch.
- [ ] Commit: `ci: drop the stale dev-branch trigger from fx-train`

### Task 7.4: Resolve the two secrets

- [ ] `NEWS_API_KEY` is referenced by three workflows and **is not configured**, so
      the dashboard's news panel is permanently empty. Either
      `gh secret set NEWS_API_KEY` (free Financial Modeling Prep key) **or** remove
      the three `env:` references and the "add a key" message at
      `forex/dashboard.py:1386`.
- [ ] `TIINGO_API_SECRET` exists as a repo secret with **zero code references**.
      Either use it (register Tiingo via `data.register_fallback` and set
      `DATA_FALLBACK_SOURCE`, which also closes the F14 gap) or
      `gh secret delete TIINGO_API_SECRET`. An unused credential is pure risk.

### Task 7.5: Repair the broken real-data backtest

- [ ] `backtest.yml` last ran 2026-07-24 and **failed**; `state/backtest_equity.json`
      is dated 24 July, so the dashboard's BACKTEST tab is two months stale.
- [ ] Run: `gh run view 30086799639 --log-failed` to see the cause, fix it,
      dispatch the workflow, and confirm `state/backtest_equity.json` updates.

### Task 7.6: Document the deliberately dormant modules

- [ ] Add a short `## Deliberately dormant` section to `README.md` listing
      `execution_ibkr.py`, `crypto_exec.py`, `state_repair.py`, the
      `oanda`/`alpaca`/`openbb` adapters, and the `aggressive`/`hf_crypto` profiles,
      each with one line saying why it is off and what would turn it on. This is what
      stops the next audit re-deriving all of it.

---

## Self-Review

**1. Coverage.** Every Tier A-E finding from the audit maps to a task: Tier A →
Phase 6 + Task 7.1; Tier B → Phases 0, 3, 4, 5 + Task 7.6; Tier C → Phases 1, 2 +
Task 7.4; Tier D → Task 7.2, 7.6; Tier E → Tasks 7.3, 7.4, 7.5 and the SQLite
backlog (explicitly deferred below). The two "live errors" are resolved as
misclassifications in Phase 0 rather than as bugs.

**2. Placeholders.** Three tasks intentionally require a human command rather than
code — Task 1.2 Step 3 (create a secret), Task 3.1 Step 3 (interpret a measurement),
Task 7.1 (choose delete vs wire). Each states the exact command or the exact
decision criteria, so none is a "TBD". Task 4.1 Step 1 and Task 6.1 Step 1 instruct
the implementer to read the real signature rather than trusting a guessed flag name —
deliberate, because I did not verify those two signatures.

**3. Type consistency.** `Finding(level, book, code, message, detail)` is used with
that exact positional order throughout (matches `verify.py:64`). `HISTORICAL_CUTOFF_DAYS`,
`CACHE_TTL_HOURS`, `MAX_PANEL_STALENESS_DAYS` are each defined once and referenced by
the same name. `last_flat_reason` is written in Task 0.2 Step 3 and read in Step 4.
`load_meta` is added in Task 3.3 and used only there.

**4. Corrections made during two verification repasses.** Recorded because they
bear on how much to trust the rest:

1. `check_idle_sleeves` does not exist — the `never-traded` logic is inside
   `check_liveness(account, state, kind, today)` (`verify.py:302`). Task 0.2's
   tests were calling a function that was never there.
2. `log.registry[gid]` is a record wrapping the genome, not the genome — the DNA
   is at `registry[gid]["dna"]`. Task 3.1's script would have raised `KeyError`.
3. `run_single(region_key, synthetic, point_in_time)` has no defaults, and
   `run_backtest` is imported into that module's namespace, so the spy must patch
   `rb.run_backtest` and take `(prices, index_px, region, **kw)`. Task 6.1's test
   was wrong on all three counts.
4. `--write-cache` does not exist; `--account` alone writes the FX backtest cache.
   Task 4.1 named a flag that would have failed the CI step.

Verified working during the repass, so these are safe to build on: `parse_bar`
returns **naive** datetimes (so the Phase 0 `ts < cutoff` comparison is valid),
`data.load_volume` genuinely works against live Yahoo (Phase 6 is viable),
`paper_trade --tca/--promotion` both produce real output today, and the DSR
function is correctly calibrated.

**5. Not covered — deferred deliberately.** The SQLite store completion (BACKLOG.md:
dashboards still glob JSON at `registry.py:148`, plus the migration helper, query CLI
and dual-write removal) is a self-contained subsystem and belongs in its own plan.
It is invisible to the books today because the JSON fallback works.
