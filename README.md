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

## Paper trading test
The **Paper test** tab runs a virtual account alongside the real signals:
- Starts with 1,000 USD. Opens a trade on every new STRONG BUY or STRONG SELL with a good track record.
- Stake: 1% of the balance, never less than 10 USD (so 10 USD while the balance is under 1,000).
- Leverage as suggested on the signal. Fee 0.1% on entry and exit.
- Closes at the trailing stop, the target, liquidation, a signal flip, or a time limit (48 bars on 1h, 42 on 4h, 20 on 1d).
  Exits are found by replaying 5-minute bars, so a stop hit between scans is filled at the stop.
- Shows equity, return, win rate, profit factor, drawdown, open and closed test trades.
- **Reset the test** at the bottom of the tab starts again from 1,000 USD.
- All rules are in the `paper` section of `scanner/config.json`.
- `python tests/test_paper.py` checks the maths.

## Using the dashboard
- Signals are ranked by strength. By default only signals with a good track record on that asset are shown.
- Each card shows stop, target, suggested maximum leverage and position size, and how earlier signals of the same kind worked out.
- When you really open a trade, correct the entry price, leverage and size on the card and press **I bought (long)** or **I went short**.
- The trade then appears under **My open positions** with a "What to do" column: Hold, Weakening, Take profit or Close.
- When you really close it, enter the exit price and press **I closed it**.

## Telegram alerts
- New STRONG BUY or STRONG SELL signals with a good track record (at most 8 per scan).
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
