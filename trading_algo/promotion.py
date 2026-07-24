"""Paper→live promotion gate (backlog F10) — the Decision-agent capital gate.

Switching a book from paper to real money is the single highest-consequence
action in the system, and today it is an unguarded manual call. This module makes
it an objective, auditable checklist. A book is promotable only when EVERY
criterion holds:

  * track record — at least `MIN_PROMOTION_REBALANCES` distinct monthly rebalances
    of paper history (a momentum edge is unmeasurable over a few weeks).
  * overfitting  — the strategy cleared the F2 Deflated-Sharpe / PBO gate
    (DSR >= min, PBO <= max) — evidence passed in from the sweep.
  * tracking     — live-vs-backtest tracking error within the F3 budget.
  * capacity     — equity is above the min-viable floor (fees don't dominate).
  * integrity    — the F18 state schema validates and the book isn't halted.

`require_live_ok()` is the hard block: `execution_ibkr` calls it before ever
connecting to a LIVE port, so an unqualified book cannot fire a real order
without an explicit, audited human override.
"""
from __future__ import annotations

from . import config as cfg
from . import state_schema


class PromotionError(Exception):
    """Raised when a live order is attempted on a book that isn't promotable."""


def _distinct_rebalance_months(state: dict) -> int:
    """How many distinct calendar months the book actually traded in."""
    months = {t["date"][:7] for t in state.get("trades", []) if t.get("date")}
    return len(months)


def promotion_check(state: dict, *, dsr: float | None = None,
                    pbo: float | None = None,
                    tracking_error_bps: float | None = None) -> dict:
    """Evaluate a book's promotion readiness. Returns a verdict dict with a
    per-criterion breakdown and an overall `ready` flag. Evidence that lives
    outside the book (DSR/PBO from the sweep, tracking error from F3) is passed
    in; a missing piece of evidence fails its check rather than being assumed."""
    checks: dict[str, bool] = {}
    reasons: list[str] = []

    # track record
    n_months = _distinct_rebalance_months(state)
    checks["track_record"] = n_months >= cfg.MIN_PROMOTION_REBALANCES
    if not checks["track_record"]:
        reasons.append(f"only {n_months} rebalance month(s) "
                       f"(need {cfg.MIN_PROMOTION_REBALANCES})")

    # capacity / min viable size
    eh = state.get("equity_history", [])
    equity = float(eh[-1][1]) if eh else float(state.get("initial_capital_base", 0.0))
    checks["capacity"] = equity >= cfg.MIN_VIABLE_EQUITY_BASE
    if not checks["capacity"]:
        reasons.append(f"equity {equity:,.0f} below min viable "
                       f"{cfg.MIN_VIABLE_EQUITY_BASE:,.0f}")

    # integrity
    checks["schema_valid"] = not state_schema.validate_state(state)
    if not checks["schema_valid"]:
        reasons.append("state file fails schema validation")
    checks["not_halted"] = not state.get("risk_halted", False)
    if not checks["not_halted"]:
        reasons.append("book is in a drawdown halt")

    # overfitting (F2 evidence)
    checks["overfitting"] = (dsr is not None and dsr >= cfg.PROMOTION_DSR_MIN
                             and (pbo is None or pbo <= cfg.PROMOTION_PBO_MAX))
    if not checks["overfitting"]:
        reasons.append(f"overfitting gate not passed (DSR={dsr}, PBO={pbo}; "
                       f"need DSR>={cfg.PROMOTION_DSR_MIN}, PBO<={cfg.PROMOTION_PBO_MAX})")

    # tracking (F3 evidence)
    checks["tracking"] = (tracking_error_bps is not None
                          and tracking_error_bps <= cfg.PROMOTION_TRACKING_BUDGET_BPS)
    if not checks["tracking"]:
        reasons.append(f"tracking error {tracking_error_bps}bps not within "
                       f"{cfg.PROMOTION_TRACKING_BUDGET_BPS}bps budget")

    return {
        "ready": all(checks.values()),
        "checks": checks,
        "reasons": reasons,
        "rebalance_months": n_months,
        "equity": round(equity, 2),
    }


def require_live_ok(state: dict, *, override: bool = False,
                    dsr: float | None = None, pbo: float | None = None,
                    tracking_error_bps: float | None = None) -> dict:
    """Hard gate for a live order. Raises PromotionError unless the book is
    promotable — or an explicit human `override` is given (which is allowed but
    recorded in the returned verdict for the audit trail)."""
    verdict = promotion_check(state, dsr=dsr, pbo=pbo,
                              tracking_error_bps=tracking_error_bps)
    verdict["override"] = bool(override)
    if not verdict["ready"] and not override:
        raise PromotionError(
            "Book not promotable to live capital:\n  - "
            + "\n  - ".join(verdict["reasons"]))
    return verdict
