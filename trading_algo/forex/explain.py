"""Explainability: turn a trading decision into a human-readable rationale.

`decide_and_explain` returns the target weights (from the *same* canonical
`target_weights_history`, so there's no second weight formula) **plus** a per-pair
rationale: each agent's vote, the indicator readings, the market regime, the net
ensemble tilt and how the position was sized. The paper book attaches this to
every trade, and the dashboard renders it as a callout on the candle chart so you
can see *why* the system acted at that point — the whole goal being learnability.
"""
from __future__ import annotations

import pandas as pd

from . import indicators as ind
from .agents import AgentPool
from .fx_config import FXParams
from .fx_strategy import min_history, target_weights_history

_AGENT_LABEL = {
    "trend": "Trend (EMA fast vs slow, ADX-gated)",
    "breakout": "Breakout (Donchian channel)",
    "meanrev": "Mean-reversion (Bollinger/RSI, ranging only)",
    "momentum": "Momentum (rate-of-change)",
    "carry": "Carry (rate-differential tilt)",
    "neural": "Deep-learning agent (Sharpe-loss net)",
}


def _pair_indicators(bars: pd.DataFrame, p: FXParams) -> dict:
    close, high, low = bars["close"], bars["high"], bars["low"]
    upper, lower = ind.donchian(high, low, p.donchian_window)
    return {
        "price": float(close.iloc[-1]),
        "ema_fast": float(ind.ema(close, p.ema_fast).iloc[-1]),
        "ema_slow": float(ind.ema(close, p.ema_slow).iloc[-1]),
        "adx": float(ind.adx(high, low, close, p.adx_window).iloc[-1]),
        "rsi": float(ind.rsi(close, p.rsi_window).iloc[-1]),
        "roc": float(ind.roc(close, p.roc_window).iloc[-1]),
        "bb_z": float(ind.bollinger_z(close, p.bb_window).iloc[-1]),
        "donchian_hi": float(upper.iloc[-1]) if upper.notna().iloc[-1] else None,
        "donchian_lo": float(lower.iloc[-1]) if lower.notna().iloc[-1] else None,
        "ann_vol": float(ind.realized_vol(close, p.vol_lookback).iloc[-1]),
    }


def _explain_text(sym: str, weight: float, tilt: float, agents: dict,
                  iv: dict, p: FXParams) -> str:
    side = "LONG" if weight > 1e-6 else ("SHORT" if weight < -1e-6 else "FLAT")
    if side == "FLAT":
        return (f"No position in {sym}: the agents' net view (tilt {tilt:+.2f}) was "
                f"too weak/conflicted to clear the no-trade band.")

    dir_sign = 1.0 if weight > 0 else -1.0
    # Agents that pushed in the chosen direction, strongest first.
    contributors = sorted(
        ((n, v) for n, v in agents.items() if v * dir_sign > 0.05),
        key=lambda kv: -abs(kv[1]))
    against = sorted(
        ((n, v) for n, v in agents.items() if v * dir_sign < -0.05),
        key=lambda kv: -abs(kv[1]))

    regime = "trending" if iv["adx"] >= p.adx_trend_min else "ranging"
    parts = [f"{side} {sym} at {iv['price']:.5g}, sized to {abs(weight):.0%} of "
             f"equity (signed weight {weight:+.2f})."]
    parts.append(f"Net ensemble tilt {tilt:+.2f}.")

    if contributors:
        lead = ", ".join(f"{_AGENT_LABEL.get(n, n).split(' (')[0]} {v:+.2f}"
                         for n, v in contributors[:3])
        parts.append(f"Driven by: {lead}.")
    if against:
        opp = ", ".join(f"{n} {v:+.2f}" for n, v in against[:2])
        parts.append(f"Leaning against: {opp}.")

    # Concrete indicator evidence for the dominant ideas.
    ev = []
    rel = "above" if iv["ema_fast"] >= iv["ema_slow"] else "below"
    ev.append(f"EMA{p.ema_fast} is {rel} EMA{p.ema_slow} (ADX {iv['adx']:.0f}, {regime})")
    if iv["donchian_hi"] is not None:
        if iv["price"] >= iv["donchian_hi"]:
            ev.append(f"price broke the {p.donchian_window}-bar high")
        elif iv["price"] <= iv["donchian_lo"]:
            ev.append(f"price broke the {p.donchian_window}-bar low")
    ev.append(f"RSI {iv['rsi']:.0f}")
    ev.append(f"{p.roc_window}d momentum {iv['roc']:+.1%}")
    if abs(iv["bb_z"]) >= 1.0:
        ev.append(f"price {iv['bb_z']:+.1f}σ from its Bollinger mean")
    parts.append("Evidence: " + "; ".join(ev) + ".")

    parts.append(f"Realised vol {iv['ann_vol']:.0%}; the vol-target + per-pair cap "
                 f"set the final size.")
    return " ".join(parts)


def decide_and_explain(panel: dict[str, pd.DataFrame], p: FXParams,
                       pool: AgentPool | None = None
                       ) -> tuple[pd.Series, dict[str, dict]]:
    """Return (latest target weights, {pair -> rationale dict})."""
    pool = pool or AgentPool(max_workers=1)
    n = min_history(p)
    panel = {s: df.tail(n) for s, df in panel.items()}
    weights, signals, tilts = target_weights_history(panel, p, pool=pool, return_parts=True)
    if weights.empty:
        return pd.Series(dtype=float), {}

    w_last = weights.iloc[-1]
    rationale: dict[str, dict] = {}
    for s in panel:
        agents = {name: float(signals[s][name].iloc[-1]) for name in signals[s].columns}
        iv = _pair_indicators(panel[s], p)
        tilt = float(tilts[s].iloc[-1]) if s in tilts else 0.0
        weight = float(w_last.get(s, 0.0))
        rationale[s] = {
            "weight": round(weight, 4),
            "tilt": round(tilt, 4),
            "regime": "trending" if iv["adx"] >= p.adx_trend_min else "ranging",
            "agents": {k: round(v, 3) for k, v in agents.items()},
            "indicators": {k: (round(v, 5) if isinstance(v, float) else v)
                           for k, v in iv.items()},
            "text": _explain_text(s, weight, tilt, agents, iv, p),
        }
    return w_last, rationale
