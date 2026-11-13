"""
Paper trading test, version 2: a virtual account that only trades the kinds of
signal that made money in the research backtest (research/backtest.py), sized
by how strong the evidence is.

Rules live in scanner/config.json, section "paper". Each rule says which
market, timeframe and side may be traded, the score that triggers an entry,
how to exit, and how much of the account to risk.

- Entry: when the technical score crosses the rule's entry level, at the live
  price. One open trade per asset.
- Size: the position is sized so that being stopped out costs risk_pct of the
  account (more for rules with stronger evidence). No leverage per position;
  total open positions are capped at max_exposure x the account.
- Exit "trail": stop 2 ATR below entry, then trailing 3 ATR behind the best
  price. No profit target: winners are left to run.
- Exit "partial": sell half at +1R (profit = the amount risked), move the stop
  to the entry price, trail the rest 3 ATR behind the best price.
- Also exits when the signal flips to the other side, or at the time limit.
- Exits are found by replaying 5-minute bars, so a stop hit between two scans
  is filled at the stop price (or at the bar's open after a gap).
- A fee is charged on both sides of every trade.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd

UTC = timezone.utc
TF = {"1h": timedelta(hours=1), "4h": timedelta(hours=4), "1d": timedelta(days=1)}
VERSION = 2

CONTEXT_LABELS = {
    "btc_above_ema100": "Bitcoin is above its 100-day average",
    "btc_above_ema200": "Bitcoin is above its 200-day average",
}

DEFAULTS = {
    "title": "Stocks (tested rules)",
    "intro": "Only kinds of signal that made money in the research backtest are traded; everything else is ignored.",
    "start_balance": 1000,
    "max_exposure": 1.5,            # total open positions, as a multiple of the account
    "max_position_pct": 20,         # one position, % of the account
    "trail_atr": 3.0,
    "fee_pct": {"crypto": 0.1, "us": 0.05, "eu": 0.05},
    "keep_closed": 300,
    "rules": [
        {"name": "US stocks, daily, long", "markets": ["us"], "tf": "1d", "side": "long",
         "entry_tech": 40, "exit": "trail", "risk_pct": 0.75, "max_hold_bars": 40},
        {"name": "European stocks, daily, long", "markets": ["eu"], "tf": "1d", "side": "long",
         "entry_tech": 40, "exit": "trail", "risk_pct": 0.5, "max_hold_bars": 40},
        {"name": "US stocks, 4h, long", "markets": ["us"], "tf": "4h", "side": "long",
         "entry_tech": 40, "exit": "trail", "risk_pct": 0.25, "max_hold_bars": 84},
    ],
}


def now_iso(t: datetime | None = None) -> str:
    return (t or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def market_of(it: dict) -> str:
    if it["kind"] == "crypto":
        return "crypto"
    return "eu" if "." in it["symbol"] else "us"


def settings(main_cfg: dict, account: dict | None = None) -> dict:
    """Settings for the main account (config.json "paper"), or for one extra account, merged over the defaults."""
    user = account if account is not None else main_cfg.get("paper", {})
    cfg = {**DEFAULTS, **user, "stop_atr": main_cfg["risk"]["stop_atr"]}
    cfg["fee_pct"] = {**DEFAULTS["fee_pct"], **user.get("fee_pct", {})} if isinstance(user.get("fee_pct", {}), dict) \
        else {m: user["fee_pct"] for m in DEFAULTS["fee_pct"]}
    return cfg


def fresh_state(cfg: dict, epoch: str | None = None) -> dict:
    start = float(cfg["start_balance"])
    return {
        "version": VERSION, "epoch": epoch or uuid.uuid4().hex[:8], "started": now_iso(),
        "start": start, "balance": start, "open": [], "closed": [], "prev_entry": None, "curve": [],
        "stats": {"trades": 0, "wins": 0, "gross_win": 0.0, "gross_loss": 0.0, "fees": 0.0, "sum_r": 0.0,
                  "peak": start, "max_dd_pct": 0.0},
    }


def rule_for(it: dict, cfg: dict) -> dict | None:
    """The rule this item currently triggers, if any."""
    m = market_of(it)
    for rule in cfg["rules"]:
        if m in rule["markets"] and it["tf"] == rule["tf"]:
            if rule.get("max_volume_rank") and (it.get("rank") or 10**9) > rule["max_volume_rank"]:
                continue                                  # only the most traded coins, as in the research
            trigger = rule.get("trigger", "tech")
            if trigger != "tech":                         # a named chart setup found by the scanner
                if trigger in it.get("setups", []):
                    return rule
                continue
            tech = it.get("tech", it["score"])
            if (rule["side"] == "long" and tech >= rule["entry_tech"]) or \
               (rule["side"] == "short" and tech <= -rule["entry_tech"]):
                return rule
    return None


def unrealized(tr: dict) -> float:
    side = 1 if tr["side"] == "long" else -1
    move = side * (tr["last"] / tr["entry"] - 1)
    fee = tr["fee_pct"] / 100
    return tr["notional"] * move - tr["notional"] * fee * (1 + tr["last"] / tr["entry"])


def equity_of(state: dict) -> float:
    return state["balance"] + sum(unrealized(tr) for tr in state["open"])


# --------------------------------------------------------------------------
# Opening
# --------------------------------------------------------------------------
def open_new(state: dict, items: list[dict], cfg: dict, price_now, t: datetime, context: dict | None = None) -> list[dict]:
    hits = {}
    for it in items:
        rule = rule_for(it, cfg)
        if rule:
            hits[f"{it['symbol']}|{it['tf']}|{rule['side']}"] = (it, rule)
    prev = state["prev_entry"]
    state["prev_entry"] = sorted(hits)
    if prev is None:                      # first scan: remember what already qualifies, open nothing
        return []
    opened = []
    held = {tr["symbol"] for tr in state["open"]}
    new = sorted(set(hits) - set(prev), key=lambda k: -abs(hits[k][0].get("tech", hits[k][0]["score"])))
    for key in new:
        it, rule = hits[key]
        if it["symbol"] in held:
            continue
        if not all((context or {}).get(flag) for flag in rule.get("requires", [])):
            continue                                      # the market conditions this rule needs are not met
        equity = equity_of(state)
        price = price_now(it["symbol"], it["kind"]) or it["price"]
        atr = float(it["atr"])
        stop_dist = rule.get("stop_atr", cfg["stop_atr"]) * atr
        if not (price > 0 and stop_dist > 0):
            continue
        risk_money = equity * rule["risk_pct"] / 100
        notional = min(risk_money / (stop_dist / price), equity * cfg["max_position_pct"] / 100)
        exposure = sum(tr["notional"] for tr in state["open"])
        if notional < 5 or exposure + notional > equity * cfg["max_exposure"]:
            continue
        side = 1 if rule["side"] == "long" else -1
        tr = {
            "id": uuid.uuid4().hex[:8], "symbol": it["symbol"], "name": it["name"], "kind": it["kind"],
            "tf": it["tf"], "side": rule["side"], "rule": rule["name"], "mode": rule["exit"],
            "label": it["label"], "score": it.get("tech", it["score"]),
            "opened": now_iso(t), "checked": now_iso(t), "entry": price, "last": price,
            "notional": round(notional, 2), "size0": round(notional, 2), "margin": round(notional, 2), "lev": 1,
            "risk": round(notional * stop_dist / price, 2), "atr": atr, "fee_pct": cfg["fee_pct"][market_of(it)],
            "stop0": price - side * stop_dist, "stop": price - side * stop_dist, "target": None,
            "partial_at": price + side * stop_dist if rule["exit"] == "partial" else None,
            "banked": 0.0, "best": price, "trail_atr": rule.get("trail_atr", cfg["trail_atr"]),
            "flip_exit": rule.get("flip_exit", True),
            "until": now_iso(t + TF[it["tf"]] * rule["max_hold_bars"]),
        }
        state["open"].append(tr)
        held.add(it["symbol"])
        opened.append(tr)
    return opened


# --------------------------------------------------------------------------
# Closing
# --------------------------------------------------------------------------
def book(state: dict, tr: dict, share: float, price: float) -> float:
    """Close `share` of the ORIGINAL position at `price`; returns the profit booked."""
    side = 1 if tr["side"] == "long" else -1
    part = tr["size0"] * share
    fee = tr["fee_pct"] / 100
    fees = part * fee * (1 + price / tr["entry"])
    pnl = round(part * side * (price / tr["entry"] - 1) - fees, 2)
    state["balance"] = round(state["balance"] + pnl, 2)
    state["stats"]["fees"] = round(state["stats"]["fees"] + fees, 2)
    tr["banked"] = round(tr["banked"] + pnl, 2)
    tr["notional"] = round(tr["notional"] - part, 2)
    return pnl


def settle(state: dict, tr: dict, price: float, reason: str, t: datetime, cfg: dict) -> dict:
    book(state, tr, tr["notional"] / tr["size0"], price)
    pnl = tr["banked"]
    st = state["stats"]
    st["trades"] += 1
    r = round(pnl / tr["risk"], 2) if tr["risk"] else 0.0
    st["sum_r"] = round(st["sum_r"] + r, 2)
    if pnl > 0:
        st["wins"] += 1
        st["gross_win"] = round(st["gross_win"] + pnl, 2)
    else:
        st["gross_loss"] = round(st["gross_loss"] - pnl, 2)
    done = {k: tr[k] for k in ("id", "symbol", "name", "kind", "tf", "side", "rule", "label", "opened", "entry", "lev")}
    done.update(margin=tr["size0"], exit=price, closed=now_iso(t), reason=reason, pnl=pnl, r=r,
                pct=round(pnl / tr["size0"] * 100, 1), balance=state["balance"])
    state["closed"].append(done)
    state["closed"] = state["closed"][-cfg["keep_closed"]:]
    return done


def replay(state: dict, tr: dict, bars: pd.DataFrame, cfg: dict) -> tuple[str, float, datetime] | None:
    """Walk through 5-minute bars since the last check. Returns (reason, price, time) on exit."""
    side = 1 if tr["side"] == "long" else -1
    until = parse(tr["until"])
    trail = tr.get("trail_atr", cfg["trail_atr"]) * tr["atr"]
    for t, b in bars.iterrows():
        o, h, l, c = b["open"], b["high"], b["low"], b["close"]
        worst, best = (l, h) if side == 1 else (h, l)
        moved = tr["stop"] != tr["stop0"]
        if (o - tr["stop"]) * side <= 0:                       # gap through the stop
            return ("trailing stop" if moved else "stop"), o, t
        if (worst - tr["stop"]) * side <= 0:                   # inside the bar: stop first (conservative)
            return ("trailing stop" if moved else "stop"), tr["stop"], t
        if tr["partial_at"] is not None and (best - tr["partial_at"]) * side >= 0:
            book(state, tr, 0.5, tr["partial_at"])             # half off at +1R, stop to break-even
            tr["partial_at"] = None
            tr["partial_done"] = now_iso(t.to_pydatetime())
            if (tr["entry"] - tr["stop"]) * side > 0:
                tr["stop"] = tr["entry"]
        if tr["target"] is not None and (best - tr["target"]) * side >= 0:
            return "target", tr["target"], t
        tr["last"] = float(c)
        tr["checked"] = now_iso(t.to_pydatetime() + timedelta(minutes=5))
        if (best - tr["best"]) * side > 0:
            tr["best"] = float(best)
            ns = tr["best"] - side * trail
            tr["stop"] = max(tr["stop"], ns) if side == 1 else min(tr["stop"], ns)
        if t.to_pydatetime() + timedelta(minutes=5) >= until:
            return "time limit", float(c), t
    return None


def update_open(state: dict, labels: dict, cfg: dict, bars_since, price_now, t: datetime) -> list[dict]:
    closed, still = [], []
    for tr in state["open"]:
        exit_ = None
        try:
            bars = bars_since(tr["symbol"], tr["kind"], parse(tr["checked"]))
            if bars is not None and len(bars):
                exit_ = replay(state, tr, bars, cfg)
        except Exception as e:  # data problem: keep the trade, try again next scan
            print(f"   paper: no bars for {tr['symbol']}: {e}")
        if exit_ is None:
            lab = labels.get((tr["symbol"], tr["tf"]))
            side = 1 if tr["side"] == "long" else -1
            if tr.get("flip_exit", True) and lab and ((side == 1 and "SELL" in lab) or (side == -1 and "BUY" in lab)):
                exit_ = (f"signal flipped to {lab}", price_now(tr["symbol"], tr["kind"]) or tr["last"], t)
            elif t >= parse(tr["until"]):
                exit_ = ("time limit", price_now(tr["symbol"], tr["kind"]) or tr["last"], t)
        if exit_:
            reason, price, when = exit_
            when = when.to_pydatetime() if hasattr(when, "to_pydatetime") else when
            closed.append(settle(state, tr, float(price), reason, when, cfg))
        else:
            still.append(tr)
    state["open"] = still
    return closed


def rules_text(cfg: dict) -> list[str]:
    out = [f"Start: {cfg['start_balance']:,.0f} USD. {cfg['intro']}"]
    flips = False
    for r in cfg["rules"]:
        stop, trail = r.get("stop_atr", cfg["stop_atr"]), r.get("trail_atr", cfg["trail_atr"])
        how = (f"stop {stop:g} ATR, then trailing {trail:g} ATR behind the best price, no profit target"
               if r["exit"] == "trail" else
               f"half is sold at +1R and the stop moves to the entry price, the rest trails {trail:g} ATR")
        if r.get("trigger", "tech") == "vol_breakout":
            entry = ("enter after a bar more than twice its normal range that closes near its high, "
                     "above the 50-bar average")
        else:
            entry = f"enter when the technical score crosses {r['entry_tech']:+d}"
        need = [CONTEXT_LABELS.get(f, f) for f in r.get("requires", [])]
        if need:
            entry += ", only while " + " and ".join(need)
        flips = flips or r.get("flip_exit", True)
        out.append(f"{r['name']}: {entry}; risk {r['risk_pct']}% of the account per trade; exit: {how}; "
                   f"time limit {r['max_hold_bars']} bars.")
    out.append(f"No leverage per position. All open positions together are capped at {cfg['max_exposure']}x the "
               f"account, one position at {cfg['max_position_pct']}% of it. One open trade per asset.")
    out.append(("Also closes when the signal flips to SELL. " if flips else "")
               + "Exits are checked on 5-minute bars. Fees: "
               + ", ".join(f"{m} {v}%" for m, v in cfg["fee_pct"].items()
                           if any(m in r["markets"] for r in cfg["rules"]))
               + " per side. No slippage or gaps beyond what the bars show.")
    return out


def run(state: dict | None, items: list[dict], labels: dict, cfg: dict, bars_since, price_now,
        t: datetime | None = None, context: dict | None = None) -> dict:
    """One scan's worth of paper trading. Returns the new state."""
    t = t or datetime.now(UTC)
    if not state or "balance" not in state or state.get("version") != VERSION:
        state = fresh_state(cfg, (state or {}).get("epoch"))
    closed = update_open(state, labels, cfg, bars_since, price_now, t)
    opened = open_new(state, items, cfg, price_now, t, context)
    for tr in state["open"]:
        tr["upnl"] = round(unrealized(tr), 2)
    equity = round(state["balance"] + sum(tr["upnl"] for tr in state["open"]), 2)
    st = state["stats"]
    st["peak"] = max(st["peak"], equity)
    st["max_dd_pct"] = round(max(st["max_dd_pct"], (1 - equity / st["peak"]) * 100), 2)
    state["equity"] = equity
    state["updated"] = now_iso(t)
    state["curve"] = (state["curve"] + [[now_iso(t)[:16], equity]])[-700:]
    state["rules_text"] = rules_text(cfg)
    state["title"] = cfg["title"]
    print(f"Paper test [{cfg['title']}]: opened {len(opened)}, closed {len(closed)}, open {len(state['open'])}, "
          f"balance {state['balance']:.2f}, equity {equity:.2f}")
    return state
