"""Monthly paper-book tearsheet (backlog F5).

A dated, shareable performance snapshot of a PAPER account — distinct from the
backtest report (report.py). It reads the persisted book state and renders a
Markdown tearsheet: headline return, drawdown, per-sleeve breakdown, realized
cost drag and fees. Numbers reconcile exactly with the stored equity history (no
new statistical claim), and every tearsheet carries a plain-paper disclaimer.

    python -m trading_algo.tearsheet --account full
    python -m trading_algo.tearsheet --account full --out reports/full_2026-07.md
"""
from __future__ import annotations

import argparse
import math
import os

from . import attribution
from . import config as cfg
from . import paper_trade


def _annualised(equity_history: list) -> dict:
    """Ann. vol + max drawdown from the equity history (assumes ~daily points;
    labelled as such since a paper book's cadence is roughly daily)."""
    rets = attribution.equity_returns(equity_history)
    if len(rets) < 2:
        return {"ann_vol": None, "max_drawdown": None, "sharpe": None}
    vol = float(rets.std(ddof=1) * math.sqrt(252))
    # equity path for drawdown
    vals = [float(v) for _, v in equity_history]
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0)
    excess = float(rets.mean() * 252 - cfg.RISK_FREE)
    sharpe = excess / vol if vol > 0 else None
    return {"ann_vol": vol, "max_drawdown": mdd,
            "sharpe": round(sharpe, 2) if sharpe is not None else None}


def account_tearsheet(state: dict, as_of: str | None = None) -> str:
    """Render a Markdown tearsheet for one paper book."""
    account = state.get("account", "?")
    ccy = state.get("base_currency", cfg.BASE_CURRENCY)
    eh = state.get("equity_history", [])
    ic = float(state.get("initial_capital_base", 0.0))
    total_ret = attribution.total_return(eh)
    cur = float(eh[-1][1]) if eh else ic
    start = eh[0][0] if eh else "—"
    end = as_of or (eh[-1][0] if eh else "—")
    stats = _annualised(eh)

    out: list[str] = [
        f"# Paper Tearsheet — {account}  ({end})", "",
        "> **PAPER TRADING** — simulated book, not investment advice or a "
        "performance guarantee. A short track record is dominated by noise.", "",
        "## Headline", "",
        "| Metric | Value |", "|---|---|",
        f"| Inception | {start} ({ic:,.0f} {ccy}) |",
        f"| Current equity | {cur:,.2f} {ccy} |",
        f"| Total return | {total_ret:+.2%} |",
    ]
    if stats["max_drawdown"] is not None:
        out += [f"| Max drawdown | {stats['max_drawdown']:.2%} |",
                f"| Ann. vol (≈daily) | {stats['ann_vol']:.1%} |",
                f"| Sharpe (vs {cfg.RISK_FREE:.1%}, ≈daily) | {stats['sharpe']} |"]
    out += [f"| Equity points | {len(eh)} |",
            f"| Trades to date | {len(state.get('trades', []))} |", ""]

    # Per-sleeve holdings
    out += ["## Sleeves", "", "| Sleeve | Cash | Positions |", "|---|---|---|"]
    for k, sl in state.get("sleeves", {}).items():
        held = sum(1 for v in sl.get("positions", {}).values() if v)
        out.append(f"| {k} | {sl['cash']:,.0f} {sl['currency']} | {held} |")
    out.append("")

    # Realized cost drag (F11 machinery)
    cost = attribution.realized_cost_drag(state.get("trades", []))
    if cost:
        out += ["## Realized cost", "", "| Sleeve | Cost drag | Cost |", "|---|---|---|"]
        for rk, c in cost.items():
            out.append(f"| {rk} | {c['cost_drag_bps']:.1f} bps | "
                       f"{c['cost']:,.2f} {c['currency']} |")
        out.append("")

    if state.get("fx_rebalance_cost"):
        out.append(f"- FX rebalance cost (cum.): {state['fx_rebalance_cost']:,.2f} {ccy}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Paper-book tearsheet (F5)")
    ap.add_argument("--account", default="full")
    ap.add_argument("--out", default=None, help="also write the tearsheet to this file")
    args = ap.parse_args(argv)

    state = paper_trade.load_state(args.account)
    md = account_tearsheet(state)
    print(md)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md + "\n")


if __name__ == "__main__":
    main()
