#!/usr/bin/env python3
"""Generate the house-style **Trading Algo** folder inside a personal Obsidian vault.

Two stages:
  1. Run the repo's own vault generator (`tools/build_obsidian_vault.py`, a.k.a.
     `make obsidian`) so the code-derived `obsidian/Reference.md` is current.
  2. Re-dress the repo's `obsidian/*.md` notes into the vault's house style
     (title/tags/type/source frontmatter, `#trading-algo` tag, `[[Trading Algo]]`
     hub name) and pull `Project README` / `Claude Instructions` straight from the
     live `README.md` / `CLAUDE.md`.

The vault notes are therefore a **build output** — regenerated, not hand-edited.
Edit the sources in the repo; this script rewrites the vault copies.

Output dir: $MOMENTUM_VAULT_DIR (or argv[1]), default "~/Matts Vault/Trading Algo".
Run: python3 tools/build_vault_notes.py   (also driven by a launchd agent on change)

Stdlib only, so it runs under any python3; the `make obsidian` sub-step uses the
same interpreter (must have pandas/numpy — pin one that does in the launchd plist).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OBSIDIAN = REPO / "obsidian"

DEFAULT_VAULT = Path.home() / "Matts Vault" / "Trading Algo"


def vault_dir() -> Path:
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return Path(sys.argv[1]).expanduser()
    env = os.environ.get("MOMENTUM_VAULT_DIR", "").strip()
    return Path(env).expanduser() if env else DEFAULT_VAULT


# ---------------------------------------------------------------- stage 1
def regen_repo_vault() -> bool:
    """Best-effort `make obsidian`. On failure (e.g. missing deps) we carry on
    with whatever notes are already in obsidian/ — the transform still runs."""
    try:
        r = subprocess.run(
            [sys.executable, str(REPO / "tools" / "build_obsidian_vault.py")],
            cwd=str(REPO), capture_output=True, text=True, timeout=180,
        )
        if r.returncode == 0:
            print("  make obsidian: ok —", (r.stdout.strip().splitlines() or [""])[-1])
            return True
        print("  make obsidian: FAILED (using existing obsidian/ notes)\n", r.stderr.strip()[:500])
    except Exception as e:  # noqa: BLE001
        print(f"  make obsidian: skipped ({e}); using existing obsidian/ notes")
    return False


# ---------------------------------------------------------------- transform
def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].lstrip("\n")
    return text


def fm_block(title: str, tags: list[str], type_: str, source: str) -> str:
    lines = ["---", f"title: {title}", "tags:"]
    lines += [f"  - {t}" for t in tags]
    lines += [f"type: {type_}", f"source: {source}", "---"]
    return "\n".join(lines)


def render_content(text: str, *, title, tags, type_, source, trail) -> str:
    """Repo note body, verbatim except: rename the hub link and normalise the
    nested `#trading/x` tags, then re-frontmatter and re-tag in house style."""
    body = strip_frontmatter(text)
    body = body.replace("[[Multi-Region Momentum", "[[Trading Algo")
    body = body.replace("#trading/", "#")
    lines = body.rstrip("\n").splitlines()
    # drop any trailing pure-tag line(s) — we append our own house tag line
    while lines and lines[-1].strip() and all(tok.startswith("#") for tok in lines[-1].split()):
        lines.pop()
    body = "\n".join(lines).rstrip()
    return f"{fm_block(title, tags, type_, source)}\n\n{body}\n\n{trail}\n"


def wrap_verbatim(text: str, *, title, tags, type_, source, related) -> str:
    """Wrap a repo doc (README.md / CLAUDE.md) unchanged under house frontmatter."""
    fm = fm_block(title, tags, type_, source)
    return f"{fm}\n\n{text.rstrip()}\n\n---\n\n## Related\n- {related}\n"


# Per-note house dressing for the repo's obsidian/ content notes.
CONTENT = [
    dict(src="How It Works.md", dst="Docs/How It Works.md",
         title="How It Works", tags=["trading-algo", "doc", "algorithm"], type_="doc",
         source="docs/HOW_IT_WORKS.md · obsidian/How It Works.md", trail="#trading-algo #algorithm"),
    dict(src="Reference.md", dst="Docs/Reference.md",
         title="Reference", tags=["trading-algo", "reference", "generated"], type_="reference",
         source="trading_algo/regions.py + config.py (via make obsidian)", trail="#trading-algo #reference"),
    dict(src="Concepts/12-1 Momentum.md", dst="Concepts/12-1 Momentum.md",
         title="12-1 Momentum", tags=["trading-algo", "concept", "momentum"], type_="concept",
         source="obsidian/Concepts/12-1 Momentum.md (signals.py)", trail="#trading-algo #momentum"),
    dict(src="Concepts/No-Lookahead.md", dst="Concepts/No-Lookahead.md",
         title="No-Lookahead", tags=["trading-algo", "concept", "backtesting"], type_="concept",
         source="obsidian/Concepts/No-Lookahead.md (invariant #1)", trail="#trading-algo #backtesting"),
    dict(src="Concepts/Regime & Trend Filters.md", dst="Concepts/Regime & Trend Filters.md",
         title="Regime & Trend Filters", tags=["trading-algo", "concept", "risk"], type_="concept",
         source="obsidian/Concepts/Regime & Trend Filters.md (signals.py)", trail="#trading-algo #risk"),
    dict(src="Concepts/Volatility Targeting.md", dst="Concepts/Volatility Targeting.md",
         title="Volatility Targeting", tags=["trading-algo", "concept", "risk"], type_="concept",
         source="obsidian/Concepts/Volatility Targeting.md (strategy.py)", trail="#trading-algo #risk"),
]

HUB = """---
title: Trading Algo
tags:
  - trading-algo
  - moc
type: moc
source: obsidian/ vault + repo docs
---

# Trading Algo

Map of content for the **Trading Algo** — a monthly-rebalanced **12-1 cross-sectional momentum** system run as three independent regional sleeves (**FTSE**, **US**, **ASX**), combined into one book and reported in **AUD**. Start here and follow the links.

> [!abstract] In one sentence
> Each month, in each region, buy the strongest stocks in an uptrend while the market itself is in an uptrend; otherwise hold cash. Size by inverse vol, scale to a target vol, run three books across three currencies.

> [!info] Auto-generated from the repo
> These notes are rebuilt from `~/Trading-Algo` by `tools/build_vault_notes.py` (a launchd agent runs it on change). **Don't hand-edit them here** — edit the source in the repo and they refresh. The code-derived [[Reference]] comes from `regions.py`/`config.py` via `make obsidian`.

## Start here
- [[How It Works]] — the full step-by-step walk-through (maths, decision flow, diagrams)

## Concepts — the ideas behind the edge

| Note | What's in it |
|---|---|
| [[12-1 Momentum]] | The signal: 12-month return skipping the last month, and why |
| [[Regime & Trend Filters]] | Per-stock 200d MA + index-regime crash protection (risk-off → cash) |
| [[Volatility Targeting]] | Inverse-vol weights, 15% cap, scale to a 12% vol target |
| [[No-Lookahead]] | Signal at *t*, trade at *t+1* — the sacred backtest invariant |

## Docs & reference

| Note | What's in it |
|---|---|
| [[How It Works]] | End-to-end pipeline: signal → filters → weights → sizing → trades → AUD |
| [[Reference]] | Region settings, cost schedules, strategy params, commands (generated from code) |

## Project files
- [[Project README]] — public-facing overview: why the strategy, architecture, quick start, dashboard, risk controls, limitations
- [[Claude Project Instructions]] — the in-repo `CLAUDE.md`: architecture map + invariants + commands

## The pipeline at a glance

```mermaid
flowchart TD
    A["Prices (local ccy)"] --> B["12-1 momentum"]
    A --> C["Trend & regime filters"]
    A --> E["Realised vol"]
    B --> S["Select top N"]
    C --> S
    S --> W["Inverse-vol weights, cap 15%"]
    E --> W
    W --> V["Volatility targeting → 12%"]
    V --> T["Target weights"]
    T --> X["Trade t+1 · costs on · whole shares"]
```

## Invariants — do not break these

These are enforced by the test suite; the full text lives in [[Claude Project Instructions]].

> [!danger] No lookahead
> Signals at *t* use data ≤ *t*; trades execute *t+1*. Any change to `signals.py`, `strategy.py` or `backtest.py` must preserve this. See [[No-Lookahead]].

> [!warning] Costs always on
> Never report backtest metrics without commission + slippage; UK stamp duty (0.5%) applies to FTSE buys only. See [[Reference]].

> [!warning] One weight function
> Backtest and paper trading both route through `strategy.compute_targets`. Do NOT add a second copy of the weight logic — enforced by `tests/test_consistency.py`.

> [!warning] Whole shares & currency discipline
> Paper trading uses whole shares and respects the per-region commission floor. Each sleeve trades in its **local** currency; only the portfolio/reporting layer converts to AUD via FX. Never mix currencies inside a sleeve.

> [!note] Synthetic data is a plumbing test
> `--synthetic` results validate the pipeline offline — never present them as performance.

## Beyond the equity sleeves

> [!tip] Separate FX subsystem
> The repo also contains an independent **low-latency, multi-agent FX trader** under `trading_algo/forex/` — parallel technical agents (trend/breakout/mean-reversion/momentum/carry) → performance-weighted ensemble → vol-targeted long/short book → isolated multi-account paper books. It reuses this project's principles (no lookahead, costs on, one `compute_targets`) but is otherwise separate. Not covered by these notes — see `trading_algo/forex/README.md`.

## Tags

`#trading-algo` is on every note. Filter by `#moc`, `#reference`, `#concept`, or by topic tags like `#momentum`, `#risk`, `#backtesting`.
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    out = vault_dir()
    print(f"Building vault notes → {out}")
    print("Stage 1: regenerate repo vault")
    regen_repo_vault()

    print("Stage 2: transform into house style")
    written = 0

    write(out / "Trading Algo.md", HUB)
    written += 1

    for n in CONTENT:
        src = OBSIDIAN / n["src"]
        if not src.exists():
            print(f"  ! missing source, skipped: {src}")
            continue
        text = render_content(
            src.read_text(encoding="utf-8"),
            title=n["title"], tags=n["tags"], type_=n["type_"],
            source=n["source"], trail=n["trail"],
        )
        write(out / n["dst"], text)
        written += 1

    readme = REPO / "README.md"
    if readme.exists():
        write(out / "Project README.md", wrap_verbatim(
            readme.read_text(encoding="utf-8"),
            title="Project README", tags=["trading-algo", "reference", "overview"],
            type_="reference", source="README.md",
            related="[[Trading Algo]] · [[How It Works]] · [[Reference]] · [[Claude Project Instructions]]",
        ))
        written += 1

    claude = REPO / "CLAUDE.md"
    if claude.exists():
        write(out / "Claude Project Instructions.md", wrap_verbatim(
            claude.read_text(encoding="utf-8"),
            title="Claude Project Instructions", tags=["trading-algo", "reference"],
            type_="reference", source="CLAUDE.md",
            related="[[Trading Algo]] · [[Project README]] · [[How It Works]] · [[Reference]]",
        ))
        written += 1

    print(f"Done — {written} notes written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
