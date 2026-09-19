"""After-tax reporting: CGT on realised round-trips + dividend withholding.

**NOT tax advice, and NOT a trading-path change.** Tax is entity-specific — your
marginal rate, your structure, your residency — so this is a REPORTING layer
over the realised ledger. It never touches an equity curve, a weight or a fill.
Confirm the treatment and the rates with your accountant before relying on any
number here.

Why it exists. The books rebalance monthly, so essentially nothing is held the
12 months an Australian CGT discount requires: the gains are short-term and taxed
at the full marginal rate. On a high marginal rate that can exceed every
transaction cost in this repo combined — which makes **holding period a strategy
parameter, not just a cost**. A 12-month hold is a different after-tax strategy
even if it is the same signal.

Two things are modelled:

* **CGT** on the FIFO round-trips `pnl.build_lots` already produces. Losses are
  applied against NON-discounted gains first (the ATO lets you choose the order;
  that choice is strictly better for the taxpayer).
* **Dividend withholding.** The price series is `auto_adjust=True`, i.e. TOTAL
  RETURN — the backtest silently credits you 100% of every dividend. An
  Australian resident holding US equities loses 15% to US withholding under the
  treaty (with a W-8BEN; 30% without). This measures that drag from the actual
  dividend history over the actual holding periods, rather than assuming a yield.

Franking credits on the ASX sleeve are deliberately NOT modelled: they are a
credit rather than a cost, so omitting them is conservative, and their value
depends on your marginal rate in a way a book-level report cannot know.
"""
from __future__ import annotations

import pandas as pd

# The ATO requires the asset to be held for MORE than 12 months — not at least,
# and not 365 days. Those differ: 2024-01-01 -> 2025-01-01 is 366 CALENDAR days
# because 2024 is a leap year, but it is exactly 12 months and earns NO discount.
# A day-count threshold silently halves the tax on every trade that straddles a
# 29 February. The comparison is therefore a calendar-month offset.
CGT_DISCOUNT_MONTHS = 12
CGT_DISCOUNT = 0.5


def holding_days(round_trip: dict) -> int | None:
    """Calendar days between the entry fill and the exit fill, or None if either
    date is missing (an old ledger row that predates date stamping)."""
    entry, exit_ = round_trip.get("entry_date"), round_trip.get("date")
    if not entry or not exit_:
        return None
    try:
        return int((pd.Timestamp(str(exit_)[:10]) - pd.Timestamp(str(entry)[:10])).days)
    except (ValueError, TypeError):
        return None


def is_discount_eligible(round_trip: dict, *,
                         months: int = CGT_DISCOUNT_MONTHS) -> bool:
    """Held MORE than 12 calendar months (leap-year safe — see the constant).

    An undated round-trip is treated as ineligible: the conservative direction,
    since claiming a discount you cannot evidence is the expensive mistake.
    """
    entry, exit_ = round_trip.get("entry_date"), round_trip.get("date")
    if not entry or not exit_:
        return False
    try:
        anniversary = (pd.Timestamp(str(entry)[:10])
                       + pd.DateOffset(months=months))
        return pd.Timestamp(str(exit_)[:10]) > anniversary
    except (ValueError, TypeError):
        return False


def cgt_summary(round_trips: list[dict], *, marginal_rate: float,
                discount: float = CGT_DISCOUNT,
                discount_months: int = CGT_DISCOUNT_MONTHS) -> dict:
    """Capital-gains position for a set of realised round-trips.

    `net` on each round-trip is already net of the transaction costs that
    attach to it (entry and exit), which is what the ATO's cost base does too.

    Loss application order: against NON-discounted gains first. The ATO lets the
    taxpayer choose, and this order is strictly better — a dollar of loss shields
    a dollar of fully-taxed gain, or only fifty cents of discounted gain.
    """
    disc_gain = plain_gain = losses = 0.0
    n_disc = n_plain = 0
    for rt in round_trips:
        net = float(rt.get("net") or 0.0)
        if net < 0:
            losses += -net
            continue
        if is_discount_eligible(rt, months=discount_months):
            disc_gain += net
            n_disc += 1
        else:
            plain_gain += net
            n_plain += 1

    # Losses against the fully-taxed gains first, then the discountable ones.
    used = min(losses, plain_gain)
    plain_after = plain_gain - used
    remaining_loss = losses - used
    used2 = min(remaining_loss, disc_gain)
    disc_after = disc_gain - used2
    carried = remaining_loss - used2

    discounted = disc_after * (1.0 - discount)
    net_capital_gain = plain_after + discounted
    return {
        "round_trips": len(round_trips),
        "discount_eligible_trips": n_disc,
        "short_term_trips": n_plain,
        "gross_gains": disc_gain + plain_gain,
        "gross_losses": losses,
        "short_term_gain_after_losses": plain_after,
        "discountable_gain_after_losses": disc_after,
        "discounted_gain": discounted,
        "net_capital_gain": net_capital_gain,
        "tax": max(0.0, net_capital_gain) * float(marginal_rate),
        "loss_carried_forward": carried,
        "marginal_rate": float(marginal_rate),
    }


def _region_price_scale(region: str) -> float:
    """The region's quote-unit scale — 0.01 for the LSE (pence -> pounds).

    Dividends come off Yahoo in the SAME units as prices, so they need the same
    scaling. Missing this overstated the FTSE sleeve's income 100x on the first
    run of this report.
    """
    try:
        from .regions import get_region
        return float(get_region(region).price_scale)
    except Exception:
        return 1.0


def withholding_drag(round_trips: list[dict], *, dividends, rates: dict,
                     price_scale=None) -> dict:
    """Dividends received over the actual holding periods, and the tax withheld.

    `dividends` is a callable `ticker -> pd.Series` of per-share payments indexed
    by pay date — injectable so no test touches the network. Payments are counted
    when they fall inside a round-trip's own holding window, so a name bought and
    sold between two ex-dates correctly earns nothing.

    This is an ESTIMATE of a drag the price series hides: with
    `auto_adjust=True` the backtest already credited you the full dividend.
    """
    gross = withheld = 0.0
    by_region: dict[str, float] = {}
    for rt in round_trips:
        entry, exit_ = rt.get("entry_date"), rt.get("date")
        if not entry or not exit_:
            continue
        try:
            series = dividends(rt.get("ticker"))
        except Exception:
            continue                      # a missing history is not an error
        if series is None or not len(series):
            continue
        try:
            lo, hi = pd.Timestamp(str(entry)[:10]), pd.Timestamp(str(exit_)[:10])
            idx = pd.to_datetime(series.index).tz_localize(None)
            paid = float(series[(idx >= lo) & (idx <= hi)].sum())
        except (ValueError, TypeError):
            continue
        scale = (price_scale or _region_price_scale)(rt.get("region"))
        amount = paid * abs(int(rt.get("filled") or 0)) * float(scale)
        if not amount:
            continue
        rate = float(rates.get(rt.get("region"), 0.0))
        gross += amount
        withheld += amount * rate
        by_region[rt.get("region")] = by_region.get(rt.get("region"), 0.0) + amount * rate
    return {"gross_dividends": gross, "withheld": withheld,
            "withheld_by_region": by_region}


def yahoo_dividends(ticker: str):
    """Per-share dividend history from Yahoo. The default `dividends` source.

    Network-bound and therefore never used by a test — `withholding_drag` takes
    the callable so the maths can be pinned offline.
    """
    import yfinance as yf
    try:
        return yf.Ticker(ticker).dividends
    except Exception:
        return pd.Series(dtype=float)


def open_lots_as_holdings(open_lots: dict, as_of: str | None = None) -> list[dict]:
    """Currently-held lots shaped like round-trips, so the withholding scan can
    see them.

    Dividends on OPEN positions are received just the same. Counting only closed
    round-trips systematically understates the drag — and for a book that is
    mostly still holding, understates it badly. These carry no `net`, so they
    never reach the CGT side: an unrealised gain is not a CGT event.
    """
    as_of = as_of or str(pd.Timestamp.today().date())
    out: list[dict] = []
    for (region, ticker), queue in (open_lots or {}).items():
        for lot in queue:
            qty, _price, _cost, when = lot[0], lot[1], lot[2], lot[3]
            if not qty or not when:
                continue
            out.append({"entry_date": when, "date": as_of, "region": region,
                        "ticker": ticker, "filled": abs(int(qty))})
    return out


def tax_report(state: dict, *, marginal_rate: float, rates: dict,
               dividends=None) -> str:
    """Markdown after-tax summary for one paper book."""
    from . import pnl

    open_lots, realized = pnl.build_lots(state.get("trades") or [])
    cg = cgt_summary(realized, marginal_rate=marginal_rate)
    # Withholding applies to OPEN positions too — a dividend does not wait for
    # you to sell. CGT deliberately does not: an unrealised gain is not an event.
    holdings = open_lots_as_holdings(open_lots)
    wh = withholding_drag(realized + holdings,
                          dividends=dividends or yahoo_dividends, rates=rates)
    ccy = state.get("base_currency", "AUD")
    held = [d for d in (holding_days(r) for r in realized) if d is not None]
    median_hold = sorted(held)[len(held) // 2] if held else 0

    lines = [
        f"# After-tax summary — {state.get('account', '?')}",
        "",
        "> **Not tax advice.** A reporting layer over the realised ledger, not a "
        "trading input. Confirm the treatment and the rates with your accountant.",
        "",
        "## Capital gains",
        "",
        "| | |", "|---|---|",
        f"| Realised round-trips | {cg['round_trips']} |",
        f"| Median holding period | {median_hold} days |",
        f"| Cleared 12 months (discount-eligible) | "
        f"**{cg['discount_eligible_trips']}** of {cg['round_trips']} |",
        f"| Gross gains | {cg['gross_gains']:,.2f} |",
        f"| Gross losses | {cg['gross_losses']:,.2f} |",
        f"| Net capital gain (after losses + discount) | {cg['net_capital_gain']:,.2f} |",
        f"| Estimated tax @ {cg['marginal_rate']:.0%} | **{cg['tax']:,.2f}** |",
        f"| Loss carried forward | {cg['loss_carried_forward']:,.2f} |",
        "",
        "## Dividend withholding",
        "",
        "The price series is `auto_adjust=True` — total return — so the book "
        "already credited itself 100% of these dividends.",
        "",
        "| | |", "|---|---|",
        f"| Dividends received while held | {wh['gross_dividends']:,.2f} |",
        f"| Withheld at source (never arrives) | **{wh['withheld']:,.2f}** |",
    ]
    for region, amt in sorted(wh["withheld_by_region"].items()):
        lines.append(f"| — {region} | {amt:,.2f} |")
    lines += ["", f"_Amounts in each sleeve's local currency, not converted to "
                  f"{ccy} — invariant #6._"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    import argparse

    from . import config as cfg
    from . import paper_trade

    ap = argparse.ArgumentParser(description="After-tax summary (reporting only)")
    ap.add_argument("--account", default="full")
    ap.add_argument("--rate", type=float, default=None,
                    help=f"marginal tax rate (default {cfg.MARGINAL_TAX_RATE})")
    ap.add_argument("--no-dividends", action="store_true",
                    help="skip the withholding section (avoids network calls)")
    ap.add_argument("--out", default=None, help="also write the report here")
    args = ap.parse_args(argv)

    state = paper_trade.load_state(args.account)
    md = tax_report(
        state, marginal_rate=args.rate if args.rate is not None else cfg.MARGINAL_TAX_RATE,
        rates=cfg.DIVIDEND_WITHHOLDING,
        dividends=(lambda _t: pd.Series(dtype=float)) if args.no_dividends else None)
    print(md)
    if args.out:
        import os
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(md + "\n")


if __name__ == "__main__":
    main()
