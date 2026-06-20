"""The parallel agent ecosystem.

Each *agent* is a self-contained technical strategy that reads an OHLC frame and
emits a directional signal in **[-1, +1]** per bar (−1 = max short, +1 = max
long, 0 = flat). Agents are deliberately diverse and weakly correlated so the
ensemble can blend complementary edges:

* ``TrendAgent``        EMA-fast/slow spread, ATR-normalised, gated by ADX (only
                        acts in trending regimes).
* ``BreakoutAgent``     Donchian-channel breakout — flips on a new N-bar
                        high/low and rides it (turtle-style).
* ``MeanReversionAgent`` fades Bollinger/RSI extremes, but *only* when ADX says
                        the market is ranging (so it never fights a strong trend).
* ``MomentumAgent``     rate-of-change, normalised by its own volatility.
* ``CarryAgent``        tilts toward the positive-carry side of each pair.

Agents share one interface (`generate`) and one signal convention, so adding a
sixth is a single class. They are evaluated **in parallel** by `AgentPool` — one
task per (pair, agent) — which is what makes this an "ecosystem" rather than a
single model: independent opinions computed concurrently, then combined.

No lookahead: every agent's value at bar t uses only data ≤ t. The backtest then
applies weight_t to the return realised over t→t+1.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import indicators as ind
from .fx_config import FXParams
from .pairs import Pair


@dataclass(frozen=True)
class PairContext:
    """Per-pair info an agent may need beyond the bars (e.g. carry)."""
    pair: Pair


def _clip_signal(s: pd.Series) -> pd.Series:
    return s.clip(-1.0, 1.0).fillna(0.0)


class Agent:
    """Base class: turn an OHLC frame into a [-1, 1] signal series."""
    name: str = "agent"

    def generate(self, bars: pd.DataFrame, ctx: PairContext, p: FXParams) -> pd.Series:
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<{type(self).__name__} {self.name!r}>"


class TrendAgent(Agent):
    name = "trend"

    def generate(self, bars, ctx, p):
        close, high, low = bars["close"], bars["high"], bars["low"]
        fast = ind.ema(close, p.ema_fast)
        slow = ind.ema(close, p.ema_slow)
        atr = ind.atr(high, low, close, p.atr_window).replace(0.0, np.nan)
        strength = np.tanh((fast - slow) / (atr * 3.0))
        gate = (ind.adx(high, low, close, p.adx_window) >= p.adx_trend_min).astype(float)
        return _clip_signal(strength * gate)


class BreakoutAgent(Agent):
    name = "breakout"

    def generate(self, bars, ctx, p):
        close, high, low = bars["close"], bars["high"], bars["low"]
        upper, lower = ind.donchian(high, low, p.donchian_window)
        event = pd.Series(np.nan, index=close.index)
        event[close > upper] = 1.0
        event[close < lower] = -1.0
        # Hold the last breakout, but only for a bounded window — an un-confirmed
        # breakout shouldn't persist forever (and unbounded ffill memory would
        # break the windowed fast path's exactness).
        return _clip_signal(event.ffill(limit=2 * p.donchian_window))


class MeanReversionAgent(Agent):
    name = "meanrev"

    def generate(self, bars, ctx, p):
        close, high, low = bars["close"], bars["high"], bars["low"]
        z = ind.bollinger_z(close, p.bb_window)
        rsi = ind.rsi(close, p.rsi_window)
        ranging = (ind.adx(high, low, close, p.adx_window) < p.adx_trend_min).astype(float)
        fade = -np.tanh(z / p.bb_z)                       # fade deviations from mean
        # Reinforce when RSI confirms the extreme; soften otherwise.
        boost = np.where(rsi < p.rsi_oversold, 1.0,
                         np.where(rsi > p.rsi_overbought, 1.0, 0.6))
        return _clip_signal(fade * boost * ranging)


class MomentumAgent(Agent):
    name = "momentum"

    def generate(self, bars, ctx, p):
        close = bars["close"]
        r = ind.roc(close, p.roc_window)
        scale = r.rolling(p.vol_lookback).std().replace(0.0, np.nan)
        return _clip_signal(np.tanh(r / scale))


class CarryAgent(Agent):
    name = "carry"

    def generate(self, bars, ctx, p):
        # Static tilt toward the side that earns more overnight financing.
        net = ctx.pair.swap_long_pips - ctx.pair.swap_short_pips
        tilt = float(np.clip(net / 1.0, -1.0, 1.0)) * 0.5
        return pd.Series(tilt, index=bars.index)


def default_agents() -> list[Agent]:
    """The standard five-agent roster."""
    return [TrendAgent(), BreakoutAgent(), MeanReversionAgent(),
            MomentumAgent(), CarryAgent()]


class AgentPool:
    """Evaluates every (pair, agent) pair concurrently.

    The hot work is vectorized numpy/pandas (which releases the GIL on the heavy
    array ops), so a thread pool gives real overlap and keeps per-cycle latency
    flat as the universe or roster grows. Set ``max_workers=1`` for a
    deterministic single-threaded run (used in tests)."""

    def __init__(self, agents: list[Agent] | None = None, max_workers: int | None = None):
        self.agents = agents if agents is not None else default_agents()
        self.max_workers = max_workers

    def evaluate(self, panel: dict[str, pd.DataFrame],
                 contexts: dict[str, PairContext], p: FXParams
                 ) -> dict[str, pd.DataFrame]:
        """Return {symbol -> DataFrame(index=time, columns=agent names)}."""
        tasks = [(sym, agent) for sym in panel for agent in self.agents]

        def run(task):
            sym, agent = task
            return sym, agent.name, agent.generate(panel[sym], contexts[sym], p)

        if self.max_workers == 1:
            results = [run(t) for t in tasks]
        else:
            with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
                results = list(ex.map(run, tasks))

        out: dict[str, dict[str, pd.Series]] = {sym: {} for sym in panel}
        for sym, name, series in results:
            out[sym][name] = series
        return {sym: pd.DataFrame(cols) for sym, cols in out.items()}
