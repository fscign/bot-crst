# Handover notes: how this bot works and how to keep it running

Written 15 Nov 2026 for whoever maintains this next (a person or another AI model). Read this first.

## What it is
A signal and paper-trading bot. It never places real trades.
- **Scanner** (`scanner/`, Python, GitHub Actions): every 15 minutes scores about 170 crypto pairs and 700 stocks,
  gathers market context, runs the paper accounts, and posts the result to the dashboard.
- **Dashboard** (`worker/`, Cloudflare Worker + KV): shows signals, context, paper accounts and the owner's real
  positions; checks real positions every minute; sends Telegram messages; starts each scan on GitHub.
- **Research** (`research/`): backtests that decide which rules the paper accounts may trade. Findings: `research/RESULTS.md`.

## The findings that drive everything (do not skip)
1. No tested rule predicts crypto. Short timeframes lose to fees. Daily trend rules made money until 2022, about zero since.
2. On stocks, entry signals do not beat random entries. Profit comes from being invested in stocks that are in an
   uptrend. The best consistent rule is the 200-day trend (buy on a close above the 200-day average, sell on a close below).
3. Market context (Fear & Greed, S&P 500, VIX, dollar, yields, stablecoins) did not help reliably. One exception:
   crypto breakouts opened while Bitcoin was lower than 30 days earlier lost in every period.
4. Partial profit-taking and tight targets made results worse. Short selling lost.

If someone proposes a new rule, the bar is: test it in `research/` on a bias-free universe, choose on the older
data, judge on the latest quarter, compare with **random entries using the same exit**, and measure profit **per day
held**. Rules that only beat zero in a rising market are not an edge. Two earlier "winners" died on exactly these checks.

## Where things are set
- `scanner/config.json`: everything tunable. `paper.rules` (stock account), `paper.accounts.crypto` (experiment),
  `account` (sizes shown on signal cards), `universe`, `context_note`.
- `scanner/paper.py`: the paper engine (entries, sizing, exits, accounts). `tests/test_paper.py` checks its maths:
  run `python tests/test_paper.py` after any change.
- `scanner/scan.py`: data, scoring, setups (`setups()`), verdicts (`load_verdicts()`), the self-check (`pause_stale_rules()`).
- `scanner/context.py`: market context sources and flags.
- `worker/src/index.js` and `page.js`: dashboard backend and page.
- GitHub secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `DASH_PASSWORD`, `INGEST_TOKEN`, `TG_TOKEN`,
  `TG_CHAT`, `DASH_DOMAIN`, `GH_DISPATCH_TOKEN`.

## What runs by itself
| What | When | Where to look if it stops |
|---|---|---|
| Market scan | every 15 minutes (started by the dashboard) | Actions, "Scan markets" |
| Stock list refresh | nightly | Actions, "Update stock list" |
| Research re-run and rule self-check | 1st of each month | Actions, "Research backtest" |
| Weekly report | Sundays 17:00 UTC, Telegram | Cloudflare, Workers, bot-crst, Logs |
| Stalled-scan warning | Telegram, when no scan for 3 hours | usually an expired `GH_DISPATCH_TOKEN` |

## Routine upkeep
- **`GH_DISPATCH_TOKEN` expires.** Renew it on GitHub (fine-grained token, this repo only, Actions: read and write),
  update the secret, run "Deploy dashboard". Until then scans fall back to once an hour.
- **GitHub disables schedules after 60 days without repo activity** on public repos. The nightly stock-list commit
  normally keeps it active.
- **Testing changes:** `python scanner/scan.py --demo` runs offline on random data and writes `data/latest.json` and
  `data/paper.json`. `cd worker && npx wrangler dev --local` serves the dashboard locally (needs `.dev.vars` with
  `INGEST_TOKEN` and `DASH_PASSWORD`).
- **Changing paper rules does not reset the accounts.** Open trades keep the parameters they were opened with.
  "Reset the test" on the dashboard clears everything.

## Known limits
- Free data: stock prices are delayed about 15 minutes; Binance futures data (funding) is blocked from GitHub's servers.
- A stock that drops out of the daily stock list while held is no longer scanned, so its "close below the 200-day
  average" exit cannot fire. Its safety stop and time limit still apply.
- Backtests ignore slippage and use today's liquid stocks (survivorship bias). Real results will be worse.
- Headline sentiment is word counting. An AI news reader was considered and not built (needs a paid API key,
  cannot be backtested).

## Before real money
Wait for at least 50 closed paper trades per account and compare the average result with the backtest expectation
in the weekly report. Nothing here has been shown to beat simply holding an index fund.
