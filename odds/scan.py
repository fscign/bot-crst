"""Odds scanner: fetch prices, find value bets and arbitrage, alert on Telegram, update the dashboard.

Runs in GitHub Actions (see .github/workflows/odds-scan.yml).

Environment:
    ODDS_API_KEY   key from the-odds-api.com (required)
    ODDS_DOMAIN    e.g. odds.example.com   (optional: without it the state is kept in odds/state.json)
    INGEST_TOKEN   shared secret with the dashboard
    TG_TOKEN, TG_CHAT   Telegram alerts (optional)

Local dry run on saved data, no network and no credits:  python odds/scan.py --offline tests/odds_fixture.json
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).parent))
import engine as E  # noqa: E402

HERE = Path(__file__).parent
API = "https://api.the-odds-api.com/v4"
CFG = json.loads((HERE / "config.json").read_text())
LOCAL_STATE = HERE / "state.json"


class Api:
    def __init__(self, key):
        self.key, self.remaining, self.spent = key, None, 0

    def get(self, path, **params):
        r = requests.get(f"{API}{path}", params={"apiKey": self.key, **params}, timeout=30)
        if "x-requests-remaining" in r.headers:
            self.remaining = int(float(r.headers["x-requests-remaining"]))
            self.spent += int(float(r.headers.get("x-requests-last", 0) or 0))
        if r.status_code != 200:
            raise RuntimeError(f"Odds API {path}: {r.status_code} {r.text[:200]}")
        return r.json()


def dash_url():
    domain = os.environ.get("ODDS_DOMAIN", "").strip().removeprefix("https://").rstrip("/")
    return f"https://{domain}" if domain else ""


def load_state():
    dash, token = dash_url(), os.environ.get("INGEST_TOKEN", "")
    if dash and token:
        r = requests.get(f"{dash}/api/state", headers={"authorization": f"Bearer {token}"}, timeout=20)
        r.raise_for_status()
        return r.json() or {}
    return json.loads(LOCAL_STATE.read_text()) if LOCAL_STATE.exists() else {}


def save_state(state):
    dash, token = dash_url(), os.environ.get("INGEST_TOKEN", "")
    if dash and token:
        r = requests.post(f"{dash}/api/ingest", headers={"authorization": f"Bearer {token}"},
                          data=json.dumps(state), timeout=60)
        r.raise_for_status()
        print("dashboard updated")
    else:
        LOCAL_STATE.write_text(json.dumps(state, indent=1))
        print(f"state written to {LOCAL_STATE}")


def telegram(text):
    token, chat = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT")
    if not token or not chat:
        print("--- telegram (not configured) ---\n" + text)
        return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=20)
    except requests.RequestException as e:
        print("telegram failed", e)


def when(text):
    return E.parse_time(text).astimezone(ZoneInfo(CFG["timezone"])).strftime("%a %d %b %H:%M")


def book_name(key):
    return CFG["my_books"].get(key, key)


def describe(a):
    head = f"{a['home']} v {a['away']}\n{a['league']}, {when(a['start'])}"
    market = "" if a["market"] == "h2h" else f" ({a['market']})"
    if a["kind"] == "value":
        note = CFG.get("book_notes", {}).get(a["book"])
        return (f"VALUE +{a['edge'] * 100:.1f}% at {book_name(a['book'])}\n{head}\n"
                f"Bet: {a['outcome']}{market} @ {a['odds']:.2f}\n"
                f"Fair price {a['fair']:.2f} ({a['source']}{', confirmed by exchange' if a['confirmed'] else ''})\n"
                f"Paper stake: {a['stake']:.2f} {CFG['currency']}"
                + (f"\n{note}" if note else ""))
    legs = "\n".join(f"  {l['outcome']} @ {l['odds']:.2f} at {book_name(l['book'])}: {l['share'] * 100:.1f}% of the stake"
                     for l in a["legs"])
    return f"ARBITRAGE +{a['profit'] * 100:.2f}%{market}\n{head}\n{legs}"


def run(now, fetch_sports, fetch_events, fetch_odds, fetch_scores, remaining_hint=None, api=None):
    state = load_state()
    paper = state.get("paper") or E.new_paper(CFG, now)
    board = state.get("board") or {}
    last_scan = state.get("last_scan") or {}
    seen = state.get("seen") or {}
    remaining = remaining_hint if remaining_hint is not None else (state.get("quota") or {}).get("remaining")
    if remaining is None:
        remaining = CFG["monthly_credits"]

    # 1. which leagues have games soon (these calls are free)
    active = fetch_sports()
    until = now + timedelta(hours=CFG["window_hours"])
    candidates = []
    for league in CFG["leagues"]:
        if active is not None and league not in active:
            continue
        games = [g for g in fetch_events(league, now, until) if E.parse_time(g["commence_time"]) > now]
        if games:
            candidates.append((league, len(games), min(g["commence_time"] for g in games)))
    if api and api.remaining is not None:
        remaining = api.remaining

    # 2. settle finished paper bets, at most once a day per league (2 credits each)
    settled = []
    if CFG["settle_results"]:
        today = E.iso(now)[:10]
        due = {b["sport"] for b in paper["open"] if now - E.parse_time(b["start"]) > timedelta(hours=4)}
        checked = state.get("settle_checked") or {}
        for league in sorted(due):
            if checked.get(league) == today or remaining < 2:
                continue
            settled += E.paper_settle(paper, fetch_scores(league), now)
            checked[league] = today
            remaining = api.remaining if api and api.remaining is not None else remaining - 2
        state["settle_checked"] = checked
        if due:
            settled += E.paper_settle(paper, [], now)      # voids games that never got a result

    # 3. fetch odds for as many leagues as the monthly credits allow
    cost = len(CFG["markets"])
    allowance = E.run_allowance(remaining, now, CFG, cost)
    chosen = E.pick_leagues(candidates, last_scan, allowance, now)
    print(f"credits left {remaining}, this run may fetch {allowance} league(s): {chosen or 'none'}")
    fresh_alerts = []
    for league in chosen:
        events = fetch_odds(league, until)
        rows, alerts = [], []
        for ev in events:
            row, values, arbs = E.analyse(ev, CFG, now, paper["bankroll"])
            rows.append(row)
            alerts += values + arbs
        board[league] = {"scanned": E.iso(now), "rows": rows, "alerts": alerts}
        last_scan[league] = E.iso(now)
        fresh_alerts += alerts
        E.paper_update_close(paper, rows, now)
    if api and api.remaining is not None:
        remaining = api.remaining

    # 4. drop games that have started; keep each league's last table until it is scanned again
    for league in list(board):
        b = board[league]
        b["rows"] = [r for r in b["rows"] if E.parse_time(r["start"]) > now]
        b["alerts"] = [a for a in b["alerts"] if E.parse_time(a["start"]) > now]
        if not b["rows"]:
            del board[league]

    # 5. paper bets and Telegram for what is new
    placed = E.paper_place(paper, fresh_alerts)
    new = [a for a in fresh_alerts if a["key"] not in seen]
    for a in new:
        seen[a["key"]] = a["start"]
    seen = {k: v for k, v in seen.items() if E.parse_time(v) > now - timedelta(days=1)}
    if new:
        best = sorted(new, key=lambda a: -(a.get("edge") or a.get("profit")))[:8]
        more = f"\n\n(+{len(new) - 8} more on the dashboard)" if len(new) > 8 else ""
        telegram("\n\n".join(describe(a) for a in best) + more
                 + "\n\nPrices move fast: check the price is still there before betting.")
    if settled:
        lines = [f"{b['home']} v {b['away']}: {b['outcome']} @ {b['odds']:.2f} {b['result'].upper()} "
                 f"{b['pnl']:+.2f}" for b in settled[-8:]]
        telegram("Paper bets settled\n" + "\n".join(lines) + f"\nPaper bankroll: {paper['bankroll']:.2f} {CFG['currency']}")

    state.update(
        updated=E.iso(now), board=board, last_scan=last_scan, seen=seen, paper=paper,
        summary=E.paper_summary(paper),
        quota={"remaining": remaining, "monthly": CFG["monthly_credits"], "runs_per_day": CFG["runs_per_day"]},
        config={k: CFG[k] for k in ("currency", "timezone", "my_books", "book_notes", "manual_books", "min_edge",
                                    "max_edge", "min_odds", "max_odds", "kelly_fraction", "max_stake_pct")},
        leagues_with_games=[c[0] for c in candidates], scanned_now=chosen,
    )
    save_state(state)
    print(f"{len(fresh_alerts)} alert(s) in this scan, {len(new)} new, {len(placed)} paper bet(s) placed, {len(settled)} settled")
    return state


def main():
    now = datetime.now(E.UTC)
    if len(sys.argv) > 2 and sys.argv[1] == "--offline":
        fx = json.loads(Path(sys.argv[2]).read_text())
        now = E.parse_time(fx["now"])
        return run(now, lambda: None,
                   lambda league, a, b: [e for e in fx["odds"].get(league, [])],
                   lambda league, until: fx["odds"].get(league, []),
                   lambda league: fx.get("scores", {}).get(league, []),
                   remaining_hint=fx.get("remaining"))
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        sys.exit("ODDS_API_KEY is not set (add it as a repository secret, see odds/README.md)")
    api = Api(key)
    books = ",".join(list(CFG["sharp_books"]) + list(CFG["exchange_books"]) + list(CFG["my_books"]))
    if len(books.split(",")) > 10:
        sys.exit("More than 10 bookmakers would double the credit cost of every request: trim config.json")
    run(now,
        lambda: {s["key"] for s in api.get("/sports")},
        lambda league, a, b: api.get(f"/sports/{league}/events", commenceTimeFrom=E.iso(a), commenceTimeTo=E.iso(b)),
        lambda league, until: api.get(f"/sports/{league}/odds", bookmakers=books, markets=",".join(CFG["markets"]),
                                      oddsFormat="decimal", commenceTimeTo=E.iso(until)),
        lambda league: api.get(f"/sports/{league}/scores", daysFrom=3),
        api=api)


if __name__ == "__main__":
    main()
