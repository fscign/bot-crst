"""
Telegram signal channels: read public channels, recognise trade signals, and
paper-trade each channel in its own account so channels can be compared.

How it reads a channel: the public web preview https://t.me/s/<channel> (no
login, no API key). Private channels and groups cannot be read this way.

What counts as a signal: a post that names a coin, a direction (long/buy or
short/sell) and at least an entry or a stop. Targets, stop, entry zone and
leverage are taken from the post when present.

How a signal is traded (settings in scanner/config.json, section "telegram"):
- Entry at the live price if it is inside (or better than) the entry zone;
  otherwise a limit order at the edge of the zone, cancelled after 24 hours.
  A signal whose price already passed its first target is skipped.
- Size: losing to the stop costs risk_pct of the account. A post without a stop
  gets a default stop (default_stop_pct) and is marked so.
- Exits: an equal part is sold at each target; after the first target the stop
  moves to the entry price; the last target closes the rest. Also closes on the
  stop, on a "close"/"cancel" post for that coin, or after max_hold_days.
- The channel's leverage is shown, but results are in plain USD on the position.
"""
from __future__ import annotations

import html
import re
import uuid
from datetime import datetime, timedelta, timezone

import requests

import paper

UTC = timezone.utc
UA = {"User-Agent": "Mozilla/5.0 (compatible; signal-bot)"}

DEFAULTS = {
    "start_balance": 1000,
    "risk_pct": 1.0,
    "default_stop_pct": 5.0,
    "max_position_pct": 50,
    "max_exposure": 3.0,
    "max_hold_days": 7,
    "limit_hours": 24,
    "max_signal_age_hours": 6,
    "fee_pct": 0.05,
}

NUM = r"\d[\d,]*(?:\.\d+)?"


# --------------------------------------------------------------------------
# Reading a channel
# --------------------------------------------------------------------------
def fetch_posts(channel: str) -> list[dict]:
    """Recent posts of a public channel: [{id, time, text}], oldest first."""
    r = requests.get(f"https://t.me/s/{channel}", headers=UA, timeout=20)
    r.raise_for_status()
    return parse_page(r.text, channel)


def parse_page(page: str, channel: str = "") -> list[dict]:
    posts = []
    for block in re.split(r'(?=<div class="tgme_widget_message_wrap)', page)[1:]:
        m_id = re.search(r'data-post="[^"/]+/(\d+)"', block)
        m_time = re.search(r'<time[^>]*datetime="([^"]+)"', block)
        m_text = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.S)
        if not (m_id and m_time and m_text):
            continue
        text = re.sub(r"<br\s*/?>", "\n", m_text.group(1))
        text = html.unescape(re.sub(r"<[^>]+>", "", text))
        t = datetime.fromisoformat(m_time.group(1).replace("Z", "+00:00")).astimezone(UTC)
        posts.append({"id": int(m_id.group(1)), "time": t, "text": text.strip(), "channel": channel})
    return sorted(posts, key=lambda p: p["id"])


class Reader:
    """Reads channels as a logged-in Telegram account (needed for private channels).
    Uses the secrets TG_API_ID, TG_API_HASH and TG_SESSION. One connection per scan."""

    def __init__(self):
        import os
        self.client, self.error = None, None
        api_id, api_hash, session = (os.environ.get(k, "") for k in ("TG_API_ID", "TG_API_HASH", "TG_SESSION"))
        if not (api_id and api_hash and session):
            self.error = "TG_API_ID, TG_API_HASH or TG_SESSION is not set"
            return
        try:
            from telethon.sync import TelegramClient
            from telethon.sessions import StringSession
            self.client = TelegramClient(StringSession(session), int(api_id), api_hash)
            self.client.connect()
            if not self.client.is_user_authorized():
                self.error = "the Telegram login (TG_SESSION) is no longer valid; make a new one"
                self.client.disconnect()
                self.client = None
                return
            self.chats = {d.id: d.entity for d in self.client.get_dialogs() if d.is_channel or d.is_group}
        except Exception as e:
            self.error = f"could not log in to Telegram: {e}"
            self.client = None

    def posts(self, chat_id: int, limit: int = 30) -> list[dict]:
        if self.client is None:
            raise RuntimeError(self.error)
        entity = self.chats.get(chat_id)
        if entity is None:
            raise RuntimeError(f"the reader account is not a member of channel {chat_id}")
        out = []
        for m in self.client.get_messages(entity, limit=limit):
            text = (m.message or "").strip()
            if text:
                out.append({"id": m.id, "time": m.date.astimezone(UTC), "text": text, "channel": str(chat_id)})
        return sorted(out, key=lambda p: p["id"])

    def close(self):
        if self.client is not None:
            self.client.disconnect()


# --------------------------------------------------------------------------
# Understanding a post
# --------------------------------------------------------------------------
def _num(s: str) -> float:
    s = s.strip()
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", s):          # 60,000 or 1,234.5
        s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    return float(s)


def _nums_after(text: str, labels: str, limit: int = 8) -> list[float]:
    """Numbers that follow a label such as 'Entry' or 'TP1', on the same line."""
    out = []
    for m in re.finditer(rf"(?i)(?:{labels})[^\n\d]{{0,25}}((?:{NUM}[^\n\d]{{0,6}}){{1,{limit}}})", text):
        for x in re.findall(NUM, m.group(1)):
            try:
                out.append(_num(x))
            except ValueError:
                pass
    return out


SYMBOL_RE = [
    r"#\s?([A-Z0-9]{2,15})\s?/\s?USDT",
    r"\b([A-Z0-9]{2,15})\s?/\s?USDT\b",
    r"\b([A-Z0-9]{2,15})USDT(?:\.P|PERP)?\b",
    r"#([A-Z0-9]{2,15})\b",
    r"\$([A-Z0-9]{2,15})\b",
    r"(?i)\b(?:coin|pair|symbol|монета|пара)\s*[:\-]\s*([A-Za-z0-9]{2,15})",
    r"\b([A-Z][A-Z0-9]{1,9})\b",                     # a bare ticker like BTC (last resort)
]
NOT_COINS = {"USDT", "USD", "LONG", "SHORT", "BUY", "SELL", "SPOT", "FUTURES", "TP", "SL", "VIP", "SIGNAL", "ENTRY",
             "TARGET", "TARGETS", "STOP", "LEVERAGE", "CROSS", "ISOLATED", "UPDATE", "PROFIT", "NEW", "FREE", "TP1",
             "TP2", "TP3", "TP4", "TP5", "SL1", "ROI", "PNL", "ATH", "DCA", "OK", "UTC", "MSK", "AI", "ETF", "FOMC", "CPI",
             "USA", "US", "EU", "RSI", "MACD", "EMA", "MA", "TA", "TG", "IMO", "ALT", "ALTS", "CEO", "SEC", "FED", "X"}


def find_symbol(text: str) -> str | None:
    for pat in SYMBOL_RE:
        for m in re.finditer(pat, text):
            base = m.group(1).upper()
            if base not in NOT_COINS and not base.isdigit():
                return base + "USDT"
    return None


def parse_signal(text: str) -> dict | None:
    """A trade signal, a close instruction, or None."""
    sym = find_symbol(text)
    if not sym:
        return None
    low = text.lower()
    if re.search(r"\b(close|closed|cancel|cancelled|canceled|exit now|close all|закрыва\w*|закрыл\w*|закрыт\w*|"
                 r"отмен\w*|выход\w*|фиксиру\w*)", low) and \
            not re.search(r"\b(entry|enter|buy zone|targets?|tp ?1|вход\w*|цел[ьи]\w*|тейк\w*)", low):
        return {"action": "close", "symbol": sym}
    long_hit = re.search(r"\b(long|buy|лонг\w*|покупк\w*|купи\w*|покупаем)", low)
    short_hit = re.search(r"\b(short|sell|шорт\w*|продаж\w*|прода[её]м|продать)", low)
    if bool(long_hit) == bool(short_hit):
        if not (long_hit and short_hit):
            return None
        side = "long" if long_hit.start() < short_hit.start() else "short"   # first one mentioned
    else:
        side = "long" if long_hit else "short"
    entries = _nums_after(text, r"entry(?: zone| price)?|entries|enter|buy(?: zone| at| price)?|sell(?: zone| at)?|"
                                r"short(?: at)?|long(?: at)?|open(?: price)?|price|точка входа|вход\w*|зона входа|"
                                r"лонг\w*|шорт\w*|покупк\w*|цена", 2)
    num = r"\s?(?:\d(?![\d.,]))?"                                    # skip a target's own number, as in "TP 1: 140"
    targets = _nums_after(text, rf"(?:targets?|take[ -]?profits?|\btp|\bt(?=\d)|цел[ьи]\w*|тейк\w*|\bтп){num}", 6)
    stops = _nums_after(text, r"stop[ -]?loss|stoploss|\bsl\b|stop|стоп[ -]?лосс\w*|стоп\w*|\bсл\b", 1)
    lev = re.search(r"(?i)(?:leverage|lev)[^\d\n]{0,15}(\d{1,3})\s?x|\b(\d{1,3})\s?x\b", text)
    if not entries and not stops:
        return None
    sig = {"action": "open", "symbol": sym, "side": side,
           "entry_low": min(entries) if entries else None, "entry_high": max(entries) if entries else None,
           "targets": [], "stop": stops[0] if stops else None,
           "leverage": int(next(g for g in lev.groups() if g)) if lev else None}
    ref = sig["entry_high"] if side == "long" else sig["entry_low"]
    if ref is not None:
        sgn = 1 if side == "long" else -1
        sig["targets"] = sorted({t for t in targets if (t - ref) * sgn > 0}, reverse=(side == "short"))[:6]
        if sig["stop"] is not None and (ref - sig["stop"]) * sgn <= 0:
            sig["stop"] = None                                  # stop on the wrong side: ignore it
    return sig


# --------------------------------------------------------------------------
# Trading one channel
# --------------------------------------------------------------------------
def settings(main_cfg: dict, channel: dict) -> dict:
    tg = {**DEFAULTS, **main_cfg.get("telegram", {}).get("defaults", {}), **channel}
    tg["fee_pct"] = {"crypto": tg["fee_pct"], "us": tg["fee_pct"], "eu": tg["fee_pct"]}
    tg["title"] = channel.get("title") or f"Telegram: {channel['channel']}"
    tg["display"] = channel.get("name") or f"@{channel['channel']}"
    tg.update(intro=f"Trades the signals posted in the Telegram channel {tg['display']}.",
              rules=[], keep_closed=300, trail_atr=0, stop_atr=2.0, max_exposure=tg["max_exposure"])
    return tg


def rules_text(cfg: dict) -> list[str]:
    return [
        f"Start: {cfg['start_balance']:,.0f} USD. Copies every signal posted in the Telegram channel "
        f"{cfg['display']} (read at each scan, every 15 minutes). Signals posted only as pictures cannot be read.",
        "Entry at the live price when it is inside or better than the posted entry zone; otherwise a limit order at "
        f"the zone, cancelled after {cfg['limit_hours']} hours. Skipped if the price already passed the first target "
        f"or the post is older than {cfg['max_signal_age_hours']} hours.",
        f"Size: losing to the posted stop costs {cfg['risk_pct']}% of the account (one position at most "
        f"{cfg['max_position_pct']}% of it). No stop in the post: a {cfg['default_stop_pct']}% stop is used.",
        "Exit: an equal part at each posted target, stop moved to the entry price after target 1, the rest at the last "
        f"target or the stop. Also closes on a 'close' post for that coin, or after {cfg['max_hold_days']} days.",
        f"Fee {cfg['fee_pct']['crypto']}% per side. The channel's leverage is shown but results are plain USD on the "
        "position. Prices: Binance spot, so coins that only trade as futures cannot be followed.",
    ]


def open_trade(state: dict, sig: dict, post: dict, cfg: dict, price: float, t: datetime) -> tuple[dict | None, str]:
    side = 1 if sig["side"] == "long" else -1
    lo, hi = sig["entry_low"], sig["entry_high"]
    if lo is None:
        lo = hi = price
    targets = sig["targets"]
    if targets and (price - targets[0]) * side >= 0:
        return None, "price already past target 1"
    zone_edge = hi if side == 1 else lo
    pending = (price - zone_edge) * side > zone_edge * 0.003      # worse than the zone: wait for it
    entry = zone_edge if pending else price
    stop, stop_given = sig["stop"], sig["stop"] is not None
    if not stop_given:
        stop = entry * (1 - side * cfg["default_stop_pct"] / 100)
    if (entry - stop) * side <= 0:
        return None, "stop already hit"
    equity = paper.equity_of(state)
    stop_frac = abs(entry - stop) / entry
    notional = min(equity * cfg["risk_pct"] / 100 / stop_frac, equity * cfg["max_position_pct"] / 100)
    exposure = sum(tr["notional"] for tr in state["open"])
    if notional < 5 or exposure + notional > equity * cfg["max_exposure"]:
        return None, "account fully in use"
    tr = {"id": uuid.uuid4().hex[:8], "symbol": sig["symbol"], "name": sig["symbol"][:-4], "kind": "crypto", "tf": "tg",
          "side": sig["side"], "rule": f"{cfg['display']} post {post['id']}", "label": "TELEGRAM", "score": 0,
          "opened": paper.now_iso(t), "checked": paper.now_iso(t), "entry": entry, "last": price,
          "notional": round(notional, 2), "size0": round(notional, 2), "margin": round(notional, 2), "lev": 1,
          "channel_lev": sig["leverage"], "risk": round(notional * stop_frac, 2), "atr": 0, "fee_pct": cfg["fee_pct"]["crypto"],
          "stop0": stop, "stop": stop, "stop_given": stop_given, "targets": targets, "tp_hit": 0,
          "target": targets[-1] if targets else None, "partial_at": None, "banked": 0.0, "best": entry,
          "trail_atr": 0, "flip_exit": False, "post": post["id"],
          "until": paper.now_iso(t + timedelta(days=cfg["max_hold_days"]))}
    if pending:
        tr.update(pending=True, limit=zone_edge, expires=paper.now_iso(t + timedelta(hours=cfg["limit_hours"])))
    state["open"].append(tr)
    return tr, ("limit order at " + f"{zone_edge:g}") if pending else "opened"


def feed_settings(main_cfg: dict) -> dict:
    """Settings for copying Telegram signals into an existing account (all channels as one feed)."""
    chans = main_cfg.get("telegram", {}).get("channels", [])
    names = " and ".join(c.get("name") or f"@{c['channel']}" for c in chans) or "none"
    cfg = settings(main_cfg, {"channel": "feed", "name": names, "title": "Telegram feed"})
    return cfg


def process_feed(state: dict, feed: list[tuple[str, list[dict] | None]], cfg: dict, price_now,
                 t: datetime | None = None) -> None:
    """Open or close trades in `state` from new posts of several channels, treated as ONE source:
    the same coin posted in two channels opens one trade. Exits of open trades are handled by
    paper.update_open, so call this after paper.run for the account."""
    t = t or datetime.now(UTC)
    last = state.setdefault("tg_last", {})
    state.setdefault("signals", [])
    state["tg"] = True
    errors = []
    for chan, posts in feed:
        if posts is None:
            errors.append(chan)
            continue
        if chan not in last:                             # first look at a channel: only note where it is now
            last[chan] = max((p["id"] for p in posts), default=0)
            continue
        for post in posts:
            if post["id"] <= last[chan]:
                continue
            _handle_post(state, post, chan, cfg, price_now, t)
        last[chan] = max([last[chan]] + [p["id"] for p in posts])
    if errors:
        state["feed_error"] = paper.now_iso(t)
        state["feed_error_channels"] = errors
    else:
        state.pop("feed_error", None)
        state.pop("feed_error_channels", None)
    for tr in state["open"]:
        if tr.get("pending"):
            tr["upnl"] = 0.0


def _handle_post(state: dict, post: dict, chan: str, cfg: dict, price_now, t: datetime) -> None:
    sig = parse_signal(post["text"])
    log = {"post": post["id"], "channel": chan, "time": paper.now_iso(post["time"]), "text": post["text"][:300]}
    if sig is None:                                      # kept so unreadable formats can be spotted and added
        state["unread"] = (state.get("unread", []) + [log])[-15:]
        return
    log.update(symbol=sig["symbol"], side=sig.get("side"), action=sig["action"])
    if t - post["time"] > timedelta(hours=cfg["max_signal_age_hours"]):
        log["result"] = "too old when first seen"
    elif sig["action"] == "close":
        hits = [tr for tr in state["open"] if tr["symbol"] == sig["symbol"] and tr.get("tf") == "tg"]
        for tr in hits:
            state["open"].remove(tr)
            if not tr.get("pending"):
                paper.settle(state, tr, price_now(tr["symbol"], "crypto") or tr["last"], "channel said close", t, cfg)
        log["result"] = f"closed {len(hits)} trade(s)" if hits else "nothing open to close"
    elif any(tr["symbol"] == sig["symbol"] for tr in state["open"]):
        log["result"] = "already holding this coin (or posted in both channels)"
    else:
        price = price_now(sig["symbol"], "crypto")
        if not price:
            log["result"] = "no Binance spot price for this coin"
        else:
            tr, msg = open_trade(state, sig, {**post, "id": post["id"]}, {**cfg, "display": chan}, price, t)
            log["result"] = msg
            log.update(entry=sig["entry_low"], entry_high=sig["entry_high"], stop=sig["stop"],
                       targets=sig["targets"], leverage=sig["leverage"])
    state["signals"] = (state["signals"] + [log])[-40:]


def feed_rules_text(cfg: dict) -> list[str]:
    lines = rules_text({**cfg, "display": cfg["display"]})
    lines[0] = (f"Telegram signals: every signal posted in {cfg['display']} is copied into this account, as one feed "
                f"(a coin posted in both channels opens one trade). Read at each scan, every 15 minutes. "
                f"Signals posted only as pictures cannot be read.")
    return lines


def run_channel(state: dict | None, cfg: dict, bars_since, price_now, posts: list[dict] | None,
                t: datetime | None = None) -> dict:
    t = t or datetime.now(UTC)
    if not state or state.get("version") != paper.VERSION or "balance" not in state:
        state = paper.fresh_state(cfg, (state or {}).get("epoch"))
    state.setdefault("signals", [])
    paper.update_open(state, {}, cfg, bars_since, price_now, t)
    if posts is None:
        state["feed_error"] = paper.now_iso(t)
    else:
        state.pop("feed_error", None)
        last = state.get("last_post")
        for post in posts:
            if last is None or post["id"] <= last:       # first look: only note where the channel is now
                continue
            sig = parse_signal(post["text"])
            log = {"post": post["id"], "time": paper.now_iso(post["time"]), "text": post["text"][:300]}
            if sig is None:                              # kept so unreadable formats can be spotted and added
                state["unread"] = (state.get("unread", []) + [log])[-15:]
                continue
            log.update(symbol=sig["symbol"], side=sig.get("side"), action=sig["action"])
            if t - post["time"] > timedelta(hours=cfg["max_signal_age_hours"]):
                log["result"] = "too old when first seen"
            elif sig["action"] == "close":
                hits = [tr for tr in state["open"] if tr["symbol"] == sig["symbol"]]
                for tr in hits:
                    state["open"].remove(tr)
                    if tr.get("pending"):
                        continue
                    paper.settle(state, tr, price_now(tr["symbol"], "crypto") or tr["last"], "channel said close", t, cfg)
                log["result"] = f"closed {len(hits)} trade(s)" if hits else "nothing open to close"
            elif any(tr["symbol"] == sig["symbol"] for tr in state["open"]):
                log["result"] = "already holding this coin"
            else:
                price = price_now(sig["symbol"], "crypto")
                if not price:
                    log["result"] = "no Binance spot price for this coin"
                else:
                    tr, msg = open_trade(state, sig, post, cfg, price, t)
                    log["result"] = msg
                    log.update(entry=sig["entry_low"], entry_high=sig["entry_high"], stop=sig["stop"],
                               targets=sig["targets"], leverage=sig["leverage"])
            state["signals"] = (state["signals"] + [log])[-40:]
        if posts:
            state["last_post"] = max(p["id"] for p in posts) if last is None else max(last, max(p["id"] for p in posts))
    for tr in state["open"]:
        tr["upnl"] = 0.0 if tr.get("pending") else round(paper.unrealized(tr), 2)
    equity = round(state["balance"] + sum(tr["upnl"] for tr in state["open"]), 2)
    st = state["stats"]
    st["peak"] = max(st["peak"], equity)
    st["max_dd_pct"] = round(max(st["max_dd_pct"], (1 - equity / st["peak"]) * 100), 2)
    state.update(equity=equity, updated=paper.now_iso(t), title=cfg["title"], rules_text=rules_text(cfg),
                 curve=(state.get("curve", []) + [[paper.now_iso(t)[:16], equity]])[-700:])
    print(f"{cfg['title']}: open {len(state['open'])}, closed {st['trades']}, "
          f"equity {equity:.2f}{', FEED UNAVAILABLE' if posts is None else ''}")
    return state
