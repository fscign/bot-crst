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
                g = groups.setdefault(gkey, {"books": {}, "lay": {}, "updated": {}, "links": {}})
                g["lay" if lay else "books"].setdefault(book, {})[o["name"]] = float(price)
                if not lay:
                    g["updated"][book] = updated
                    link = o.get("link") or m.get("link") or bm.get("link")
                    if link:
                        g["links"].setdefault(book, {})[o["name"]] = link
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


def references(group, cfg, now):
    """Sharp references: [(name, {outcome: probability})]. Pinnacle with its margin removed,
    and the Betfair Exchange midpoint when the exchange market is deep enough to mean something."""
    names, refs = group["outcomes"], []
    for book in cfg["sharp_books"]:
        prices = group["books"].get(book)
        if not prices or set(prices) != set(names) or len(names) < 2:
            continue
        if not fresh(group["updated"].get(book), now, cfg):
            continue
        margin = sum(1 / prices[n] for n in names) - 1
        if not 0 <= margin <= cfg["max_sharp_margin"]:
            continue
        refs.append((book, dict(zip(names, devig_power([prices[n] for n in names])))))
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
        refs.append((book, {n: m / s for n, m in zip(names, mid)}))
    return refs


def consensus(group, cfg, now):
    """Median fair probability across every other bookmaker in the feed. Weaker than a sharp
    price (soft books copy each other), so it is only a fallback, with a higher bar."""
    names = group["outcomes"]
    skip = set(cfg["my_books"]) | set(cfg["sharp_books"]) | set(cfg["exchange_books"])
    per = []
    for book, prices in group["books"].items():
        if book in skip or set(prices) != set(names) or not fresh(group["updated"].get(book), now, cfg):
            continue
        if 0 <= sum(1 / prices[n] for n in names) - 1 <= cfg["max_consensus_margin"]:
            per.append(devig_power([prices[n] for n in names]))
    if len(per) < cfg["min_consensus_books"]:
        return None, len(per)
    med = []
    for i in range(len(names)):
        col = sorted(p[i] for p in per)
        k = len(col) // 2
        med.append(col[k] if len(col) % 2 else (col[k - 1] + col[k]) / 2)
    s = sum(med)
    return {n: m / s for n, m in zip(names, med)}, len(per)


def fair_probs(group, cfg, now):
    """-> {"probs", "source", "refs", "min_edge", "sharp"} or {"probs": None, "source": reason}.

    With sharp references the fair price is their average, and a value bet must also beat
    each of them on its own. Without any, the market consensus is used with a higher bar."""
    names = group["outcomes"]
    refs = references(group, cfg, now)
    if refs:
        avg = [sum(r[n] for _, r in refs) / len(refs) for n in names]
        s = sum(avg)
        return {"probs": {n: a / s for n, a in zip(names, avg)}, "source": " + ".join(b for b, _ in refs),
                "refs": [r for _, r in refs], "min_edge": cfg["min_edge"], "sharp": True}
    cons, n = consensus(group, cfg, now)
    if cons:
        return {"probs": cons, "source": f"consensus of {n} bookmakers", "refs": [],
                "min_edge": cfg["min_edge_consensus"], "sharp": False}
    return {"probs": None, "source": "no fresh sharp price"}


def market_rank(group, outcome, price, now, cfg):
    """(rank, books): 1 means no bookmaker in the feed pays more. Exchanges are left out (commission)."""
    others = [p[outcome] for b, p in group["books"].items()
              if outcome in p and b not in cfg["exchange_books"] and fresh(group["updated"].get(b), now, cfg)]
    return 1 + sum(o > price + 1e-9 for o in others), len(others)


def add_trend(alerts, rows, previous_rows):
    """How the fair price moved since the previous scan of this league. A fair price shortening
    towards the bet means the sharp market moved and this bookmaker has not followed yet (the
    best kind of value); one drifting away means the gap may be closing by itself."""
    before = {(r["id"], m["market"], o["name"]): o["fair"] for r in previous_rows for m in r["markets"]
              for o in m["outcomes"] if o.get("fair")}
    for a in alerts:
        if a["kind"] != "value":
            continue
        prev = before.get((a["event"], a["market"], a["outcome"]))
        a["prev_fair"] = prev
        a["trend"] = None if not prev else "toward" if a["fair"] < prev * 0.99 else "away" if a["fair"] > prev * 1.01 else "flat"


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
        fair = fair_probs(g, cfg, now)
        probs, source = fair["probs"], fair["source"]
        market = {"market": gkey, "source": source if probs else None, "note": None if probs else source, "outcomes": []}
        for n in names:
            p = probs[n] if probs else None
            prices = {b: g["books"][b][n] for b in mine if set(g["books"].get(b, {})) == set(names)}
            best = max(prices, key=prices.get) if prices else None
            market["outcomes"].append({
                "name": n, "prob": round(p, 4) if p else None, "fair": round(1 / p, 3) if p else None,
                "value_from": round((1 + fair["min_edge"]) / p, 2) if p else None,
                "prices": prices, "best": best,
            })
            if not (probs and bettable):
                continue
            for book, price in prices.items():
                edge = price * p - 1
                if edge < fair["min_edge"] or not cfg["min_odds"] <= price <= cfg["max_odds"]:
                    continue
                if edge > cfg["max_edge"]:
                    continue                # too good to be true: almost always a stale or mistaken price
                if not fresh(g["updated"].get(book), now, cfg):
                    continue
                if any(price * r[n] - 1 <= 0 for r in fair["refs"]):
                    continue                # one of the sharp references disagrees
                rank, nbooks = market_rank(g, n, price, now, cfg)
                values.append({
                    "kind": "value", "key": f"{event['id']}|{gkey}|{n}|{book}", "event": event["id"],
                    "sport": row["sport"], "league": row["league"], "home": row["home"], "away": row["away"],
                    "start": row["start"], "market": gkey, "outcome": n, "book": book, "odds": price,
                    "prob": round(p, 4), "fair": round(1 / p, 3), "edge": round(edge, 4), "source": source,
                    "sharp": fair["sharp"], "confirmed": len(fair["refs"]) > 1, "rank": rank, "books_n": nbooks,
                    "link": g["links"].get(book, {}).get(n),
                    "stake": kelly_stake(price, p, bankroll * (1 if fair["sharp"] else 0.5), cfg), "found": iso(now),
                })
        row["markets"].append(market)

        # arbitrage between the bookmakers you can use
        if bettable and len(mine) >= 2:
            legs = []
            for n in names:
                cands = [(g["books"][b][n], b) for b in mine
                         if set(g["books"].get(b, {})) == set(names) and fresh(g["updated"].get(b), now, cfg)]
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
                        "legs": [{"outcome": n, "book": b, "odds": price, "share": round((1 / price) / inv, 4),
                                  "link": g["links"].get(b, {}).get(n)} for n, price, b in legs],
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
        bet["sharp"] = a.get("sharp", True)
        bet["trend"] = a.get("trend")
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


def paper_settle(paper, scores, now, no_scores=()):
    """Settle finished bets. Leagues the feed has no results for (no_scores) are closed 6 hours after
    the start as "unscored": no profit or loss, but their closing line value still counts."""
    by_id = {s["id"]: s for s in scores if s.get("completed")}
    done, still = [], []
    for b in paper["open"]:
        if b["sport"] in no_scores:
            if now - parse_time(b["start"]) > timedelta(hours=6):
                b.update(result="unscored", pnl=0.0, settled=iso(now), balance=paper["bankroll"])
                done.append(b)
            else:
                still.append(b)
            continue
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
    graded = [b for b in s if b["result"] not in ("void", "unscored")]
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


def plan_scan(candidates, last_scan, now, remaining, cfg, cost=1):
    """Which leagues to fetch this run. candidates: [(league, games_in_window, first_start)].

    Prices move most, and soft bookmakers lag most, in the last hours before kick-off (team news,
    late money). The closing price there is also what the paper test is judged against. So:
      1. "hot" leagues, with a game in the next few hours and not fetched in the last ~hour,
         soonest first, may take up to `burst` times this run's fair share of credits;
      2. other leagues are refreshed every `refresh_hours`, within the plain fair share.
    The fair share is recalculated from the real remaining credits every run, so a busy Saturday
    borrows from quiet hours and the month can never overspend.
    """
    runs_left = max(1, math.ceil(days_to_reset(now, cfg["quota_reset_day"]) * cfg["runs_per_day"]))
    share = remaining / (runs_left * cost)
    cap = int(remaining // cost)
    since = lambda lg: (now - parse_time(last_scan[lg])).total_seconds() / 60 if last_scan.get(lg) else float("inf")  # noqa: E731
    live = [c for c in candidates if c[1] > 0]
    hot = sorted((c for c in live if parse_time(c[2]) - now <= timedelta(hours=cfg["hot_hours"])
                  and since(c[0]) >= cfg["hot_rescan_min"]), key=lambda c: c[2])
    hot_ids = {c[0] for c in hot}
    stale = sorted((c for c in live if c[0] not in hot_ids and since(c[0]) >= cfg["refresh_hours"] * 60),
                   key=lambda c: (-since(c[0]), c[2]))
    # runs since anything was fetched: their unspent share carries over, so a share below one
    # league per run (start of the month on the free plan) still gets leagues refreshed
    latest = min((since(lg) for lg in last_scan), default=0)
    idle = min(max(1.0, latest * cfg["runs_per_day"] / 1440), cfg["refresh_hours"] * cfg["runs_per_day"] / 24)
    n_hot = min(len(hot), max(int(share * cfg["burst"] + 1e-9), int(share * idle + 1e-9)), cap)
    n_stale = min(len(stale), max(0, int(share * idle + 1e-9) - n_hot), cap - n_hot)
    return [c[0] for c in hot[:n_hot]] + [c[0] for c in stale[:n_stale]]
