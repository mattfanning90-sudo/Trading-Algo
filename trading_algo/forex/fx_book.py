"""Persistent multi-account FX paper-trading book.

Each account is an isolated JSON state file (``fx_state_{account}.json``) so the
account holder and their partner — or any number of books — run independently
with their own capital, risk profile and history. No broker connection required.

A *position* is a signed weight (fraction of equity); the book is marked to
market each run from the move in each pair since the last close, accrues
overnight carry, then rebalances toward fresh targets from the shared
`fx_strategy.compute_targets` (the same function the backtest uses), crossing
half the dealing spread on every weight change. A peak-to-trough drawdown
breaker flattens the book and sits out a cooldown, matching the backtest.

Usage
-----
    python -m trading_algo.forex.paper --init                 # open matt + partner
    python -m trading_algo.forex.paper --account matt         # daily update
    python -m trading_algo.forex.paper --status --account matt
    python -m trading_algo.forex.paper --compare matt partner
    (append --synthetic to run fully offline)
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
import pandas as pd

from . import explain
from . import fx_config as cfg
from . import fx_data
from .agents import AgentPool
from .fx_config import FXParams, profile
from .pairs import DEFAULT_UNIVERSE, get_pair

STATE_DIR = os.environ.get("FX_STATE_DIR") or os.path.join(os.path.dirname(__file__), "..", "..")
_DUST = 1e-4   # drop near-zero weights


def _state_file(account: str) -> str:
    return os.path.join(STATE_DIR, f"fx_state_{account}.json")


def load_state(account: str) -> dict:
    path = _state_file(account)
    if not os.path.exists(path):
        raise SystemExit(f"No FX account '{account}'. Run --init first.")
    with open(path) as f:
        return json.load(f)


def save_state(account: str, state: dict) -> None:
    with open(_state_file(account), "w") as f:
        json.dump(state, f, indent=2)


def ml_pool(models_dir: str | None = None) -> "AgentPool":
    """Build an AgentPool that includes the trained NeuralAgent if a model exists,
    else the five technical agents only. Lets paper trading opt into the DL layer."""
    from .ml_agent import ModelBundle, default_neural_agents
    md = models_dir or os.path.join(os.path.dirname(__file__), "models")
    path = os.path.join(md, "neural_sharpe.json")
    if os.path.exists(path):
        print(f"  using deep-learning agent from {path}")
        return AgentPool(default_neural_agents(ModelBundle.load(path)), max_workers=1)
    print("  (no trained model found — using the 5 technical agents only)")
    return AgentPool(max_workers=1)


def list_accounts() -> list[str]:
    out = []
    for fn in os.listdir(os.path.abspath(STATE_DIR)):
        if fn.startswith("fx_state_") and fn.endswith(".json"):
            out.append(fn[len("fx_state_"):-len(".json")])
    return sorted(out)


# ---------------------------------------------------------------------------
# Data / params helpers
# ---------------------------------------------------------------------------
def _params(state: dict) -> FXParams:
    return profile(state.get("profile", "balanced"))


def _panel(symbols: list[str], synthetic: bool):
    if synthetic:
        return fx_data.synthetic_panel(symbols)
    return fx_data.load_panel(symbols, cfg.START, use_cache=False)


def _sign(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


# ---------------------------------------------------------------------------
# Account lifecycle
# ---------------------------------------------------------------------------
def init_account(account: str, capital: float, profile_name: str,
                 symbols: list[str] | None = None,
                 currency: str = cfg.ACCOUNT_CURRENCY, force: bool = False) -> None:
    if os.path.exists(_state_file(account)) and not force:
        print(f"  account '{account}' already exists — skipping (use --force to reset)")
        return
    profile(profile_name)  # validate
    state = {
        "account": account,
        "currency": currency,
        "profile": profile_name,
        "symbols": list(symbols or DEFAULT_UNIVERSE),
        "initial_capital": float(capital),
        "equity": float(capital),
        "positions": {},
        "last_close": {},
        "last_bar_date": None,
        "peak_equity": float(capital),
        "risk_halted": False,
        "halt_cooldown": 0,
        "trades": [],
        "equity_history": [],
    }
    save_state(account, state)
    print(f"  FX account '{account}' opened: {capital:,.0f} {currency} "
          f"[{profile_name}] over {len(state['symbols'])} pairs")


def init_defaults(synthetic: bool, force: bool = False) -> None:
    """Open the two ready-to-run books from config (matt + partner)."""
    for name, spec in cfg.ACCOUNTS.items():
        init_account(name, spec["capital"], spec["profile"], force=force)


# ---------------------------------------------------------------------------
# Daily run
# ---------------------------------------------------------------------------
def _apply_band(positions: dict[str, float], target: pd.Series,
                p: FXParams) -> dict[str, float]:
    """No-churn band: keep current weight unless the target moves by >= min_delta."""
    new: dict[str, float] = {}
    keys = set(positions) | set(target.index)
    for k in keys:
        cur = positions.get(k, 0.0)
        tgt = float(target.get(k, 0.0))
        new[k] = cur if abs(tgt - cur) < p.rebalance_min_delta else tgt
    return {k: v for k, v in new.items() if abs(v) > _DUST}


def run_once(account: str, synthetic: bool = False,
             pool: AgentPool | None = None) -> None:
    state = load_state(account)
    p = _params(state)
    # Pick up any newly-added instruments (e.g. crypto) without losing history.
    symbols = list(dict.fromkeys([*state.get("symbols", []), *DEFAULT_UNIVERSE]))
    state["symbols"] = symbols
    panel = _panel(symbols, synthetic)
    if not panel:
        print(f"  [{account}] no market data available — skipping.")
        return

    px = fx_data.closes(panel)
    bar_date = px.index[-1].strftime("%Y-%m-%d")
    px_last = px.iloc[-1]

    if state["last_bar_date"] == bar_date:
        print(f"  [{account}] no new bar ({bar_date}) — equity "
              f"{state['equity']:,.2f} {state['currency']}")
        return

    positions = {k: float(v) for k, v in state["positions"].items()}
    last_close = state.get("last_close", {})

    # --- mark to market over the move since the last close ----------------
    pnl_frac = 0.0
    for s, w in positions.items():
        lc = last_close.get(s)
        nc = px_last.get(s)
        if lc and nc and lc == lc and nc == nc and lc > 0:
            pnl_frac += w * (nc / lc - 1.0)

    # carry for the (business) days elapsed since the last mark
    days = 1
    if state["last_bar_date"]:
        days = int(np.clip((pd.Timestamp(bar_date) - pd.Timestamp(state["last_bar_date"])).days, 1, 7))
    carry_frac = 0.0
    if p.include_carry:
        for s, w in positions.items():
            if w:
                carry_frac += abs(w) * get_pair(s).carry_fraction(px_last.get(s), _sign(w)) * days

    equity = state["equity"] * (1.0 + pnl_frac + carry_frac)

    # --- drawdown breaker --------------------------------------------------
    peak = max(state.get("peak_equity", equity), equity)
    halted = state.get("risk_halted", False)
    if halted:
        state["halt_cooldown"] = state.get("halt_cooldown", 0) - 1
        if state["halt_cooldown"] <= 0:
            halted = False
    elif p.max_drawdown_stop is not None and equity / peak - 1 <= -p.max_drawdown_stop:
        halted = True
        state["halt_cooldown"] = p.drawdown_cooldown_days
        print(f"  [{account}] ⛔ drawdown {equity / peak - 1:.1%} breached "
              f"{p.max_drawdown_stop:.0%} — flattening for {p.drawdown_cooldown_days} runs.")

    # --- target weights ----------------------------------------------------
    rationale: dict[str, dict] = {}
    if halted:
        target = pd.Series(dtype=float)
    else:
        target, rationale = explain.decide_and_explain(panel, p, pool=pool)
    new_positions = {} if halted else _apply_band(positions, target, p)

    # --- turnover cost (cross half the spread on each weight change) -------
    cost_frac = 0.0
    trades = []
    for s in sorted(set(positions) | set(new_positions)):
        delta = new_positions.get(s, 0.0) - positions.get(s, 0.0)
        if abs(delta) < _DUST:
            continue
        price = px_last.get(s)
        cost_frac += abs(delta) * 0.5 * get_pair(s).spread_fraction(price)
        why = rationale.get(s, {})
        trades.append({"date": bar_date, "pair": s,
                       "side": "BUY" if delta > 0 else "SELL",
                       "delta_weight": round(delta, 4),
                       "target_weight": round(new_positions.get(s, 0.0), 4),
                       "price": round(float(price), 5) if price == price else None,
                       "why": why.get("text"),
                       "regime": why.get("regime"),
                       "agents": why.get("agents"),
                       "indicators": why.get("indicators")})
    equity *= (1.0 - cost_frac)

    # --- persist -----------------------------------------------------------
    state["equity"] = float(equity)
    state["positions"] = {k: round(v, 5) for k, v in new_positions.items()}
    state["last_close"] = {s: float(px_last[s]) for s in symbols if px_last.get(s) == px_last.get(s)}
    state["last_bar_date"] = bar_date
    state["peak_equity"] = float(peak)
    state["risk_halted"] = halted
    state["decisions"] = rationale          # latest per-pair read (held or flat)
    state["trades"].extend(trades)
    if not state["equity_history"] or state["equity_history"][-1][0] != bar_date:
        state["equity_history"].append([bar_date, round(float(equity), 2)])
    save_state(account, state)

    gross = sum(abs(v) for v in new_positions.values())
    ret = equity / state["initial_capital"] - 1.0
    print(f"  [{account}] {bar_date}  equity {equity:,.2f} {state['currency']} "
          f"({ret:+.2%})  gross {gross:.2f}x  {len(new_positions)} pairs  "
          f"{len(trades)} trades")


def run_all(synthetic: bool = False, pool: AgentPool | None = None) -> None:
    accts = list_accounts() or list(cfg.ACCOUNTS)
    for name in accts:
        if os.path.exists(_state_file(name)):
            run_once(name, synthetic, pool=pool)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def status(account: str) -> None:
    state = load_state(account)
    print("=" * 56)
    print(f"  FX Paper Account '{account}'  [{state['profile']}]  "
          f"(base {state['currency']})")
    print("=" * 56)
    eq = pd.DataFrame(state["equity_history"], columns=["date", "equity"])
    if eq.empty:
        print("  No history yet — run a daily update first.")
    else:
        eq["date"] = pd.to_datetime(eq["date"])
        s = eq.set_index("date")["equity"]
        rets = s.pct_change(fill_method=None).dropna()
        print(f"  Inception        {s.index[0].date()}  "
              f"({state['initial_capital']:,.0f} {state['currency']})")
        print(f"  Current equity   {state['equity']:,.2f} {state['currency']}")
        print(f"  Total return     {state['equity'] / state['initial_capital'] - 1:+.2%}")
        if len(rets) > 20:
            print(f"  Ann. vol         {rets.std() * np.sqrt(cfg.ANNUALIZATION):.1%}")
            print(f"  Max drawdown     {(s / s.cummax() - 1).min():.2%}")
        print(f"  Trades to date   {len(state['trades'])}")
    if state.get("risk_halted"):
        print(f"  ⛔ RISK-HALTED   {state.get('halt_cooldown', 0)} runs remaining")
    print("\n  Open positions (signed weight = frac of equity):")
    if not state["positions"]:
        print("    (flat)")
    for s_, w in sorted(state["positions"].items(), key=lambda kv: -abs(kv[1])):
        side = "LONG " if w > 0 else "SHORT"
        print(f"    {side} {s_:<8} {w:+.3f}")


def compare(accounts: list[str]) -> None:
    print(f"{'Account':<12}{'Profile':<14}{'Capital':>12}{'Equity':>14}{'Return':>10}{'Trades':>8}")
    for name in accounts:
        if not os.path.exists(_state_file(name)):
            continue
        s = load_state(name)
        print(f"{name:<12}{s['profile']:<14}{s['initial_capital']:>12,.0f}"
              f"{s['equity']:>14,.2f}{s['equity'] / s['initial_capital'] - 1:>+9.2%}"
              f"{len(s['trades']):>8}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Multi-account FX paper trader")
    ap.add_argument("--account", default=None, help="account name (omit to run all)")
    ap.add_argument("--init", action="store_true", help="open the default accounts (matt + partner)")
    ap.add_argument("--capital", type=float, default=cfg.DEFAULT_CAPITAL)
    ap.add_argument("--profile", default="balanced", choices=cfg.profile_names())
    ap.add_argument("--force", action="store_true", help="overwrite existing state on --init")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--compare", nargs="+", metavar="ACCT")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--synthetic", action="store_true", help="run offline on synthetic data")
    args = ap.parse_args(argv)

    if args.list:
        print("Accounts:", ", ".join(list_accounts()) or "(none)")
    elif args.compare:
        compare(args.compare)
    elif args.init:
        if args.account:
            init_account(args.account, args.capital, args.profile, force=args.force)
        else:
            init_defaults(args.synthetic, force=args.force)
    elif args.status:
        if not args.account:
            raise SystemExit("--status needs --account")
        status(args.account)
    elif args.account:
        run_once(args.account, args.synthetic)
    else:
        run_all(args.synthetic)


if __name__ == "__main__":
    main()
