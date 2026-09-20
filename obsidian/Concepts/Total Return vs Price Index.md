---
title: Total Return vs Price Index
type: concept
tags: [trading-algo, concept, benchmarking]
created: 2026-09-20
up: ["[[How It Works]]"]
---

# 💰 Total Return vs Price Index

**Two ways to measure the same investment, and they differ by the dividends.**

A **price index** tracks share prices only. The S&P 500 you see quoted on the news (`^GSPC`) is a price index: when a company pays a dividend its share price drops by that amount on the ex-dividend date, and the index drops with it. The cash you would have received does not appear anywhere.

A **total-return series** counts the dividends too, as if you reinvested every one. Over long periods the difference is large: the S&P 500 price index grew about 12% a year from 2012 to 2026; with dividends reinvested it was about 14%. On the ASX, where dividends are higher, the gap is 3 to 4 points a year.

## Why it matters here

The strategy's own prices come from Yahoo with `auto_adjust=True`, which folds dividends into the price series. So the strategy is credited with every dividend. The benchmark it is compared against is built from the four regional price indices, which are not. The comparison is unfair in the strategy's favour by roughly the dividend yield, 2 to 4 points a year.

| 2012 to 2026, in AUD | CAGR | Sharpe |
|---|---|---|
| Strategy (dividends counted) | 8.6% | 0.62 |
| Benchmark as built (price indices) | 9.6% | 0.50 |
| Fair benchmark (index funds, dividends reinvested) | 11.3% | 0.65 |

Against the fair yardstick the strategy trails by 2.7 points a year and does not win on Sharpe either.

## The paper-trading twist

There is a second, opposite mismatch. The paper books hold share counts and mark them at the latest real price. When a stock goes ex-dividend the price drops, the book records that as a loss, and no cash ever arrives, because nothing in the paper engine pays dividends. So the **backtest** counts dividends and the **paper books** lose them. The two will diverge by the dividend yield for reasons that have nothing to do with the strategy.

> [!tip] The rule
> Compare like with like. Either both sides count dividends or neither does, and say which on every chart.

## Related
- [[Full System Review 2026-09]] · [[Sharpe Ratio]] · [[How It Works]]
