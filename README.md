# bot-crst: trading signal bot

Buy and sell signals for crypto and stocks on 1h, 4h and daily bars, running around the clock for free.
It gives signals and watches the positions you tell it about. It never places trades. Not financial advice.

## How it works

| Part | Where it runs | What it does |
|---|---|---|
| Scanner (`scanner/`) | GitHub Actions, started by the dashboard every 15 minutes (hourly GitHub schedule as a backup) | Scores every liquid USDT pair on Binance and the stock lists, adds news sentiment, ranks the signals |
| Stock list (`scanner/universe.py`) | GitHub Actions, daily | Rebuilds the list of liquid US stocks. European stocks are in `scanner/europe.txt` |
| Dashboard (`worker/`) | Cloudflare Workers, on your domain | Shows the signals, stores your open positions, checks their prices every minute, sends Telegram alerts |

## Setup (about 30 minutes, once)

### 1. Cloudflare
1. Log in at dash.cloudflare.com. Your domain must already be added to this Cloudflare account.
2. Copy your **Account ID** (Workers & Pages, right-hand side of the overview page).
3. Go to My Profile, API Tokens, Create Token, and use the template **Edit Cloudflare Workers**.
   Under Account Resources choose your account, under Zone Resources choose your domain. Create it and copy the token.

### 2. Telegram
1. In Telegram, message **@BotFather**, send `/newbot`, follow the prompts and copy the token.
2. Send any message to your new bot.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy the number at `"chat":{"id":...`.

### 3. GitHub settings
In this repo: Settings, Secrets and variables, Actions.

**Secrets** tab, "New repository secret", one for each:

| Name | Value |
|---|---|
| `CLOUDFLARE_API_TOKEN` | the token from step 1 |
| `CLOUDFLARE_ACCOUNT_ID` | the Account ID from step 1 |
| `DASH_PASSWORD` | the password you want for the dashboard |
| `INGEST_TOKEN` | any long random text (the scanner and dashboard use it to trust each other) |
| `TG_TOKEN` | the Telegram bot token |
| `TG_CHAT` | the Telegram chat id |

**Variables** tab, "New repository variable":

| Name | Value |
|---|---|
| `DASH_DOMAIN` | the address for the dashboard, for example `signals.yourdomain.com` (no https://). No domain? Deploy once without this variable, then set it to the free address Cloudflare gives you, like `bot-crst.yourname.workers.dev` |

### 4. Start it
In the Actions tab (enable workflows if GitHub asks):
1. **Deploy dashboard**, Run workflow. Wait for the green tick.
2. **Scan markets**, Run workflow. The first results appear on the dashboard a few minutes later.
3. **Update stock list**, Run workflow. This widens the US list from the starter 60 to about 600 liquid stocks.

Then open `https://<your DASH_DOMAIN>` and sign in with `DASH_PASSWORD`.
After this everything runs by itself.

If the deploy step fails on the domain, deploy once without the `DASH_DOMAIN` variable, then add the domain by hand in
Cloudflare (Workers & Pages, bot-crst, Settings, Domains & Routes, Add, Custom domain) and set the variable again.

### 5. Scans every 15 minutes (one more secret)
GitHub's own scheduler is unreliable, so the dashboard starts each scan itself at :00, :15, :30 and :45.
It needs a GitHub token that may start workflows in this repo, and nothing else:
1. On GitHub: your profile picture, Settings, Developer settings, Personal access tokens, **Fine-grained tokens**, Generate new token.
2. Name: `bot-crst scans`. Expiration: the longest offered (remember to renew it). Repository access: **Only select repositories**, `bot-crst`.
3. Permissions, Repository permissions: **Actions: Read and write**. Leave everything else as it is.
4. Generate, copy the token, and add it as the repository secret `GH_DISPATCH_TOKEN`.
5. Run **Deploy dashboard** once more.
Without this secret the scan still runs once an hour from GitHub's own schedule.

## Research and the paper trading test
`research/` tests rules on years of real data; the findings are in `research/RESULTS.md`, and `MAINTENANCE.md`
explains how everything fits together and how to keep it running.

The **Paper test** tab runs two virtual 1,000 USD accounts:
- **Stocks (200-day trend):** buys a stock when its daily close crosses above its 200-day average and sells when it
  closes below. US stocks risk 0.75% of the account per trade, European 0.5%. No leverage.
- **Crypto (experiment):** daily volatility breakouts on the 80 most traded coins, only while Bitcoin is higher than
  30 days ago, 0.5% risk. The research found no proven crypto edge, so expect roughly break-even.
- The research re-runs monthly and pauses a rule automatically if its kind of trade stops showing a profit.
- Telegram reports every test trade, a weekly summary on Sundays, and a warning if scans stop arriving.
- Rules are in the `paper` section of `scanner/config.json`. `python tests/test_paper.py` checks the maths.

## Using the dashboard
- Signals are ranked by strength. By default only signals with a good track record on that asset are shown.
- Each card shows stop, target, suggested maximum leverage and position size, and how earlier signals of the same kind worked out.
- When you really open a trade, correct the entry price, leverage and size on the card and press **I bought (long)** or **I went short**.
- The trade then appears under **My open positions** with a "What to do" column: Hold, Weakening, Take profit or Close.
- When you really close it, enter the exit price and press **I closed it**.

## Telegram alerts
- Every trade the bot opens or closes in its test accounts, a weekly summary, and a warning if scans stop.
- Any change in "What to do" for an open position: stop hit, target reached, signal flipped, near liquidation.

## Settings
`scanner/config.json`:
- `account`: your account size, risk per trade (%) and leverage caps. Set `size` to your real number.
- `thresholds`: scores needed for BUY, SELL and the strong versions.
- `risk`: stop and target distance in ATRs.
- `universe`: minimum volume for crypto pairs and stocks, and how many of each to scan.
- `history_check`: what counts as a good track record.

`scanner/europe.txt`: the European stock list, one Yahoo Finance ticker per line.

## Limits to know
- GitHub's scheduler is often 5 to 30 minutes late. That is fine for 1h and longer bars. Open positions are checked every minute by Cloudflare, independently.
- Stocks get daily signals for the whole list, and 1h/4h signals only for the 40 strongest daily candidates. Free stock prices are delayed about 15 minutes.
- The track record is measured on the same history the signal comes from, without fees. Treat it as a filter, not a forecast.
- Liquidation prices are estimates. Trust your exchange's number.
- Leverage multiplies losses as much as gains.

## Running it on your own computer instead
`pip install -r requirements.txt`, then `python scanner/signal_bot.py --serve` opens a local dashboard with a small fixed watchlist.

## Odds watch (separate tool)
`odds/` and `odds-worker/` are a second, independent tool in this repo: it compares sports betting prices and
alerts on value bets and arbitrage. Setup and details are in [odds/README.md](odds/README.md).
