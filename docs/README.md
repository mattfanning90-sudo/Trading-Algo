# Docs catalogue

Every knowledge document in the repo, what it's for, and whether to trust it as
current. **Check here before reading a long doc** — several are dated records.
Updated 2026-10-13.

**How knowledge is organised (three layers):**
1. **Always read** — the root `CLAUDE.md` (kept under 150 lines).
2. **Read when working in a folder** — `trading_algo/forex/CLAUDE.md`,
   `trading_algo/dashboard/CLAUDE.md`, `trading_algo/home/CLAUDE.md` (Claude Code
   loads these automatically when you touch files there).
3. **Read on purpose** — everything below.

**Statuses:** *current* = maintained, trust it · *active* = work in progress,
owned by that piece of work · *record* = a finished plan/spec, true when written ·
*dated* = a point-in-time review or audit; check newer docs before acting on it ·
*background* = research and context.

## One home per fact
| Fact | Its home | Others should link, not copy |
|---|---|---|
| The rules that must not break (invariants 1–6) | `CLAUDE.md` → Invariants | README, product/PROCESS.md, forex README |
| Module map / what each file does | `docs/ARCHITECTURE.md` | README, HOW_IT_WORKS, reviews |
| How the algorithm works, in plain English | `docs/HOW_IT_WORKS.md` | `obsidian/How It Works.md` (generated) |
| Commands | `README.md` → Quick start (+ FX: `trading_algo/forex/README.md`) | CLAUDE.md lists only the core few |
| Data sources per book | `docs/DATA_FEEDS.md` | forex README |
| Product backlog and its statuses | `product/backlog/backlog.json` | product/README.md |
| Plain-English concepts | `obsidian/Concepts/*.md` | paired `docs/*_RESEARCH.md` |

## Start here — *current*
| Doc | Purpose |
|---|---|
| `../CLAUDE.md` | Agent instructions: what this is, invariants, how to check work |
| `../README.md` | Human overview, quick start, dashboard, CI, risk, limitations |
| `ARCHITECTURE.md` | Module map, reporting groups, region gate, where the books run |
| `HOW_IT_WORKS.md` | The algorithm, step by step |
| `DATA_FEEDS.md` | Data feeds, close-only policy, per-book sources, known data issues |
| `../trading_algo/forex/README.md` | FX subsystem tour |

## Active work
| Doc | Status | Purpose |
|---|---|---|
| `HANDOFF_2026-09-24.md` | active | Resume point for the measurement-truth block |
| `superpowers/specs/2026-09-24-measurement-truth-design.md` | active (header says "pending implementation plan"; the plan below is executing) | Measurement-truth design |
| `superpowers/plans/2026-09-24-measurement-truth.md` | active — owned by that block | 27-task plan; its **Progress** section near the top is the part to read |
| `superpowers/specs/2026-10-09-mac-dashboard-design.md` | active — **start at its "Resume here" block** | Mac app umbrella design (D0, A, B, C, D) and §11 the modern screens |
| `specs/home-screen.md` | draft — built 2026-10-10, awaiting Matt's checks | B1 home screen spec + acceptance results |
| `specs/equity-decision-record.md` | draft | D0 spec — its blocker (measurement-truth Task 10, next-close fills) has landed |

## Specs and plans — *record*
| Doc | Status | Purpose |
|---|---|---|
| `specs/_example.md` | example | The spec schema, worked |
| `specs/paper-live-parity.md` | agreed | Paper ↔ live parity |
| `specs/swarm-insample-permutation.md` | done | Swarm permutation test |
| `superpowers/specs/2026-07-24-fx-swarm-design.md` | implemented (`forex/evolve.py`, `champions.py`, `swarm_view.py`) | Swarm design |
| `superpowers/specs/2026-07-25-paper-live-parity-design.md` | approved | Parity design |
| `superpowers/specs/2026-09-16-champion-challenger-learning-design.md` | design agreed; no plan in `superpowers/plans/` | Learning loop — holds decision D1 (models + roster only) |
| `superpowers/specs/2026-09-16-carry-regime-monitor-design.md` | design agreed; not built | Carry regime monitor |
| `superpowers/specs/2026-07-24-algo-animation-design.md` | approved | The animated explainer |
| `superpowers/plans/2026-10-10-home-screen.md` | done (built 2026-10-10) | B1 build plan |
| `superpowers/plans/2026-07-24-fx-swarm.md`, `2026-07-25-paper-live-parity.md`, `2026-07-25-self-learning-loop-phase0.md`, `2026-09-16-cost-aware-neural-objective.md`, `2026-09-19-dormant-feature-remediation.md` | record — see each plan's own progress | Earlier build plans |

## Research and decisions — *background* (each ends in a decision)
`SHARPE_RESEARCH.md` (+ `obsidian/Concepts/Sharpe Ratio.md`) ·
`PERMUTATION_TESTING.md` (+ `obsidian/Concepts/Permutation Testing.md`) ·
`FX_DEEP_RESEARCH.md` · `research/COMBATING_BACKTEST_BIAS.md` ·
`research/COST_AWARE_OBJECTIVE_RESULT.md` · `research/REALISED_TRADE_EVIDENCE.md` ·
`EFFECTIVE_PUBLIC_STRATEGIES.md` · `CRYPTO_HF.md` · `HFT_REALITY.md` ·
`backlog/crypto-subsystem.md` · `backlog/regime-adaptive-derisk-experiment.md`.
Not yet committed (main checkout only): `MONTE_CARLO_RESEARCH.md`, cited by
several of these.

## Reviews and audits — *dated*
| Doc | Date | Note |
|---|---|---|
| `FULL_SYSTEM_REVIEW_2026-09.md` | 2026-09 | Whole-app review; §15 holds later corrections. Digest: `obsidian/Reviews/Full System Review 2026-09.md` |
| `FORENSIC_AUDIT_2026-09.md` | 2026-09 | Dormant-feature audit + status table |
| `FORENSIC_AUDIT_2026-07.md` | 2026-07 | Partly superseded by the September docs |
| `LIVE_BOOK_AUDIT.md` | 2026-07 | What `verify` checked then; grading and `detail["subject"]` came later — `verify.py`'s docstrings are newer |
| `../packaging/README.md` | 2026-06 | The old py2app `.app` recipe; never built. The home screen runs via `python -m trading_algo.home` |
| `EFFICIENCY_REVIEW.md` | 2026-07 | Performance/network review |
| `CTO_ARCHITECTURE_BENCHMARK.md` | 2026-07 | Architecture benchmark |
| `../product/ARCHITECTURE_REVIEW.md` | 2026-07 | Backlog build-order review |

## Product operating model — *current*
`../product/README.md` (index) · `../product/PROCESS.md` (backlog loop, Definition
of Ready/Done, RICE) · `../product/roles.md` · `../product/backlog/` (the backlog
itself, validated in CI) · `../product/schema/`.

## Plain-English and visual explainers
`../obsidian/` — the Obsidian vault source (`tools/build_obsidian_vault.py`
generates several notes; `tools/build_vault_notes.py` syncs the vault) ·
`explainer/index.html` and `explainer/how-it-works.html` (animated field guide).

## Historical
`../HANDOFF.md` (June design history, ASX → multi-region) · `../BACKLOG.md`
(SQLite-store wiring checklist) · `../AGENTS.md` (now a pointer to `CLAUDE.md`).
