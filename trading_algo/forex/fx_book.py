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
from . import feeds
from . import fx_config as cfg
from . import fx_data
from . import fxconv
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


def _panel(symbols: list[str], synthetic: bool, interval: str = "1d",
           source: str = "yahoo", exchange: str | None = None,
           min_bars: int | None = None):
    """Aligned OHLC panel from any data source (see feeds.SOURCES). `min_bars`
    bounds the daily fetch to the strategy's warm-up need (see feeds.load)."""
    return feeds.load(symbols, synthetic=synthetic, interval=interval,
                      source=source, exchange=exchange, use_cache=False,
                      min_bars=min_bars)


def _bar_key(ts, interval: str) -> str:
    """Bar identifier — date for daily (unchanged), timestamp for intraday."""
    return ts.strftime("%Y-%m-%d") if interval in ("1d", "B") else ts.strftime("%Y-%m-%d %H:%M")


def _sign(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


# ---------------------------------------------------------------------------
# Account lifecycle
# ---------------------------------------------------------------------------
def init_account(account: str, capital: float, profile_name: str,
                 symbols: list[str] | None = None,
                 currency: str = cfg.ACCOUNT_CURRENCY, source: str = "yahoo",
                 force: bool = False) -> None:
    if os.path.exists(_state_file(account)) and not force:
        print(f"  account '{account}' already exists — skipping (use --force to reset)")
        return
    profile(profile_name)  # validate
    state = {
        "account": account,
        "currency": currency,
        "profile": profile_name,
        "source": source,
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
          f"[{profile_name}] over {len(state['symbols'])} instruments "
          f"(source: {source})")


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
             pool: AgentPool | None = None, interval: str = "1d",
             source: str | None = None, exchange: str | None = None) -> None:
    state = load_state(account)
    p = _params(state)
    # Resolve the data source: explicit CLI override, else the book's own stored
    # source (defaults to yahoo). `--exchange` implies crypto (back-compat).
    src = feeds.resolve_source(source if source is not None
                               else state.get("source", "yahoo"), exchange)
    if src == "yahoo":
        # Pick up any newly-added instruments (e.g. crypto) without losing history.
        symbols = list(dict.fromkeys([*state.get("symbols", []), *DEFAULT_UNIVERSE]))
    else:
        symbols = list(state.get("symbols") or [])  # non-default feeds trade their own book
    state["symbols"] = symbols
    state["source"] = src
    from .fx_strategy import min_history
    panel = _panel(symbols, synthetic, interval, source=src, exchange=exchange,
                   min_bars=min_history(p) + 5)
    if not panel:
        print(f"  [{account}] no market data available — skipping.")
        return

    px = fx_data.closes(panel)
    bar_date = _bar_key(px.index[-1], interval)
    px_last = px.iloc[-1]

    if state["last_bar_date"] == bar_date:
        # No new bar to trade, but keep the dashboard's "today's read" current by
        # refreshing the per-pair reasoning snapshot.
        if not state.get("risk_halted"):
            try:
                _, rationale = explain.decide_and_explain(panel, p, pool=pool)
                state["decisions"] = rationale
                save_state(account, state)
            except Exception as exc:                       # never let display break a run
                print(f"  [{account}] (decision refresh skipped: {exc!r})")
        print(f"  [{account}] no new bar ({bar_date}) — equity "
              f"{state['equity']:,.2f} {state['currency']}")
        return

    positions = {k: float(v) for k, v in state["positions"].items()}
    last_close = state.get("last_close", {})
    prev_equity = float(state.get("equity") or state["initial_capital"])  # before today's mark

    # --- mark to market over the move since the last close ----------------
    pnl_frac = 0.0
    day_contribs: list[dict] = []          # per-pair P&L attribution for the daily summary
    for s, w in positions.items():
        lc = last_close.get(s)
        nc = px_last.get(s)
        if lc and nc and lc == lc and nc == nc and lc > 0:
            # Translate the position's quote-currency P&L into AUD: an AUD account
            # converts to the quote currency to hold the pair, so AUD/quote moves
            # (esp. AUD/USD) are part of the real P&L. Falls back to 1.0 when the
            # AUD/quote rate can't be derived (e.g. a crypto-only book).
            fxf = fxconv.conversion_factor(get_pair(s).quote, last_close, px_last)
            contrib = w * ((nc / lc) * fxf - 1.0)
            pnl_frac += contrib
            day_contribs.append({"pair": s, "weight": round(w, 4),
                                 "move": round(nc / lc - 1.0, 6),     # the pair's own move
                                 "fx": round(fxf - 1.0, 6),           # AUD/quote translation
                                 "contrib": round(contrib, 6)})        # P&L as a frac of equity

    # Carry scales with the actual elapsed time since the last mark (so it's
    # correct for intraday/1-minute bars, not just daily). Daily is unchanged:
    # consecutive days -> 1.0, a weekend gap -> 3.0.
    elapsed = 1.0
    if state["last_bar_date"]:
        secs = (pd.Timestamp(bar_date) - pd.Timestamp(state["last_bar_date"])).total_seconds()
        elapsed = float(np.clip(secs / 86400.0, 0.0, 7.0))
    carry_frac = 0.0
    if p.include_carry:
        for s, w in positions.items():
            if w:
                carry_frac += abs(w) * get_pair(s).carry_fraction(px_last.get(s), _sign(w)) * elapsed

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
    state["last_close"] = {s: float(px_last[s]) for s in symbols
                           if s in px_last.index and px_last[s] == px_last[s]}
    state["last_bar_date"] = bar_date
    state["peak_equity"] = float(peak)
    state["risk_halted"] = halted
    state["decisions"] = rationale          # latest per-pair read (held or flat)
    # --- daily P&L attribution snapshot (the "what drove today" summary) ----
    day_contribs.sort(key=lambda c: -abs(c["contrib"]))
    state["daily"] = {
        "date": bar_date,
        "start_equity": round(prev_equity, 2),
        "end_equity": round(float(equity), 2),
        "pnl_pct": round(pnl_frac, 6),          # market P&L (positions × moves)
        "carry_pct": round(carry_frac, 6),      # financing/swap
        "cost_pct": round(-cost_frac, 6),       # spread paid on today's rebalance (negative)
        "net_pct": round(equity / prev_equity - 1.0, 6) if prev_equity else 0.0,
        "net_aud": round(float(equity) - prev_equity, 2),
        "by_pair": day_contribs,
        "halted": halted,
    }
    state["trades"].extend(trades)
    if not state["equity_history"] or state["equity_history"][-1][0] != bar_date:
        state["equity_history"].append([bar_date, round(float(equity), 2)])
    save_state(account, state)

    gross = sum(abs(v) for v in new_positions.values())
    ret = equity / state["initial_capital"] - 1.0
    print(f"  [{account}] {bar_date}  equity {equity:,.2f} {state['currency']} "
          f"({ret:+.2%})  gross {gross:.2f}x  {len(new_positions)} pairs  "
          f"{len(trades)} trades")


def run_all(synthetic: bool = False, pool: AgentPool | None = None,
            interval: str = "1d", source: str | None = None,
            exchange: str | None = None) -> None:
    accts = list_accounts() or list(cfg.ACCOUNTS)
    for name in accts:
        if os.path.exists(_state_file(name)):
            run_once(name, synthetic, pool=pool, interval=interval,
                     source=source, exchange=exchange)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def status(account: str) -> None:
    state = load_state(account)
    print("=" * 56)
    print(f"  FX Paper Account '{account}'  [{state['profile']}]  "
          f"(base {state['currency']}, source {state.get('source', 'yahoo')})")
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
    ap.add_argument("--bar", default="1d",
                    help="data bar interval, e.g. 60m / 15m / 1m (default daily). "
                         "Live intraday needs a real-time feed; see docs/HFT_REALITY.md.")
    ap.add_argument("--source", default=None, choices=feeds.SOURCES,
                    help="market-data source: yahoo (default), crypto, oanda, "
                         "alpaca, openbb. See docs/DATA_FEEDS.md.")
    ap.add_argument("--exchange", default=None,
                    help="crypto exchange via ccxt (e.g. binance) for the crypto "
                         "source; default binance. See docs/CRYPTO_HF.md.")
    args = ap.parse_args(argv)

    if args.list:
        print("Accounts:", ", ".join(list_accounts()) or "(none)")
    elif args.compare:
        compare(args.compare)
    elif args.init:
        if args.account:
            # A non-yahoo source (or the hf_crypto profile) seeds that source's
            # natural universe — crypto for ccxt, FX majors for OANDA, US equities
            # for Alpaca/OpenBB — and the book remembers its source.
            src = feeds.resolve_source(args.source, args.exchange)
            if args.profile == "hf_crypto" and src == "yahoo":
                src = "crypto"
            symbols = None if src == "yahoo" else feeds.default_universe(src, args.profile)
            init_account(args.account, args.capital, args.profile, symbols=symbols,
                         source=src, force=args.force)
        else:
            init_defaults(args.synthetic, force=args.force)
    elif args.status:
        if not args.account:
            raise SystemExit("--status needs --account")
        status(args.account)
    elif args.account:
        run_once(args.account, args.synthetic, interval=args.bar,
                 source=args.source, exchange=args.exchange)
    else:
        run_all(args.synthetic, interval=args.bar,
                source=args.source, exchange=args.exchange)


if __name__ == "__main__":
    main()
