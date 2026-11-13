"""Checks of the paper trading maths. Run: python tests/test_paper.py"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scanner"))
import paper

UTC = timezone.utc
CFG = paper.settings({"risk": {"stop_atr": 2.0, "target_atr": 3.0}, "paper": {"fee_pct": 0.0}})
T0 = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)


def item(sym, score=60, label="STRONG BUY", price=100.0, atr=1.0, lev=5, tf="1h", proven=True):
    return {"symbol": sym, "name": sym, "kind": "crypto", "tf": tf, "score": score, "label": label,
            "price": price, "atr": atr, "lev": {"x": lev}, "proven": proven}


def bars(start, rows):
    idx = pd.date_range(start=start, periods=len(rows), freq="5min")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def scan(state, items, t, feed=None, labels=None, price=100.0):
    feed = feed or {}
    return paper.run(state, items, labels or {}, CFG,
                     lambda s, k, since: feed.get(s, pd.DataFrame()), lambda s, k: price, t)


# stake rule
assert paper.stake_for(1000, CFG) == 10
assert paper.stake_for(600, CFG) == 10          # below 1000: still 10
assert paper.stake_for(2500, CFG) == 25         # above 1000: 1%

# first scan only remembers existing strong signals
s = scan(None, [item("AAA")], T0)
assert s["open"] == [] and s["prev_strong"] == ["AAA|1h|long"]

# a new strong signal opens one trade; it does not reopen while still strong
s = scan(s, [item("AAA"), item("BBB")], T0)
assert [t["symbol"] for t in s["open"]] == ["BBB"]
tr = s["open"][0]
assert tr["margin"] == 10 and tr["notional"] == 50 and tr["stop"] == 98 and tr["target"] == 103
s = scan(s, [item("AAA"), item("BBB")], T0 + timedelta(minutes=15))
assert len(s["open"]) == 1

# target hit: +3% x 5x leverage on 10 USD = +1.50
feed = {"BBB": bars(T0, [[100, 101, 99.5, 100.5], [100.5, 103.2, 100.4, 103]])}
s = scan(s, [item("AAA"), item("BBB")], T0 + timedelta(minutes=30), feed)
c = s["closed"][-1]
assert c["reason"] == "target" and c["pnl"] == 1.5 and s["balance"] == 1001.5, c

# short that hits its stop: -2% x 5 on 10 USD = -1.00
s = scan(s, [item("AAA"), item("CCC", score=-60, label="STRONG SELL")], T0 + timedelta(minutes=45))
assert s["open"][0]["side"] == "short"
feed = {"CCC": bars(T0 + timedelta(minutes=45), [[100, 102.5, 99.8, 102.2]])}
s = scan(s, [item("AAA"), item("CCC", score=-60, label="STRONG SELL")], T0 + timedelta(hours=1), feed)
c = s["closed"][-1]
assert c["reason"] == "stop" and c["pnl"] == -1.0 and s["balance"] == 1000.5, c

# trailing stop: price runs to 102.5, stop follows to 100.5, then falls back
s = scan(s, [item("AAA"), item("DDD")], T0 + timedelta(hours=2))
feed = {"DDD": bars(T0 + timedelta(hours=2), [[100, 102.5, 99.9, 102.4], [102.4, 102.4, 100.2, 100.3]])}
s = scan(s, [item("AAA"), item("DDD")], T0 + timedelta(hours=3), feed)
c = s["closed"][-1]
assert c["reason"] == "trailing stop" and c["exit"] == 100.5, c

# gap below the stop fills at the open, and a loss never exceeds the stake (liquidation)
s = scan(s, [item("AAA"), item("EEE", lev=20)], T0 + timedelta(hours=4))
feed = {"EEE": bars(T0 + timedelta(hours=4), [[90, 91, 89, 90]])}
bal = s["balance"]
s = scan(s, [item("AAA"), item("EEE", lev=20)], T0 + timedelta(hours=5), feed)
c = s["closed"][-1]
assert c["reason"] == "liquidated" and c["pnl"] == -c["margin"] and s["balance"] == round(bal - c["margin"], 2), c

# signal flip closes at the live price
s = scan(s, [item("AAA"), item("FFF")], T0 + timedelta(hours=6))
s = scan(s, [item("AAA")], T0 + timedelta(hours=7), labels={("FFF", "1h"): "SELL"}, price=101)
assert s["closed"][-1]["reason"].startswith("signal flipped") and s["closed"][-1]["pnl"] == 0.5

# time limit: 1h trades close after 48 hours
s = scan(s, [item("AAA"), item("GGG")], T0 + timedelta(hours=8))
s = scan(s, [item("AAA")], T0 + timedelta(hours=57), price=100)
assert s["closed"][-1]["reason"] == "time limit"

# unproven strong signals are ignored when require_proven is on
s = scan(s, [item("AAA"), item("HHH", proven=False)], T0 + timedelta(hours=58))
assert not any(t["symbol"] == "HHH" for t in s["open"])

# balance below 1000 still stakes 10; above 1000 stakes 1%
s["balance"] = 3000.0
s = scan(s, [item("AAA"), item("III")], T0 + timedelta(hours=59))
assert [t["margin"] for t in s["open"] if t["symbol"] == "III"] == [30.0]

# fees: 0.1% each side on 50 USD notional = 0.10 total at an unchanged price
cfg_fee = paper.settings({"risk": {"stop_atr": 2.0, "target_atr": 3.0}})
st = paper.fresh_state(cfg_fee)
tr = {"id": "x", "symbol": "Z", "name": "Z", "kind": "crypto", "tf": "1h", "side": "long", "label": "STRONG BUY",
      "opened": "2026-11-13T10:00:00Z", "entry": 100.0, "margin": 10, "lev": 5, "notional": 50}
assert paper.settle(st, tr, 100.0, "test", T0, cfg_fee)["pnl"] == -0.1

print("All paper trading checks passed")
