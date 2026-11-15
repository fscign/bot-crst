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

## Crypto, round 3: does market context help? (15 Nov 2026)

`research/context_research.py`, files `context_trades.csv` and `context_btc.csv`. The daily crypto rules from round 2
(bias-free coin universe, about 7 years) were split by what the world looked like the day before each trade:
17 yes/no conditions covering Bitcoin's own trend, Fear & Greed, the S&P 500, VIX, the dollar index, the US 10-year
yield and stablecoin supply. Futures funding could not be tested (Binance blocks that data from GitHub's servers).
Conditions were chosen on the first two periods and judged on the third.

1. **Most context did not help.** Fear & Greed, the S&P 500 trend, the dollar, yields and stablecoin growth each
   improved results in one period and hurt in another.
2. **One condition was consistent: Bitcoin's own 30-day direction.** Volatility-breakout trades opened while Bitcoin
   was higher than 30 days earlier averaged +0.69R, +0.22R and +0.11R in the three periods. Opened while it was
   lower, they averaged -0.03R, -0.04R and -0.22R. Same pattern for the other breakout rules.
3. **High VIX** (above 20) also went with better trades in all three periods, but on only 84 trades in the last
   one, so it is not used.
4. **As a Bitcoin on/off switch, nothing beat holding in all three periods.** The best switches (Bitcoin higher than
   30 days ago, or above its 100-day average) lost far less in bad years (worst drop 47% and 39% against 77%) and
   returned more over the whole 7.5 years, but gave up part of the 2023 to 2024 rally.

With 34 condition states tested, one consistent result can also be luck. The effect is modest: even with the
filter, the latest period is only slightly positive.

**Applied:** the experimental crypto account only opens trades while Bitcoin is higher than 30 days ago.

## Stocks, round 4: can the stock rule be improved? (15 Nov 2026)

`research/stocks.py`, files `stock_trades.csv` and `stock_efficiency.csv`. Daily bars, 10 years, 600 US and about
110 European stocks, 430,000 trades, three periods (the latest, mid-2024 to now, as holdout). New this round: profit
is measured **per day in the market**, so rules that hold for different lengths of time can be compared fairly.

1. **The score-based rule was worse than random.** Per day held, buying when the score crossed +40 earned
   0.05%, 0.03% and 0.06% in the three periods (US). Buying on random days with the same exit earned 0.07%, 0.08%
   and 0.09%. The round 1 conclusion "this signal has an edge" was wrong: its profit was the market's own rise.
2. **Stock entries do not matter much; staying in does.** Wider stops and longer holds beat tight ones in every
   period and both markets, mostly because they stay invested in a rising market.
3. **Best consistent rule: the 200-day trend.** Buy when the daily close crosses above the stock's 200-day
   average, sell when it closes below. Per day held (US): 0.08%, 0.09%, 0.13%; Europe: 0.04%, 0.11%, 0.07%.
   That is better than the old rule in all six cases, and equal to or better than random entries.
   Only about 1 trade in 5 wins: many small losses, a few large gains. Average hold about 30 trading days.
4. **Buying sharp dips in an uptrend** (close 2.5 standard deviations below the 20-day average) also did well per
   day held (65 to 70% winners), but trades are rare and short, so money would sit idle most of the time. Not used yet.

What this means: on stocks the bot earns roughly what the market gives while a stock is in an uptrend, and
steps aside when it is not. It has not been shown to beat an index fund, and the stock list is today's liquid
names over a mostly rising decade, which flatters every number here.

**Applied:** the stock paper account now trades the 200-day trend rule (US 0.75% risk, Europe 0.5%).
The old score rules and the 4h rule were removed. Trades already open under the old rules run to their own exits.

## What the bot does with this
- **Stock account:** the 200-day trend rule from round 4, without leverage, total exposure capped at 1.5x the account.
- **Crypto account (experiment):** daily volatility breakouts on the 80 most traded coins, only while Bitcoin is
  higher than 30 days ago (rounds 2 and 3). Expect roughly break-even.
- **Self-check:** the research re-runs on the 1st of every month. A rule whose kind of trade no longer shows a profit
  in both the older and the latest part of history is paused automatically (no new trades) until it recovers.
- Signal cards show the round 1 verdict for their kind. Read "made money" as "made money in a rising market".
- Re-run by hand: Actions tab, "Research backtest", Run workflow.
