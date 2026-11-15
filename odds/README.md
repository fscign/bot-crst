# Odds watch: value bets and arbitrage

Compares bookmaker prices and alerts on Telegram when one of your bookmakers pays more than the fair price.
It gives information only. It never places bets, and no bet is certain to win.

## How it works

| Part | Where it runs | What it does |
|---|---|---|
| Scanner (`odds/`) | GitHub Actions, every 4 hours | Fetches prices from The Odds API, works out fair prices from Pinnacle and the Betfair Exchange, finds value bets and arbitrage at your bookmakers, sends Telegram alerts, keeps a paper-bet log |
| Dashboard (`odds-worker/`) | Cloudflare Workers | Shows the alerts, every scanned game with its fair price, and the paper test |

A **value bet** is a price at your bookmaker at least 3% above the fair price. The fair price is Pinnacle's price with the
margin removed (Betfair Exchange midpoint as a second opinion). **Arbitrage** is when your bookmakers disagree enough that
backing every outcome returns more than the total stake.

Covered automatically: **Coolbet** and **Betsson** (same group as Betsafe; confirm the price on betsafe.ee).
**Olybet** and **Optibet** are not in the data feed: on the dashboard's "All games" tab, compare their price with the
"Value from" column by hand.

## Setup (10 minutes, on top of the trading bot setup)

It reuses the Cloudflare, Telegram, `DASH_PASSWORD` and `INGEST_TOKEN` settings the trading bot already has.

1. Get a free key at https://the-odds-api.com (500 credits a month, no card needed).
2. In this repo: Settings, Secrets and variables, Actions.
   - **Secrets** tab: add `ODDS_API_KEY` with the key.
   - **Variables** tab: add `ODDS_DOMAIN` with the address for this dashboard, for example `odds.yourdomain.com`
     (no https://). It must be a different address from the trading dashboard.
3. Actions tab: run **Deploy odds dashboard**, wait for the green tick.
4. Run **Odds scan**. Open `https://<your ODDS_DOMAIN>` and sign in with the same password as the trading dashboard.

After that it scans by itself every 4 hours.

## The free plan and when to upgrade

Each league costs 1 credit per scan. With 500 credits a month the scanner can fetch about 2 leagues per run, so it
rotates: leagues with games in the next 36 hours, longest-unscanned first. It spreads the credits so they last the month.
Settling paper bets costs 2 credits per league per day, only on days with finished bets.

That is enough to see whether the alerts are real. It is not enough to catch most of them, because good prices often last
minutes. If the paper test looks good, the 20,000-credit plan (30 USD a month) allows every league every 15 to 30 minutes:

1. In `odds/config.json` set `"monthly_credits": 20000` and `"runs_per_day": 48`.
2. In `.github/workflows/odds-scan.yml` change the schedule to `"7,37 * * * *"`.
3. Optional: `"markets": ["h2h", "totals"]` adds over/under (doubles the cost per league).

If your plan renews on a day other than the 1st, set `"quota_reset_day"` to that day.

## Settings (`odds/config.json`)

| Setting | Meaning |
|---|---|
| `my_books` | Bookmakers you can bet at (keys from The Odds API). Value and arbitrage alerts only use these |
| `leagues` | Leagues to watch, in The Odds API's names. Out-of-season leagues cost nothing |
| `min_edge` / `max_edge` | Alert between 3% and 15% above the fair price. Bigger gaps are nearly always stale or mistaken prices |
| `min_odds` / `max_odds` | Only prices from 1.30 to 6.00 |
| `max_age_min` | Ignore any price the feed has not refreshed in the last 20 minutes |
| `paper_bankroll`, `kelly_fraction`, `max_stake_pct` | Paper test: 1,000 EUR, quarter-Kelly stakes, at most 2% per bet |

## Reading the paper test

Every value alert becomes a paper bet at the alerted price. Two numbers matter:

- **Closing line value**: the alerted price against the last fair price before the game starts. If the average stays
  above zero over 100+ bets, the alerts are finding real value. This is the early, reliable signal.
- **Return on stakes**: needs a few hundred bets before it means anything. Losing runs of 10+ bets are normal.

## Limits to know about

- The feed is a few minutes behind the bookmakers. Always confirm the price on the site before betting.
- Bookmakers limit or close accounts that keep taking value prices.
- Pinnacle and Betfair are used as price references only.
- Winnings from operators licensed in Estonia are tax-free; check the tax board's list before using any other operator.

`python tests/test_odds.py` checks the maths. `python odds/scan.py --offline tests/odds_fixture.json` runs a scan on
sample data without using credits.
