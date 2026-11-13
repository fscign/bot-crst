# Research backtest: what actually made money

Run on 13 Nov 2026 by `research/backtest.py` (GitHub Actions). 1.3 million simulated trades, net of fees.
Full numbers: `research/results.csv`. A second, crypto-only round is further down.

## What was tested
- **Data:** 80 most traded crypto pairs (1h: 1 year, 4h: 2 years, 1d: 4 years); 600 US and 100 European stocks
  (1d: 5 years); 97 US stocks on 1h and 4h (2 years).
- **Entries:** the bot's score crossing 40, 55 or 70; 20-bar breakouts in a trend; buying dips in a trend;
  and random entries as a no-skill baseline.
- **Exits:** stop + target (the bot's original rule), trailing stop only, half off at +1R, and a quick +1R target.
- **Filters:** market trend (Bitcoin or S&P 500 above its 200-day average), and "worked before on this asset".
- **Honesty check:** history is split in two. A rule counts only if it made money in the older 60% AND in the
  recent 40%, which it was not tuned on.

Results are in R: +1R means a trade made as much as it risked to its stop.

## Findings
1. **The bot's original rules lost money almost everywhere.** Strong signals with stop + target averaged
   between -0.02R and -0.33R per trade depending on the market.
2. **Crypto: no rule worked.** Not one entry/exit/filter combination was profitable in both halves on any
   timeframe. On 1h, fees alone cost about 0.3R per trade (random entries: -0.30R).
3. **Short selling stocks lost heavily** on every timeframe (-0.15R to -0.30R per trade).
4. **What held up: buying stocks on daily signals and letting winners run.**

| Rule (long only, trailing stop, no target) | Older period | Recent period | Trades |
|---|---|---|---|
| US stocks, daily, score crosses +40 | +0.12R | +0.13R | 12,857 |
| European stocks, daily, score crosses +40 | +0.16R | +0.12R | 2,425 |
| US stocks, 4h, score crosses +40 | +0.20R | +0.09R (weaker evidence) | 2,930 |

5. **Trailing beat partial profit-taking.** Selling half at +1R raised the win rate (51% against 38%) but cut
   the average result by about a third (+0.09R against +0.13R). The big winners pay for everything.
6. **The filters did not help.** Neither the market-trend filter nor "worked before on this asset" improved
   results in the recent period.

## Caveats
- Stocks rose over these 5 years. Random long entries with the same trailing exit made +0.08R per trade in the
  recent period, so most of the profit is the market itself; the signal adds roughly +0.05R on top.
  This has not been shown to beat simply holding an index fund.
- No slippage beyond the fee, no overnight-gap modelling beyond daily bars, and the stock list is today's
  liquid names (survivorship bias flatters the results).
- A falling market would look different. The rule is long-only.

## Crypto, round 2 (13 Nov 2026)

A wider search just for crypto, in `research/crypto.py`: 556,000 simulated trades on up to 6 years of data.
Files: `crypto_trades.csv`, `crypto_trades_pit.csv`, `crypto_portfolio.csv`.

- **Tested:** 20 and 55-bar breakouts, moving-average trend following, buying sharp dips (RSI-2, Bollinger),
  volatility breakouts, two short rules, each with tight and wide trailing exits, with and without a
  "Bitcoin above its 200-day average" filter, at three fee levels (0.10%, 0.05%, 0.02% per side), on 1h, 4h and 1d.
  Plus 30 portfolio rules (coins in an uptrend, recent winners, recent losers, Bitcoin-gated baskets).
- **Method:** three periods. Rules were chosen on the first two; the last one (about the latest two years) was a
  holdout used only to judge them.

Findings:
1. **1h and 4h: nothing survived the holdout.** Every rule that looked good in the first two periods lost money
   in the third, even at the lowest fee level.
2. **Daily breakouts looked like a winner, then failed the bias check.** On the 80 coins most traded today they
   made +0.3R to +0.4R per trade in all three periods. But "most traded today" quietly selects coins that did
   well recently. Re-run on a point-in-time universe (671 coins; a coin can only be traded while it was in the
   top 80 by volume over the previous 30 days), the same rules made +0.4R to +0.6R in 2019 to 2022 and about
   zero since (+0.01R in the holdout).
3. **Trend rules lose less, they do not win.** In the holdout, random long entries lost about 0.24R per trade and
   breakout entries about broke even. Holding Bitcoin only while it is above its 100-day average returned 56% a
   year over the whole 7.5 years against 46% for holding, with a worst drawdown of 39% against 77%, but it still
   lost 7% a year in the last two years (holding lost 16%).
4. **Buying dips and shorting rallies:** small positive numbers in places, negative after fees on 1h and 4h.

Conclusion: no simple chart-based rule was found that reliably makes money in crypto. The honest best case is
daily trend following, which made money in the 2019 to 2022 bull markets and has roughly broken even since.

## What the bot does with this
- The paper test trades only the three rules above, risking 0.75%, 0.5% and 0.25% of the account per trade
  (more where the evidence is stronger), without leverage, total exposure capped at 1.5x the account.
- Every signal card shows the backtest verdict for its kind, and the dashboard hides kinds that lost money
  unless you untick the filter.
- Re-run the research any time: Actions tab, "Research backtest", Run workflow.
