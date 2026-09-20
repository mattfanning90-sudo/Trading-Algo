---
title: Drawdown Breaker
type: concept
tags: [trading-algo, concept, risk]
created: 2026-09-20
up: ["[[How It Works]]"]
---

# 🛑 Drawdown Breaker

A **drawdown** is a fall from the account's best-ever value (its **high-water mark**). A **drawdown breaker** is a rule that sells everything when the fall exceeds a limit (25% for the equity books, 20% for the FX books) and waits a **cooldown** before trading again (21 market days, or 10 bars). It is a catastrophe backstop on top of the regime filter.

## The latch

In every simulator in the repo the high-water mark is only ever raised, never lowered. After the cooldown the breaker checks again against the same old peak. If the account is still 20% below it, which it usually is right after a 20% loss, the breaker fires again on the first bar. Ten bars later, again. It becomes a permanent off switch disguised as a pause.

On the `matt` FX backtest that is 381 halts and 3,804 flat bars. It has not fired on a live book yet because none has fallen 20%. The test only checks that the breaker fires at least once, not that it ever lets go.

## The phantom trip

On 27 August 2026 the main paper book's FTSE sleeve had no prices for a day. The book valued those holdings at zero, read a 26% loss that had not happened, tripped the breaker, sold sixteen names, and sat in cash for three weeks. It cost a lost rebalance and £46 of stamp duty to get back in. The cause is fixed (the book now refuses to value itself when held names have no price) and the state carries a correction record, but no audit document mentions it.

> [!tip] What a breaker should do
> Reset the high-water mark to the post-halt equity, or measure the drawdown from the point of re-entry. Otherwise the cooldown means nothing.

## Related
- [[Full System Review 2026-09]] · [[Regime & Trend Filters]] · [[Cash Drag and Exposure]]
