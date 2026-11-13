"""Checks of the paper trading maths. Run: python tests/test_paper.py"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scanner"))
import paper

UTC = timezone.utc
T0 = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)


def cfg(exit_="trail", fee=0.0, **kw):
    main = {"risk": {"stop_atr": 2.0}, "paper": {
        "fee_pct": {"crypto": fee, "us": fee, "eu": fee},
        "rules": [{"name": "US daily long", "markets": ["us"], "tf": "1d", "side": "long", "entry_tech": 40,
                   "exit": exit_, "risk_pct": 1.0, "max_hold_bars": 40}], **kw}}
    return paper.settings(main)


def item(sym, tech=50, label="BUY", price=100.0, atr=1.0, tf="1d", kind="stock"):
    return {"symbol": sym, "name": sym, "kind": kind, "tf": tf, "score": tech, "tech": tech, "label": label,
            "price": price, "atr": atr}


def bars(start, rows):
    idx = pd.date_range(start=start, periods=len(rows), freq="5min")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def scan(state, items, t, c, feed=None, labels=None, price=100.0):
    feed = feed or {}
    return paper.run(state, items, labels or {}, c,
                     lambda s, k, since: feed.get(s, pd.DataFrame()), lambda s, k: price, t)


C = cfg()
# first scan only remembers what already qualifies
s = scan(None, [item("AAA")], T0, C)
assert s["open"] == [] and s["prev_entry"] == ["AAA|1d|long"]

# a new qualifying signal opens one trade, sized so the stop costs 1% of the account
s = scan(s, [item("AAA"), item("BBB")], T0, C)
tr = s["open"][0]
assert tr["symbol"] == "BBB" and tr["stop"] == 98 and tr["target"] is None
assert tr["risk"] == 4.0 and tr["notional"] == 200.0, tr        # 1% risk at a 2% stop would be 500; capped at 20% of the account

# things that must NOT open: crypto, shorts, weak scores, European stock (no rule in this test), same asset twice
no = [item("BTCUSDT", kind="crypto"), item("CCC", tech=-60, label="STRONG SELL"), item("DDD", tech=35),
      item("SAP.DE"), item("BBB")]
s = scan(s, [item("AAA"), item("BBB")] + no, T0 + timedelta(minutes=15), C)
assert [t["symbol"] for t in s["open"]] == ["BBB"]

# trailing exit: runs to 106, stop trails 3 ATR behind (103), then falls back -> +3% on 200 = +6.00
feed = {"BBB": bars(T0, [[100, 106, 99.5, 105.5], [105.5, 105.6, 102.5, 102.8]])}
s = scan(s, [item("AAA"), item("BBB")], T0 + timedelta(minutes=30), C, feed)
c = s["closed"][-1]
assert c["reason"] == "trailing stop" and c["exit"] == 103 and c["pnl"] == 6.0 and c["r"] == 1.5, c
assert s["balance"] == 1006.0 and s["stats"]["sum_r"] == 1.5

# plain stop: -2% on the position
s = scan(s, [item("AAA"), item("EEE")], T0 + timedelta(hours=1), C)
n = s["open"][0]["notional"]
feed = {"EEE": bars(T0 + timedelta(hours=1), [[100, 100.5, 97.5, 98]])}
s = scan(s, [item("AAA"), item("EEE")], T0 + timedelta(hours=2), C, feed)
c = s["closed"][-1]
assert c["reason"] == "stop" and abs(c["pnl"] + n * 0.02) < 0.011, c

# gap below the stop fills at the open
s = scan(s, [item("AAA"), item("FFF")], T0 + timedelta(hours=3), C)
feed = {"FFF": bars(T0 + timedelta(hours=3), [[95, 96, 94, 95]])}
s = scan(s, [item("AAA"), item("FFF")], T0 + timedelta(hours=4), C, feed)
assert s["closed"][-1]["exit"] == 95

# signal flip closes at the live price; time limit closes after 40 daily bars
s = scan(s, [item("AAA"), item("GGG")], T0 + timedelta(hours=5), C)
s = scan(s, [item("AAA")], T0 + timedelta(hours=6), C, labels={("GGG", "1d"): "SELL"}, price=101)
assert s["closed"][-1]["reason"].startswith("signal flipped")
s = scan(s, [item("AAA"), item("HHH")], T0 + timedelta(hours=7), C)
s = scan(s, [item("AAA")], T0 + timedelta(days=41), C, price=100)
assert s["closed"][-1]["reason"] == "time limit"

# partial exit: half sold at +1R (102), stop to entry; the rest stopped at entry -> +2.00 total on 200
P = cfg("partial")
s = scan(None, [item("AAA")], T0, P)
s = scan(s, [item("AAA"), item("BBB")], T0, P)
feed = {"BBB": bars(T0, [[100, 102.2, 99.8, 102], [102, 102.1, 99.9, 100.2]])}
s = scan(s, [item("AAA"), item("BBB")], T0 + timedelta(minutes=30), P, feed)
c = s["closed"][-1]
assert c["pnl"] == 2.0 and c["exit"] == 100 and c["r"] == 0.5 and s["balance"] == 1002.0, c

# exposure cap: at 1.5x the account only 7 positions of 200 fit
s = scan(None, [item("AAA")], T0, C)
s = scan(s, [item("AAA")] + [item(f"S{i}") for i in range(12)], T0, C)
assert len(s["open"]) == 7 and sum(t["notional"] for t in s["open"]) <= 1500

# fees: 0.05% each side on 200 = 0.20 at an unchanged price
F = cfg(fee=0.05)
s = scan(None, [item("AAA")], T0, F)
s = scan(s, [item("AAA"), item("BBB")], T0, F)
s = scan(s, [item("AAA")], T0 + timedelta(hours=1), F, labels={("BBB", "1d"): "SELL"}, price=100)
assert s["closed"][-1]["pnl"] == -0.2

# an old (version 1) test is restarted cleanly, keeping its id so the dashboard accepts it
s = scan({"epoch": "abc", "balance": 950, "open": [], "closed": []}, [item("AAA")], T0, C)
assert s["version"] == 2 and s["balance"] == 1000 and s["epoch"] == "abc"

# extra account: crypto volatility breakout, needs a context flag, wide stop, ignores signal flips
ACC = {"title": "Crypto (experiment)", "start_balance": 1000, "max_position_pct": 15, "max_exposure": 1.0,
       "fee_pct": {"crypto": 0.0, "us": 0.0, "eu": 0.0},
       "rules": [{"name": "vb", "markets": ["crypto"], "tf": "1d", "side": "long", "trigger": "vol_breakout",
                  "max_volume_rank": 80, "requires": ["btc_above_ema100"], "exit": "trail", "stop_atr": 3.0,
                  "trail_atr": 5.0, "risk_pct": 0.5, "max_hold_bars": 120, "flip_exit": False}]}
A = paper.settings({"risk": {"stop_atr": 2.0}}, ACC)


def coin(sym, setups=("vol_breakout",), rank=5):
    return {**item(sym, tech=10, label="HOLD", kind="crypto"), "setups": list(setups), "rank": rank}


def scan_ctx(state, items, t, flags, **kw):
    return paper.run(state, items, kw.get("labels", {}), A, lambda s, k, since: pd.DataFrame(),
                     lambda s, k: kw.get("price", 100.0), t, context=flags)


s = scan_ctx(None, [], T0, {"btc_above_ema100": True})
# not opened: gate off
s = scan_ctx(s, [coin("XUSDT")], T0, {"btc_above_ema100": False})
assert s["open"] == []
# not opened: no setup, rank too low, or a stock
s = scan_ctx(s, [], T0 + timedelta(days=1), {"btc_above_ema100": True})
s = scan_ctx(s, [coin("YUSDT", setups=()), coin("ZUSDT", rank=200), {**item("AAPL"), "setups": ["vol_breakout"]}],
             T0 + timedelta(days=1), {"btc_above_ema100": True})
assert s["open"] == []
# opened: stop 3 ATR away, risk 0.5% of the account, trailing 5 ATR, title set
s = scan_ctx(s, [coin("XUSDT")], T0 + timedelta(days=2), {"btc_above_ema100": True})
tr = s["open"][0]
assert tr["stop"] == 97 and tr["risk"] == 4.5 and tr["notional"] == 150 and tr["trail_atr"] == 5.0, tr
assert s["title"] == "Crypto (experiment)" and "Bitcoin is above its 100-day average" in s["rules_text"][1]
# a SELL label does not close it (flip_exit off)
s = scan_ctx(s, [coin("XUSDT")], T0 + timedelta(days=3), {"btc_above_ema100": True}, labels={("XUSDT", "1d"): "SELL"})
assert len(s["open"]) == 1

print("All paper trading checks passed")
