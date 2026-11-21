# Odds watch: value bets and arbitrage

Compares bookmaker prices and alerts on Telegram when one of your bookmakers pays more than the fair price.
It gives information only. It never places bets, and no bet is certain to win.

## How it works

| Part | Where it runs | What it does |
|---|---|---|
| Scanner (`odds/`) | GitHub Actions, every hour | Fetches prices from about 20 European bookmakers via The Odds API, works out fair prices, finds value bets and arbitrage at your bookmakers, sends Telegram alerts, keeps a paper-bet log |
| Dashboard (`odds-worker/`) | Cloudflare Workers | Shows the alerts, every scanned game with its fair price, and the paper test |

A **value bet** is a price at your bookmaker at least 3% above the fair price. The fair price is the average of Pinnacle
(margin removed) and the Betfair Exchange midpoint, and the bet must also be value against each of them alone. When neither
has a price, the median of the other European bookmakers is used instead, with a 6% bar and half the stake.
**Arbitrage** is when your bookmakers disagree enough that backing every outcome returns more than the total stake.
Each alert also says where the price ranks among all bookmakers in the feed, and links to the bet where the feed has a link.

Covered automatically: **Coolbet**, **Betsson** (same group as Betsafe; confirm the price on betsafe.ee) and **Unibet**
(the feed has Unibet's Swedish site; confirm the price on unibet.ee). No Unibet account? Remove `unibet_se` from
`my_books` in `odds/config.json`.
**Olybet**, **Optibet**, **Bet365**, **Paf** and **Tonybet** are licensed in Estonia but not in the data feed: on the
dashboard's "All games" tab, compare their price with the "Value from" column by hand.

Each value alert also says how the fair price moved since the previous scan. "Shortened towards the bet" means the sharp
market moved and the bookmaker has not followed yet, the strongest kind of alert. The paper test tracks these separately.

## Setup (10 minutes, on top of the trading bot setup)

It reuses the Cloudflare, Telegram, `DASH_PASSWORD` and `INGEST_TOKEN` settings the trading bot already has.

1. Get a free key at https://the-odds-api.com (500 credits a month, no card needed).
2. In this repo: Settings, Secrets and variables, Actions.
   - **Secrets** tab: add `ODDS_API_KEY` with the key.
   - Optional, **Variables** tab: `ODDS_DOMAIN` with your own address for this dashboard, for example
     `odds.yourdomain.com` (no https://, different from the trading dashboard). Without it the dashboard uses the free
     Cloudflare address `bot-odds.<your-name>.workers.dev`, which the scanner finds by itself.
3. Actions tab: run **Deploy odds dashboard**, wait for the green tick.
4. Run **Odds scan**. Open `https://<your ODDS_DOMAIN>` and sign in with the same password as the trading dashboard.

After that it scans by itself every hour.

### Reliable hourly scans (GitHub token)
GitHub skips many scheduled runs on free accounts, so the dashboard's Cloudflare timer starts the scan every hour instead.
It needs a GitHub token with permission to run Actions on this repo, saved as the repository secret `GH_DISPATCH_TOKEN`
(then run **Deploy odds dashboard** once). GitHub's own schedule stays as a fallback and skips itself when a scan ran in
the last 40 minutes. If the timer fails, the dashboard's top line says why. The token expires: make a new one before then.

## The free plan and when to upgrade

Each league costs 1 credit per fetch, whatever the number of bookmakers in it. The scanner runs every hour and decides
each time how much it may spend, from the real credits left and the days to the monthly reset:
- **Hot leagues**, with a game in the next 4 hours, come first (soonest game first) and may use up to 3 times a run's
  fair share. Prices move most and slower bookmakers lag most in these hours, and it is the closing price the paper test
  is judged against.
- Other leagues with games in the next 36 hours are refreshed about every 12 hours, from what is left.
- Quiet hours spend nothing, which leaves more for busy weekend afternoons. The month can never overspend.
Settling paper bets costs 2 credits per league per day, only on days with finished bets.

That is enough to see whether the alerts are real. It is not enough to catch most of them, because good prices often last
minutes. If the paper test looks good, the 20,000-credit plan (30 USD a month) allows every league every 15 to 30 minutes:

1. In `odds/config.json` set `"monthly_credits": 20000`, `"runs_per_day": 48` and `"hot_rescan_min": 25`.
2. In `.github/workflows/odds-scan.yml` change the schedule to `"7,37 * * * *"`.
3. Optional: `"markets": ["h2h", "totals"]` adds over/under (doubles the cost per league).

If your plan renews on a day other than the 1st, set `"quota_reset_day"` to that day.

## Settings (`odds/config.json`)

| Setting | Meaning |
|---|---|
| `my_books` | Bookmakers you can bet at (keys from The Odds API). Value and arbitrage alerts only use these |
| `leagues` | Leagues to watch, in The Odds API's names. Out-of-season leagues cost nothing |
| `min_edge` / `max_edge` | Alert between 3% and 15% above the fair price. Bigger gaps are nearly always stale or mistaken prices |
| `min_edge_consensus`, `min_consensus_books` | Without a sharp price: at least 6% above the median of at least 6 other bookmakers |
| `hot_hours`, `hot_rescan_min`, `refresh_hours`, `burst` | Scheduling: see "The free plan" above |
| `regions` | Which bookmakers the feed returns (`eu`). Adding `uk` doubles the cost |
| `min_odds` / `max_odds` | Only prices from 1.30 to 6.00 |
| `max_age_min` | Ignore any price the feed has not refreshed in the last 20 minutes |
| `paper_bankroll`, `kelly_fraction`, `max_stake_pct` | Paper test: 1,000 EUR, quarter-Kelly stakes, at most 2% per bet |

## Reading the paper test

Every value alert becomes a paper bet at the alerted price. The "What is working" table splits the results by bookmaker,
league, edge size and price source, so you can see which part of the method earns its keep and drop the rest (for example,
remove a league from `leagues`, or raise `min_edge`). Two numbers matter:

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
