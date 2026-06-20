# What real high-frequency trading actually requires (an honest roadmap)

You asked about adding high-frequency trading (HFT). This document is the
straight answer: **HFT is not a feature you bolt onto this system — it's a
different sport, played on different equipment, against opponents who spend
hundreds of millions to be a microsecond faster.** Below is what it really takes,
why this stack can't do it, and the achievable "faster" paths that *are* real.

## What HFT is

HFT profits come almost entirely from **speed and market structure**, not from
clever forecasts:
- **Market making** — quoting both sides and earning the spread, thousands of
  times a second, managing inventory in microseconds.
- **Latency arbitrage** — seeing a price move on one venue and trading a related
  instrument on another before everyone else.
- **Queue position / rebates** — being early in the order book.

The edge is *being faster than the next participant*. If you're not the fastest,
you are the one being picked off.

## What it requires (the bar)

| Need | Reality |
|------|---------|
| **Latency** | tick-to-trade in **nanoseconds to low microseconds** — FPGAs or hand-tuned C++, kernel-bypass NICs (Solarflare), busy-polling, no garbage collection |
| **Location** | **Colocation**: your server racked in the exchange's data centre; some firms pay for equal-length cables to the matching engine |
| **Data** | Direct exchange feeds / full order book (ITCH/OUCH, etc.), microsecond timestamps — not vendor bars |
| **Connectivity** | Microwave / millimetre-wave / hollow-core fibre between venues (e.g. Chicago–NJ) |
| **Access** | Exchange membership or sponsored direct market access; for FX, prime-broker credit + an ECN (EBS, etc.) |
| **Capital & team** | Typically **$100k–millions/year** in infrastructure alone, plus quant/dev/network engineers |
| **Competition** | Citadel Securities, Virtu, Jump, XTX, Tower, HRT — they capture the vast majority of HFT profit |

## Why *this* system cannot do HFT

| | Real HFT | This system |
|---|---|---|
| Decision latency | ~10⁻⁶ s | ~0.15 s (Python/pandas) |
| Cadence | continuous, sub-millisecond | once per day (or per bar) |
| Data | direct tick / order book | Yahoo, **delayed ~15 min**, 1-minute bars at best, days of history |
| Where it runs | colocated bare metal | GitHub Actions, shared cloud |
| Language | C++/FPGA | Python |

That's roughly **a million times** too slow, on delayed data, in a high-level
language, in the wrong place. Critically, **you cannot even paper-trade HFT
honestly** here: the edge is winning a speed race, and a daily-bar backtest with
delayed vendor data has no way to simulate the queue, the latency, or the
competition. Any "HFT mode" in this repo would be theatre.

**"Retail HFT" is, bluntly, a marketing trap** — without colocation and direct
feeds you are structurally on the losing side of every fast trade.

## What *is* achievable here (and worth doing)

There's a wide, legitimate band between "once a day" and "HFT":

1. **Medium-frequency / intraday** (minutes to hours). The architecture is
   bar-agnostic, so it already supports 15-minute or 60-minute bars via
   `engine --interval` and an `intraday` risk profile with shorter windows.
   - *The real prerequisite is data, not speed code*: Yahoo intraday is delayed
     and history-limited, so **live** intraday needs a real-time broker feed
     (OANDA or IBKR). The plumbing is here; the feed is the gate.
   - Caveat: costs bite far harder intraday (you cross the spread much more
     often), and the current performance metrics are calibrated for daily bars.
2. **Quant-research agent** (`forex/research.py`) — systematically search for
   edges and judge them with Deflated Sharpe + PBO. Honest, and it mostly
   *disproves* edges, which is the point.
3. **Execution quality** — if you ever go live via a broker, the achievable
   "speed" win is smarter *execution* (limit orders, slicing) to reduce cost —
   not competing on raw latency.

## Bottom line

Adding genuine HFT is out of reach (and would be dishonest to fake). The
realistic upgrades are **medium-frequency intraday** (gated by a real-time data
feed) and **rigorous research** — both of which are built and documented here.
