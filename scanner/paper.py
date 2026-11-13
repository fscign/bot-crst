"""
Paper trading test: a virtual account that trades every new strong signal.

Rules (all adjustable in scanner/config.json, section "paper"):
- Start with 1,000 USD.
- Open a trade when a signal becomes STRONG BUY or STRONG SELL (with a good
  track record, if require_proven is on). One trade per signal: it opens again
  only after the signal has dropped out of STRONG and come back.
- Stake (margin) = 1% of the balance, but never less than 10 USD.
  Balance under 1,000 -> 10 USD. Balance over 1,000 -> 1%.
- Leverage = the leverage the dashboard suggests for that signal.
- Entry at the live price when the scan sees the signal.
- Exit at whichever comes first: stop (trails behind the best price), target,
  liquidation, the signal flipping to the other side, or a time limit.
- Exits are found by replaying 5-minute bars, so a stop hit between two
  scans is filled at the stop price (or at the bar's open after a gap).
- A fee is charged on both sides of every trade.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd

UTC = timezone.utc
TF = {"1h": timedelta(hours=1), "4h": timedelta(hours=4), "1d": timedelta(days=1)}

DEFAULTS = {
    "start_balance": 1000,
    "min_stake": 10,
    "stake_pct": 1.0,
    "require_proven": True,
    "fee_pct": 0.1,
    "max_open": 100,
    "max_hold_bars": {"1h": 48, "4h": 42, "1d": 20},
    "keep_closed": 300,
}


def now_iso(t: datetime | None = None) -> str:
    return (t or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def fresh_state(cfg: dict, epoch: str | None = None) -> dict:
    return {
        "epoch": epoch or uuid.uuid4().hex[:8], "started": now_iso(),
        "start": cfg["start_balance"], "balance": float(cfg["start_balance"]),
        "open": [], "closed": [], "prev_strong": None, "curve": [],
        "stats": {"trades": 0, "wins": 0, "gross_win": 0.0, "gross_loss": 0.0, "fees": 0.0,
                  "peak": float(cfg["start_balance"]), "max_dd_pct": 0.0, "liquidations": 0},
        "rules": {},
    }


def stake_for(balance: float, cfg: dict) -> float:
    """1% of the balance, never below the minimum stake (10 USD)."""
    return round(max(cfg["min_stake"], balance * cfg["stake_pct"] / 100), 2)


def strong_key(it: dict) -> str:
    return f"{it['symbol']}|{it['tf']}|{'long' if it['score'] > 0 else 'short'}"


def is_strong(it: dict, cfg: dict) -> bool:
    return it["label"].startswith("STRONG") and (it.get("proven") or not cfg["require_proven"])


# --------------------------------------------------------------------------
# Opening
# --------------------------------------------------------------------------
def open_new(state: dict, items: list[dict], cfg: dict, price_now, t: datetime) -> list[dict]:
    strong = {strong_key(i): i for i in items if is_strong(i, cfg)}
    prev = state["prev_strong"]
    state["prev_strong"] = sorted(strong)
    if prev is None:                      # first scan: remember what is already strong, open nothing
        return []
    opened = []
    held = {tr["symbol"] for tr in state["open"]}
    for key in sorted(set(strong) - set(prev), key=lambda k: -abs(strong[k]["score"])):
        it = strong[key]
        if it["symbol"] in held or len(state["open"]) >= cfg["max_open"]:
            continue
        stake = stake_for(state["balance"], cfg)
        free = state["balance"] - sum(tr["margin"] for tr in state["open"])
        if free < stake:
            continue
        price = price_now(it["symbol"], it["kind"]) or it["price"]
        side = 1 if it["score"] > 0 else -1
        lev = max(1, int(it["lev"]["x"]))
        notional = stake * lev
        atr = float(it["atr"])
        stop = price - side * cfg["risk"]["stop_atr"] * atr
        target = price + side * cfg["risk"]["target_atr"] * atr
        tr = {
            "id": uuid.uuid4().hex[:8], "symbol": it["symbol"], "name": it["name"], "kind": it["kind"],
            "tf": it["tf"], "side": "long" if side == 1 else "short", "label": it["label"], "score": it["score"],
            "opened": now_iso(t), "checked": now_iso(t), "entry": price, "last": price,
            "margin": stake, "lev": lev, "notional": notional, "atr": atr,
            "stop0": stop, "stop": stop, "target": target, "best": price,
            "liq": price * (1 - side / lev) if lev > 1 else None,
            "until": now_iso(t + TF[it["tf"]] * cfg["max_hold_bars"][it["tf"]]),
        }
        state["open"].append(tr)
        held.add(it["symbol"])
        opened.append(tr)
    return opened


# --------------------------------------------------------------------------
# Closing
# --------------------------------------------------------------------------
def settle(state: dict, tr: dict, price: float, reason: str, t: datetime, cfg: dict) -> dict:
    side = 1 if tr["side"] == "long" else -1
    move = side * (price / tr["entry"] - 1)
    fee = cfg["fee_pct"] / 100
    fees = tr["notional"] * fee + tr["notional"] * (price / tr["entry"]) * fee
    pnl = tr["notional"] * move - fees
    if reason == "liquidated" or pnl <= -tr["margin"]:
        pnl, reason = -tr["margin"], "liquidated"
        state["stats"]["liquidations"] += 1
    pnl = round(pnl, 2)
    state["balance"] = round(state["balance"] + pnl, 2)
    st = state["stats"]
    st["trades"] += 1
    st["fees"] = round(st["fees"] + fees, 2)
    if pnl > 0:
        st["wins"] += 1
        st["gross_win"] = round(st["gross_win"] + pnl, 2)
    else:
        st["gross_loss"] = round(st["gross_loss"] - pnl, 2)
    done = {k: tr[k] for k in ("id", "symbol", "name", "kind", "tf", "side", "label", "opened",
                               "entry", "margin", "lev")}
    done.update(exit=price, closed=now_iso(t), reason=reason, pnl=pnl,
                pct=round(pnl / tr["margin"] * 100, 1), balance=state["balance"])
    state["closed"].append(done)
    state["closed"] = state["closed"][-cfg["keep_closed"]:]
    return done


def replay(tr: dict, bars: pd.DataFrame, stop_atr: float) -> tuple[str, float, datetime] | None:
    """Walk through 5-minute bars since the last check. Returns (reason, price, time) on exit."""
    side = 1 if tr["side"] == "long" else -1
    until = parse(tr["until"])
    for t, b in bars.iterrows():
        o, h, l, c = b["open"], b["high"], b["low"], b["close"]
        worst, best = (l, h) if side == 1 else (h, l)
        liq = tr["liq"]
        # gap through liquidation or stop at the open
        if liq is not None and (o - liq) * side <= 0:
            return "liquidated", liq, t
        if (o - tr["stop"]) * side <= 0:
            return "stop", o, t
        # inside the bar: assume the worse outcome first (stop before target)
        if (worst - tr["stop"]) * side <= 0:
            if liq is not None and (tr["stop"] - liq) * side <= 0:
                return "liquidated", liq, t
            return ("trailing stop" if tr["stop"] != tr["stop0"] else "stop"), tr["stop"], t
        if (best - tr["target"]) * side >= 0:
            return "target", tr["target"], t
        tr["last"] = float(c)
        tr["checked"] = now_iso(t.to_pydatetime() + timedelta(minutes=5))
        if (best - tr["best"]) * side > 0:
            tr["best"] = float(best)
            trail = tr["best"] - side * stop_atr * tr["atr"]
            tr["stop"] = max(tr["stop"], trail) if side == 1 else min(tr["stop"], trail)
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
                exit_ = replay(tr, bars, cfg["risk"]["stop_atr"])
        except Exception as e:  # data problem: keep the trade, try again next scan
            print(f"   paper: no bars for {tr['symbol']}: {e}")
        if exit_ is None:
            lab = labels.get((tr["symbol"], tr["tf"]))
            side = 1 if tr["side"] == "long" else -1
            if lab and ((side == 1 and "SELL" in lab) or (side == -1 and "BUY" in lab)):
                price = price_now(tr["symbol"], tr["kind"]) or tr["last"]
                exit_ = (f"signal flipped to {lab}", price, t)
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


def unrealized(tr: dict, cfg: dict) -> float:
    side = 1 if tr["side"] == "long" else -1
    move = side * (tr["last"] / tr["entry"] - 1)
    fee = cfg["fee_pct"] / 100
    return max(-tr["margin"], tr["notional"] * move - tr["notional"] * fee * (1 + tr["last"] / tr["entry"]))


def settings(main_cfg: dict) -> dict:
    """Paper settings from config.json merged over the defaults, plus the stop/target rules."""
    user = main_cfg.get("paper", {})
    cfg = {**DEFAULTS, **user, "risk": main_cfg["risk"]}
    cfg["max_hold_bars"] = {**DEFAULTS["max_hold_bars"], **user.get("max_hold_bars", {})}
    return cfg


def run(state: dict | None, items: list[dict], labels: dict, cfg: dict, bars_since, price_now,
        t: datetime | None = None) -> dict:
    """One scan's worth of paper trading. Returns the new state."""
    t = t or datetime.now(UTC)
    if not state or "balance" not in state:
        state = fresh_state(cfg, (state or {}).get("epoch"))
    closed = update_open(state, labels, cfg, bars_since, price_now, t)
    opened = open_new(state, items, cfg, price_now, t)
    for tr in state["open"]:
        tr["upnl"] = round(unrealized(tr, cfg), 2)
    equity = round(state["balance"] + sum(tr["upnl"] for tr in state["open"]), 2)
    st = state["stats"]
    st["peak"] = max(st["peak"], equity)
    st["max_dd_pct"] = round(max(st["max_dd_pct"], (1 - equity / st["peak"]) * 100), 2)
    state["equity"] = equity
    state["updated"] = now_iso(t)
    state["curve"] = (state["curve"] + [[now_iso(t)[:16], equity]])[-700:]
    state["rules"] = {k: cfg[k] for k in ("start_balance", "min_stake", "stake_pct", "require_proven",
                                          "fee_pct", "max_hold_bars")}
    print(f"Paper test: opened {len(opened)}, closed {len(closed)}, open {len(state['open'])}, "
          f"balance {state['balance']:.2f}, equity {equity:.2f}")
    return state
