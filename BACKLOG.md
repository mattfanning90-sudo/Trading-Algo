# Backlog

Tracked follow-up work. Newest section on top; check items off as they land.

## SQLite book store — remaining wiring

The paper-trading books now persist to a SQLite store (`paper_books.db`,
`fx_books.db`) as the source of truth, with the legacy per-account `*.json`
files **dual-written** as a fallback so the dashboards, CI `*.json` globs and
hand-editing workflows keep working unchanged (see `trading_algo/storage.py`).

That fallback is deliberately transitional. To make SQLite the *sole* source of
truth, the following still needs wiring:

- [ ] **Dashboard reads from the DB.** `dashboard/registry.py`, `dashboard/api.py`,
      `dashboard/fx_api.py` and `dashboard/overview.py` still discover and read
      books by globbing / statting `paper_state_*.json` / `fx_state_*.json`.
      Point them at `storage.db_accounts` / `storage.db_load` so a DB-only book
      is visible without a JSON copy.
- [ ] **CI site builder reads from the DB.** `scripts/build_site.sh` loops over
      `"$STATE_DIR"/*_state_*.json` to discover accounts. Switch discovery to the
      DB (a tiny `python -m ... --list` helper) so the published site does not
      depend on the JSON fallback.
- [ ] **Migration helper.** One-shot `python -m trading_algo.storage --import`
      that loads every legacy `*_state_*.json` into its DB, so existing books
      move over without a manual first-save.
- [ ] **Query CLI.** Small read-only commands over the DB
      (e.g. list books, show a book, dump trade history across all accounts) —
      the payoff of having a queryable store instead of loose blobs.
- [ ] **Drop the JSON dual-write.** Once the above land and the tracked
      `state/*.json` fixtures are regenerated from the DB, stop writing the
      per-account JSON files and remove the read-fallback in
      `paper_trade.load_state` / `fx_book.load_state`.
- [ ] **Decide what CI commits.** `.github/workflows/*paper*.yml` currently
      persists tracked `state/*.json`. Choose whether to commit the `*.db` files
      instead (already whitelisted in `.gitignore`) or keep exporting JSON
      snapshots for diff-ability.
