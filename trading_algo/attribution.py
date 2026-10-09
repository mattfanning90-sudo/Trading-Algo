"""Live-vs-backtest tracking and P&L attribution (backlog F3).

The paper (and, later, live) book has no reference point today: you stare at raw
P&L with no idea whether it is tracking the strategy the backtest promised. This
module closes that loop. Given a book's realized equity curve and a backtest's
predicted curve over the SAME window, it reports:

  * divergence    — realized total return minus backtest-predicted total return.
  * tracking_error — annualised std of the per-period return differences, with an
    alert when it exceeds a budget (AC4: 200bps).
  * cost drag     — realized transaction cost per region, measured from the trade
    log (commission + stamp + slippage-from-decision-vs-fill, reusing F11's
    decision price), the one attribution bucket we can measure exactly.

No lookahead (invariant #1): the predicted curve must come from a backtest over
the identical price window the book actually saw — the caller passes it in; this
module never refetches with hindsight. Cost is reported per region in the sleeve's
LOCAL currency (invariant #6) — sleeves are not blended across currencies here.
"""
from __future__ import annotations

from bisect import bisect_left

import pandas as pd

from . import config as cfg
from . import fees, metrics, pnl, tca
from .regions import get_region

# AC4: alert when annualised tracking error exceeds this budget.
TRACKING_ERROR_ALERT_BPS = 200.0

# The reconciliation bridge (spec §5). 1 basis point on total return.
BRIDGE_TOLERANCE = 1e-4

BRIDGE_LINES = ("dividends", "cash_interest", "fill_convention", "commission_floor",
                "stamp_duty", "exposure_gap", "rebalance_timing", "fx_translation")


def equity_returns(equity_history: list) -> pd.Series:
    """[[date, value], ...] -> period-return Series indexed by date."""
    if not equity_history:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([d for d, _ in equity_history])
    vals = pd.Series([float(v) for _, v in equity_history], index=idx).sort_index()
    return vals.pct_change().dropna()


def total_return(equity_history: list) -> float:
    if not equity_history or len(equity_history) < 2:
        return 0.0
    first, last = float(equity_history[0][1]), float(equity_history[-1][1])
    return last / first - 1.0 if first else 0.0


def tracking_error(realized_ret: pd.Series, predicted_ret: pd.Series,
                   periods_per_year: int = metrics.ANNUALIZATION) -> dict:
    """Annualised std of (realized - predicted) per-period returns on common dates.

    The annualisation is the measurement layer's (`metrics.annualised_vol`), so
    a tracking error and a vol on this repo's pages can never mean two
    different things."""
    df = pd.concat([realized_ret.rename("r"), predicted_ret.rename("p")], axis=1).dropna()
    if len(df) < 2:
        return {"tracking_error_bps": None, "n_obs": int(len(df))}
    te = metrics.annualised_vol(df["r"] - df["p"],
                                periods_per_year=periods_per_year)
    return {"tracking_error_bps": round(te * 1e4, 1), "n_obs": int(len(df))}


def realized_cost_drag(trades: list) -> dict:
    """Per-region realized transaction cost (commission + stamp + slippage) as a
    fraction of traded notional, in the sleeve's local currency."""
    by_region: dict[str, dict] = {}
    for t in trades:
        fill = t.get("fill")
        shares = t.get("shares", 0)
        if not fill or not shares or not pnl.is_fill(t):
            continue                       # a dividend is not traded notional
        notional = float(shares) * float(fill)
        decision = t.get("decision", fill)
        slippage = abs(float(fill) - float(decision)) * float(shares)
        cost = float(t.get("commission", 0.0)) + float(t.get("stamp_duty", 0.0)) + slippage
        agg = by_region.setdefault(t.get("region"), {
            "cost": 0.0, "notional": 0.0, "currency": t.get("currency")})
        agg["cost"] += cost
        agg["notional"] += notional

    out: dict = {}
    for rk, agg in by_region.items():
        drag_bps = (agg["cost"] / agg["notional"] * 1e4) if agg["notional"] else 0.0
        out[rk] = {
            "cost": round(agg["cost"], 2),
            "notional": round(agg["notional"], 2),
            "cost_drag_bps": round(drag_bps, 1),
            "currency": agg["currency"],
        }
    return out


def attribution_report(paper_state: dict, predicted_equity: pd.Series | None = None,
                       predicted_cost_fraction: float | None = None) -> dict:
    """Tracking + attribution for one book.

    `predicted_equity` is a backtest equity curve over the SAME window (from the
    caller — no hindsight refetch here). `predicted_cost_fraction` is the
    backtest's modelled round-trip cost, if available, to attribute the cost
    bucket of the divergence.
    """
    eh = paper_state.get("equity_history", [])
    realized_total = total_return(eh)
    realized_ret = equity_returns(eh)
    cost = realized_cost_drag(paper_state.get("trades", []))

    report: dict = {
        "realized_total_return": round(realized_total, 4),
        "cost_drag_by_region": cost,
        "n_equity_points": len(eh),
    }

    if predicted_equity is not None and len(predicted_equity) >= 2:
        pred_total = float(predicted_equity.iloc[-1] / predicted_equity.iloc[0] - 1.0)
        pred_ret = predicted_equity.pct_change().dropna()
        te = tracking_error(realized_ret, pred_ret)
        divergence = realized_total - pred_total
        report.update({
            "predicted_total_return": round(pred_total, 4),
            "divergence": round(divergence, 4),
            "tracking_error_bps": te["tracking_error_bps"],
            "tracking_obs": te["n_obs"],
        })
        # Cost bucket of the divergence: paper paying MORE than the modelled cost
        # drags realized below predicted (a negative contribution).
        if predicted_cost_fraction is not None:
            paid = _blended_cost_fraction(cost)
            report["cost_bucket"] = round(predicted_cost_fraction - paid, 4)
            report["residual"] = round(divergence - report["cost_bucket"], 4)
        te_bps = te["tracking_error_bps"]
        report["tracking_alert"] = bool(te_bps is not None and te_bps > TRACKING_ERROR_ALERT_BPS)

    return report


def _blended_cost_fraction(cost_by_region: dict) -> float:
    """Rough cross-region cost as a fraction of total traded notional (currency
    labels dropped — a coarse blend used only for the cost-bucket estimate)."""
    tot_cost = sum(v["cost"] for v in cost_by_region.values())
    tot_notional = sum(v["notional"] for v in cost_by_region.values())
    return (tot_cost / tot_notional) if tot_notional else 0.0


def _commission_bps(region_key: str | None) -> float | None:
    """The bps rate the BACKTEST charges for this region, or None if unknown."""
    try:
        return float(get_region(region_key).commission_bps)
    except Exception:
        return None


def _window_fills(paper_state: dict, start: pd.Timestamp,
                  end: pd.Timestamp) -> list[tuple[dict, float | None]]:
    """Each fill dated inside (start, end], with its base-per-local FX rate.

    Equity is stamped AFTER a run's trades, so a fill dated `start` is already
    inside the start equity (on a real book that day is the build-out) and is
    left out, while a fill dated `end` is inside the end equity and is counted.
    The rate is the book's CURRENT `fx_snapshot` (see `reconcile`), or None for
    a currency the snapshot has no rate for (`_base_per_local`).
    """
    return [(t, _base_per_local(paper_state, t.get("currency")))
            for t in paper_state.get("trades") or []
            if start < pd.Timestamp(t.get("date")) <= end and pnl.is_fill(t)
            and t.get("shares") and t.get("fill") is not None]


def _line_fill_convention(fills: list[tuple[dict, float]]) -> tuple[float, float]:
    """(shortfall BEYOND the modelled slippage, slippage paid), base currency.

    Both engines charge `region.slippage_bps`, so only the excess diverges.
    Floored at zero per fill, as `_line_commission_floor` is: a fill better than
    modelled is not credited and stays in the residual."""
    excess = paid_total = 0.0
    for t, rate in fills:
        bps = tca._modelled_bps(t.get("region"))
        if not t.get("decision") or bps is None:
            continue
        shares, decision = float(t["shares"]), float(t["decision"])
        paid = tca.implementation_shortfall(decision, t["fill"], shares,
                                            t.get("side", "BUY"))
        paid_total += paid * rate
        excess += max(0.0, paid - shares * decision * bps / 1e4) * rate
    return excess, paid_total


def _line_commission_floor(fills: list[tuple[dict, float]]) -> float:
    """Commission paid ABOVE the backtest's bps model, base currency."""
    excess = 0.0
    for t, rate in fills:
        bps = _commission_bps(t.get("region"))
        if bps is not None:
            modelled = float(t["shares"]) * float(t["fill"]) * bps / 1e4
            excess += max(0.0, float(t.get("commission") or 0.0) - modelled) * rate
    return excess


def _base_per_local(paper_state: dict, ccy: str | None) -> float | None:
    """Base units per 1 `ccy`, from the book's CURRENT `fx_snapshot`, or None.

    1.0 only for the book's base currency. `paper_trade.fx_snapshot` omits a
    currency that has no prior good rate, so a missing key means "no rate",
    never "parity": guessing 1.0 would convert at a made-up rate.
    """
    if ccy == (paper_state.get("base_currency") or cfg.BASE_CURRENCY):
        return 1.0
    try:
        rate = float((paper_state.get("fx_snapshot") or {}).get(ccy))
    except (TypeError, ValueError):
        return None
    return rate if rate > 0 else None              # NaN > 0 is False


def _sleeve_close(paper_state: dict, key: str,
                  day: pd.Timestamp) -> tuple[float, dict]:
    """(local cash, shares by ticker) for sleeve `key` at the close of `day`.

    Rebuilt BACKWARD from the stored sleeve, which is the book after every
    trade: cash moves only on fills (`rebalance_sleeve` debits
    `delta * fill + commission + stamp_duty`) and cash events (a dividend
    credits `pnl.cash_amount` and moves no shares), so undoing each dated
    after `day` recovers that close. A forward replay would need the inception FX
    rate, which the state does not keep.
    """
    sleeve = paper_state["sleeves"][key]
    cash = float(sleeve.get("cash") or 0.0)
    pos = {t: float(n) for t, n in (sleeve.get("positions") or {}).items()}
    for t in paper_state.get("trades") or []:
        if t.get("region") != key or pd.Timestamp(t["date"]) <= day:
            continue
        if pnl.is_cash_event(t):
            cash -= pnl.cash_amount(t)
            continue
        delta = float(t["shares"]) * (1.0 if t.get("side") == "BUY" else -1.0)
        cash += (delta * float(t["fill"]) + float(t.get("commission") or 0.0)
                 + float(t.get("stamp_duty") or 0.0))
        pos[t["ticker"]] = pos.get(t["ticker"], 0.0) - delta
    return cash, pos


def _sleeve_terms(paper_state: dict, bt: dict, key: str, values: dict,
                  start: pd.Timestamp, end: pd.Timestamp) -> dict:
    """One sleeve's backtest-minus-paper gap, and the parts the lines name.

    `values` maps each paper date in [start, end] to the sleeve's base value.
    Every term is a fraction of the sleeve's window-start value. Each backtest
    day's difference splits, Brinson-style, into

        (g_bt - g_p) * h   exposure: invested more or less, priced at h
        i                  the backtest's idle-cash credit (paper: none yet)
        the rest           what was held and when, plus every unnamed cause

    g is the gross invested fraction; h is what the backtest's holdings earned
    per unit invested, BEFORE its trading cost and cash credit (neither is a
    property of being invested). Days are linked GRAP-style, each scaled by the
    paper's growth before it and the backtest's after, so the parts sum to the
    sleeve's compounded gap: a sleeve whose ONLY difference is exposure puts
    all of its gap in exposure, where an unlinked daily sum leaves a
    compounding cross term behind.

    The paper's g is 1 - cash/value at its last close before the day, from
    `_sleeve_close`, and held there until its next run. Local cash goes into
    base along the backtest's own FX path, levelled to the sleeve's OWN rate on
    its latest all-cash close in the window (value / cash is exactly that
    day's rate); only a sleeve never flat in the window falls back to the
    book's snapshot, its one stored rate. A flat close is g = 0 outright.
    `exposure` is None, with `why`, when the cash path cannot give g: a short's
    proceeds sit in cash, so cash says only NET exposure; and with no rate the
    cash cannot be put in base. `bt_flat` counts days the backtest held
    nothing while the paper was invested: no backtest holdings price them, so
    they cannot be exposure and stay in the remainder.
    """
    rl, rb = bt["returns"], bt["base_returns"]
    costs = bt.get("costs")
    costs = costs if costs is not None else pd.Series(dtype=float)
    days = sorted(values)
    v0 = values[days[0]]
    idx = rl.index[(rl.index > start) & (rl.index <= end)]
    growth = (1 + rb.loc[idx]).cumprod()
    b_end = float(growth.iloc[-1]) if len(idx) else 1.0
    fx_step = (1 + rb) / (1 + rl)                  # the backtest's FX move per day
    gaps = rl.index.to_series().diff().dt.days.fillna(0)
    # backtest.py records weights[D] at D's CLOSE, after any fill on D, so day
    # t was earned by the book recorded at the backtest's previous bar.
    held_from = rl.index.to_series().shift(1)
    credit_on = bool(cfg.CREDIT_IDLE_CASH and cfg.CASH_RATE_ANNUAL)

    ccy = paper_state["sleeves"][key].get("currency")
    closes = {d: _sleeve_close(paper_state, key, d) for d in days[:-1]}
    flat = [d for d, (cash, pos) in closes.items()
            if cash > 0 and not any(pos.values())]
    if flat:                                       # all cash: value / cash IS the rate
        rate, at = values[flat[-1]] / closes[flat[-1]][0], flat[-1]
    else:
        rate, at = _base_per_local(paper_state, ccy), end
    why = None if rate is not None else f"no {ccy} rate in fx_snapshot for {key}'s cash"
    if any(n < 0 for _, pos in closes.values() for n in pos.values()):
        why = f"{key} held a short (cash gives only net exposure)"
    g_p: dict = {}
    if why is None:
        level = fx_step.cumprod()
        for d, (cash, pos) in closes.items():
            m = rate * float(level.asof(d)) / float(level.asof(at))
            g_p[d] = 1.0 - cash * m / values[d] if any(pos.values()) else 0.0

    exposure = interest = 0.0
    bt_flat = 0
    for t in idx:
        w = bt["weights"].get(held_from[t])
        w = w if w is not None else pd.Series(dtype=float)
        if why is None and bool((w < 0).any()):
            why = f"{key}'s backtest held a short (no paper gross to match)"
        g_bt = float(w.abs().sum())
        i = (fees.idle_cash_credit(float(w.sum()), float(gaps[t]),
                                   cfg.CASH_RATE_ANNUAL) if credit_on else 0.0)
        h = (float(rl[t]) + float(costs.get(t, 0.0)) - i) / g_bt if g_bt > 0 else 0.0
        d = days[bisect_left(days, t) - 1]         # the paper's last close before t
        link = float(fx_step[t]) * values[d] / v0 * b_end / float(growth[t])
        interest += i * link
        if why is None:
            exposure += (g_bt - g_p[d]) * h * link
            bt_flat += g_bt == 0 and g_p[d] > 1e-6
    return {"v0": v0, "gap": b_end - values[days[-1]] / v0, "interest": interest,
            "exposure": None if why else exposure, "why": why, "bt_flat": bt_flat}


def _holdings_terms(paper_state: dict, backtest: dict, start: pd.Timestamp,
                    end: pd.Timestamp) -> tuple[dict, str | None]:
    """Every sleeve's `_sleeve_terms`, or ({}, why) when one cannot be had —
    all or nothing, so no sleeve's gap is half-named while another's is not."""
    rows = [r for r in paper_state.get("sleeve_history") or []
            if start <= pd.Timestamp(r["date"]) <= end]
    bts = backtest.get("sleeves") or {}
    terms: dict = {}
    for key in paper_state.get("sleeves") or {}:
        values = {pd.Timestamp(r["date"]): float(r[key]) for r in rows
                  if r.get(key) is not None}
        if key not in bts:
            return {}, f"the backtest carries no {key} sleeve"
        if start not in values or end not in values or values[start] <= 0:
            return {}, f"sleeve_history has no {key} value on {start.date()} and {end.date()}"
        terms[key] = _sleeve_terms(paper_state, bts[key], key, values, start, end)
    if not terms:
        return {}, "the book records no sleeves"
    return terms, None


def _bps_by_sleeve(terms: dict, equity: float, part: str) -> str:
    return ", ".join(f"{k} {t['v0'] / equity * t[part] * 1e4:+.1f}bp"
                     for k, t in terms.items())


def _line_cash_interest(terms: dict, equity: float) -> float:
    """The backtest's idle-cash credit, less the paper's (none yet)."""
    return sum(t["v0"] / equity * t["interest"] for t in terms.values())


def _line_exposure_gap(terms: dict, equity: float) -> float:
    """Return missed (+) or gained (-) by holding a different invested
    fraction than the backtest, priced at the backtest's own holdings."""
    return sum(t["v0"] / equity * t["exposure"] for t in terms.values())


def _line_rebalance_timing(terms: dict, equity: float, lines: dict) -> float:
    """The rest of every sleeve's gap once each other line is taken out.

    Every other line is a sleeve-level cause, inside the sleeves' returns, so
    taking them out here leaves the residual with the portfolio layer only."""
    gap = sum(t["v0"] / equity * t["gap"] for t in terms.values())
    return gap - sum(v for n, v in lines.items() if n != "rebalance_timing")


def reconcile(paper_state: dict, predicted_equity: pd.Series, *,
              backtest: dict | None = None) -> dict:
    """Walk from the backtest's total return to the paper book's, line by line.

    Every line is a SIGNED return fraction of the book's equity at the window
    start, positive when the cause put the BACKTEST ahead:

        backtest_return - sum(lines) - paper_return = residual

    A line is non-zero only where the two engines treat that cause DIFFERENTLY.
    A cost both charge identically (stamp duty; the slippage inside
    `fees.round_trip_cost_rate`) nets to zero and its measured paper-side amount
    goes in `notes`, so the reader sees the money without the arithmetic
    double-counting it — which is why `fill_convention` carries only the
    shortfall beyond the modelled slippage. Anything not yet named lands in
    `residual` rather than being absorbed into a line — spec §5 sets NO target
    for the residual; the honest sequence is fix, measure, then judge.

    `backtest` is the `run_portfolio_backtest` result `predicted_equity` came
    from — the SAME regions, allocations, params and window as the book. With
    it, the holdings lines are measured from its per-sleeve weights and
    returns (see `_sleeve_terms`): `cash_interest`, `exposure_gap`, and
    `rebalance_timing`. The last is each sleeve's gap less every other line,
    so it is the one place an unnamed SLEEVE-level cause can hide, and its
    note lists what it carries; the residual is then the portfolio layer
    alone (the backtest trues sleeves to target monthly while the paper funds
    once and drifts). Without `backtest`, or when a sleeve's holdings cannot
    be had, those lines stay 0.0, say why, and leave the gap in residual.

    Local-currency fill amounts are converted with the book's CURRENT
    `fx_snapshot`, not the rate on the trade date; the paper keeps no FX history
    (see the `fx_translation` note).
    """
    eh = paper_state.get("equity_history") or []
    paper = pd.Series({pd.Timestamp(d): float(v) for d, v in eh}).sort_index()
    pred = predicted_equity.dropna().sort_index()
    common = paper.index.intersection(pred.index)
    if len(common) < 2:
        raise ValueError("bridge needs >= 2 dates common to the book and the backtest")

    start, end = common[0], common[-1]
    paper_return = float(paper.loc[end] / paper.loc[start] - 1.0)
    backtest_return = float(pred.loc[end] / pred.loc[start] - 1.0)
    start_equity = float(paper.loc[start])

    fills = _window_fills(paper_state, start, end)
    unrated = sorted({str(t.get("currency")) for t, rate in fills if rate is None})
    left_out = len(fills)
    fills = [(t, rate) for t, rate in fills if rate is not None]
    left_out -= len(fills)
    slip_excess, slip_paid = _line_fill_convention(fills)
    duty = sum(float(t.get("stamp_duty") or 0.0) * rate for t, rate in fills)
    divs = [(t, _base_per_local(paper_state, t.get("currency")))
            for t in paper_state.get("trades") or []
            if t.get("side") == pnl.DIVIDEND and start < pd.Timestamp(t["date"]) <= end]
    credited = sum(pnl.cash_amount(t) * r for t, r in divs if r)
    div_unrated = sorted({str(t.get("currency")) for t, r in divs if r is None})
    lines = {name: 0.0 for name in BRIDGE_LINES}
    lines["fill_convention"] = slip_excess / start_equity
    lines["commission_floor"] = _line_commission_floor(fills) / start_equity

    ccy = paper_state.get("base_currency") or "base"
    notes = {
        "dividends": (f"0.0 — both engines include them: the backtest's prices "
                      f"are total-return, and the paper book's DIV ledger rows "
                      f"credited a net {credited:,.2f} {ccy} over this window "
                      f"(a short pays its dividend)"
                      + (f" ({', '.join(div_unrated)} rows left out — no rate in "
                         "fx_snapshot)" if div_unrated else "")
                      + ". A dividend the ledger does not hold (one that went ex "
                        "before the book credited them) is not in that figure and "
                        "sits in rebalance_timing, or in residual without it"),
        "cash_interest": "0.0 — the paper book accrues no interest on idle cash "
                         "yet; the backtest applies fees.idle_cash_credit, which "
                         "this bridge measures only from the backtest's holdings "
                         "(backtest=)",
        "fill_convention": f"only the shortfall beyond the modelled slippage, "
                           f"which both engines charge; the paper book paid "
                           f"{slip_paid:,.2f} {ccy} of slippage over this window "
                           f"(fills that record a decision price)",
        "stamp_duty": f"0.0 — both engines charge it; the paper book paid "
                      f"{duty:,.2f} {ccy} over this window",
        "exposure_gap": "0.0 — needs the backtest's per-sleeve holdings "
                        "(backtest=)",
        "rebalance_timing": "0.0 — measured with exposure_gap, from the "
                            "backtest's per-sleeve holdings (backtest=)",
        "fx_translation": "0.0 — not derivable: the paper book keeps only its "
                          "latest fx_snapshot, not the rate each run translated "
                          "at. Recording that rate per run would make this line "
                          "computable; until then the difference (and this "
                          "bridge's current-rate approximation for fills) sits "
                          "in rebalance_timing, or in residual without it",
    }

    timed = one_sleeve = False          # rebalance_timing measured; one sleeve
    if backtest is not None:
        terms, why = _holdings_terms(paper_state, backtest, start, end)
        if why:
            for name in ("cash_interest", "exposure_gap", "rebalance_timing"):
                notes[name] = f"0.0 — not measured: {why}; the gap stays in residual"
        else:
            lines["cash_interest"] = _line_cash_interest(terms, start_equity)
            notes["cash_interest"] = (
                f"the backtest's fees.idle_cash_credit on its idle fraction, "
                f"{lines['cash_interest'] * start_equity:,.2f} {ccy} over this "
                f"window; the paper book accrues none yet ("
                f"{_bps_by_sleeve(terms, start_equity, 'interest')})")
            missing = [t["why"] for t in terms.values() if t["why"]]
            if missing:
                for name in ("exposure_gap", "rebalance_timing"):
                    notes[name] = (f"0.0 — not measured: {'; '.join(missing)}; "
                                   "the gap stays in residual")
            else:
                lines["exposure_gap"] = _line_exposure_gap(terms, start_equity)
                lines["rebalance_timing"] = _line_rebalance_timing(
                    terms, start_equity, lines)
                idle = sum(t["bt_flat"] for t in terms.values())
                notes["exposure_gap"] = (
                    "a different invested fraction from the backtest's, priced "
                    "at what its holdings earned per unit invested; the "
                    "paper's fraction is rebuilt from its cash path ("
                    f"{_bps_by_sleeve(terms, start_equity, 'exposure')})"
                    + (f"; on {idle} sleeve-day(s) the backtest held nothing "
                       "while the paper was invested, so there is no backtest "
                       "price for that exposure and it stays in "
                       "rebalance_timing" if idle else ""))
                notes["rebalance_timing"] = (
                    "the rest of each sleeve's gap ("
                    f"{_bps_by_sleeve(terms, start_equity, 'gap')}) after every "
                    "other line: rebalance-date mismatch AND name selection "
                    "(data-quality exclusions, whole-share rounding of the name "
                    "mix), which cannot be separated without per-name "
                    "counterfactual books — plus every sleeve-level cause no "
                    "line names yet: turnover-cost differences, dividends the "
                    "ledger does not hold, FX translation timing, bars stamped "
                    "a day apart")
                notes["residual"] = (
                    "the portfolio layer: the backtest trues its sleeves back to "
                    "target each month and pays the FX spread; the paper book "
                    "funds once and drifts")
                timed, one_sleeve = True, len(terms) == 1
                if one_sleeve:
                    notes["residual"] = (
                        "zero by construction — one sleeve, so there is no "
                        "portfolio layer and rebalance_timing is the sleeve's "
                        "whole remainder; it tests nothing")

    if unrated:
        # Inside its sleeve's return but in no line: the remainder holds it.
        where = "rebalance_timing" if timed else "residual"
        why = (f"{left_out} fill(s) in {', '.join(unrated)} left out — no rate "
               f"in fx_snapshot, and parity would be a guess; their cost "
               f"stays in {where}")
        for name in ("fill_convention", "commission_floor", "stamp_duty"):
            notes[name] = "; ".join(filter(None, (notes.get(name), why)))

    residual = backtest_return - sum(lines.values()) - paper_return
    # A one-sleeve book with measured holdings cannot fail the identity, so
    # None — "not tested" — rather than a pass that certifies nothing, or a
    # failure that did not happen. None is falsy: a gate reads it as not ok.
    return {"start": str(start.date()), "end": str(end.date()),
            "start_equity": start_equity,
            "backtest_return": backtest_return, "paper_return": paper_return,
            "lines": lines, "notes": notes, "residual": residual,
            "identity_ok": (None if one_sleeve
                            else abs(residual) <= BRIDGE_TOLERANCE)}


def format_bridge(report: dict, currency: str = "AUD", initial: float = 0.0) -> str:
    """Fixed-width text table: line name, return fraction, bps/yr, base amount.

    `initial` is the book's equity at the WINDOW start in `currency` — the
    denominator every line is a fraction of (`report["start_equity"]`), so the
    money column prints the book's actual amounts; pass 0.0 to print the return
    and bps columns only. bps/yr divides by `metrics.years_between` (calendar
    time) and does not compound, so it sums down the table like the return
    column. The verdict line states whether the named lines close the identity
    to BRIDGE_TOLERANCE — or, for a one-sleeve book whose residual is zero by
    construction (`identity_ok` None), that it was not tested — and every
    line's note follows it, so a reader can see WHY a line is zero rather than
    assuming it is nothing.
    """
    years = max(metrics.years_between(report["start"], report["end"]), 1e-9)
    rows = [("backtest return", report["backtest_return"])]
    rows += [(n.replace("_", " "), report["lines"][n]) for n in BRIDGE_LINES]
    rows += [("residual", report["residual"]), ("paper return", report["paper_return"])]

    width = 22 + 11 + 10 + (14 if initial else 0)
    out = [f"  window {report['start']} -> {report['end']} ({years:.2f} yr)",
           "  backtest return - lines - residual = paper return "
           "(a + line put the backtest ahead)",
           f"{'line':<22}{'return':>11}{'bps/yr':>10}"
           + (f"{currency:>14}" if initial else ""),
           "-" * width]
    for name, val in rows:
        out.append(f"{name:<22}{val:>11.4%}{val / years * 1e4:>10.0f}"
                   + (f"{val * initial:>14,.0f}" if initial else ""))
    out.append("-" * width)
    ok = report["identity_ok"]
    verdict = "NOT TESTED" if ok is None else "HOLDS" if ok else "DOES NOT HOLD"
    out.append(f"identity {verdict} "
               f"— residual {report['residual'] * 1e4:+.2f}bps "
               f"(tolerance {BRIDGE_TOLERANCE * 1e4:.0f}bp)")
    if ok is None:
        out.append("  the residual is zero by construction for a one-sleeve book "
                   "and is not evidence the comparison is like for like")
    for name, note in (report.get("notes") or {}).items():
        out.append(f"  note [{name}] {note}")
    return "\n".join(out)
