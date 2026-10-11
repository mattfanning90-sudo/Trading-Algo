# Mac home screen — working rules

Loaded when you work in `trading_algo/home/`. Root `CLAUDE.md` invariants apply.
Design: `docs/superpowers/specs/2026-10-09-mac-dashboard-design.md`; spec:
`docs/specs/home-screen.md`. Run: `python -m trading_algo.home [--browser]`.

## Rules
- **Nothing in this package imports `trading_algo.dashboard`, `paper_trade` or
  `forex.fx_book` at module level.** Those readers fix their state folder at import
  time, so the launcher syncs, points `MOMENTUM_STATE_DIR` / `FX_STATE_DIR` at the
  stable `current` link, and only then imports the dashboard
  (`tests/test_home_app.py` proves the order in a fresh process).
- **Read-only toward the books.** The app writes only under
  `~/Library/Application Support/Momentum/` (override: `MOMENTUM_HOME_DIR`).
  Snapshots hold top-level `state/*.json` only — never `*.db`, because opening
  SQLite writes.
- **Tests never use the network or the real app folder** — temp dirs, a
  throwaway local git remote, recorded GitHub API fixtures.

## How the parts work
- `sync.py` — a blobless bare clone (full history, file contents fetched only
  when needed). The JSON files' blob ids are listed and fetched in **one** request,
  then read locally. Don't use `git archive`: in a partial clone it downloads every
  missing file, including the ~7 MB SQLite store (measured 2026-10-10). Git gives
  up on a stalled connection after 20 s; fetches time out within a minute.
- `jobs.py` — GitHub's public run list, verified over HTTPS with certifi's
  certificate list (python.org's macOS Python ships none). Grace periods are
  **measured**, not guessed: daily jobs are "late" for 8 h 30 m after their slot,
  then "missed"; DAYTRADER is "missed" after 14 h without a run on a weekday.
  Anything GitHub can't tell us is "unknown", never "ok".
- `seen.py` — a WARN/ERROR finding is new in the session it is first seen (or
  becomes more severe) and known afterwards; identity is (book, code, subject),
  never the message. A lost or unreadable file never floods the screen.
- `__main__.py` — the launcher (native window via optional `pywebview`, else the
  browser).
