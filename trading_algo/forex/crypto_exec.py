"""Live crypto execution via ccxt — the cheapest real-time path to *actually* trade.

This turns the strategy's signed target weights into real exchange orders. It is
the crypto analogue of `execution_ibkr.py`, and follows the same discipline:

    1. Compute target weights from the strategy (the shared `compute_targets`).
    2. Pull current holdings + equity from the exchange (or assume flat offline).
    3. Diff target notional vs current -> orders, skipping dust + sub-minimums.
    4. Place as market orders — **only** when explicitly asked (`dry_run=False`).

SAFETY FIRST — read before going live:
* **Dry-run is the default.** Nothing is sent unless you pass `--live` (CLI) or
  `dry_run=False` (API). Watch the printed plan for days/weeks first.
* **Spot is long-only.** You cannot short or use leverage on a spot balance, so
  negative target weights are clamped to zero (you can only sell what you hold).
  The book's short/leveraged legs simply aren't expressed on spot — shorts and
  leverage need a margin/perp account (out of scope for this first version).
  This means spot execution is a *constrained projection* of the strategy.
* **A per-order notional cap** (`max_order_notional`) guards against fat-fingers.
* Keys come from the environment, never the repo:
      {EXCHANGE}_API_KEY / {EXCHANGE}_API_SECRET   (e.g. BINANCE_API_KEY)
      or the generic CRYPTO_API_KEY / CRYPTO_API_SECRET
      plus *_API_PASSWORD for exchanges that need a passphrase (OKX/KuCoin).

See docs/CRYPTO_HF.md and docs/DATA_FEEDS.md.
"""
from __future__ import annotations

import argparse
import os

import pandas as pd

from . import crypto_data, feeds, fx_book, fx_data
from .fx_config import profile
from .fx_strategy import compute_targets

DEFAULT_MIN_NOTIONAL = 10.0   # quote ccy (USDT): skip dust + below most exchange mins
# When going live without an explicit per-order cap, fall back to this fraction of
# equity so an uncapped fat-finger order can never leave the machine.
DEFAULT_MAX_NOTIONAL_FRACTION = 0.25


def _live_guards(target_weights: pd.Series, equity: float, *, dry_run: bool,
                 max_order_notional: float | None, risk_halted: bool,
                 quote: str) -> tuple[pd.Series, float | None]:
    """Pre-trade safety rails shared by rebalance() and the CLI.

    * A drawdown-halted book is flattened to cash (all target weights -> 0): it
      may only *reduce* risk, never open or rebalance into a risk-on book.
    * A live run with no per-order cap gets a default fraction-of-equity cap so we
      never send an uncapped order.

    Returns the (possibly zeroed) weights and the effective per-order cap.
    """
    if risk_halted:
        print("⛔ RISK-HALTED — drawdown breaker tripped; flatten-only, "
              "no opening/rebalancing orders.")
        target_weights = pd.Series(0.0, index=target_weights.index)
    if not dry_run and max_order_notional is None:
        max_order_notional = DEFAULT_MAX_NOTIONAL_FRACTION * float(equity)
        print(f"⚠ no per-order cap set for a LIVE run — applying a default "
              f"fat-finger cap ≈ {max_order_notional:,.2f} {quote} "
              f"({DEFAULT_MAX_NOTIONAL_FRACTION:.0%} of equity). "
              f"Pass --max-notional to override.")
    return target_weights, max_order_notional


def _finalize_order(o: dict, ex, max_order_notional: float | None) -> None:
    """Round to exchange precision, then RE-VALIDATE the notional against the cap
    (rounding can nudge an order back over the cap) and refresh the reported
    notional so it reflects the amount that will actually trade."""
    try:
        o["amount"] = float(ex.amount_to_precision(o["market"], o["amount"]))
    except Exception:
        pass
    if max_order_notional is not None and o["price"] > 0:
        if o["amount"] * o["price"] > max_order_notional:
            o["amount"] = max_order_notional / o["price"]
    o["notional"] = round(o["amount"] * o["price"], 2)


def _env(exchange: str, suffix: str) -> str | None:
    """Read EXCHANGE_API_<suffix>, falling back to the generic CRYPTO_API_<suffix>."""
    return (os.environ.get(f"{exchange.upper()}_API_{suffix}")
            or os.environ.get(f"CRYPTO_API_{suffix}"))


def private_exchange(name: str):
    """A ccxt client authenticated from the environment (for balances + orders)."""
    try:
        import ccxt
    except ImportError as e:
        raise SystemExit("ccxt not installed — run `pip install ccxt` for live crypto "
                         "execution.") from e
    key, secret = _env(name, "KEY"), _env(name, "SECRET")
    if not (key and secret):
        raise SystemExit(f"set {name.upper()}_API_KEY and {name.upper()}_API_SECRET "
                         "(or CRYPTO_API_KEY/SECRET) for live execution; "
                         "see docs/CRYPTO_HF.md.")
    cfg = {"apiKey": key, "secret": secret, "enableRateLimit": True}
    pwd = _env(name, "PASSWORD")
    if pwd:
        cfg["password"] = pwd
    return getattr(ccxt, name)(cfg)


def plan_orders(target_weights: pd.Series, prices: dict[str, float],
                equity: float, current_notional: dict[str, float] | None = None,
                *, spot: bool = True, min_notional: float = DEFAULT_MIN_NOTIONAL,
                max_order_notional: float | None = None) -> list[dict]:
    """Pure order planner (no network) — the testable core.

    Diffs target notional (weight × equity) against current holdings and returns
    one order dict per symbol that needs trading. Spot books are long-only, so
    negative weights are clamped to zero; sells are capped at what's held; each
    order is capped at `max_order_notional` if given.
    """
    current_notional = dict(current_notional or {})
    orders: list[dict] = []
    symbols = sorted(set(target_weights.index) | set(current_notional))
    for sym in symbols:
        price = prices.get(sym)
        if not price or price != price or price <= 0:
            continue
        w = float(target_weights.get(sym, 0.0))
        if spot:
            w = max(w, 0.0)                       # can't short / lever a spot balance
        target_value = w * equity
        held_value = current_notional.get(sym, 0.0)
        delta = target_value - held_value
        if abs(delta) < min_notional:
            continue
        if max_order_notional is not None:
            delta = max(-max_order_notional, min(max_order_notional, delta))
        side = "buy" if delta > 0 else "sell"
        amount = abs(delta) / price
        if spot and side == "sell":               # on spot, never sell more than you hold
            amount = min(amount, held_value / price)
        notional = amount * price
        if amount <= 0 or notional < min_notional:
            continue
        orders.append({"symbol": sym, "market": crypto_data.SPOT.get(sym, sym),
                       "side": side, "amount": amount,
                       "price": float(price), "notional": round(notional, 2)})
    return orders


def _exchange_state(ex, symbols: list[str], quote: str
                    ) -> tuple[float, dict[str, float], dict[str, float]]:
    """(equity_in_quote, current_notional_by_symbol, last_price_by_symbol)."""
    balance = ex.fetch_balance()
    tickers = ex.fetch_tickers([crypto_data.SPOT.get(s, s) for s in symbols])
    prices, current = {}, {}
    equity = float(balance.get(quote, {}).get("total", 0.0) or 0.0)
    for s in symbols:
        mkt = crypto_data.SPOT.get(s, s)
        t = tickers.get(mkt) or {}
        px = t.get("last") or t.get("close")
        if not px:
            continue
        prices[s] = float(px)
        base = mkt.split("/")[0]
        held = float(balance.get(base, {}).get("total", 0.0) or 0.0)
        val = held * float(px)
        current[s] = val
        equity += val
    return equity, current, prices


def rebalance(target_weights: pd.Series, *, exchange: str = "binance",
              quote: str = "USDT", spot: bool = True, dry_run: bool = True,
              min_notional: float = DEFAULT_MIN_NOTIONAL,
              max_order_notional: float | None = None,
              risk_halted: bool = False) -> list[dict]:
    """Connect, diff target vs live holdings, and (only if not dry_run) place orders.

    A drawdown-halted book (`risk_halted=True`) is flattened, never re-risked; a
    live run always carries a per-order notional cap (an explicit `max_order_notional`
    or a default fraction of equity)."""
    ex = private_exchange(exchange)
    ex.load_markets()
    symbols = list(target_weights.index)
    equity, current, prices = _exchange_state(ex, symbols, quote)
    target_weights, max_order_notional = _live_guards(
        target_weights, equity, dry_run=dry_run,
        max_order_notional=max_order_notional, risk_halted=risk_halted, quote=quote)
    orders = plan_orders(target_weights, prices, equity, current, spot=spot,
                         min_notional=min_notional,
                         max_order_notional=max_order_notional)
    for o in orders:
        _finalize_order(o, ex, max_order_notional)
        if not dry_run:
            ex.create_order(o["market"], "market", o["side"], o["amount"])
            o["status"] = "SENT"
        else:
            o["status"] = "DRY-RUN"
    return orders


def _print_plan(orders: list[dict], live: bool, equity: float, quote: str) -> None:
    banner = "🔴 LIVE — placing real orders" if live else "🟢 DRY-RUN — nothing sent"
    print("=" * 60)
    print(f"  Crypto execution  [{banner}]   equity ≈ {equity:,.2f} {quote}")
    print("=" * 60)
    if not orders:
        print("  Already in line with targets — no orders.")
        return
    for o in orders:
        print(f"  {o['side'].upper():<4} {o['market']:<12} "
              f"{o['amount']:.6f} @ ~{o['price']:,.2f}  "
              f"≈ {o['notional']:,.2f} {quote}  [{o['status']}]")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Live crypto execution (ccxt) — dry-run by default")
    ap.add_argument("--account", required=True, help="paper book whose symbols/profile to use")
    ap.add_argument("--exchange", default="binance", help="ccxt exchange (default binance)")
    ap.add_argument("--quote", default="USDT", help="quote currency for sizing (default USDT)")
    ap.add_argument("--bar", default="1m", help="signal bar interval (default 1m)")
    ap.add_argument("--live", action="store_true",
                    help="actually place orders (default: dry-run, nothing sent)")
    ap.add_argument("--allow-short", action="store_true",
                    help="express short/leveraged legs (needs a margin/perp account; "
                         "default spot long-only)")
    ap.add_argument("--min-notional", type=float, default=DEFAULT_MIN_NOTIONAL)
    ap.add_argument("--max-notional", type=float, default=None,
                    help="per-order notional cap (fat-finger guard)")
    ap.add_argument("--equity", type=float, default=None,
                    help="override equity (offline/synthetic sizing)")
    ap.add_argument("--synthetic", action="store_true",
                    help="offline dry-run: synthetic prices, flat book (forces dry-run)")
    args = ap.parse_args(argv)

    state = fx_book.load_state(args.account)
    symbols = list(state.get("symbols") or crypto_data.CRYPTO_UNIVERSE)
    p = profile(state.get("profile", "balanced"))
    panel = feeds.load(symbols, synthetic=args.synthetic, interval=args.bar,
                       source="crypto", exchange=args.exchange)
    if not panel:
        raise SystemExit("no crypto market data (offline? add --synthetic).")
    targets = compute_targets(panel, p)
    spot = not args.allow_short
    # Drawdown circuit-breaker: honour the persisted halt owned by the paper engine.
    risk_halted = bool(state.get("risk_halted", False))

    if args.synthetic:
        # Fully offline: size off the book's equity, assume a flat starting book.
        px = fx_data.closes(panel).iloc[-1]
        prices = {s: float(px[s]) for s in symbols if px.get(s) == px.get(s)}
        equity = float(args.equity if args.equity is not None else state.get("equity", 0.0))
        targets, _ = _live_guards(targets, equity, dry_run=True,
                                  max_order_notional=args.max_notional,
                                  risk_halted=risk_halted, quote=args.quote)
        orders = plan_orders(targets, prices, equity, {}, spot=spot,
                             min_notional=args.min_notional,
                             max_order_notional=args.max_notional)
        for o in orders:
            o["status"] = "DRY-RUN"
        _print_plan(orders, live=False, equity=equity, quote=args.quote)
        return

    ex = private_exchange(args.exchange)
    ex.load_markets()
    equity, current, prices = _exchange_state(ex, symbols, args.quote)
    targets, max_notional = _live_guards(targets, equity, dry_run=not args.live,
                                         max_order_notional=args.max_notional,
                                         risk_halted=risk_halted, quote=args.quote)
    orders = plan_orders(targets, prices, equity, current, spot=spot,
                         min_notional=args.min_notional,
                         max_order_notional=max_notional)
    for o in orders:
        _finalize_order(o, ex, max_notional)
        if args.live:
            ex.create_order(o["market"], "market", o["side"], o["amount"])
            o["status"] = "SENT"
        else:
            o["status"] = "DRY-RUN"
    _print_plan(orders, live=args.live, equity=equity, quote=args.quote)


if __name__ == "__main__":
    main()
