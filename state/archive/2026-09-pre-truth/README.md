# Paper books before the measurement-truth restart (archived 2026-11-13)

These are the eight paper books as they stood on 2026-11-13, archived just before the
restart that spec decision **D4** calls for
(`docs/superpowers/specs/2026-09-24-measurement-truth-design.md`). A book's profit and
loss is worked out from its trade ledger, and the ledger is the only record a book
has, so it is kept here rather than deleted. **Nothing here is a performance claim.**
Every one of these books ran under rules the repo has since corrected (below).

The code these books belong to is commit **`1789e85e08ce0e50fccb3c0914d32264ec54841c`**
(`main` just before the merge).

## What is here

| File | What it is |
|---|---|
| `paper_state_{full,small,ultra,experimental}.json` | The four equity books (schema v2) |
| `fx_state_{matt,partner,daytrader,multiasset}.json` | The four FX books |
| `paper_books.db`, `fx_books.db` | The same books in SQLite, the store the code reads first. Copied with SQLite's online backup (not `cp`), so pages still in the write-ahead log were included. `PRAGMA integrity_check` = ok on both |
| `swarm_log_*.json`, `champions_*.json` | The FX swarm breeder's history and promoted champions |
| `permtest_matt.json` | The permutation test result for `matt` |

Every JSON file was checked byte-for-byte against the original, and each database row
was checked equal to its JSON twin.

## Where each book stood

| Book | Opened with | Last equity | Return | Trades |
|---|---|---|---|---|
| `full` | A$100,000, ASX/US/FTSE at a third each | A$99,591.57 | −0.41% | 125 |
| `small` | A$1,000, US only | A$1,121.60 | +12.16% | 3 |
| `ultra` | A$10,000, US only, `ultra` profile | A$8,929.18 | −10.71% | 41 |
| `experimental` | A$10,000, US only, `experimental` profile | A$8,421.85 | −15.78% | 32 |
| `matt` (FX) | A$5,000, balanced, 16 instruments | A$4,732.97 | −5.34% | 525 |
| `partner` (FX) | A$5,000, conservative, 16 instruments | A$4,834.15 | −3.32% | 426 |
| `daytrader` (FX) | A$10,000, intraday 60m bars, 16 instruments | A$8,706.71 | −12.93% | 3,995 |
| `multiasset` (FX) | A$10,000, balanced, locked 10-instrument universe | A$10,236.89 | +2.37% | 471 |

Read with the code at `1789e85`, which is the code that produced them. These are the
very rules listed as wrong below, so the figures describe the old rules, not the
strategy.

## What was wrong with them

- **Dividends were never credited.** The paper marked positions at the latest close
  and no code path paid a dividend into cash, so every ex-dividend price drop was
  booked as a loss that never reversed.
- **Cash interest was never paid.** `fees.idle_cash_credit` existed in the backtester
  only, and `full` held a large share of its money in cash.
- **Fills used the signal's own close** rather than the next day's, in both engines.
- **Two FTSE names were priced in the wrong currency.** Yahoo quotes `CPG.L` and
  `IHG.L` in USD, but `regions.py` applied the blanket pence-to-pounds scale to every
  FTSE name.
- **The drawdown breaker latched.** Its high-water mark never fell after a halt, so a
  book below its old peak tripped again after every cooldown.
- **`full` traded three sleeves, not four.** TSX is funded at 25% in
  `config.ALLOCATIONS`, but a running book keeps the allocations baked in at its own
  `--init`, so it never received capital.
- **The FX books held 16 instruments, not 10.** Six G10 crosses were added to give the
  neural layer more training rows and reached the books as a side effect.
- **`experimental` could not form its short leg**, so the "market-neutral" book was
  not market-neutral.

## Where the successors are

`state/paper_state_*.json`, `state/fx_state_*.json` and the two `state/*.db` files,
reopened on 2026-11-13 on the same capital and the same settings as the scheduler's
own `--init` (`.github/workflows/paper-trade.yml`, `fx_config.ACCOUNTS`). `full` now
has four sleeves and the FX books ten instruments. The promotion clock
(`MIN_PROMOTION_REBALANCES = 6`) restarts from zero: the accepted cost of D4.

## How to restore these books (rollback)

**Code and data roll back together.** The new books are schema v3; the code at
`1789e85` reads v2. Putting the old code back in front of the new books, or the old
books in front of the new code, mixes two sets of rules. Always do both steps.

```bash
# 1. Pause the schedulers so nothing writes while you work (GitHub → Actions):
gh workflow disable "Day Trading Paper (hourly)"
gh workflow disable "FX Paper Trading (live)"
gh workflow disable "Paper Trade & Publish Dashboard"

# 2. Return the code to 1789e85 (docs/HANDOFF_2026-09-24.md §14):
git restore --source=1789e85 --staged --worktree -- . ':!state/'
git commit -m "revert: return the code to 1789e85, before measurement-truth"

# 3. Put the books back: the JSON first, then the databases, then delete the
#    databases' side files BEFORE anything opens them. A leftover -wal file would
#    replay the new books' pages onto the restored database.
cp state/archive/2026-09-pre-truth/*.json state/
cp state/archive/2026-09-pre-truth/*.db   state/
rm -f state/paper_books.db-wal state/paper_books.db-shm \
      state/fx_books.db-wal    state/fx_books.db-shm
git add state/ && git commit -m "chore(state): restore the pre-truth paper books"
git push

# 4. Re-enable the three workflows.
```

### This restore has been run

On 2026-11-13, before any book was reopened: the archive was copied into a scratch
state directory in exactly the order of step 3, and the code at `1789e85`
(`git archive 1789e85`) was run against it with `MOMENTUM_STATE_DIR` and
`FX_STATE_DIR` pointing there.

- `python -m trading_algo.verify --no-alert` → exit 0; **0 errors, 12 warnings,
  8 notes across 8 books**. The warnings are the known old-rule defects: equity
  `cash-drift`, FX `dead-price-turnover` and `hold-shorter-than-signal`.
- `python -m trading_algo.paper_trade --account full --status` → exit 0; equity
  A$99,591.57, 125 trades.
- `python -m trading_algo.forex.fx_book --account matt --status` → exit 0; equity
  A$4,732.97, 525 trades.
- The status of the other six books, read the same way, gave the figures in the
  table above.
