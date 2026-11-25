# Handover for Claude sessions

Owner: Aleksandrs (Tallinn, Estonia). Not a programmer, but analytical and fluent with Excel. Explain changes in plain
words, give click-by-click steps for anything he has to do in GitHub or Cloudflare, and never ask him to edit code.
No em dashes in text written for him (use commas or parentheses).

This repo holds two independent tools. Ask which one is meant if it is unclear, and never change the other one by accident.

| Tool | Folders and workflows | Docs |
|---|---|---|
| Trading signal bot (crypto and stocks) | `scanner/`, `worker/`, `research/`, `data/`, `scan.yml`, `deploy.yml`, `research*.yml`, `universe.yml`, `diagnose.yml` | `README.md`, `MAINTENANCE.md` |
| **Odds watch** (sports betting value bets and arbitrage) | `odds/`, `odds-worker/`, `tests/test_odds.py`, `tests/odds_fixture.json`, `odds-scan.yml`, `odds-deploy.yml` | `odds/README.md` |

The trading bot section comes first, then Odds watch.

# Trading signal bot

Read `MAINTENANCE.md` (how it works, upkeep) and `research/RESULTS.md` (what was tested and why the rules are what they
are) before changing anything. A signal and paper-trading bot: it never places real trades.

## How it runs
- **Scanner** (`scanner/scan.py`) in GitHub Actions (`scan.yml`), started every 15 minutes by the dashboard Worker's
  cron through the GitHub API (`GH_DISPATCH_TOKEN`); hourly GitHub schedule as fallback. Scores about 170 crypto
  pairs and about 700 US and European stocks on 1h, 4h and 1d, gathers market context (`scanner/context.py`), runs
  the paper accounts (`scanner/paper.py`), reads Telegram (`scanner/tg_signals.py`) and posts everything to the dashboard.
- **Dashboard** (`worker/`, Cloudflare Worker `bot-crst`, KV) at `https://bot-crst.ahorsun.workers.dev`, password
  `DASH_PASSWORD`. Paper state is in KV key `paper`; the scanner reads it via `/api/paper` and posts back to `/api/ingest`
  with `INGEST_TOKEN`. Also: real-position tracking checked every minute, Telegram alerts for every paper trade, a
  weekly report (Sundays 17:00 UTC) and a warning if no scan arrives for 3 hours.
- **Research** (`research/`) re-runs monthly (1st of the month); `pause_stale_rules()` pauses a paper rule whose kind
  of trade no longer shows a profit.
- `diagnose.yml` (run by hand) prints all paper trades to its log; use it to investigate results. Repo and logs are
  PUBLIC: never print Telegram post text or secrets in logs (counts only).

## Paper accounts (all rules in `scanner/config.json`)
1. **Stocks (200-day trend)**: buy after 2 daily closes above the 200-day SIMPLE average, sell after 2 closes below.
   US risk 0.75% per trade, Europe 0.5%, no leverage. Reset by Aleksandrs on 23 Nov 2026 after the fix below.
   On 25 Nov: 18 open trades, equity 1,017. Expect about 1 winner in 4.
2. **Crypto (bot rule + Telegram)**, one account, two sources, dashboard shows results by source:
   - Bot rule (experiment): daily volatility breakout on the top 80 coins, only while Bitcoin is higher than 30 days
     ago (`btc_30d_up`). That gate has been False since mid-November, so no rule trades; this is intended.
   - Telegram feed: the owner's two channels by the same author, read as ONE feed (a coin posted in both opens one
     trade): `-1001299359703` "Miloslavski" and `-1002106711314` "📈". Private, so read through a separate reader
     account (Telethon; secrets `TG_API_ID`, `TG_API_HASH`, `TG_SESSION`) that Aleksandrs set up on a spare SIM.
     Trades: entry in zone or limit order (24 h), 1% risk to the posted stop (5% default), equal part off at each
     target, stop to entry after target 1, 7-day limit, "close" posts honoured.

## How the author posts signals (solved 25 Nov 2026)
His signals are **screenshots of his own open position** in an exchange app (coin, Short/Long, leverage, Entry Price,
Mark Price, Est. Liq. Price, "TP/SL Entire position: 71.000/--"), with a short caption such as "присоединяйтесь"
("join in"). Text-only posts are commentary. The reader downloads pictures of NEW posts, reads them with RapidOCR
(`scanner/ocr.py`, pip `rapidocr-onnxruntime`) and `parse_position_screenshot` turns them into a signal that is joined
at the live price (`market: True`). No stop in his screenshots usually, so the 5% default stop applies.
Check after the next posts: the scan log line "(N new pictures read), M look like trade signals", and on the dashboard
the "Telegram signals seen" and "Posts the bot could not read" lists. If his app shows a closed-position screenshot,
`parse_position_screenshot` treats "Closed"/"Close price" without "Mark Price" as a close instruction; confirm with a
real example when one appears. Never commit his screenshots or their text to this public repo.

## Lessons that must not be lost
- Live bug (Nov 2026): the 200-day EXPONENTIAL average depended on how much history Yahoo returned, so it flipped
  between scans and created ~20 fake trades. Daily rules must use stable inputs and decide once per daily bar
  (`entry_bar`, `last_exit_bar` in `paper.py`). Never sell on the purchase bar or rebuy on the sale bar.
- Test new rules in `research/`: choose on older data, judge on the latest quarter, compare with random entries using
  the same exit, measure profit per day held. Two earlier "winners" failed exactly these checks. No rule predicts crypto.
- Aleksandrs wants a profitable, smart bot; be honest that nothing here has been shown to beat an index fund.

## Working on it
- `python tests/test_paper.py` and `python tests/test_tg.py` after every change (no network).
- `python scanner/scan.py --demo` runs offline on random data. `cd worker && npx wrangler dev --local` (needs
  `worker/.dev.vars` with `INGEST_TOKEN` and `DASH_PASSWORD`) serves the dashboard locally. Delete `data/paper.json`,
  `data/latest.json` and `.wrangler` afterwards.
- Push to `main`; the dashboard deploys itself (`deploy.yml`). To test a scan: run "Scan markets" and read its log.

# Odds watch

## Odds watch: what it is

It compares bookmaker prices and alerts on Telegram when a bookmaker Aleksandrs can use in Estonia pays more than the
fair price. It gives information only and never places bets. It does not predict results: the edge comes from
(1) knowing the fair price from sharp markets, (2) catching slow bookmakers before they correct, (3) measuring honestly
with a paper test. Keep it that way; do not add "AI prediction" features that claim to beat the market.

## How it runs

- **Scanner** (`odds/scan.py`, maths in `odds/engine.py`, settings in `odds/config.json`) runs in GitHub Actions
  (`odds-scan.yml`). It fetches The Odds API (`regions=eu`, about 20 bookmakers, 1 credit per league per market),
  analyses, sends Telegram, and posts the state to the dashboard.
- **Dashboard** (`odds-worker/`, Cloudflare Worker `bot-odds`, KV namespace `bot-odds-data`) at
  `https://bot-odds.<cloudflare-subdomain>.workers.dev`, password `DASH_PASSWORD` (same as the trading dashboard).
  It stores the state in KV and serves `/api/state`. Deployed by `odds-deploy.yml` on every push touching `odds-worker/`.
- **Hourly timer**: the Worker's Cloudflare cron (`17 * * * *`) starts `odds-scan.yml` through the GitHub API with the
  secret `GH_DISPATCH_TOKEN` (shared with the trading bot). GitHub's own schedule is only a fallback and skips itself
  if a scan ran in the last 40 minutes (`SKIP_IF_RECENT`). Reason: GitHub ran only 19 of ~144 scheduled runs in a week.
- **State** lives only in Cloudflare KV (key `state`; results entered by hand in key `manual`). The scanner loads it from
  `/api/state` with `INGEST_TOKEN` and posts it back to `/api/ingest`. `odds/state.json` exists only for offline runs.
- The scanner finds the dashboard address itself from `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` when
  `ODDS_DOMAIN` is a workers.dev address.

Secrets used: `ODDS_API_KEY`, `INGEST_TOKEN`, `DASH_PASSWORD`, `TG_TOKEN`, `TG_CHAT`, `CLOUDFLARE_API_TOKEN`,
`CLOUDFLARE_ACCOUNT_ID`, `GH_DISPATCH_TOKEN`. Variable or secret: `ODDS_DOMAIN`.

## The method (engine.py)

- **Fair price**: Pinnacle with margin removed by the power method, and the Betfair Exchange / Matchbook midpoint when
  the back/lay spread is under 5%. With several references the fair probability is their average, and a value bet must
  also be positive against each reference alone.
- **Fallback**: no sharp price, then the median de-vigged price of at least 6 other bookmakers. Stricter: 6% minimum
  edge instead of 3%, half stake. Alerts say so.
- **Value bet**: one of `my_books` (Coolbet, Betsson as proxy for Betsafe.ee, Unibet Sweden as proxy for Unibet.ee)
  pays 3% to 15% above fair, odds 1.30 to 6.00, both prices refreshed in the last 20 minutes, game at least 10 minutes away.
  Above 15% is treated as a stale or wrong price and dropped.
- **Arbitrage**: best prices across `my_books` (at least two different bookmakers) return more than the stake.
- A bookmaker quoting a different set of outcomes (3-way vs 2-way, common in hockey) is never compared.
- Alerts carry: rank among all bookmakers in the feed, deep link when available, and the fair-price trend since the last
  scan ("toward" the bet = the bookmaker has not followed a sharp move yet, the strongest kind).
- **Scheduling** (`plan_scan`): credits are spread over the days to the monthly reset (The Odds API resets on the 1st).
  Leagues with a game in the next 4 hours ("hot") come first and may use up to 3x a run's share; others refresh about
  every 12 hours; unspent share of quiet hours carries over. It cannot overspend: it recalculates from the real balance.
- **Paper test**: every value alert becomes a paper bet (1,000 EUR start, quarter Kelly, max 2% per bet), judged first
  by closing line value (alerted price vs last fair price before kick-off), then by profit after a few hundred bets.
  The dashboard "What is working" table splits results by bookmaker, league, edge size, price source and trend.
- **Settling** uses the scores endpoint (2 credits per league per day, only when needed). `icehockey_liiga` has no
  results feed (`no_scores` in config): its bets close as "unscored" 6 hours after the start, and Aleksandrs enters the
  result with Won / Lost / Void buttons on the dashboard.

## Coverage

24 leagues in `odds/config.json`: top 5 European football leagues, Champions/Europa/Conference League, Nations League,
Eredivisie, Portugal, Turkey, Belgium, Scotland, English Championship, FA Cup, Copa del Rey, Coppa Italia, DFB-Pokal,
NBA, EuroLeague, NHL, Liiga, SHL. Not in the feed at all: national basketball leagues (ACB, Lega, BBL, VTB, Latvian-Estonian),
KHL, DEL, Swiss and Czech hockey. Bookmakers licensed in Estonia but not in the feed (checked by hand against the
"Value from" column): Olybet, Optibet, Bet365, Paf, Tonybet.

## Decisions Aleksandrs made

- Alerts: value bets, arbitrage and a best-price table.
- Bookmakers he has or will open: Coolbet, Olybet, Betsafe, Optibet (Unibet added later as a feed proxy; remove
  `unibet_se` from `my_books` if he has no Unibet account).
- Free data plan (500 credits a month) to start; he is open to the 30 USD plan (20,000 credits) if the paper test
  shows value. Upgrade steps are in `odds/README.md`.
- Sports chosen by default when he did not pick: football, basketball, hockey. Tennis left out.

## Open points

- Judge the alerts by average closing line value after about 100 paper bets before suggesting real money or the paid plan.
- `GH_DISPATCH_TOKEN` expires on the date set when it was created; when it does, both tools stop starting on time.
- On the free plan many good prices are missed because they last minutes; the paid plan is the main upgrade.

## Working on it

- Run `python tests/test_odds.py` after every change (11 tests, no network) and
  `python odds/scan.py --offline tests/odds_fixture.json` for a dry run that uses no credits; delete `odds/state.json` after.
- The dashboard page is plain JS built by wrangler, which wraps functions in `__name()`; the page defines `__name`, keep it.
- Push to `main`; the dashboard deploys by itself. To test a scan, run the "Odds scan" workflow and read its log.
- The trading bot commits to `main` often: fetch and rebase before pushing.
