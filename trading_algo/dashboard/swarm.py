"""The terminal's SWARM payload (/api/swarm/<KEY>).

A thin kind-aware wrapper over `forex.swarm_view.summary()`: the swarm is an FX
subsystem, so an equity book gets an explicit "not applicable" rather than an
empty swarm — a blank panel reads as "your swarm vanished", which is exactly
the confusion this tab exists to end.

Served on its own route, not folded into /api/account/<KEY>: the account payload
is polled every 5 seconds and the evolution log only changes when the monthly
breeder runs, so the swarm is fetched once, lazily, when the tab is opened.
"""
from __future__ import annotations


def build_swarm(kind: str, account: str) -> dict:
    """Swarm state for one book. Offline — reads persisted state only."""
    if kind != "fx":
        return {"available": False, "applicable": False, "account": account,
                "gate": {"code": "not_applicable",
                         "reason": ("The swarm breeds FX agents; equity sleeves "
                                    "trade the momentum strategy directly.")}}
    from ..forex import swarm_view
    return {**swarm_view.summary(account), "applicable": True}
