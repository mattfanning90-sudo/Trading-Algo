"""Overfitting-aware statistics for the FX subsystem.

The core López de Prado stats (PSR / DSR / PBO, and the normal CDF/PPF helpers)
now live in `trading_algo.validation` — the ONE shared implementation (foundation
P0-E) used by both the equity sleeves and this subsystem. They are re-exported
here unchanged for backward compatibility; only the FX/ML-specific meta-label bet
sizing is defined locally.

References (full citations in docs/FX_DEEP_RESEARCH.md): Bailey & López de Prado,
*The Deflated Sharpe Ratio* (2014); *The Probability of Backtest Overfitting*
(2015); *Advances in Financial Machine Learning* (2018).
"""
from __future__ import annotations

import numpy as np

# Re-export the shared implementation so `trading_algo.forex.validation.<fn>`
# keeps working for every existing caller (dashboard, research, ml_backtest, …).
from ..validation import (  # noqa: F401
    _GAMMA,
    _E,
    _norm_cdf,
    _norm_ppf,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    pbo,
    probabilistic_sharpe_ratio,
    sharpe_ratio,
)


def bet_size_from_prob(p: np.ndarray) -> np.ndarray:
    """Map a meta-model probability of a correct call to a size in [0, 1].

    López de Prado bet sizing: z = (p − 0.5)/√(p(1−p)); size = 2·Φ(z) − 1.
    Multiply by the primary signal's sign for a signed position.
    """
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    z = (p - 0.5) / np.sqrt(p * (1 - p))
    vec = np.vectorize(_norm_cdf)
    return 2.0 * vec(z) - 1.0
