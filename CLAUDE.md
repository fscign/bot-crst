# Handover for Claude sessions

Owner: Aleksandrs (Tallinn, Estonia). Not a programmer, but analytical and fluent with Excel. Explain changes in plain
words, give click-by-click steps for anything he has to do in GitHub or Cloudflare, and never ask him to edit code.
No em dashes in text written for him (use commas or parentheses).

This repo holds two independent tools. Ask which one is meant if it is unclear, and never change the other one by accident.

| Tool | Folders and workflows | Docs |
|---|---|---|
| Trading signal bot (crypto and stocks) | `scanner/`, `worker/`, `research/`, `data/`, `scan.yml`, `deploy.yml`, `research*.yml`, `universe.yml`, `diagnose.yml` | `README.md`, `MAINTENANCE.md` |
| **Odds watch** (sports betting value bets and arbitrage) | `odds/`, `odds-worker/`, `tests/test_odds.py`, `tests/odds_fixture.json`, `odds-scan.yml`, `odds-deploy.yml` | `odds/README.md` |

The rest of this file is about Odds watch.

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
