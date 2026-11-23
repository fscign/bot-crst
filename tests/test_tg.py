"""Checks of the Telegram signal reader. Run: python tests/test_tg.py"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scanner"))
import tg_signals as tg

UTC = timezone.utc
T0 = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)

# ---- parsing typical formats
s = tg.parse_signal("#BTC/USDT LONG\nEntry: 60,000 - 61,000\nTargets: 62,000 - 63,500 - 65,000\nStop Loss: 58,500\nLeverage: Cross 10x")
assert s == {"action": "open", "symbol": "BTCUSDT", "side": "long", "entry_low": 60000, "entry_high": 61000,
             "targets": [62000, 63500, 65000], "stop": 58500, "leverage": 10}, s
s = tg.parse_signal("📉 SHORT $SOL\nEntry zone 145.2 - 147\nTP1 142\nTP2 139.5\nTP3 135\nSL 150.4\n20x")
assert s["side"] == "short" and s["symbol"] == "SOLUSDT" and s["targets"] == [142, 139.5, 135] and s["stop"] == 150.4 \
    and s["entry_low"] == 145.2 and s["leverage"] == 20, s
s = tg.parse_signal("Coin: DOGE\nBuy 0.1520\nTake profit 0.1600, 0.1700\nStoploss 0.1450")
assert s["symbol"] == "DOGEUSDT" and s["side"] == "long" and s["targets"] == [0.16, 0.17] and s["stop"] == 0.145, s
s = tg.parse_signal("ETHUSDT.P Long\nEntry 3,250\nSL 3,180")
assert s["symbol"] == "ETHUSDT" and s["entry_low"] == 3250 and s["stop"] == 3180 and s["targets"] == [], s
assert tg.parse_signal("#BTC is looking strong today, what do you think?") is None
assert tg.parse_signal("Close #ETH/USDT now") == {"action": "close", "symbol": "ETHUSDT"}
assert tg.parse_signal("Good morning everyone!") is None
s = tg.parse_signal("#ADA/USDT LONG\nEntry 0.90\nSL 0.95")                # stop on the wrong side: ignored
assert s["stop"] is None

# ---- reading the public page format
page = '''<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message" data-post="chan/101">
<div class="tgme_widget_message_text js-message_text" dir="auto">#BTC/USDT LONG<br/>Entry: 60000<br/>SL: 58000</div>
<time datetime="2026-11-23T09:50:00+00:00" class="time">09:50</time></div></div>
<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message" data-post="chan/102">
<div class="tgme_widget_message_text js-message_text" dir="auto">Hello &amp; welcome</div>
<time datetime="2026-11-23T09:55:00+00:00" class="time">09:55</time></div></div>'''
posts = tg.parse_page(page, "chan")
assert [p["id"] for p in posts] == [101, 102] and posts[0]["text"] == "#BTC/USDT LONG\nEntry: 60000\nSL: 58000"
assert posts[1]["text"] == "Hello & welcome"

# ---- trading a channel
CFG = tg.settings({"telegram": {}}, {"channel": "chan", "fee_pct": 0.0})


def post(i, text, mins_ago=5, at=T0):
    return {"id": i, "time": at - timedelta(minutes=mins_ago), "text": text, "channel": "chan"}


def bars(start, rows):
    idx = pd.date_range(start=start, periods=len(rows), freq="5min")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def run(state, posts, t, price=100.0, feed=None):
    return tg.run_channel(state, CFG, lambda s, k, since: (feed or {}).get(s, pd.DataFrame()), lambda s, k: price, posts, t)


st = run(None, [post(1, "#XYZ/USDT LONG\nEntry 100\nTP 105 110\nSL 95")], T0)     # first look: only remembers the last post
assert st["open"] == [] and st["last_post"] == 1
sig = "#XYZ/USDT LONG\nEntry 99 - 101\nTargets 104 / 108\nStop 96\nLeverage 10x"
st = run(st, [post(1, "old"), post(2, sig)], T0)
tr = st["open"][0]
assert tr["entry"] == 100 and tr["stop"] == 96 and tr["targets"] == [104, 108] and tr["risk"] == 10.0 and tr["notional"] == 250, tr
assert st["signals"][-1]["result"] == "opened" and tr["channel_lev"] == 10
# target 1 hit: half sold at 104, stop to 100; then target 2: rest at 108 -> 250*0.04/2 + 250*0.08/2 = 5 + 10
feed = {"XYZUSDT": bars(T0, [[100, 104.5, 99.5, 104], [104, 108.2, 103.5, 108]])}
st = run(st, [], T0 + timedelta(minutes=15), feed=feed)
c = st["closed"][-1]
assert c["reason"] == "all 2 targets hit" and c["pnl"] == 15.0 and st["balance"] == 1015.0, c
# target 1, then back to the entry: half at +4%, half at 0 -> +5
st = run(st, [post(3, "#ABC/USDT LONG\nEntry 100\nTP1 104\nTP2 110\nSL 96")], T0 + timedelta(hours=1))
feed = {"ABCUSDT": bars(T0 + timedelta(hours=1), [[100, 104.2, 99.8, 103], [103, 103.1, 99.5, 99.7]])}
st = run(st, [], T0 + timedelta(hours=2), feed=feed)
c = st["closed"][-1]
assert c["reason"] == "stop (moved to entry after target 1)" and c["pnl"] == round(c["margin"] * 0.02, 2) and c["r"] == 0.5, c
# price above the zone: limit order, filled later at the zone, then stopped
st = run(st, [post(4, "#LMN/USDT LONG\nEntry 95 - 96\nTP 100\nSL 92")], T0 + timedelta(hours=3), price=98)
tr = st["open"][0]
assert tr["pending"] and tr["limit"] == 96 and st["signals"][-1]["result"].startswith("limit order")
feed = {"LMNUSDT": bars(T0 + timedelta(hours=3), [[98, 98.5, 95.8, 96.2], [96.2, 96.5, 91.5, 92]])}
st = run(st, [], T0 + timedelta(hours=4), feed=feed)
c = st["closed"][-1]
assert c["entry"] == 96 and c["exit"] == 92 and c["reason"] == "stop", c
# a limit order that never fills is cancelled without a result
n = st["stats"]["trades"]
st = run(st, [post(5, "#QRS/USDT LONG\nEntry 50\nSL 48")], T0 + timedelta(hours=5), price=53)
st = run(st, [], T0 + timedelta(hours=30), price=53)
assert st["open"] == [] and st["stats"]["trades"] == n and st["cancelled"][-1]["symbol"] == "QRSUSDT"
# short, then the channel says close
st = run(st, [post(6, "SHORT #DEF/USDT\nEntry 100\nTP 90\nSL 105", at=T0 + timedelta(hours=31))], T0 + timedelta(hours=31))
assert st["open"][0]["side"] == "short"
st = run(st, [post(7, "Close DEF/USDT", at=T0 + timedelta(hours=32))], T0 + timedelta(hours=32), price=97)
c = st["closed"][-1]
assert c["reason"] == "channel said close" and c["pnl"] > 0, c
# skipped: price already past target 1; stale post; feed down keeps state
st = run(st, [post(8, "#GHI/USDT LONG\nEntry 100\nTP 103\nSL 97", at=T0 + timedelta(hours=33))], T0 + timedelta(hours=33), price=104)
assert st["signals"][-1]["result"] == "price already past target 1"
st = run(st, [post(9, "#JKL/USDT LONG\nEntry 100\nSL 97", mins_ago=60 * 8, at=T0 + timedelta(hours=34))], T0 + timedelta(hours=34))
assert st["signals"][-1]["result"] == "too old when first seen"
st = run(st, None, T0 + timedelta(hours=35))
assert "feed_error" in st and st["last_post"] == 9

# ---- several channels as one feed inside an existing account
import paper
FC = tg.feed_settings({"telegram": {"channels": [{"channel": "-1", "name": "A"}, {"channel": "-2", "name": "B"}]}})
FC["fee_pct"] = {"crypto": 0.0, "us": 0.0, "eu": 0.0}
acc = paper.fresh_state(FC)
tg.process_feed(acc, [("A", [post(1, "hi")]), ("B", [post(5, "hi")])], FC, lambda s, k: 100.0, T0)
assert acc["open"] == [] and acc["tg_last"] == {"A": 1, "B": 5}
same = "#XYZ/USDT LONG\nEntry 100\nTP 110\nSL 95"
tg.process_feed(acc, [("A", [post(2, same)]), ("B", [post(6, same), post(7, "#BTC is nice today")])], FC,
                lambda s, k: 100.0, T0)
assert len(acc["open"]) == 1 and acc["open"][0]["rule"] == "A post 2", acc["open"]
assert acc["signals"][-1]["result"].startswith("already holding") and acc["signals"][-1]["channel"] == "B"
assert acc["unread"][-1]["text"] == "#BTC is nice today"
tg.process_feed(acc, [("A", None), ("B", [])], FC, lambda s, k: 100.0, T0)
assert acc["feed_error_channels"] == ["A"] and len(acc["open"]) == 1
assert "both channels" in tg.feed_rules_text(FC)[0] and "A and B" in tg.feed_rules_text(FC)[0]

print("All Telegram signal checks passed")
