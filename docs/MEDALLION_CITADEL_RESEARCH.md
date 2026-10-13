# Medallion and Citadel expose our breadth problem

*Research report, 13 October 2026, for Matt Fanning. Built from six research notes in [`docs/research/medallion-citadel/`](research/medallion-citadel/), the repo's own docs, and measurements taken on the system's real 2012–2026 price panel.*

*Plain-English companion: [`obsidian/Concepts/Breadth.md`](../obsidian/Concepts/Breadth.md). Full research notes behind every external claim: `docs/research/medallion-citadel/`.*

**The short answer.** Renaissance Technologies' Medallion fund and Citadel make money in ways a small monthly momentum book mostly cannot copy. On the public record Medallion works like a casino. It makes tens of millions of trades a year **[primary]**, and each is right only slightly more than half the time: Robert Mercer's "50.75%" **[second-hand]**. Those trades are spread across thousands of instruments, mostly held for days, hedged long against short, and multiplied by borrowed money of up to 20 dollars of positions per dollar of capital **[primary]**. Citadel's hedge fund gives each portfolio manager a yearly budget of allowed volatility, requires its stock books to cancel out the market's ups and downs, and runs a 50-plus-person risk group that hunts for hidden shared bets **[primary]**. Citadel Securities is a different business altogether. It is a market maker that earns fractions of a cent a share on a huge stream of orders from people who mostly know no more than it does **[primary regulator data; revenue second-hand]**.

Measured the way they would measure it, our system's skill per bet is not the problem. Our top-10 momentum picks beat the typical stock 50.9–52.3% of the time, the same territory as Medallion's 50.75%, although the two figures are not defined the same way. What we lack is *independent bets*. Each region behaves like only 14–20 independent bets a month, whether it ranks 52 stocks or 124. The four regions move together enough to count as fewer than two. The whole equity book makes roughly 350–400 independent bets a year, while at Medallion's hit rate a Sharpe ratio of 2 needs about 17,800.

The same measurements gave three uncomfortable new results:

- **US single stocks:** the 12-1 ranking shows no measurable predictive power (IC 0.006, t 0.35). Most of the US sleeve's edge comes from ranking ETFs across asset classes and from a stock list chosen with hindsight.
- **Hedged the Citadel way:** each sleeve hedged against its own stock list keeps −0.6% to +3.1% a year, and none of it is statistically distinguishable from zero.
- **Diversification:** the 0.06 ASX–US correlation the repo credits for its diversification is a time-zone illusion. Corrected, it is about 0.42.

What we can borrow is their discipline, not their machinery:

- Fix two wrong correlation numbers.
- Measure breadth and run the stricter significance test as a matter of routine.
- Trade less often (already recommended in the repo).
- Place live orders in the closing auction (new).
- Give each sleeve its own drawdown breaker (already recommended).
- Finish point-in-time index membership (already locked) before trusting any number.

What we cannot use is their leverage, their tax structures, high-frequency trading, market making, short-horizon statistical arbitrage, or beta-neutral long/short books. **Every signal number below is survivorship-biased and, unless marked otherwise, gross of costs.** It is a diagnostic, not a measure of how the strategy performs.

## How to read this report

Every claim about Medallion or Citadel carries one of three labels:

- **[primary]**: a documented primary source, such as sworn Senate testimony, an SEC release, a rating-agency report, a signed academic paper, or an on-record interview with a named executive.
- **[second-hand]**: credible but indirect, such as Zuckerman's 2019 book quoting insiders, Bloomberg reporting "people familiar", or a review that quotes the book.
- **[speculation]**: an inference, our own arithmetic, or circulating folklore. Folklore is named as such.

Nothing about Medallion's internals is public, so the report says "consistent with" rather than "Medallion does X" unless a primary source says so.

Academic papers are cited without a label. Their own caveats travel with their numbers: most published momentum results are long/short, value-weighted, built from hundreds or thousands of stocks, and gross of costs.

Numbers measured on our own system are marked **[measured]**. They were taken on 13 October 2026 on Yahoo total-return prices from 2012-01-03 to 2026-10-12, using the repo's own loader and signal (`our_system_measurements.md`). Money is always labelled: US$, A$, £ or C$. Code is cited as `file:line`, meaning a line number inside a file under `trading_algo/`.

## Glossary

| Term | Plain meaning | Everyday analogy |
|---|---|---|
| Hedge fund | A private investment pool that can bet on prices falling as well as rising, and can borrow | — |
| Multi-strategy fund, "pod shop" | A hedge fund that splits capital among many small independent teams ("pods"), each with its own risk limit | A franchise owner who closes any branch that loses money too fast |
| Market maker, wholesaler | A firm that always stands ready to buy from sellers and sell to buyers, earning the small gap between its two prices | An airport currency booth |
| Bid-ask spread | The gap between the best price to sell (bid) and the best price to buy (ask) | The booth buys at 0.64 and sells at 0.66 |
| Basis point (bp) | One hundredth of one percent (0.01%) | A$1 on A$10,000 |
| Adverse selection | Losing to the customer who knows more than you do | Selling a used car to a mechanic |
| Payment for order flow (PFOF) | A market maker paying a retail broker for the right to fill its customers' orders | A restaurant paying a hotel concierge to send guests |
| Market order / limit order | "Buy now at any price" versus "buy only at this price or better" | — |
| Closing auction; MOC / LOC | A short end-of-day session that matches all orders at one price, which becomes the official close. Market-on-close (MOC) means "fill me at that price"; limit-on-close (LOC) adds a price limit | The final call at a fish market |
| Leverage | Holding more assets than your own capital by borrowing. At 20:1, a 5% fall wipes out the capital | A mortgage |
| Long, short, long-only, long/short | Long: own it and profit if it rises. Short: borrow it, sell it, and profit if it falls | — |
| Beta | How much a portfolio moves when the market moves. A beta of 0.3 moves 30% as much | The tide lifting every boat |
| Alpha | Return left after taking beta out: the part you can credit to skill | How far the boat went beyond the tide |
| Beta-neutral, market-neutral | Longs and shorts balanced so the market's ups and downs cancel out | — |
| Factor | A driver many stocks share: the market, company size, cheapness (value), momentum, sector | — |
| Idiosyncratic or residual return | The part of a stock's move that the shared factors do not explain | — |
| 12-1 momentum | Rank stocks by their return over the past 12 months, skipping the latest month, and buy the leaders | Backing the in-form team |
| Cross-sectional | Comparing stocks against each other at the same moment | — |
| Sleeve | One regional sub-portfolio: US, ASX, FTSE or TSX | — |
| Rebalance, turnover | Re-picking and trading the portfolio; turnover is the share of it replaced each time | — |
| Gross / net; "costs on" | Before / after trading costs and fees | — |
| Sharpe ratio | Average return above cash divided by its volatility, i.e. reward per unit of bumpiness. Around 0.5 is decent for one strategy, 1 is very good, 2 is exceptional | — |
| Information ratio (IR) | The Sharpe ratio measured against a benchmark instead of cash | — |
| Information coefficient (IC) | The correlation between a signal's ranking and what actually happened next. 0 is useless and 1 is perfect; monthly ICs of 0.02–0.05 are normal for stock signals | — |
| Hit rate | The share of bets that won | — |
| Breadth; fundamental law | Breadth is the number of genuinely independent bets a year. Grinold's fundamental law of active management says IR ≈ IC × √breadth | A casino wins on volume, not on any one spin |
| Transfer coefficient (TC) | How much of the signal's view the portfolio can actually express. A long-only book cannot act on "this will fall" | — |
| Null test, null hypothesis | Build fake versions of the experiment with the real effect removed, and see how often luck alone matches the real result | Testing whether a "lucky coin" beats a fair one |
| p-value | The share of null (fake) results at least as good as the real one. 0.05 means 1 in 20 | — |
| t-statistic | How many standard errors a result sits from zero. Above about 2 is the usual bar, above 3 the stricter one | — |
| Newey-West | A correction to t-statistics when consecutive months are related | — |
| Survivorship bias | Studying only the stocks still listed today, which quietly drops the failures | Judging a school only by its graduates |
| Point-in-time membership | The list of index members as it actually stood on each past date | — |
| Signal decay | How fast a forecast stops being useful | A weather forecast for next week versus next month |
| Statistical arbitrage (stat-arb) | Trading many small, short-lived price relationships, usually long/short and hedged | — |
| Short-term reversal | The tendency of a sharp price move to partly undo itself | — |
| Kelly criterion | The bet size that maximises long-run growth. Betting more than Kelly lowers growth and risks ruin | — |
| Drawdown; breaker | A drawdown is the fall from a previous peak; a breaker is a rule that cuts risk after a set fall | — |
| Volatility targeting | Scaling positions so expected bumpiness stays near a target | — |
| Risk budget, volatility allocation | Giving each manager a fixed amount of allowed volatility rather than a fixed amount of money | — |
| Dimson correction | Measuring correlation with the previous and next day included, to catch markets that close at different hours | — |
| Block bootstrap, confidence interval | Re-sampling chunks of history to see how much a result could vary. The interval is the plausible range | — |
| Market impact, capacity | How far your own trading moves the price against you, and so how much money a strategy can run | — |
| Hidden Markov model (HMM) | A statistical model that guesses which unseen "state" (calm or stressed) the market is in | — |
| CGT discount; stamp duty | Australian capital-gains tax is halved on assets held more than 12 months. UK stamp duty is a 0.5% tax on share purchases | — |
| Backtest; paper book | A backtest replays the strategy on history; a paper book is a simulated live account with no real money | — |

## Medallion's public record shows a casino, not a crystal ball

**Medallion averaged 66.07% a year before fees and 39.20% after, from 1988 to 2018, and its gross return was never negative in a calendar year [second-hand]** ([Cornell 2019](https://community.portfolio123.com/uploads/short-url/wZJco8G9Xd3diZqEN2YNc3RdaH.pdf)). Every one of those figures comes from the appendix of Gregory Zuckerman's 2019 book, which Bradford Cornell reproduced. No audited series exists.

The "net" column carries a second caveat. It equals (gross return − 5%) × (1 − performance fee) in all 31 years, to within 0.05 percentage points. So it is a formula, not what investors actually received: it even credits investors with a negative performance fee in 1989 **[speculation: our arithmetic on Cornell's table]**. Where actual after-fee figures were reported, they differ from the formula:

- 1990: 55.9% reported, against the formula's 58.24%.
- 2008: 98.2% reported, against 82.38%.

Sources: [Wikipedia citing Bloomberg 2015](https://en.wikipedia.org/wiki/Renaissance_Technologies); [breakingthemarket quoting the book](https://breakingthemarket.com/the-greatest-geometric-balancers-renaissance-technologies-part-i/) **[second-hand]**.

Cornell's "Sharpe ratio exceeding 2.0" rests on just 31 annual numbers. It holds only if the risk-free rate is at or below about 2.5%: it is 2.09 at 0% and 1.99 at 3% **[speculation: arithmetic]**. His estimate of a beta near −1.0 to the US market is correct arithmetic on fragile data. The fund's best years were the crisis years 2000, 2007 and 2008, at 128.1%, 136.1% and 152.1% gross **[second-hand data]**.

Two more facts frame the record:

- **Fees.** Fees were 5% of assets plus 44% of profits from 2002, per Zuckerman and Cornell. Bloomberg reported "36 percent" in 2007, so the sources conflict.
- **Size.** The fund has been held near **US$10 billion since 2010**. It stopped taking outside money in 1993 and had returned almost all of it about twelve years later **[primary]** ([Bloomberg 2007](https://web.stanford.edu/class/archive/cs/cs224n/cs224n.1114/renaissance.html)).

### Many tiny edges plus leverage, not one big idea

The mechanism the public record supports is **breadth plus leverage**. The 2014 US Senate investigation found that Renaissance ran **26 to 39 million trades a year** through two banks' accounts. It held positions for **less than three months 87% of the time, and for more than six months less than 1% of the time**. A 2010 SEC examination counted about **129 million trade orders in nearly 10,000 instruments across 18 countries** in the year to March 2009 **[primary]** ([Senate PSI report, via mirror](http://math.nyu.edu/faculty/avellane/basket.pdf); [hearing record](https://www.govinfo.gov/content/pkg/CHRG-113shrg89882/html/CHRG-113shrg89882.htm)). Zuckerman reports equity holds of "two days or so", and futures holds cut from a week and a half to a day and a half **[second-hand]** ([twoquants](https://twoquants.com/the-man-who-solved-the-market-part-2/)).

Mercer's line belongs in that context: "We're right 50.75 percent of the time... but we're 100 percent right 50.75 percent of the time. You can make billions that way." It is **Robert Mercer quoted by Zuckerman [second-hand]**, and is often misattributed online to "John Mercer" or to Peter Brown ([Novel Investor notes](https://novelinvestor.com/notes/the-man-who-solved-the-market-by-gregory-zuckerman/); [Cornell](https://community.portfolio123.com/uploads/short-url/wZJco8G9Xd3diZqEN2YNc3RdaH.pdf)). Nobody has said what counts as one "time": a fill, a position or a day.

The fundamental law turns that hit rate into arithmetic. A 50.75% hit rate on equal, independent bets is an IC of 0.015, and the annual Sharpe is roughly 0.015 × √(independent bets a year). So **a Sharpe of 1 needs about 4,400 independent bets a year, and a Sharpe of 2 about 17,800 [speculation: arithmetic on a second-hand figure]** ([Graham Capital](https://www.grahamcapital.com/quant-post/how-good-is-good-enough-some-math-behind-sharpe-information-and-hit-ratios/); [Ding 2010](https://math.nyu.edu/inmemoriam/avellaneda/FundamentalLawFT.pdf)).

Treating all 26–39 million trades as independent would imply a Sharpe of 76–94, which is absurd. Most "trades" must be slices of larger orders, hedges and correlated positions. Medallion's record is therefore consistent with an effective breadth of roughly 1/1,500 to 1/2,200 of its trade count. That is still tens of thousands of independent bets a year **[speculation]**.

### Mundane signals combined in one model

**No exotic signal is documented.** The on-record pieces are ordinary ones:

- Simons said early commodity and currency markets "had a tendency to trend", and that "in a certain sense, what we did was machine learning" **[primary]** ([TED 2015](https://singjupost.com/a-rare-interview-with-the-mathematician-who-cracked-wall-street-jim-simons-at-ted-full-transcript/)).
- A court decision revealed an opening-gap reversion rule in futures: short a contract that opens sharply higher than the previous close, and buy one that opens sharply lower **[primary]** ([Bloomberg 2007](https://web.stanford.edu/class/archive/cs/cs224n/cs224n.1114/renaissance.html)).
- Zuckerman adds day-of-week effects, overnight-versus-day sessions, five-minute bars, pairs such as gold and silver, and volatility effects. He also describes one model shared across all assets that judges every new signal against its trading cost **[second-hand]** ([breakingthemarket II](https://breakingthemarket.com/the-greatest-geometric-balancers-renaissance-technologies-part-ii/); [Shortform](https://www.shortform.com/blog/the-man-who-solved-the-market-book/)).

Nick Patterson said on record that "our most important statistical tool was simple regression with one target and one independent variable" **[primary]** ([QuantSeeker transcription](https://x.com/quantseeker/status/1879118660108693792)). Peter Brown's account of why the signals look strange is **[second-hand]**: "If there were signals that made a lot of sense that were very strong, they would have long-ago been traded out" ([bagerbach notes](https://bagerbach.com/books/the-man-who-solved-the-market/)).

The closest published glimpse of this kind of business is WorldQuant's "101 Formulaic Alphas". They are real production signals with:

- a median holding period of 2.1 days;
- a median gross Sharpe of 2.2;
- a median edge of about 0.40¢ a share;
- an average pairwise correlation of 15.9%.

That correlation caps what combining them can achieve at about 2.5× a single signal ([Kakushadze 2016](https://arxiv.org/pdf/1601.00991)). Hidden Markov models are often called Medallion's secret. Leonard Baum did work for Simons on currencies before Medallion existed, but **nothing public ties HMMs to Medallion's production trading [speculation]**.

The lesson for us cuts against us. On Brown's own logic, 12-1 momentum is the textbook "sensible" signal, the least likely place for a hidden edge. We are harvesting a published premium, and across 97 published predictors returns fell 58% after publication on average ([McLean & Pontiff 2016](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12365)). Momentum has held up better than that average: it persisted in every 20-year US sub-period, including the decades after it was published ([Israel & Moskowitz 2013](https://www.sciencedirect.com/science/article/pii/S0304405X12002401)).

### Leverage and a tax wrapper made tiny edges large and fragile

Senate testimony documents leverage of **up to 20:1 through bank "basket options"**:

- Renaissance put up 10% and the bank lent 90%.
- The account was liquidated automatically if it lost 10%.
- Deutsche Bank could seize control intraday if longs underperformed shorts by 5.5% **[primary]**.
- Renaissance's own 2012 letter to the IRS, quoted in the Senate report, gives **4.6:1 average leverage with a 6.4:1 peak in 2005–06** under a different arrangement **[primary]**.
- The widely repeated "12.5× average" comes from Zuckerman, and the exact passage was not located **[second-hand, unverified]** ([govinfo](https://www.govinfo.gov/content/pkg/CHRG-113shrg89882/html/CHRG-113shrg89882.htm); [Senate report](http://math.nyu.edu/faculty/avellane/basket.pdf)).

The same options were held about 400–450 days, which turned short-term trading gains into long-term capital gains. On the Senate's estimate that covered about **US$34 billion of profit and avoided US$6–6.8 billion of tax [primary]**. Renaissance executives settled with the IRS in 2021 for **up to about US$7 billion [primary: Reuters]** ([Reuters via Yahoo](https://finance.yahoo.com/news/renaissance-executives-pay-7-billion-231750167.html)).

The fragility showed in August 2007, when Medallion lost about 20% in a week **[second-hand]** ([Leithner quoting Zuckerman](https://www.chrisleithner.com/how-warren-buffett-has-trounced-the-worlds-greatest-hedge-fund-manager/)). The simplest short-horizon contrarian strategy shows why leverage kept climbing. Its raw edge fell from 1.38% a day in 1995 to 0.13% in 2007, so holding returns steady needed leverage to rise from about 2.6:1 to about 9:1, and that leverage is what made the August 2007 unwind so violent ([Khandani & Lo 2007](https://web.mit.edu/Alo/www/Papers/august07.pdf)).

On rough arithmetic, a 66% gross return at 12.5× leverage is about 5% a year on each dollar of positions **[speculation]**. The edge is small per dollar; leverage and turnover make it huge per dollar of capital.

### The comparison that fits us is RIEF, not Medallion

Renaissance runs a second, slower product, the Institutional Equities Fund (RIEF). It holds positions "for months or longer", and Simons said it "could theoretically manage as much as $100 billion" **[primary]** ([Bloomberg 2007](https://web.stanford.edu/class/archive/cs/cs224n/cs224n.1114/renaissance.html)). In 2020 Medallion made +76% while RIEF lost 22.62% **[primary: Institutional Investor 2021]**. A later Institutional Investor article gives −19.4% for the same year, an unresolved conflict ([II 2021](https://www.institutionalinvestor.com/article/2bswms7wco7as686o8ikg/portfolio/renaissances-medallion-fund-surged-76-in-2020-but-funds-open-to-outsiders-tanked); [II 2024](https://www.institutionalinvestor.com/article/2e0uykr3vn5booz0smrcw/hedge-funds/renaissances-2024-rebirth)).

Same firm, same software, very different results. That is consistent with a simple trade-off: fast signals are strong but small, slow signals scale but are crowded and factor-like **[speculation]**. Our monthly momentum book sits firmly on the RIEF side of that line.

## Citadel's hedge fund rations risk and forbids market bets

Citadel LLC runs five core businesses: equities, fixed income and macro, commodities, credit, and quantitative strategies **[primary]** ([Risk.net 2023](https://www.risk.net/awards/7955800/hedge-fund-of-the-year-citadel)). Its flagship Wellington fund compounded at **19.46% a year after fees from 1990 to 2024 [primary]** ([KBRA 2025](https://www.kbra.com/publications/gdbMxFGn/kbra-affirms-the-issuer-ratings-for-citadel-s-flagship-multi-strategy-funds-and-the-rating-to-the-senior-unsecured-notes-issued-by-citadel-finance-llc)). It lost about 55% in 2008, suspended withdrawals, and made +62% in 2009 **[primary]** ([Risk.net 2021](https://www.risk.net/awards/7755351/lifetime-achievement-award-ken-griffin)). In 2022 it made +38.1%, about US$16 billion ([Forbes](https://www.forbes.com/sites/hanktucker/2023/01/22/citadels-16-billion-gain-in-2022-makes-ken-griffins-firm-the-top-earning-hedge-fund-ever/)). **Its volatility and Sharpe ratio have never been published**, so any Sharpe quoted for Citadel cannot be checked.

How it makes money is better documented than its returns. Each portfolio manager gets **an annual "volatility allocation" from a senior committee, with limits "even down to the level of individual factors"**. Extra risk is granted case by case and "always and without exception done with conditions attached", including drawdown limits **[primary]** ([Risk.net 2023](https://www.risk.net/awards/7955800/hedge-fund-of-the-year-citadel)). Chief risk officer Joanna Welsh: "We're not taking beta. We're not chasing factor themes." Equity books are **required to be beta-neutral**, ETF use is capped, and the goal is idiosyncratic winners and losers **[primary]**.

The central risk group is "more than 50 people". It checks its own factor model continuously against what actually happens, and adds new "transient factors" when leftover returns reveal a new shared driver **[primary]**. Portfolio managers are "not allowed to gravitate towards the most popular stocks, the things that are making the news" **[primary, as reported]**, and the head of quant strategies describes the job as carving out "the precise part of an instrument's returns that it can explain in terms of idiosyncratic drivers" **[primary]** ([Risk.net 2025](https://www.risk.net/awards/7960368/hedge-fund-of-the-year-citadel)). The fund-level safeguards are just as concrete: **more than 40% of net assets in cash and equivalents**, and 70% of investors can withdraw only 1/16 of their capital a quarter **[primary]** ([KBRA](https://www.kbra.com/publications/gdbMxFGn/kbra-affirms-the-issuer-ratings-for-citadel-s-flagship-multi-strategy-funds-and-the-rating-to-the-senior-unsecured-notes-issued-by-citadel-finance-llc)).

Two widely repeated numbers need correcting:

- **Drawdown thresholds.** The "cut risk at 5%, shut the pod at 7.5%" rule is **Millennium's**, not Citadel's **[second-hand]** ([Net Interest](https://www.netinterest.co/p/peak-pod)). The "Citadel cuts at 5%, stops at 10%" version circulating in Substack explainers has no traceable source **[speculation: folklore]** ([youngandcalculated](https://youngandcalculated.substack.com/p/risk-management-inside-a-pod-how)).
- **The economics.** From January 2021 to September 2024 the three flagship funds made US$56.8 billion gross, and investors kept about US$30 billion of it, roughly 53% **[second-hand: bond prospectus via Hedgeweek]**. Pass-through expenses, mostly staff pay, took about US$17 billion ([Hedgeweek](https://www.hedgeweek.com/citadel-prospectus-reveals-57bn-in-gains-from-largest-funds/)). Citadel returns profits to stay at a chosen size: US$5 billion for 2025, taking assets from US$72 billion to US$67 billion **[second-hand]** ([CNBC via Yahoo](https://finance.yahoo.com/news/citadel-return-5-billion-profits-155153589.html)). That article's "US$32 billion since 2017" conflicts with the prospectus's "US$18 billion since 2018". The gap is flagged, not reconciled.

The industry critique matters for us. Multi-manager funds held about 27% of hedge-fund US-equity positions on about 8% of hedge-fund assets **[second-hand: Goldman data via Net Interest]** ([Net Interest](https://www.netinterest.co/p/peak-pod)). When every pod uses similar stop-losses and the same risk model, the stops become a shared selling trigger **[speculation]**. In March 2026 Citadel's Global Fixed Income fund fell 8.2% in a month while Wellington lost only 1.9%. Diversification across businesses absorbed one unit's crash **[second-hand]** ([Hedgeweek citing Bloomberg](https://www.hedgeweek.com/citadels-global-fixed-income-strategy-hit-by-march-market-turmoil/)).

The table below sets Citadel's documented controls against ours.

| Control | Citadel (evidence) | Our system |
|---|---|---|
| Unit of allocation | Volatility budget per manager, capped by factor **[primary]** | Fixed equal dollars: `config.py:200-205` `ALLOCATIONS = {"ASX": 0.25, "US": 0.25, "FTSE": 0.25, "TSX": 0.25}` |
| Market exposure | Equity books must be beta-neutral **[primary]** | Long-only; measured beta 0.18–0.30, mostly from cash |
| Drawdown control | Per-manager conditions; numbers private **[primary]** | One global breaker: `config.py:269` `MAX_DRAWDOWN_STOP = 0.25` |
| Risk-model check | Model checked continuously against outcomes; transient factors **[primary]** | Vol targeting assumes `config.py:29` `avg_correlation: float = 0.6`; measured within-sleeve 0.18–0.36 |
| Crowding | Barred from "the most popular stocks" **[primary, as reported]** | Top 10 by trailing return, which tilts towards popular names **[speculation]** |
| Liquidity buffer | More than 40% of net assets in cash **[primary]** | About 60% cash through the regime filter and vol target ("a 40%-invested book", `docs/FULL_SYSTEM_REVIEW_2026-09.md` §3.1) |

## Citadel Securities earns fractions of a basis point on a firehose of orders

Citadel Securities is a separate company, a market maker. Its reported trading revenue was:

- **US$9.7 billion in 2024**, with about US$5.2 billion EBITDA (earnings before interest, tax, depreciation and amortisation) on about US$16 billion of trading capital;
- **US$12.2 billion in 2025**;
- **US$4.3 billion in Q1 2026**.

All three are **[second-hand: Bloomberg "people familiar"; no audited accounts are public]** ([Hedgeweek](https://www.hedgeweek.com/griffins-citadel-securities-reports-record-9-7bn-trading-revenue/); [Benzinga](https://www.benzinga.com/markets/private-markets/26/03/51466069/citadel-securities-sees-record-breaking-12-billion-in-trading-revenue-in-2025); [Yahoo/Bloomberg](https://finance.yahoo.com/markets/stocks/articles/citadel-securities-pushes-4-25-173119974.html)). It says it handles about 35% of US retail equity volume, more than 23% of all US equity volume and about 30% of US listed options **[second-hand: company pages seen via search snippets]** ([Citadel Securities](https://www.citadelsecurities.com/what-we-do/equities/)).

The mechanism is documented by the regulator. Retail orders are valuable because they carry little information. Price impact, the regulator's measure of adverse selection, is **1.26 bps on orders filled by wholesalers against 4.40 bps on exchanges, 71% lower**. Over 5 minutes, wholesalers earn a realised half-spread of +0.61 bps while exchange liquidity providers earn −0.38 bps. Wholesalers pay brokers about US$0.001–0.003 a share for the flow; the six largest paid US$235 million in Q1 2022 **[primary]** ([SEC Release 34-96495](https://www.sec.gov/files/rules/proposed/2022/34-96495.pdf); [Schwarz et al.](https://microstructure.exchange/papers/Schwartz_et_al_,_2022_WP,_The_'Actual_Retail_Price'_of_Equity_Trades.pdf)).

Scaling the SEC's figures gives about US$1.25 billion a year of realised spread across all six wholesalers combined, against about US$7.5 billion of Citadel Securities revenue in 2022. The famous retail-internalisation business is probably a minority of revenue, and most of it comes from options, institutional, bond and currency flow and from managing inventory beyond 5 minutes **[speculation]**. The edge rests on four things we cannot have:

- privileged access to low-information flow;
- netting millions of buys against sells;
- prediction and hedging over seconds to minutes;
- scale.

What a retail account *can* reach is narrower. In a test of 85,000 simultaneous tiny market orders, a round trip cost 0.072% at TD Ameritrade but **0.444% at IBKR Pro** (Interactive Brokers' professional account), before commissions. Having no PFOF did not mean a better price on small market orders **[primary]** ([Schwarz et al.](https://microstructure.exchange/papers/Schwartz_et_al_,_2022_WP,_The_'Actual_Retail_Price'_of_Equity_Trades.pdf)). The cheaper route for a scheduled rebalancer is the **closing auction**. For large US stocks it fills at about a 1.47 bp half-spread plus 1.19 bps of impact. At the open, by contrast, effective spreads widened by about 10 bps over 2010–2018 and depth at the best quotes fell 63% **[primary]** ([Bogousslavsky & Muravyev](https://static1.squarespace.com/static/6310c0b9bb63a25599f4418c/t/634ffc92f81e226b2c30654f/1666186387645/who-trades-at-the-close_June2021.pdf)). The ASX closing auction is about 21% of daily turnover **[primary]** ([ASX](https://www.asx.com.au/blog/finding-value-through-asx-closing-auctions)), and developed-Europe closes about 35% of daily volume **[second-hand]** ([SSGA](https://www.ssga.com/us/en/institutional/insights/how-passive-investing-reshaping-microstructure)). Put simply, we are the low-information customer whose orders are the product. The one piece of market-maker economics we can capture is choosing *where and when* we trade.

## Our skill per bet matches Medallion's; our count of independent bets does not

The first lens is Medallion's: skill per bet times number of bets. We measured each month's top-10 picks against the month that followed, on all four sleeves, from 2012 to 2026 **[measured; gross, pre-cost, no regime filter or vol targeting, survivorship-biased]**:

| Sleeve | Months | Names ranked | Mean IC | IC t (Newey-West) | Top-10 hit rate | Hit t vs 50% | Top-10 spread/month | Spread t | Bottom-10 vs universe/month |
|---|---|---|---|---|---|---|---|---|---|
| US | 164 | 124 | 0.021 | 1.29 | 50.9% | 0.58 | +0.96% | 2.76 | **+0.26%** |
| ASX | 165 | 54 | 0.031 | 1.64 | 51.6% | 1.26 | +0.96% | 3.32 | −0.26% |
| FTSE | 164 | 63 | 0.040 | 2.10 | 52.3% | 1.72 | +0.45% | 1.92 | −0.33% |
| TSX | 164 | 52 | 0.030 | 1.49 | 51.2% | 0.80 | +0.49% | 1.72 | −0.03% |

**Per-bet hit rates of 50.9–52.3% are in the same territory as Mercer's 50.75%, so per-bet skill is not what we lack.** The comparison is loose. Ours is the share of top-10 picks that beat the middle stock that month; his is undefined. None of our hit rates is individually significant (all t below 2 over 164 months).

Our ICs also sit where the published literature says 12-1 momentum lives. Russell 1000 stocks give a monthly IC of 0.029 with a month-to-month standard deviation of 0.179, a realistic ceiling of about 0.56 annual IR for a gross long/short book ([Ding 2010](https://math.nyu.edu/inmemoriam/avellaneda/FundamentalLawFT.pdf)). Nothing is broken. This is the ceiling of this kind of signal.

The new number is breadth. Effective independent bets a year were estimated as 12 ÷ (the variance of the monthly IC). As a check, under a shuffle null where names are independent by construction, the method reproduces the textbook names × 12 (US: 1,477 against 1,483). So the shortfall below is not a flaw in the method **[measured]**. It has two causes the measurement cannot split: stocks moving together (sector and factor bets), and the signal's own strength swinging from year to year (momentum's good and bad regimes). Both cap the Sharpe ratio the signal can deliver, which is what matters here.

| Sleeve | Names ranked | Naive bets/year (names × 12) | Effective bets/year | Effective independent names/month | IC-IR (annual, gross) |
|---|---|---|---|---|---|
| US | 124 | 1,483 | 220 | 19.4 | 0.32 |
| ASX | 54 | 653 | 224 | 19.7 | 0.46 |
| FTSE | 63 | 755 | 212 | 18.6 | 0.59 |
| TSX | 52 | 624 | 154 | 13.8 | 0.37 |

**Every region collapses to 14–20 independent bets a month, whether it ranks 52 names or 124 [new].** A bigger list drawn from the same market buys almost no breadth, because the cross-section has only about 20 independent dimensions (sectors and factors). With the four sleeves about 0.41 correlated (see below), the book holds roughly 4/(1 + 3 × 0.41) ≈ 1.8 independent sleeves. **The whole equity book therefore makes approximately 350–400 independent bets a year.** At Medallion's reported hit rate, a Sharpe of 1 needs about 4,400 and a Sharpe of 2 about 17,800, roughly 45–50 times our count. At our own better per-bet IC of about 0.03, a Sharpe of 1 still needs about (1/0.03)² ≈ 1,100 independent bets a year with nothing lost to the long-only constraint, three times what we have **[speculation: arithmetic on measured inputs]**. The long-only constraint costs more again. In one study, the share of the signal a portfolio could express (the transfer coefficient) rose from 0.33 to 0.68 when the long-only rule was relaxed ([Clarke, de Silva & Sapra 2004](https://www.hillsdaleinv.com/uploads/Toward_More_Information-Efficient_Portfolios,_Roger_G._Clarke,_Harindra_de_Silva,_Steven_Sapra,_The_Journal_of_Portfolio_Management,_Fall_2004,_Pages_54-63.pdf)).

The repo already names Grinold's law in `docs/SHARPE_RESEARCH.md` §10 but never measured breadth. That section also says "Higher frequency is the only reliable way to raise it". The decay curve qualifies that **[new]**:

| Months ahead | 1 | 3 | 6 | 8 | 10 | 12 |
|---|---|---|---|---|---|---|
| US IC | .021 | .017 | .023 | .018 | .007 | .006 |
| ASX IC | .031 | .023 | .020 | .021 | .025 | .011 |
| FTSE IC | .040 | .029 | .030 | .022 | .014 | .016 |
| TSX IC | .030 | .033 | .033 | .021 | .006 | .004 |

**The signal stays roughly flat for six to eight months and fades to about zero by month 10–12 in every region [measured, gross].** Rebalancing a signal that slow every month mostly re-measures the same bet rather than adding a new one. Faster signals that *would* add independent bets are exactly the ones costs destroy. Short-term reversal lost 1.28% a month net of costs in value-weighted US data ([Novy-Marx & Velikov](https://www.nber.org/system/files/working_papers/w20721/w20721.pdf)), and the repo already rules it out as a funded book. For this book, then, more breadth has to come from a *different* signal or asset class, not from more names or more frequent trading.

The same curve helps explain a puzzle in the review. `docs/FULL_SYSTEM_REVIEW_2026-09.md` §15 found that quarterly rebalancing, averaged over its three possible phases, beats monthly in all four sleeves. The handoff that followed (`docs/HANDOFF_2026-09-24.md` §3) adds that annual beats quarterly in three of four, "which no signal-decay story permits". A signal this flat loses little by being traded less often and saves cost. That does not *prove* annual is best: shifting the rebalance date alone moves CAGR by 1.4–3.5 points (§5.2), which is the same size as these differences. The curve also bears on tax. The signal has mostly expired by the 12 months an Australian CGT discount needs, and `tax.py` found 0 of 50 round-trips clearing that bar.

## A stricter test erases the US timing edge but not ASX or FTSE

Patterson's "do the simple things right" applies to how an edge is tested. Applied to the IC, the within-month signal shuffle (the null the repo sanctions for *portfolio Sharpe* tests) gives the US p = 0.002. That shuffle breaks something important. It destroys the score's factor structure, so the shuffled IC varies only 0.09 a month (the textbook 1/√(N−1) for independent names) while the real IC varies 0.23, 2.6 times wider. For the IC statistic the shuffle null is therefore far too narrow and its p-values are overconfident. For a portfolio *Sharpe ratio* the shuffle is still the right null (`docs/PERMUTATION_TESTING.md`), and this criticism applies to IC only **[measured]**.

A second test, the **time-pairing null [new]**, keeps the structure and breaks only the timing. It pairs month *i*'s rankings with a random month *j*'s returns at least 12 months away, using the same names. The football analogy: if a ladder from a random past season predicts this month's results almost as well as last month's form does, you are measuring which clubs are rich, not current form.

| Sleeve | Real IC | Null mean IC | p (IC) | Real top-10 spread/month | Null spread | p (spread) |
|---|---|---|---|---|---|---|
| US | 0.021 | **+0.016** | **0.354** | 0.96% | **+0.62%** | 0.090 |
| ASX | 0.031 | −0.002 | 0.012 | 0.96% | +0.40% | 0.012 |
| FTSE | 0.040 | −0.008 | <0.002 | 0.45% | −0.09% | 0.014 |
| TSX | 0.030 | −0.003 | 0.058 | 0.49% | +0.06% | 0.060 |

**In the US, a ranking from an unrelated month earns most of the same IC and two-thirds of the spread [measured, gross, survivorship-biased].** The result holds with the minimum gap set to 12, 24 or 36 months (US p = 0.297 / 0.347 / 0.297). ASX (0.003 / 0.013 / 0.027) and FTSE (0.000 at all three) survive. TSX is borderline (0.050 / 0.020 / 0.027). The test leans conservative: genuine momentum that persists beyond 12 months is counted as null.

Splitting the US list finds the cause **[new]**. `universes.py:27-47` holds two lists:

- 90 single stocks described as "S&P 500 large caps", chosen by today's size and so by what won;
- 36 ETFs spanning stocks, bonds (TLT, IEF, HYG) and commodities (GLD, SLV).

The measurement used 89 stocks: `MMC` returned no data from Yahoo on 2026-10-13, and the loader dropped it with a dead-ticker alert (the review records four dead universe tickers).

| US subset | Names | IC | IC t | Top-10 spread/month | Time-null IC | p |
|---|---|---|---|---|---|---|
| Stocks only | 89 | **0.006** | **0.35** | 0.73% | +0.005 | 0.48 |
| All, as traded | 125 | 0.021 | 1.29 | 0.96% | +0.015 | 0.31 |

**Among US single stocks, the 12-1 ranking has no measurable predictive power.** Most of the US IC comes from ranking ETFs across asset classes whose average returns differed persistently over 2012–2026. The rank correlation between the momentum score and each name's full-sample average return is 0.29 in the US against 0.16–0.22 elsewhere. Two explanations cannot be separated without point-in-time membership:

- the universe was selected with hindsight; or
- 2012–2026 was a genuine era of persistent mega-cap winners that momentum would have bought live.

Either way, the US edge is not momentum *timing*. `docs/PERMUTATION_TESTING.md` §7's US p = 0.039 (signal shuffle, gross) is not wrong, but it answers a different question and cannot tell these explanations apart.

## Hedged the Citadel way, no sleeve shows significant alpha

The second lens is Citadel's: take out the market and see what is left. Each sleeve's real backtest, with costs on and a 3.5% idle-cash credit, was regressed against two markets. The first is the regional price index, which excludes dividends and so flatters alpha. The second is an equal-weight basket of the sleeve's own survivor stock list, which puts the same survivors on both sides, so survivorship largely cancels **[measured, costs on, from 2013-04-30]**:

| Sleeve | Excess Sharpe | CAGR | Beta (index) | Alpha/yr vs index (t) | Beta (own list) | Alpha/yr vs own list (t) | R² |
|---|---|---|---|---|---|---|---|
| US | 0.73 | 9.6% | 0.27 | +3.5% (2.00) | 0.30 | +2.1% (1.20) | 0.23 |
| ASX | 0.44 | 6.4% | 0.25 | +2.5% (1.91) | 0.25 | −0.6% (−0.48) | 0.27 |
| FTSE | 0.17 | 4.5% | 0.23 | +0.9% (0.59) | 0.23 | −0.1% (−0.06) | 0.21 |
| TSX | 0.89 | 9.4% | 0.18 | +4.9% (2.69) | 0.20 | +3.1% (1.72) | 0.12 |

**Hedged against its own stock list, each sleeve keeps between −0.6% and +3.1% a year, and none of it is significant. The best t is 1.72, for TSX [new].** The sleeves' low beta of 0.18–0.30 comes from the regime filter and vol targeting keeping about 60% in cash, not from Citadel-style neutrality.

The survivor problem is visible in the benchmarks. The own-list baskets earned excess Sharpe 1.00 (US), 1.03 (ASX), 0.40 (FTSE) and 1.16 (TSX). The price indices earned 0.66, **0.12**, 0.08 and 0.40. ASX's index at 0.12 against today's ASX survivors at 1.03 is survivorship bias you can see directly.

This agrees with the repo's own review. The four-sleeve portfolio does not beat total-return index ETFs on a risk-adjusted basis: Sharpe 0.62 against 0.65, inside each other's ±0.26 error bars. What it delivers instead is a much shallower worst loss, about −10% against −30% (`docs/FULL_SYSTEM_REVIEW_2026-09.md` §3.1).

Citadel's quant chief describes the job as carving out idiosyncratic returns. The nearest academic tool is **residual momentum**: rank stocks on returns left after removing the market, not on raw returns. The published case is strong on paper. Long/short Sharpe roughly doubles: US 0.47 to 0.95, Australia 1.28 to 1.78, UK 0.79 to 1.32, Canada 0.71 to 1.10. All of that is gross, long/short, and by Robeco-affiliated authors, a disclosed conflict ([Hanauer & Windmüller](http://wp.lancs.ac.uk/mhf2019/files/2019/09/MHF-2019-076-Matthias-Hanauer.pdf)). On our panel it is mixed **[new; measured; gross, top-10 equal-weight, one-factor version, 140 months]**:

| Sleeve | IC raw → residual | Spread/month | Name turnover/month | Volatility | Gross Sharpe | Beta | Max drawdown |
|---|---|---|---|---|---|---|---|
| US | .013 → .002 | 0.88% → 0.41% | 27% → 44% | 23.6% → 17.3% | 0.99 → 1.02 | 1.15 → 0.96 | −21.8% → −22.3% |
| ASX | .020 → .037 | 0.93% → 0.56% | 23% → 30% | 19.9% → 17.3% | 1.23 → 1.13 | 1.00 → 0.94 | −21.1% → −17.1% |
| FTSE | .023 → .023 | 0.30% → 0.44% | 26% → 33% | 16.2% → 14.3% | 0.49 → 0.67 | 0.82 → 0.79 | −33.9% → −23.5% |
| TSX | .020 → .025 | 0.45% → 0.38% | 24% → 32% | 17.9% → 16.4% | 0.98 → 1.01 | 0.91 → 0.88 | −17.9% → −13.7% |

Residual momentum lowers volatility and beta in all four sleeves and maximum drawdown in three. It costs 7–17 extra percentage points of names replaced each month, which is expensive under FTSE's stamp duty, and it does not consistently raise IC or spread. It improves the *shape* of the risk; it is not a free return upgrade, and nowhere near the published "doubling". It needs a costs-on backtest before adoption.

Two of Citadel's other habits fail on our data **[new]**:

- **Beta-neutral long/short.** It is wrong for the US core: the US losers *beat* their universe by 0.26% a month, so a short leg would have lost money. Survivorship flatters past losers, so part of this is an artefact. The direction still matches the review's measured US long/short Sharpe of 0.21 against 0.83 long-only, and the published finding that about half of momentum's profit comes from the long side ([Israel & Moskowitz](https://www.sciencedirect.com/science/article/pii/S0304405X12002401)).
- **Risk budgets.** Weighting the sleeves by inverse volatility (from trailing 36-month sleeve volatility) instead of equal capital changed Sharpe by **−0.030 (95% block-bootstrap interval −0.110 to +0.046; probability of improvement 0.22) [measured]**. Our sleeves already carry similar risk, and risk-budgeting would have cut the best-Sharpe sleeve, the US.

## Four regional sleeves behave like fewer than two

`docs/SHARPE_RESEARCH.md` §6 says the portfolio diversifies "because ASX–US correlation is 0.058". That number is a **time-zone artefact [new]**. The ASX closes before the US opens, so a US shock reaches Australian prices the next calendar day and same-day correlation misses it **[measured, local-currency sleeve returns, 2013-05 to 2026-09]**:

| Pair | Daily | Daily, lead-lag corrected (Dimson) | Weekly | Monthly |
|---|---|---|---|---|
| US–ASX | **0.06** | 0.42 | 0.38 | 0.38 |
| US–FTSE | 0.31 | 0.41 | 0.40 | 0.30 |
| US–TSX | 0.50 | 0.50 | 0.50 | 0.23 |
| ASX–FTSE | 0.20 | 0.41 | 0.35 | 0.28 |
| ASX–TSX | 0.09 | 0.42 | 0.39 | 0.29 |
| FTSE–TSX | 0.38 | 0.50 | 0.44 | 0.39 |

Corrected, the ASX is about 0.4 correlated with everything, and the average pair is about **0.41, not 0.26**. That changes three things:

- The effective number of independent sleeves falls from about 2.25 to about 1.8.
- §6's frictionless benchmark Sharpe of 1.010 becomes about 0.674 × √(4/(1 + 3 × 0.41)) ≈ 0.90.
- The "0.204 Sharpe leak" it attributes to the portfolio layer shrinks to about 0.10. Roughly half of the leak was the correlation estimate.

The US–TSX figure of about 0.50 holds on three of four estimators; the monthly 0.23 is the outlier.

Published evidence points the same way. Each equity market's momentum strategy correlates about 0.65 with the average of the other markets, and that correlation rose from 0.46 (1972–91) to 0.77 (1992–2011) ([Asness, Moskowitz & Pedersen 2013](https://w4.stern.nyu.edu/facdir/lpederse/papers/ValMomEverywhere.pdf)). That implies about 1.5 independent sleeves out of four **[speculation: inversion assuming equal correlations]**. Four regions of one signal is mostly one bet, four times.

This mirrors Citadel's discipline of checking its risk model against reality. Our system holds **two correlation numbers that are wrong in opposite directions [new observation]**:

- **Too high:** the within-sleeve assumption that sizes the vol target, `avg_correlation: float = 0.6` (`config.py:29`), against the measured 0.18–0.36. This makes the book take less risk than intended.
- **Too low:** the cross-sleeve figure the diversification argument rests on, 0.26 against about 0.41. This overstates how much the regions protect each other.

## Costs bind only in FTSE, and execution is the one market-maker lever we hold

Medallion's single model only added a signal if its gains beat its costs **[second-hand]**. Measured the same way, cost per bet against edge per bet shows that costs are not the binding constraint outside FTSE **[measured; lower bound, ignores re-weighting turnover; uses `regions.py` cost settings]**:

| Sleeve | Round-trip cost per replaced name | Cost/month | Gross top-10 spread/month | Cost as share of edge |
|---|---|---|---|---|
| US | 14 bps | 3.7 bps | 96 bps | 4% |
| ASX | 36 bps | 8.1 bps | 96 bps | 8% |
| FTSE | 76 bps (incl. 50 bp stamp duty) | 19.8 bps | 45 bps | **44%** |
| TSX | 22 bps | 5.4 bps | 49 bps | 11% |

This agrees with `docs/SHARPE_RESEARCH.md` §8, which found costs take 41% of FTSE's gross Sharpe; that finding is already recorded. Unlike Medallion, a book this size is **bound by fixed costs, not by capacity**. A A$2,500 order (a A$100,000 book × 25% sleeve ÷ 10 names) in a stock trading A$20 million a day has an estimated impact of about 2 bps. The ASX A$5 commission floor on the same order is 20 bps a side, ten times larger **[speculation: square-root-law arithmetic, extrapolated below the tested range]** ([Tóth et al. 2011](https://arxiv.org/pdf/1105.1694)). Fewer, larger trades matter more to us than clever order handling.

One execution gap is new. **Live orders go out as plain market orders: `execution_ibkr.py:173` `trade = ib.placeOrder(contract, MarketOrder(action, qty))`**. The paper book and backtest, meanwhile, assume a fill at the next close (`paper_trade.py:12-13`: "it fills at the close of the next session the sleeve's data shows, never at the close that produced it"). Matt locked that next-close convention as decision D3 (`docs/HANDOFF_2026-09-24.md`). The live instrument that matches it is a **market-on-close or limit-on-close order in each sleeve's own closing auction**. No repo doc mentions closing auctions **[new]**. It is also the cheapest transparent venue available: about 1.5 bps half-spread plus 1.2 bps impact for large US stocks ([Bogousslavsky & Muravyev](https://static1.squarespace.com/static/6310c0b9bb63a25599f4418c/t/634ffc92f81e226b2c30654f/1666186387645/who-trades-at-the-close_June2021.pdf)). Momentum's return also accrues overnight rather than intraday, so buying at the close keeps that leg ([Lou, Polk & Skouras](https://conference.nber.org/confer/2015/APf15/Lou_Polk_Skouras.pdf)).

The risks are known:

- **Index-rebalance days.** Auction deviations jump by about 21 bps around S&P 500 index changes.
- **Large deviations.** About 1% of auctions miss the pre-close midpoint by more than 63 bps.
- **Unfilled orders.** A limit-on-close order may not fill.
- **Stamp duty.** The 50 bp duty still dominates any FTSE execution choice.

IBKR's per-exchange cut-off times for these orders were not checked.

One related observation is open, not a recommendation. Measured large-cap closing half-spreads of about 1.5 bps suggest the 5 bps-per-side US slippage assumption (`regions.py:96-98`) may be conservative. It should be checked against the actual universe's spreads before anyone changes it.

Two smaller items concern the currency and crypto books:

- **FX week edge.** `forex/sessions.py:55` `_FX_EDGE_HOUR = 22` fixes the FX week's edge at 22:00 UTC. The market actually turns at 17:00 New York time, which is 21:00 UTC during US daylight time, so the edge is an hour late about eight months a year **[new; matters only for intraday books]**.
- **Crypto fees.** The default crypto venue is Binance (`forex/crypto_exec.py:174`). At the entry tier it charges 0.10% for both maker and taker orders, so posting limit orders there saves only the spread, not fees ([Binance](https://www.binance.com/en/fee/schedule)).

## What we're missing, ranked by evidence per unit of effort

The system already does several things the way these firms do:

- **One model.** One weight function, `strategy.compute_targets`, serves backtest and paper trading alike (invariant #3). That is the governance point of Medallion's single model: no two copies of the logic trading against each other.
- **Costs always on.** Costs are always charged and the overfitting gates (Deflated Sharpe, PBO) are in place. That is Patterson's "simple things right".
- **Sim-versus-live auditing.** `verify.py` audits the live books against their own ledger. Medallion's best-documented near-miss was the gap between simulated and live results: a hard-coded S&P 500 level in its stock system, found only by digging into why live profits "evaporated" **[second-hand]** ([bagerbach](https://bagerbach.com/books/the-man-who-solved-the-market/)).

The gaps, ordered by how much evidence supports each against the effort it takes:

| Rank | What | Status | Effort |
|---|---|---|---|
| 1 | Fix the within-sleeve correlation assumption | Recorded | Trivial |
| 2 | Correct the cross-sleeve correlation in the diversification maths | **New** | Trivial |
| 3 | Make breadth, hit rate and the time-pairing null standing diagnostics | **New** | Small |
| 4 | Trade less often: buy/hold band plus slower or tranched rebalance | Recorded; **new** supporting evidence | Small to moderate |
| 5 | Point-in-time membership, US first | Recorded and locked (D2); **new** evidence on where it matters most | Large |
| 6 | Closing-auction orders for live execution | **New** | Small |
| 7 | Per-sleeve drawdown breaker | Recorded | Small to moderate |
| 8 | Graded, volatility-responsive de-risking | Recorded | Moderate |
| 9 | A genuinely different signal to buy breadth | Recorded ideas; **new** rationale | Large |
| 10 | Residual momentum, but only after a costs-on backtest | Recorded idea; **new** measurement | Moderate |
| 11 | Key the FX week edge to 17:00 New York | **New** | Trivial; low value |

**1. Fix the within-sleeve correlation assumption.** `config.py:29` assumes 0.6 against a measured 0.18–0.36. The review measured US CAGR rising from 9.6% to 11.5% at the same Sharpe. Citadel's risk group checks its model continuously against outcomes **[primary]**. Already recorded as the first upgrade in `docs/FULL_SYSTEM_REVIEW_2026-09.md` §5.3. One number.

**2. Correct the cross-sleeve correlation used in the diversification maths.** Replace the same-day daily matrix in `docs/SHARPE_RESEARCH.md` §6 with weekly or Dimson-corrected correlations (about 0.41, not 0.26), and re-state the portfolio "leak" (about 0.10, not 0.204) **[new]**. Without this, any future sleeve decision overstates how much regions protect each other. Effort: a doc correction plus one metric.

**3. Make breadth, per-bet hit rate and the time-pairing null standing diagnostics, next to the shuffle test.** The two nulls answer different questions: US IC p = 0.002 under the shuffle, 0.354 under time-pairing. Only the second can tell momentum timing from persistent drift **[new]**. The scripts that produced every measured number in this report are committed in `scripts/medallion_lens/` (`measure_lens.py`, `null_check.py`, `null_robust.py`, `us_split.py`, `alloc_test.py`, `corr_check.py`), so the evidence is reproducible (see *How to reproduce*). What remains is to run them as routine diagnostics rather than one-offs. Small effort.

**4. Trade less often: a buy/hold band plus a slower or tranched rebalance.** Already recorded:

- `docs/FULL_SYSTEM_REVIEW_2026-09.md` §5.3 items 3–4.
- §15: quarterly beats monthly averaged over phase, and halves FTSE's cost drag from 24.7% to about 11%.
- `docs/SHARPE_RESEARCH.md` §8.
- Code fact: `signals.py:98` `picks = metric.nlargest(min(p.top_n, len(metric))).index` re-picks every month with no hold band.

New support comes from the flat six-to-eight-month decay curve and FTSE's 44% cost-to-edge ratio. Published: a 10%/20% buy/hold band cut US momentum turnover from 34.5% to 18.8% a month and raised net return from 0.68% to 0.85% a month ([Novy-Marx & Velikov](https://mysimon.rochester.edu/novy-marx/research/ToAatTC.pdf)). Momentum's rebalance-date luck was the largest of the common factors, 4.4% a year for semi-annual 100-stock books ([Hoffstein, Faber & Braun](https://www.panoptica.com/rebalance-timing-luck-the-dumb-timing-luck-of-smart-beta-by-hoffstein-faber-and-braun/?from_src=epsilontheory)). The review calls slower rebalancing "the strongest unexploited lead in the equity book". Thresholds should be set on our panel, not copied from the paper.

**5. Point-in-time index membership, US first.** Already locked as decision D2 (`docs/HANDOFF_2026-09-24.md`), with Matt's acceptance of the data cost. The new evidence says where it matters most:

- US single stocks show IC 0.006 and a score-to-full-sample-return correlation of 0.29.
- ASX's price index Sharpe of 0.12 sits against its survivor basket's 1.03.

Until membership is point-in-time, hindsight selection cannot be separated from a genuine winners' era. Large effort, but every other number here is an upper bound until it lands.

**6. Closing-auction orders (MOC/LOC) for live execution.** This aligns the live order type (`execution_ibkr.py:173`, market orders) with the simulated next-close fill (`paper_trade.py:12-13`, decision D3), on the cheapest transparent venue each market offers **[new]**. Published support is in [Bogousslavsky & Muravyev](https://static1.squarespace.com/static/6310c0b9bb63a25599f4418c/t/634ffc92f81e226b2c30654f/1666186387645/who-trades-at-the-close_June2021.pdf) and the [ASX](https://www.asx.com.au/blog/finding-value-through-asx-closing-auctions). It pays only once real money trades, and needs a rule for index-rebalance days. Small effort.

**7. Per-sleeve drawdown breaker instead of one global 25%.** `config.py:269` sets one threshold for every sleeve, but the chance of tripping it on luck alone differs: TSX 0.9%, ASX 1.6%, US 5.4%, FTSE 19.1%. Already recorded in `docs/MONTE_CARLO_RESEARCH.md` §7, which explicitly does *not* recommend reweighting FTSE. The cleanest documented template is **Millennium's** 5%-cut / 7.5%-stop rule **[second-hand]**. Citadel's own per-manager numbers are not public **[primary that they exist; numbers unknown]**. Small to moderate effort.

**8. Graded, volatility-responsive de-risking.** Already recorded as the first ranked candidate in `docs/EFFECTIVE_PUBLIC_STRATEGIES.md` §12 and the second upgrade in `docs/FULL_SYSTEM_REVIEW_2026-09.md` §5.3. The review's §15 correction matters here: the existing binary filter wins on Calmar (return per unit of worst drawdown) in every sleeve, so this improves a working tool rather than removing a drag. Medallion's system reportedly "tended to automatically reduce positions and risk" when volatility surged **[second-hand]** ([breakingthemarket II](https://breakingthemarket.com/the-greatest-geometric-balancers-renaissance-technologies-part-ii/)). Published evidence is mixed:

- In favour: scaling momentum by its own volatility lifted Sharpe from 0.53 to 0.97 ([Barroso & Santa-Clara](http://www.snifferquant.com/gyantal/Incode/papers/Momentum%20Has%20Its%20Moments(scaling%20Momentum%20by%20vol),2014.pdf)), and momentum is one of only two factors where this partly survives costs ([Barroso & Detzel](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3088828)).
- Against: across 103 strategies, volatility management in general showed no real-time benefit ([Cederburg et al.](https://www.sciencedirect.com/science/article/abs/pii/S0304405X2030132X)).

Moderate effort.

**9. A genuinely different signal to buy breadth.** Our breadth result is the new reason this matters: more names in the same market and more frequent trading both fail to add independent bets. The candidates are already recorded in `docs/EFFECTIVE_PUBLIC_STRATEGIES.md` §12:

- a quality/profitability tilt, which needs a fundamentals feed;
- a time-series-momentum diversifier sleeve;
- the repo's own value proxy, which is dormant. `config.py:116` reads `use_value: bool = False`, even though that §12 lists "Momentum + value" among things the system "already does well" **[new: doc inconsistency]**.

Published support:

- Value and momentum are negatively correlated, about −0.6, and their combination beats either alone in every market tested ([Asness, Moskowitz & Pedersen](https://w4.stern.nyu.edu/facdir/lpederse/papers/ValMomEverywhere.pdf)).
- A value-momentum-profitability blend netted 0.99% a month against plain momentum's 0.68%, at lower turnover ([Novy-Marx & Velikov](https://mysimon.rochester.edu/novy-marx/research/ToAatTC.pdf)).
- For long-only books, one combined score per stock beats separate sleeves ([Fitzgibbons et al.](https://www.aqr.com/Insights/Research/White-Papers/Long-Only-Style-Investing)).

Large effort, and any candidate goes through the repo's register-then-fund gate.

**10. Residual momentum, only after a costs-on backtest.** The idea is already recorded in `docs/FULL_SYSTEM_REVIEW_2026-09.md` §5.2 ("about 2x risk-adjusted profit"). Our measurement is new and tempers it: it reshapes risk rather than raising return, at higher turnover. Moderate effort.

**11. Key the FX week edge to 17:00 New York.** `forex/sessions.py:55` is a fixed 22:00 UTC **[new]**. Trivial, but low value: it matters only to intraday books, and the review found no edge in the FX books under its corrections.

## What not to copy, and why

| Their practice | Why it works for them | Why not for us | Repo status |
|---|---|---|---|
| Leverage of 12.5–20:1 (Medallion) | Tightly hedged long/short book; bank-imposed loss caps (10% knock-out; seizure at 5.5% long-versus-short underperformance) **[primary]** | Retail margin is about 2:1. Sleeve Sharpes are uncertain by about ±0.5 at 95%. Over-betting an over-estimated edge gives zero growth: betting full Kelly on an estimate twice the truth has zero expected growth ([Thorp 2006](https://gwern.net/doc/statistics/decision/2006-thorp.pdf)) | `docs/SHARPE_RESEARCH.md` §7: `ultra` at 3× compounds to about 0.09 Sharpe |
| Basket options as a tax wrapper (Medallion) | Turned short-term gains into long-term ones: about US$6–6.8 billion of tax avoided **[primary]** | Challenged by the IRS and settled for up to about US$7 billion **[primary]**. Nothing comparable is available to an Australian retail account. Holding 12 months for the CGT discount means holding a mostly expired signal (decay curve) | `tax.py` reports after-tax figures only |
| High-frequency trading, market making, PFOF, internalisation (Citadel Securities) | Privileged flow contracts, netting at scale, sub-second hedging, US$16–21 billion of trading capital **[second-hand]** | A different sport played with different equipment | Already rejected: `docs/EFFECTIVE_PUBLIC_STRATEGIES.md` §12, `docs/HFT_REALITY.md` |
| Short-horizon stat-arb and reversal (Medallion-style) | Thousands of faint, 1–6-day edges, crossed internally to save costs ([Kakushadze](https://arxiv.org/pdf/1601.00991)) | Standalone reversal lost 1.28% a month net ([Novy-Marx & Velikov](https://www.nber.org/system/files/working_papers/w20721/w20721.pdf)). The simplest contrarian edge decayed tenfold from 1995 to 2007 ([Khandani & Lo](https://web.mit.edu/Alo/www/Papers/august07.pdf)) | Already rejected as a funded book; research probes only |
| Beta-neutral long/short equity books (Citadel) | Removes market risk to isolate stock-picking | US losers beat their universe (+0.26% a month). US long/short momentum Sharpe 0.21 against 0.83 long-only. Momentum crashes come from the short leg ([Daniel & Moskowitz](https://www.nber.org/system/files/working_papers/w20439/w20439.pdf)) | **New** evidence against converting the core book. The separate `experimental` book is not affected by this |
| Volatility budgets per sleeve (Citadel) | Equalises risk across many uneven teams | Measured null: ΔSharpe −0.030 (interval −0.110 to +0.046). It would cut the US sleeve | **New**; do not copy |
| Mining thousands of signals "that make no sense" (Medallion) | Tens of thousands of independent bets a year can confirm a faint signal | At about 350–400 bets a year, a non-intuitive signal cannot be told from luck. New factors need t > 3 ([Harvey, Liu & Zhu](https://people.duke.edu/~charvey/Research/Published_Papers/P118_and_the_cross.PDF)) | Gated by Deflated Sharpe and PBO |
| A self-adapting model that retunes itself (Medallion's "adaptive" system, **[second-hand]**) | Continuous replacement of decaying edges | Self-tuning strategy knobs are out of bounds without Matt's say-so. The ML loop is scoped to FX models and roster only | Standing rule |
| HMM regime models as "the secret" | Nothing public shows Medallion uses them **[speculation]** | Out of sample, an HMM switch lifted S&P 500 Sharpe only from 0.48 to 0.54, at 141% turnover a year ([Shu, Yu & Mulvey](https://arxiv.org/pdf/2402.05272)). The existing filter already wins on Calmar | Not a priority |
| Size caps, capital returns, pass-through fees (both) | Capacity management at US$10–67 billion | We are bound by fixed costs, not capacity. Minimum viable sleeve size is our constraint | Not applicable |
| Switching to a PFOF broker for "free" execution | — | IBKR Lite was the worst account tested (−0.462% per tiny round trip). PFOF applies to US orders only ([Schwarz et al.](https://microstructure.exchange/papers/Schwartz_et_al_,_2022_WP,_The_'Actual_Retail_Price'_of_Equity_Trades.pdf)) | Not recommended |

None of this re-proposes the ideas the repo has already rejected: training the neural model longer, forward shadow confirmation as a promotion gate, FX holding-policy knobs, inverting agent signals, or an FX carry signal built on the hardcoded `swap_long_pips`.

## How this was produced and its limits

**Sources and checks.** Five research agents searched the public record on Medallion's history and methods, a quantitative reconstruction of Medallion-style trading, Citadel's hedge fund, Citadel Securities, and retail-accessible momentum improvements. A sixth set of measurements was run by the coordinating session on the real equity panel. I read all six notes in full, then checked what the report relies on:

- **Code facts.** Every code fact was opened and confirmed: `signals.py:15-18` and `:98`, `config.py:29`, `:116`, `:200-205`, `:269`, `execution_ibkr.py:173`, `paper_trade.py:12-13`, `forex/sessions.py:55`, `universes.py:27-47`, `regions.py:96-98`, `forex/crypto_exec.py:174`.
- **Doc claims.** The claims about `EFFECTIVE_PUBLIC_STRATEGIES.md` §12, `FULL_SYSTEM_REVIEW_2026-09.md` §3.1, §3.4, §5.2, §5.3 and §15, `SHARPE_RESEARCH.md` §6, §7, §8 and §10, `MONTE_CARLO_RESEARCH.md` §7, `PERMUTATION_TESTING.md` §7 and `HANDOFF_2026-09-24.md` were checked against the documents.
- **Measurements.** Headline numbers for all four sleeves were spot-checked against the raw output file (`lens_results.json`); I did not re-run the scripts. One small discrepancy turned up: `universes.py` lists 90 US stocks while the measurement used 89 (`MMC` returned no data). The coordinating session then re-checked every code reference, the doc quotations and the main outside figures (Cornell, the Senate subcommittee, KBRA, Risk.net, Novy-Marx & Velikov, Bogousslavsky & Muravyev) against their sources, and re-ran `us_split.py` from its saved location: identical output.

**Limits of our own numbers.**

- **Survivorship.** Every one of our numbers uses today's index members.
- **Gross diagnostics.** The signal, breadth, decay, null and residual-momentum figures are gross of costs and switch off the regime filter and vol targeting. They describe the signal, not the strategy.
- **Narrow tests.** One signal and one variant were tested, across four regions, with no tuning.
- **Sample size.** About 164 months is short. Standard errors are wide, and none of our per-sleeve hit rates is individually significant.
- **Approximations.** The 350–400 independent bets for the whole book uses an equal-correlation shortcut. The risk-budget test's interval (−0.110 to +0.046) means "no evidence of benefit", not proof of harm.

**Limits of the outside evidence.**

- **Medallion.** No audited return series exists. The headline record is Zuckerman's unaudited appendix, the "net" column is computed rather than reported, the "50.75%" is an anecdote with no definition, and the "12.5×" leverage passage was never located.
- **Citadel.** It publishes no volatility or Sharpe, and its per-manager drawdown numbers are private.
- **Citadel Securities.** Its revenue figures come from Bloomberg's unnamed sources.
- **Access.** Several sources were read only through search summaries or mirrors: the Senate staff report via a university mirror, citadel.com and some Bloomberg pages blocked or paywalled.
- **Transfer to our book.** Almost every published momentum result is long/short, broad-universe and gross. None tests a 10-name long-only book.

**Arguing the other side.** I argued the other side of each main finding; here is what survived:

- **The US null result.** The time-pairing test is conservative: momentum that genuinely persists more than 12 months is counted as null. A real but slow-moving US edge could be understated. It survives as "no evidence of US single-stock momentum *timing*", not "US momentum is worthless".
- **The hit-rate comparison.** The two hit rates are defined differently. It survives only as "same territory".
- **Long/short.** The US short-leg result is partly a survivorship artefact. The direction still matches the review's independent long/short measurement.
- **The correlation correction.** It survives cleanly. It is a known measurement effect and holds on three estimators.
- **Breadth.** The finding that list size does not buy breadth survives. ASX with 54 names and US with 124 give nearly the same effective bets, which is hard to explain any other way.

## Conclusion

The useful surprise is where the gap is *not*. Our per-bet skill and our costs (outside FTSE) are about what the published literature says 12-1 momentum allows. The binding limits are two numbers that none of the repo's dashboards shows: about 20 independent bets per region per month, and fewer than two independent regions. That reframes the roadmap. More stocks in the same markets and more frequent rebalancing both add almost nothing, while trading less often costs almost nothing. Real breadth can only come from a different signal or asset class. In the business-model sense this system is a small RIEF, a slow, scalable, widely known premium. It is not a Medallion, and trying to become one through leverage or speed would buy Medallion's fragility without its breadth.

The most transferable thing from both firms is how they *measure*, more than anything they trade. Citadel hedges out what it did not choose before crediting anyone with skill. Medallion counts only bets that are genuinely independent and makes every signal pay its own costs. Applied to our own data, that discipline found a US stock-picking edge that disappears under a fair test, a diversification benefit half as large as the docs claim, and a risk model that is wrong in two opposite directions. Each was invisible to the tests the repo already ran. The highest-value next steps are mostly already queued: point-in-time data, slower trading, per-sleeve breakers. This evidence reorders them by showing point-in-time data matters most for the US, slower trading matters most for FTSE, and breadth must come from a new signal rather than more names.

## How to reproduce

Every **[measured]** number comes from the repo's own loader and signal on live Yahoo data, so a re-run on a later date will differ slightly at the end of the sample. The scripts never touch paper-book state; the only file any of them writes is `measure_lens.py`'s own `scripts/medallion_lens/lens_results.json` (the committed copy is the 2026-10-13 run). Run from the repo root with no alert webhook set, so the loader's dead-ticker alert stays local:

```bash
D="scripts/medallion_lens"
env -u ALERT_WEBHOOK_URL PYTHONPATH=. python3 "$D/measure_lens.py"   # signal quality, breadth, decay, residual momentum, sleeve beta/alpha -> $D/lens_results.json
env -u ALERT_WEBHOOK_URL PYTHONPATH=. python3 "$D/null_check.py"     # time-pairing null vs signal shuffle
env -u ALERT_WEBHOOK_URL PYTHONPATH=. python3 "$D/null_robust.py"    # same null at 12/24/36-month gaps + 'answer key' correlation
env -u ALERT_WEBHOOK_URL PYTHONPATH=. python3 "$D/us_split.py"       # US stocks-only vs stocks+ETFs
env -u ALERT_WEBHOOK_URL PYTHONPATH=. python3 "$D/alloc_test.py"     # equal capital vs risk-budget sleeve weights
env -u ALERT_WEBHOOK_URL PYTHONPATH=. python3 "$D/corr_check.py"     # daily vs Dimson vs weekly vs monthly sleeve correlation
```

Random seeds are fixed in each script. `measure_lens.py` takes regions as arguments (default all four); run it with all four, because it overwrites `lens_results.json`.

## Sources

**Medallion and Renaissance: primary**
- Senate PSI report on basket options, 22 Jul 2014 (via mirror): http://math.nyu.edu/faculty/avellane/basket.pdf; original: https://www.hsgac.senate.gov/wp-content/uploads/imo/media/doc/REPORT-Abuse%20of%20Structured%20Financial%20Products%20(Basket%20Options)%20(7-22-14,%20updated%209-30-14).pdf
- Senate hearing record S. Hrg. 113-422: https://www.govinfo.gov/content/pkg/CHRG-113shrg89882/html/CHRG-113shrg89882.htm
- Bloomberg (Teitelbaum), Nov 2007, mirror: https://web.stanford.edu/class/archive/cs/cs224n/cs224n.1114/renaissance.html
- Cornell (2019), "Medallion Fund: The Ultimate Counterexample?": https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3504766; PDF: https://community.portfolio123.com/uploads/short-url/wZJco8G9Xd3diZqEN2YNc3RdaH.pdf
- Berlekamp autobiography: https://celebratio.org/Berlekamp_ER/article/843/
- Simons TED interview, 2015: https://www.ted.com/talks/jim_simons_the_mathematician_who_cracked_wall_street; transcript: https://singjupost.com/a-rare-interview-with-the-mathematician-who-cracked-wall-street-jim-simons-at-ted-full-transcript/
- Patterson podcast transcription: https://x.com/quantseeker/status/1879118660108693792; HN excerpt: https://news.ycombinator.com/item?id=19065226
- Peter Brown, Goldman Sachs podcast summary (2023): https://marcellus.in/story/a-conversation-with-renaissance-technologies-ceo-peter-brown/
- Reuters on the IRS settlement (2021), via Yahoo: https://finance.yahoo.com/news/renaissance-executives-pay-7-billion-231750167.html; Long Island Press: https://www.longislandpress.com/2021/09/06/how-long-island-execs-just-settled-the-biggest-tax-case-in-u-s-history/
- Accounting Today (2014): https://www.accountingtoday.com/news/renaissance-avoided-more-than-6-billion-tax-senators-say
- Institutional Investor 2021: https://www.institutionalinvestor.com/article/2bswms7wco7as686o8ikg/portfolio/renaissances-medallion-fund-surged-76-in-2020-but-funds-open-to-outsiders-tanked; 2024: https://www.institutionalinvestor.com/article/2e0uykr3vn5booz0smrcw/hedge-funds/renaissances-2024-rebirth

**Medallion and Renaissance: second-hand (Zuckerman, Mallaby, and reviews)**
- Novel Investor notes: https://novelinvestor.com/notes/the-man-who-solved-the-market-by-gregory-zuckerman/
- bagerbach notes: https://bagerbach.com/books/the-man-who-solved-the-market/
- twoquants Part 1: https://twoquants.com/the-man-who-solved-the-market-part-1/; Part 2: https://twoquants.com/the-man-who-solved-the-market-part-2/
- breakingthemarket Part I: https://breakingthemarket.com/the-greatest-geometric-balancers-renaissance-technologies-part-i/; Part II: https://breakingthemarket.com/the-greatest-geometric-balancers-renaissance-technologies-part-ii/
- Shortform overview: https://www.shortform.com/blog/the-man-who-solved-the-market-book/
- Christensen review: https://www.linkedin.com/pulse/review-man-who-solved-market-how-jim-simons-launched-hugh-christensen
- Leithner: https://www.chrisleithner.com/how-warren-buffett-has-trounced-the-worlds-greatest-hedge-fund-manager/
- AIER (2019): https://thedailyeconomy.org/article/jim-simons-and-his-quants/
- Wikipedia, Renaissance Technologies: https://en.wikipedia.org/wiki/Renaissance_Technologies
- Mallaby via A Wealth of Common Sense: https://awealthofcommonsense.com/2014/07/factor-investing-primer/
- QuantifiedStrategies (source of the "12.5×" claim): https://www.quantifiedstrategies.com/risk-and-reward-how-leverage-amplified-the-medallion-funds-gains/

**Citadel LLC**
- Risk.net, Hedge fund of the year 2023: https://www.risk.net/awards/7955800/hedge-fund-of-the-year-citadel
- Risk.net, Hedge fund of the year 2025: https://www.risk.net/awards/7960368/hedge-fund-of-the-year-citadel
- Risk.net, Ken Griffin lifetime achievement (2021): https://www.risk.net/awards/7755351/lifetime-achievement-award-ken-griffin
- KBRA rating affirmation (Apr 2025): https://www.kbra.com/publications/gdbMxFGn/kbra-affirms-the-issuer-ratings-for-citadel-s-flagship-multi-strategy-funds-and-the-rating-to-the-senior-unsecured-notes-issued-by-citadel-finance-llc
- Forbes (Jan 2023): https://www.forbes.com/sites/hanktucker/2023/01/22/citadels-16-billion-gain-in-2022-makes-ken-griffins-firm-the-top-earning-hedge-fund-ever/
- Hedgeweek on the bond prospectus: https://www.hedgeweek.com/citadel-prospectus-reveals-57bn-in-gains-from-largest-funds/
- Hedgeweek on pass-through fees: https://www.hedgeweek.com/hedge-funds-charging-billions-in-no-limit-passthrough-fees/
- Hedgeweek on March 2026: https://www.hedgeweek.com/citadels-global-fixed-income-strategy-hit-by-march-market-turmoil/
- CNBC on the US$5bn capital return, via Yahoo: https://finance.yahoo.com/news/citadel-return-5-billion-profits-155153589.html
- Investing.com relaying CNBC on 2025 returns: https://www.investing.com/news/stock-market-news/ken-griffins-citadel-flagship-fund-up-102-in-2025-4428022
- Net Interest, "Peak Pod": https://www.netinterest.co/p/peak-pod
- Resonanz Capital on quant unwinds: https://resonanzcapital.com/insights/crowding-deleveraging-a-manual-for-the-next-quant-unwind
- Folklore, cited only to flag it: https://youngandcalculated.substack.com/p/risk-management-inside-a-pod-how; https://hedgefundinterview.com/pod-shop-risk-limits

**Citadel Securities and market microstructure**
- SEC Release 34-96495 (Order Competition Rule proposal): https://www.sec.gov/files/rules/proposed/2022/34-96495.pdf
- SEC tick-size and access-fee rule fact sheet: https://www.sec.gov/files/34-101070-fact-sheet.pdf; exemptive relief: https://www.sec.gov/files/rules/exorders/2025/34-104172.pdf
- Hedgeweek on 2024 revenue: https://www.hedgeweek.com/griffins-citadel-securities-reports-record-9-7bn-trading-revenue/
- Benzinga on 2025 revenue: https://www.benzinga.com/markets/private-markets/26/03/51466069/citadel-securities-sees-record-breaking-12-billion-in-trading-revenue-in-2025
- Yahoo/Bloomberg on Q1 2026 and the term loan: https://finance.yahoo.com/markets/stocks/articles/citadel-securities-pushes-4-25-173119974.html
- Citadel Securities equities page: https://www.citadelsecurities.com/what-we-do/equities/; options page: https://www.citadelsecurities.com/what-we-do/options/
- Schwarz, Barber, Huang, Jorion & Odean, "The Actual Retail Price of Equity Trades": https://microstructure.exchange/papers/Schwartz_et_al_,_2022_WP,_The_'Actual_Retail_Price'_of_Equity_Trades.pdf; J. Finance: https://onlinelibrary.wiley.com/doi/10.1111/jofi.13467
- Dyhrberg, Shkilko & Werner: https://econpapers.repec.org/RePEc:ecl:ohidic:2022-14
- Bogousslavsky & Muravyev, "Who Trades at the Close?": https://static1.squarespace.com/static/6310c0b9bb63a25599f4418c/t/634ffc92f81e226b2c30654f/1666186387645/who-trades-at-the-close_June2021.pdf
- SSGA, "Closing time": https://www.ssga.com/us/en/institutional/insights/how-passive-investing-reshaping-microstructure
- BMLL on LSE closing auctions: https://www.bmlltech.com/news/market-insight/closing-auction-volumes-what-affects-closing-auction-volume-share
- ASX on closing auctions: https://www.asx.com.au/blog/finding-value-through-asx-closing-auctions
- TSX MOC FAQ: https://www.tsx.com/en/resource/1/
- GFXC report on last look: https://www.globalfxc.org/uploads/gfxc_report_last_look-1.pdf
- Binance fee schedule: https://www.binance.com/en/fee/schedule; Kraken fee schedule: https://www.kraken.com/features/fee-schedule

**Quantitative literature**
- Ding (2010), Fundamental Law: https://math.nyu.edu/inmemoriam/avellaneda/FundamentalLawFT.pdf
- Graham Capital, "How Good is Good Enough?": https://www.grahamcapital.com/quant-post/how-good-is-good-enough-some-math-behind-sharpe-information-and-hit-ratios/
- Clarke, de Silva & Sapra (2004), digest: https://www.hillsdaleinv.com/uploads/Toward_More_Information-Efficient_Portfolios,_Roger_G._Clarke,_Harindra_de_Silva,_Steven_Sapra,_The_Journal_of_Portfolio_Management,_Fall_2004,_Pages_54-63.pdf
- Kakushadze (2016), "101 Formulaic Alphas": https://arxiv.org/pdf/1601.00991
- Asness, Moskowitz & Pedersen (2013): https://w4.stern.nyu.edu/facdir/lpederse/papers/ValMomEverywhere.pdf
- McLean & Pontiff (2016): https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12365
- Khandani & Lo (2007): https://web.mit.edu/Alo/www/Papers/august07.pdf
- Avellaneda & Lee (2010): https://traders.studentorg.berkeley.edu/papers/Statistical%20arbitrage%20in%20the%20US%20equities%20market.pdf
- Novy-Marx & Velikov (2016): https://mysimon.rochester.edu/novy-marx/research/ToAatTC.pdf; NBER: https://www.nber.org/system/files/working_papers/w20721/w20721.pdf
- Tóth et al. (2011), square-root law: https://arxiv.org/pdf/1105.1694
- Frazzini, Israel & Moskowitz, "Trading Costs": https://spinup-000d1a-wp-offload-media.s3.amazonaws.com/faculty/wp-content/uploads/sites/3/2021/08/Trading-Cost.pdf
- Thorp (2006), Kelly criterion: https://gwern.net/doc/statistics/decision/2006-thorp.pdf
- Shu, Yu & Mulvey (2024), HMM and jump models: https://arxiv.org/pdf/2402.05272
- Harvey, Liu & Zhu (2016): https://people.duke.edu/~charvey/Research/Published_Papers/P118_and_the_cross.PDF
- Bailey & López de Prado, Deflated Sharpe: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
- Hanauer & Windmüller, "Enhanced Momentum Strategies": http://wp.lancs.ac.uk/mhf2019/files/2019/09/MHF-2019-076-Matthias-Hanauer.pdf
- Blitz, Huij & Martens (2011), residual momentum: https://econpapers.repec.org/RePEc:eee:empfin:v:18:y:2011:i:3:p:506-521
- Barroso & Santa-Clara (2015): http://www.snifferquant.com/gyantal/Incode/papers/Momentum%20Has%20Its%20Moments(scaling%20Momentum%20by%20vol),2014.pdf
- Barroso & Detzel (2021): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3088828
- Cederburg, O'Doherty, Wang & Yan (2020): https://www.sciencedirect.com/science/article/abs/pii/S0304405X2030132X
- Daniel & Moskowitz, "Momentum Crashes": https://www.nber.org/system/files/working_papers/w20439/w20439.pdf
- Israel & Moskowitz (2013): https://www.sciencedirect.com/science/article/pii/S0304405X12002401
- Fitzgibbons et al. (2017), "Don't Just Mix, Integrate": https://www.aqr.com/Insights/Research/White-Papers/Long-Only-Style-Investing
- Hoffstein, Faber & Braun, rebalance timing luck: https://www.panoptica.com/rebalance-timing-luck-the-dumb-timing-luck-of-smart-beta-by-hoffstein-faber-and-braun/?from_src=epsilontheory
- Lou, Polk & Skouras (2019): https://conference.nber.org/confer/2015/APf15/Lou_Polk_Skouras.pdf
- Blitz et al. (2023), short-term signals composite (Robeco summary): https://www.robeco.com/en-us/insights/2022/05/beyond-fama-french-alpha-from-short-term-signals

**This repo and the measurements**
- Measurements: `docs/research/medallion-citadel/our_system_measurements.md`; raw output and scripts in `scripts/medallion_lens/`: `lens_results.json`, `measure_lens.py`, `null_check.py`, `null_robust.py`, `us_split.py`, `alloc_test.py`, `corr_check.py`
- `docs/FULL_SYSTEM_REVIEW_2026-09.md` (§3.1, §3.4, §5.2, §5.3, §15)
- `docs/EFFECTIVE_PUBLIC_STRATEGIES.md` (§12)
- `docs/SHARPE_RESEARCH.md` (§6, §7, §8, §10)
- `docs/MONTE_CARLO_RESEARCH.md` (§7)
- `docs/PERMUTATION_TESTING.md` (§7)
- `docs/HANDOFF_2026-09-24.md` (locked decisions D1–D4)
- `docs/HFT_REALITY.md`
- Code: `trading_algo/signals.py`, `config.py`, `execution_ibkr.py`, `paper_trade.py`, `universes.py`, `regions.py`, `forex/sessions.py`, `forex/crypto_exec.py`
