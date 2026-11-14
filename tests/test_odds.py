"""Checks the odds maths. Run:  python tests/test_odds.py"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "odds"))
import engine as E  # noqa: E402

CFG = json.loads((ROOT / "odds" / "config.json").read_text())
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=E.UTC)
T = E.iso(NOW - timedelta(minutes=3))
OLD = E.iso(NOW - timedelta(hours=2))


def book(key, prices, updated=T, lay=None):
    markets = [{"key": "h2h", "last_update": updated, "outcomes": [{"name": n, "price": p} for n, p in prices.items()]}]
    if lay:
        markets.append({"key": "h2h_lay", "last_update": updated, "outcomes": [{"name": n, "price": p} for n, p in lay.items()]})
    return {"key": key, "title": key, "markets": markets}


def event(*books, hours=5, eid="e1"):
    return {"id": eid, "sport_key": "soccer_epl", "sport_title": "EPL", "home_team": "Arsenal", "away_team": "Chelsea",
            "commence_time": E.iso(NOW + timedelta(hours=hours)), "bookmakers": list(books)}


PIN = {"Arsenal": 2.00, "Draw": 3.50, "Chelsea": 3.80}          # 4.9% margin
close = lambda a, b, tol=1e-6: abs(a - b) < tol                  # noqa: E731


def test_devig():
    p = E.devig_power([1.95, 1.95])
    assert close(p[0], 0.5) and close(sum(p), 1)
    p = E.devig_power([1.25, 6.5, 11.0])
    assert close(sum(p), 1)
    flat = [(1 / o) / sum(1 / x for x in [1.25, 6.5, 11.0]) for o in [1.25, 6.5, 11.0]]
    assert p[2] < flat[2] and p[0] > flat[0], "power method takes more margin off the long shot"
    assert close(sum(E.devig_power([2.1, 2.1])), 1), "prices with no margin are just normalised"


def test_value():
    fair_home = E.devig_power(list(PIN.values()))[0]
    assert 0.47 < fair_home < 0.49
    row, values, arbs = E.analyse(event(book("pinnacle", PIN), book("coolbet", {"Arsenal": 2.20, "Draw": 3.3, "Chelsea": 3.5})), CFG, NOW, 1000)
    assert len(values) == 1 and values[0]["outcome"] == "Arsenal" and values[0]["book"] == "coolbet"
    v = values[0]
    assert close(v["edge"], 2.20 * fair_home - 1, 1e-3) and 0.03 < v["edge"] < 0.08
    # quarter Kelly: 1000 * 0.25 * edge / (odds - 1), under the 2% cap
    assert close(v["stake"], round(1000 * 0.25 * (2.20 * fair_home - 1) / 1.20, 2), 0.011) and v["stake"] <= 20
    home = row["markets"][0]["outcomes"][0]
    assert home["name"] == "Arsenal" and close(home["value_from"], round(1.03 / fair_home, 2))
    assert [o["name"] for o in row["markets"][0]["outcomes"]] == ["Arsenal", "Draw", "Chelsea"]
    assert not arbs


def test_no_false_value():
    soft = {"Arsenal": 2.20, "Draw": 3.3, "Chelsea": 3.5}
    none = lambda *b, **k: E.analyse(event(*b, **k), CFG, NOW, 1000)[1] == []   # noqa: E731
    assert none(book("pinnacle", PIN), book("coolbet", soft, updated=OLD)), "stale bookmaker price"
    assert none(book("pinnacle", PIN, updated=OLD), book("coolbet", soft)), "stale sharp price"
    assert none(book("coolbet", soft)), "no sharp price at all"
    assert none(book("pinnacle", PIN), book("coolbet", {**soft, "Arsenal": 2.06})), "edge under 3%"
    assert none(book("pinnacle", PIN), book("coolbet", {**soft, "Arsenal": 3.00})), "too good to be true"
    assert none(book("pinnacle", PIN), book("coolbet", soft), hours=0.1), "about to start"
    assert none(book("pinnacle", PIN), book("unibet", soft)), "a bookmaker that is not mine"
    assert none(book("pinnacle", {"Arsenal": 1.7, "Draw": 3.0, "Chelsea": 3.2}), book("coolbet", soft)), "sharp margin too wide to trust"
    # the exchange says Arsenal is 2.24/2.26: 2.20 at Coolbet is not value there, so no alert
    bf = book("betfair_ex_eu", {"Arsenal": 2.24, "Draw": 3.4, "Chelsea": 3.6}, lay={"Arsenal": 2.26, "Draw": 3.45, "Chelsea": 3.65})
    assert none(book("pinnacle", PIN), bf, book("coolbet", soft)), "exchange disagrees"


def test_exchange_fallback_and_confirm():
    bf = book("betfair_ex_eu", {"Arsenal": 2.06, "Draw": 3.65, "Chelsea": 4.0}, lay={"Arsenal": 2.08, "Draw": 3.7, "Chelsea": 4.1})
    soft = book("coolbet", {"Arsenal": 2.20, "Draw": 3.3, "Chelsea": 3.5})
    _, values, _ = E.analyse(event(bf, soft), CFG, NOW, 1000)
    assert len(values) == 1 and values[0]["source"] == "betfair_ex_eu" and not values[0]["confirmed"]
    _, values, _ = E.analyse(event(book("pinnacle", PIN), bf, soft), CFG, NOW, 1000)
    assert len(values) == 1 and values[0]["source"] == "pinnacle" and values[0]["confirmed"]
    wide = book("betfair_ex_eu", {"Arsenal": 1.9, "Draw": 3.0, "Chelsea": 3.5}, lay={"Arsenal": 2.3, "Draw": 4.2, "Chelsea": 4.6})
    assert E.analyse(event(wide, soft), CFG, NOW, 1000)[1] == [], "thin exchange market is ignored"


def test_arb():
    a = book("coolbet", {"Arsenal": 2.30, "Draw": 3.2, "Chelsea": 3.3})
    b = book("betsson", {"Arsenal": 1.95, "Draw": 3.9, "Chelsea": 4.4})
    _, _, arbs = E.analyse(event(a, b), CFG, NOW, 1000)
    inv = 1 / 2.30 + 1 / 3.9 + 1 / 4.4
    assert len(arbs) == 1 and close(arbs[0]["profit"], 1 / inv - 1, 1e-4)
    legs = arbs[0]["legs"]
    assert close(sum(l["share"] for l in legs), 1, 1e-3)
    returns = [l["share"] * l["odds"] for l in legs]
    assert max(returns) - min(returns) < 1e-3 and min(returns) > 1, "every outcome pays the same, above the stake"
    _, _, none = E.analyse(event(a, book("betsson", {"Arsenal": 2.25, "Draw": 3.3, "Chelsea": 3.4})), CFG, NOW, 1000)
    assert none == []
    _, _, one = E.analyse(event(book("coolbet", {"Arsenal": 3.2, "Draw": 3.9, "Chelsea": 4.4})), CFG, NOW, 1000)
    assert one == [], "one bookmaker alone is a pricing mistake, not an arb"


def test_paper():
    paper = E.new_paper(CFG, NOW)
    ev = event(book("pinnacle", PIN), book("coolbet", {"Arsenal": 2.20, "Draw": 3.3, "Chelsea": 3.5}))
    row, values, _ = E.analyse(ev, CFG, NOW, paper["bankroll"])
    arb = {"kind": "arb", "key": "e1|h2h|arb", "event": "e1", "market": "h2h"}
    assert len(E.paper_place(paper, [arb] + values)) == 1 and E.paper_place(paper, values) == [], "never the same bet twice"
    bet = paper["open"][0]
    stake = bet["stake"]
    # the sharp line moves towards the bet: closing line value goes up
    later = NOW + timedelta(hours=2)
    moved = event(book("pinnacle", {"Arsenal": 1.90, "Draw": 3.6, "Chelsea": 4.2}, updated=E.iso(later)))
    E.paper_update_close(paper, [E.analyse(moved, CFG, later, 1000)[0]], later)
    assert bet["clv"] > 0.08
    score = lambda h, a, done=True: [{"id": "e1", "completed": done, "home_team": "Arsenal", "away_team": "Chelsea",   # noqa: E731
                                      "scores": [{"name": "Arsenal", "score": str(h)}, {"name": "Chelsea", "score": str(a)}]}]
    end = NOW + timedelta(hours=8)
    assert E.paper_settle(paper, score(1, 0, done=False), end) == [], "not finished yet"
    done = E.paper_settle(paper, score(2, 1), end)
    assert done[0]["result"] == "win" and close(done[0]["pnl"], round(stake * 1.2, 2)) and close(paper["bankroll"], 1000 + done[0]["pnl"])
    s = E.paper_summary(paper)
    assert s["won"] == 1 and s["open"] == 0 and close(s["roi"], 1.2, 1e-3) and s["beat_close"] == 1
    assert E.result_of({"market": "h2h", "outcome": "Arsenal"}, score(1, 1)[0]) == "lose"
    assert E.result_of({"market": "h2h", "outcome": "Draw"}, score(1, 1)[0]) == "win"
    assert E.result_of({"market": "totals 2.5", "outcome": "Over"}, score(2, 1)[0]) == "win"
    assert E.result_of({"market": "totals 2.5", "outcome": "Under"}, score(2, 1)[0]) == "lose"
    assert E.result_of({"market": "totals 3", "outcome": "Over"}, score(2, 1)[0]) == "void"
    # a game with no result is voided after 4 days, stake returned
    p2 = E.new_paper(CFG, NOW)
    E.paper_place(p2, values)
    assert E.paper_settle(p2, [], NOW + timedelta(days=2)) == []
    v = E.paper_settle(p2, [], NOW + timedelta(days=5))
    assert v[0]["result"] == "void" and p2["bankroll"] == 1000


def test_budget():
    first = datetime(2026, 11, 1, 0, 0, tzinfo=E.UTC)
    assert close(E.days_to_reset(first, 1), 30) and close(E.days_to_reset(NOW, 1), 16.5)
    assert E.run_allowance(500, first, CFG) == 2            # 500 credits over 180 runs
    assert E.run_allowance(20000, first, CFG) >= 100        # the paid plan covers every league every run
    assert E.run_allowance(0, NOW, CFG) == 0
    spent = sum(E.run_allowance(500, first, CFG) for _ in range(30 * CFG["runs_per_day"]))
    assert spent <= 500, "never plans to spend more than the month's credits"
    cands = [("a", 3, "2026-11-14T18:00:00Z"), ("b", 2, "2026-11-14T15:00:00Z"), ("c", 0, ""), ("d", 1, "2026-11-15T10:00:00Z")]
    last = {"b": "2026-11-14T08:00:00Z", "d": "2026-11-14T04:00:00Z"}
    assert E.pick_leagues(cands, last, 2, NOW) == ["a", "d"], "never scanned first, then the longest wait"
    assert E.pick_leagues(cands, last, 9, NOW) == ["a", "d", "b"], "leagues with no games cost nothing"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"{len(tests)} tests passed")
