---
title: Railway Deployment
type: research
tags: [trading, infrastructure, deployment, postgres, research]
created: 2026-07-25
up: ["[[Multi-Region Momentum]]"]
status: not-started
---

# 🚄 Railway Deployment — what it would take

Research capture from a 2026-07-25 session. **Nothing here is built yet**; no
Dockerfile, `railway.json` or Postgres backend exists in the repo.

## Where things stand today

The algo runs **in GitHub Actions** (`paper-trade.yml`, `fx-paper.yml`,
`day-paper.yml`): cron fires after the close, `engine --once` advances the books,
state is **committed back into the repo** (`state/` is deliberately tracked), and
the dashboard ships as a *static export* to GitHub Pages. There is no long-lived
server anywhere. Nothing assumes Actions, so Railway is additive.

## The constraint that shapes the design

> [!important] A Railway volume mounts to exactly ONE service
> So a "web" service and a separate "cron" service cannot share `paper_books.db`.
> Either cram every process into one container, or move the store to Postgres.

## Blockers (code changes needed)

1. **Bind address / port** — `dashboard/__main__.py` defaults to `127.0.0.1:8787`;
   Railway injects `$PORT` and requires `0.0.0.0`.
2. **Authentication — the real one.** The dashboard is read-only but
   **completely unauthenticated** (`server.py` has a `warn_if_public_bind` guard
   precisely because of this), and Railway hands out a public URL. That publishes
   positions, equity, P&L and the trade ledger to anyone who finds the hostname.
3. **State persistence** — the container filesystem is wiped every deploy. Needs a
   volume at `/data` with `MOMENTUM_STATE_DIR` and `FX_STATE_DIR` pointed at it.
   Both are *already* env-driven (`paper_trade.py`, `forex/fx_book.py`), so no code
   change — but the volume needs seeding from the tracked `state/` or the books
   start from scratch and lose their history.
4. **A supervisor entrypoint** — nothing today runs the web server and the
   scheduler(s) together, and equity and FX are separate loops.

## Gotchas that bite after it is up

- **`tzdata` is missing from slim Python images.** `engine.py` / `calendars.py` do
  `ZoneInfo("Australia/Sydney")`, which raises on a bare container. Add the
  `tzdata` package. It fails at the ASX close, unattended.
- **Every dashboard request downloads market data.** `api.build_snapshot` calls
  `latest_region_data` per request, and the parquet cache is hard-coded *inside
  the package dir* (`data.py` `CACHE_DIR`), so it is lost each deploy. On a public
  URL a few refreshes earn Yahoo 429s. Needs the cache on the volume plus a TTL.
- **Two writers = divergent books.** Actions cron and Railway writing different
  stores will silently diverge. Pick one owner; if Railway wins, disable the
  `schedule:` blocks in all three workflows.
- **Healthcheck should be `/`** (a static file), never `/api/state` — that one
  downloads prices.
- **IBKR cannot work here** — `execution_ibkr.py` needs TWS/Gateway on port 7497.
  Paper only.
- **Image size / cost** — pandas + numpy + pyarrow lands ~700MB–1GB, and
  `requirements.txt` drags in pytest/ruff/hypothesis despite `requirements-dev.txt`
  existing. Keep `backtest_store` runs in Actions; they are memory-hungry.

## Should we add Postgres?

**Yes — because it removes the one-service constraint**, not because Postgres is
nicer. It lets the web service, the FX/crypto loop and the monthly equity job be
*separate* services, so a crashing loop no longer takes the dashboard down. It
also makes `storage.account_lock` correct: `fcntl.flock` on a shared filesystem
cannot work across separate containers, `pg_advisory_lock` can. Plus real backups
— the `trades` ledger *is* the audit trail behind every P&L number.

**The port is cheap.** The storage layer is deliberately narrow: four functions
(`db_load` / `db_save` / `db_accounts` / `db_has`) and only two call-site clusters
(`paper_trade.py`, `forex/fx_book.py`, ~6 lines each). The schema is one table,
`account TEXT PK -> JSON blob -> updated_at`, and the `INSERT ... ON CONFLICT DO
UPDATE` upsert is valid Postgres verbatim. Swap `TEXT` for `JSONB`.

The right shape is a **backend switch inside `storage.py`** — Postgres when
`DATABASE_URL` is set, SQLite otherwise — so `pytest -q` still runs offline and
the Actions workflows are untouched.

> [!warning] The one real cost — silent staleness
> The dashboard discovers books by **globbing the filesystem**
> (`registry.py` globs `paper_state_*.json`; `api.py` / `fx_api.py` /
> `overview.py` read those files). Because `state/*.json` is tracked in git, those
> files get baked into the image. With Postgres as the store the dashboard would
> not find *nothing* — it would find the **stale committed snapshots** and render
> them as live. So the BACKLOG item *"Dashboard reads from the DB"* becomes a hard
> prerequisite, as does dropping the JSON dual-write.

Upside: going to Railway forces four BACKLOG items that were already wanted, and
the "Query CLI" item finally pays off with real SQL across books.

**Counter-arguments, honestly:** the books are tiny (4 equity + 4 FX accounts, a
few hundred KB) so this is not a scale decision; `psycopg[binary]` is the first
genuinely external dependency against the project's "no heavy frameworks" line;
and Postgres fixes **neither** of the two biggest risks — the unauthenticated
dashboard and Yahoo rate-limiting. Do not let the migration feel like progress on
those.

## Sequencing

1. `DATABASE_URL`-switched backend in `storage.py` + `pg_advisory_lock`
2. `storage --import` migration command (already a BACKLOG item) to carry book history over
3. Dashboard reads from the DB; drop the JSON dual-write
4. Auth on the dashboard server
5. Railway config: services, Dockerfile with `tzdata`, disable the Actions schedules

Steps 1–3 are worth doing regardless of Railway — they are existing BACKLOG debt.
Roughly a day's work, most of it in step 3.

> [!note] Cadence changes this
> [[24-7 Trading]] concluded that a *minute-scale* crypto book needs an always-on
> daemon, **not** Railway cron — and that concurrent writers make the Postgres
> case stronger. Read that note before picking a service layout.

Related: [[24-7 Trading]] · [[Multi-Region Momentum]] · [[Reference]]

#trading/infrastructure
