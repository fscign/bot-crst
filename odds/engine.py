"""Odds maths: fair prices from sharp markets, value bets, arbitrage, paper bets.

No network code here, so everything can be tested offline (tests/test_odds.py).

The idea in one paragraph: Pinnacle and the Betfair Exchange take big bets from
professionals, so their prices are the best public estimate of the real chances.
Remove their margin and you get a "fair" probability for each outcome. A bookmaker
you can use that pays clearly more than the fair price is a value bet. If your
bookmakers disagree so much that backing every outcome returns more than you
stake, that is an arbitrage.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

UTC = timezone.utc


def parse_time(text):
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def iso(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- removing the bookmaker margin ------------------------------------------------
def devig_power(prices):
    """Fair probabilities from one bookmaker's prices for every outcome of a market.

    Power method: find k so that sum((1/price)^k) = 1. It takes more margin off
    long shots than off favourites, which matches how bookmakers really price,
    and is more accurate than dividing every outcome by the same number.
    """
    raw = [1.0 / p for p in prices]
    total = sum(raw)
    if total <= 1.0:                       # no margin to remove (exchange midpoint, or an arb)
        return [r / total for r in raw]
    lo, hi = 1.0, 10.0                     # sum(raw^k) falls as k grows
    for _ in range(80):
        k = (lo + hi) / 2
        if sum(r ** k for r in raw) > 1.0:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    probs = [r ** k for r in raw]
    s = sum(probs)
    return [p / s for p in probs]


# ---- reading one event from The Odds API --------------------------------------------
def normalize(event):
    """-> {group: {"outcomes": [..], "books": {book: {outcome: price}}, "lay": {...}, "updated": {book: dt}}}

    A group is one market that must be compared like with like: "h2h", or
    "totals 2.5" (a 2.5 line is a different bet from a 3.5 line).
    """
    groups = {}
    for bm in event.get("bookmakers", []):
        book = bm["key"]
        for m in bm.get("markets", []):
            key = m["key"]
            lay = key.endswith("_lay")
            base = key[:-4] if lay else key
            if base not in ("h2h", "totals"):
                continue
            updated = parse_time(m.get("last_update") or bm.get("last_update"))
            for o in m.get("outcomes", []):
                price = o.get("price")
                if not price or price <= 1.0:
                    continue
                gkey = base if base == "h2h" else f"totals {abs(float(o.get('point', 0))):g}"
                g = groups.setdefault(gkey, {"books": {}, "lay": {}, "updated": {}})
                g["lay" if lay else "books"].setdefault(book, {})[o["name"]] = float(price)
                if not lay:
                    g["updated"][book] = updated
    for gkey, g in groups.items():
        # the full outcome list is the longest one any bookmaker shows (home, away and draw)
        longest = max(g["books"].values(), key=len, default={})
        names = list(longest)
        if gkey == "h2h":
            order = {event.get("home_team"): 0, "Draw": 1, event.get("away_team"): 2}
            names.sort(key=lambda n: order.get(n, 3))
        g["outcomes"] = names
    return groups


def fresh(updated, now, cfg):
    """A price counts only if the feed refreshed it recently: stale prices create fake value."""
    return updated is not None and now - updated <= timedelta(minutes=cfg["max_age_min"])


def fair_probs(group, cfg, now):
    """-> ({outcome: probability}, source, confirm) or (None, reason, None).

    Main source: Pinnacle with the margin removed. Fallback: Betfair Exchange
    midpoint. When both exist, the second is returned as `confirm`, so a value bet
    has to look good against both of them.
    """
    names = group["outcomes"]
    found = []
    for book in cfg["sharp_books"]:
        prices = group["books"].get(book)
        if not prices or set(prices) != set(names) or len(names) < 2:
            continue
        if not fresh(group["updated"].get(book), now, cfg):
            continue
        margin = sum(1 / prices[n] for n in names) - 1
        if not 0 <= margin <= cfg["max_sharp_margin"]:
            continue
        found.append((dict(zip(names, devig_power([prices[n] for n in names]))), book))
    for book in cfg["exchange_books"]:
        back, lay = group["books"].get(book), group["lay"].get(book)
        if not back or not lay or set(back) != set(names) or set(lay) != set(names):
            continue
        if not fresh(group["updated"].get(book), now, cfg):
            continue
        if any(lay[n] < back[n] or lay[n] / back[n] - 1 > cfg["max_exchange_spread"] for n in names):
            continue                        # thin market, the midpoint means little
        mid = [(1 / back[n] + 1 / lay[n]) / 2 for n in names]
        s = sum(mid)
        found.append(({n: m / s for n, m in zip(names, mid)}, book))
    if not found:
        return None, "no fresh sharp price", None
    probs, source = found[0]
    confirm = found[1][0] if len(found) > 1 else None
    return probs, source, confirm


def kelly_stake(price, prob, bankroll, cfg):
    """Quarter Kelly by default, capped: the fair price is itself an estimate."""
    edge = price * prob - 1
    if edge <= 0:
        return 0.0
    full = edge / (price - 1)
    return round(bankroll * min(cfg["kelly_fraction"] * full, cfg["max_stake_pct"]), 2)


# ---- one event -> table row, value bets, arbitrage ----------------------------------
def analyse(event, cfg, now, bankroll):
    start = parse_time(event["commence_time"])
    groups = normalize(event)
    row = {
        "id": event["id"], "sport": event["sport_key"], "league": event.get("sport_title", event["sport_key"]),
        "home": event.get("home_team"), "away": event.get("away_team"), "start": iso(start), "markets": [],
    }
    values, arbs = [], []
    bettable = start - now >= timedelta(minutes=cfg["min_minutes_to_start"])
    mine = list(cfg["my_books"])
    for gkey in sorted(groups, key=lambda k: (k != "h2h", k)):
        g = groups[gkey]
        names = g["outcomes"]
        if len(names) < 2:
            continue
        probs, source, confirm = fair_probs(g, cfg, now)
        market = {"market": gkey, "source": source if probs else None, "note": None if probs else source, "outcomes": []}
        for n in names:
            p = probs[n] if probs else None
            prices = {b: g["books"][b][n] for b in mine if n in g["books"].get(b, {})}
            best = max(prices, key=prices.get) if prices else None
            market["outcomes"].append({
                "name": n, "prob": round(p, 4) if p else None, "fair": round(1 / p, 3) if p else None,
                "value_from": round((1 + cfg["min_edge"]) / p, 2) if p else None,
                "prices": prices, "best": best,
            })
            if not (probs and bettable):
                continue
            for book, price in prices.items():
                edge = price * p - 1
                if edge < cfg["min_edge"] or not cfg["min_odds"] <= price <= cfg["max_odds"]:
                    continue
                if edge > cfg["max_edge"]:
                    continue                # too good to be true: almost always a stale or mistaken price
                if not fresh(g["updated"].get(book), now, cfg):
                    continue
                if confirm and price * confirm[n] - 1 <= 0:
                    continue                # the second sharp source disagrees
                values.append({
                    "kind": "value", "key": f"{event['id']}|{gkey}|{n}|{book}", "event": event["id"],
                    "sport": row["sport"], "league": row["league"], "home": row["home"], "away": row["away"],
                    "start": row["start"], "market": gkey, "outcome": n, "book": book, "odds": price,
                    "prob": round(p, 4), "fair": round(1 / p, 3), "edge": round(edge, 4), "source": source,
                    "confirmed": bool(confirm), "stake": kelly_stake(price, p, bankroll, cfg), "found": iso(now),
                })
        row["markets"].append(market)

        # arbitrage between the bookmakers you can use
        if bettable and len(mine) >= 2:
            legs = []
            for n in names:
                cands = [(g["books"][b][n], b) for b in mine
                         if n in g["books"].get(b, {}) and fresh(g["updated"].get(b), now, cfg)]
                if not cands:
                    legs = []
                    break
                legs.append((n,) + max(cands))
            if legs and len({b for _, _, b in legs}) >= 2:
                inv = sum(1 / price for _, price, _ in legs)
                profit = 1 / inv - 1
                if cfg["min_arb"] <= profit <= cfg["max_edge"]:
                    arbs.append({
                        "kind": "arb", "key": f"{event['id']}|{gkey}|arb", "event": event["id"], "sport": row["sport"],
                        "league": row["league"], "home": row["home"], "away": row["away"], "start": row["start"],
                        "market": gkey, "profit": round(profit, 4), "found": iso(now),
                        "legs": [{"outcome": n, "book": b, "odds": price, "share": round((1 / price) / inv, 4)}
                                 for n, price, b in legs],
                    })
    return row, values, arbs


# ---- paper bets -----------------------------------------------------------------------
def new_paper(cfg, now):
    return {"started": iso(now), "start_bankroll": cfg["paper_bankroll"], "bankroll": cfg["paper_bankroll"],
            "open": [], "settled": []}


def paper_place(paper, alerts):
    """One paper bet per outcome of an event, at the first price that qualified."""
    taken = {(b["event"], b["market"], b["outcome"]) for b in paper["open"] + paper["settled"]}
    placed = []
    for a in alerts:
        if a["kind"] != "value" or a["stake"] <= 0:
            continue
        k = (a["event"], a["market"], a["outcome"])
        if k in taken:
            continue
        taken.add(k)
        bet = {f: a[f] for f in ("key", "event", "sport", "league", "home", "away", "start", "market", "outcome",
                                 "book", "odds", "prob", "edge", "stake")}
        bet.update(placed=a["found"], close_fair=a["fair"], clv=round(a["odds"] / a["fair"] - 1, 4))
        paper["open"].append(bet)
        placed.append(bet)
    return placed


def paper_update_close(paper, rows, now):
    """Closing line value: the bet's odds against the last fair price seen before the start.
    Beating the closing line on average is the quickest honest sign that the method works,
    long before the win/loss record means anything."""
    fair = {}
    for r in rows:
        if parse_time(r["start"]) <= now:
            continue
        for m in r["markets"]:
            for o in m["outcomes"]:
                if o["fair"]:
                    fair[(r["id"], m["market"], o["name"])] = o["fair"]
    for b in paper["open"]:
        f = fair.get((b["event"], b["market"], b["outcome"]))
        if f:
            b["close_fair"] = f
            b["clv"] = round(b["odds"] / f - 1, 4)


def result_of(bet, score):
    """'win' | 'lose' | 'void' from a finished game of the scores feed, None if it cannot be read."""
    try:
        pts = {s["name"]: float(s["score"]) for s in score["scores"]}
        home, away = pts[score["home_team"]], pts[score["away_team"]]
    except (KeyError, TypeError, ValueError):
        return None
    if bet["market"] == "h2h":
        winner = "Draw" if home == away else score["home_team"] if home > away else score["away_team"]
        return "win" if bet["outcome"] == winner else "lose"
    if bet["market"].startswith("totals "):
        line, total = float(bet["market"].split()[1]), home + away
        if total == line:
            return "void"
        return "win" if (total > line) == (bet["outcome"] == "Over") else "lose"
    return None


def paper_settle(paper, scores, now):
    by_id = {s["id"]: s for s in scores if s.get("completed")}
    done, still = [], []
    for b in paper["open"]:
        s = by_id.get(b["event"])
        res = result_of(b, s) if s else None
        if res is None:
            # a game that never shows a result (postponed, feed gap) is voided after 4 days
            if now - parse_time(b["start"]) > timedelta(days=4):
                res = "void"
            else:
                still.append(b)
                continue
        b["result"] = res
        b["pnl"] = round(b["stake"] * (b["odds"] - 1), 2) if res == "win" else -b["stake"] if res == "lose" else 0.0
        b["settled"] = iso(now)
        paper["bankroll"] = round(paper["bankroll"] + b["pnl"], 2)
        b["balance"] = paper["bankroll"]
        done.append(b)
    paper["open"] = still
    paper["settled"] = (paper["settled"] + done)[-500:]
    return done


def paper_summary(paper):
    s = paper["settled"]
    graded = [b for b in s if b["result"] != "void"]
    staked = sum(b["stake"] for b in graded)
    every = s + paper["open"]
    return {
        "bets": len(every), "open": len(paper["open"]), "won": sum(b["result"] == "win" for b in graded),
        "lost": sum(b["result"] == "lose" for b in graded), "staked": round(staked, 2),
        "pnl": round(sum(b["pnl"] for b in graded), 2),
        "roi": round(sum(b["pnl"] for b in graded) / staked, 4) if staked else None,
        "expected": round(sum(b["stake"] * b["edge"] for b in graded), 2),
        "avg_clv": round(sum(b["clv"] for b in every) / len(every), 4) if every else None,
        "beat_close": round(sum(b["clv"] > 0 for b in every) / len(every), 4) if every else None,
    }


# ---- spending the monthly credits evenly ------------------------------------------------
def days_to_reset(now, reset_day):
    nxt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if now.day >= reset_day:
        nxt = (nxt + timedelta(days=32)).replace(day=1)
    try:
        nxt = nxt.replace(day=reset_day)
    except ValueError:
        pass
    return max((nxt - now).total_seconds() / 86400, 1 / 24)


def run_allowance(remaining, now, cfg, cost_per_league=1):
    """How many leagues this run may fetch so the credits last until the monthly reset."""
    runs_left = max(1, math.ceil(days_to_reset(now, cfg["quota_reset_day"]) * cfg["runs_per_day"]))
    return max(0, int(remaining // (runs_left * cost_per_league)))


def pick_leagues(candidates, last_scan, allowance, now):
    """candidates: [(league, games_in_window, first_start)]. Longest-unscanned first, then the soonest game."""
    live = [c for c in candidates if c[1] > 0]

    def rank(c):
        seen = parse_time(last_scan.get(c[0]))
        waited = (now - seen).total_seconds() if seen else float("inf")
        return (-waited, c[2])
    return [c[0] for c in sorted(live, key=rank)[:allowance]]
