#!/usr/bin/env python3
"""
Signal bot: buy / sell / hold signals for crypto and stocks.

It combines technical indicators (trend, MACD, RSI, Bollinger Bands, volume,
ADX) with news sentiment into one score from -100 to +100 per asset and
timeframe, backtests the technical part on history, writes an HTML report and
(optionally) sends Telegram alerts when a signal changes.

It does NOT place trades. Signals are informational, not financial advice.

Usage:
    python signal_bot.py              run once, write report.html
    python signal_bot.py --serve      live dashboard at http://localhost:8765 with
                                      position tracking (keeps running and checking)
    python signal_bot.py --loop 60    run every 60 minutes, report file only
    python signal_bot.py --demo       run on synthetic data (no internet needed)
"""
from __future__ import annotations

import argparse
import html
import json
import math
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote_plus
from xml.etree import ElementTree

import numpy as np
import pandas as pd

try:
    import requests
except ImportError:  # only needed for live data
    requests = None

HERE = Path(__file__).resolve().parent
UA = {"User-Agent": "Mozilla/5.0 (signal-bot)"}
UTC = timezone.utc

DEFAULT_CONFIG = {
    "crypto": {"BTCUSDT": "Bitcoin", "ETHUSDT": "Ethereum", "SOLUSDT": "Solana"},
    "stocks": {"AAPL": "Apple", "NVDA": "Nvidia", "MSFT": "Microsoft", "SPY": "S&P 500"},
    "timeframes": ["1d", "4h"],
    "weights": {"trend": 0.30, "macd": 0.15, "rsi": 0.15, "bollinger": 0.10,
                "volume": 0.10, "news": 0.20},
    "thresholds": {"buy": 30, "strong_buy": 55, "sell": -30, "strong_sell": -55},
    "risk": {"stop_atr": 2.0, "target_atr": 3.0},
    "account": {"size": 1000, "risk_per_trade_pct": 1.0,
                "max_leverage_crypto": 10, "max_leverage_stocks": 5},
    "server": {"port": 8765, "signal_refresh_minutes": 15, "price_check_seconds": 60},
    "backtest": {"fee_pct": 0.1},
    "news": {"enabled": True, "lookback_hours": 72, "max_headlines": 25},
    "telegram": {"bot_token": "", "chat_id": ""},
    "report_file": "report.html",
}


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
def load_config(path: Path) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict) and k not in ("crypto", "stocks"):
                cfg[k].update(v)
            else:
                cfg[k] = v
    else:
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
        print(f"Created default config at {path}")
    return cfg


# --------------------------------------------------------------------------
# Price data
# --------------------------------------------------------------------------
TF_DELTA = {"1h": timedelta(hours=1), "4h": timedelta(hours=4), "1d": timedelta(days=1)}


def drop_unfinished(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Signals use closed bars only, so they do not flip while a bar forms."""
    if len(df) and df.index[-1].to_pydatetime() + TF_DELTA[tf] > datetime.now(UTC):
        df = df.iloc[:-1]
    return df


def fetch_crypto(symbol: str, tf: str, limit: int = 1000) -> pd.DataFrame:
    """Public Binance market data, no API key needed."""
    last_err = None
    for host in ("https://data-api.binance.vision", "https://api.binance.com"):
        try:
            r = requests.get(f"{host}/api/v3/klines",
                             params={"symbol": symbol, "interval": tf, "limit": limit},
                             headers=UA, timeout=20)
            r.raise_for_status()
            rows = r.json()
            df = pd.DataFrame(rows).iloc[:, :6]
            df.columns = ["time", "open", "high", "low", "close", "volume"]
            df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
            df = df.set_index("time").astype(float)
            return drop_unfinished(df, tf)
        except Exception as e:  # try the next host
            last_err = e
    raise RuntimeError(f"Binance: {last_err}")


def fetch_stock(ticker: str, tf: str) -> pd.DataFrame:
    """Yahoo Finance via yfinance. 4h bars are built from 1h bars."""
    import yfinance as yf
    if tf == "1d":
        raw = yf.Ticker(ticker).history(period="3y", interval="1d", auto_adjust=True)
    else:
        raw = yf.Ticker(ticker).history(period="1y", interval="1h", auto_adjust=True)
    if raw is None or raw.empty:
        raise RuntimeError(f"Yahoo returned no data for {ticker}")
    df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]
    df.index = pd.to_datetime(df.index, utc=True)
    if tf == "4h":
        df = df.resample("4h").agg({"open": "first", "high": "max", "low": "min",
                                    "close": "last", "volume": "sum"}).dropna()
    return drop_unfinished(df.astype(float), tf)


def demo_prices(seed: int, tf: str, n: int = 900, start: float = 100.0) -> pd.DataFrame:
    """Synthetic random-walk prices with trending regimes, for offline testing."""
    rng = np.random.default_rng(seed + {"1d": 0, "4h": 1, "1h": 2}[tf])
    vol = {"1d": 0.02, "4h": 0.009, "1h": 0.005}[tf]
    drift = np.repeat(rng.normal(0, vol * 0.12, n // 60 + 1), 60)[:n]
    close = start * np.exp(np.cumsum(drift + rng.normal(0, vol, n)))
    spread = np.abs(rng.normal(0, vol * 0.6, n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    volume = rng.lognormal(10, 0.4, n) * (1 + 8 * np.abs(np.r_[0, np.diff(np.log(close))]) / vol / 4)
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    idx = pd.date_range(end=end, periods=n, freq=TF_DELTA[tf])
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": volume}, index=idx)


# --------------------------------------------------------------------------
# Indicators
# --------------------------------------------------------------------------
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    c, h, l, v = d["close"], d["high"], d["low"], d["volume"]
    d["ema20"], d["ema50"], d["ema200"] = ema(c, 20), ema(c, 50), ema(c, 200)

    macd = ema(c, 12) - ema(c, 26)
    d["macd_hist"] = macd - macd.ewm(span=9, adjust=False, min_periods=9).mean()

    delta = c.diff()
    gain, loss = wilder(delta.clip(lower=0), 14), wilder(-delta.clip(upper=0), 14)
    d["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    d["rsi"] = d["rsi"].fillna(100).where(gain.notna())

    mid, sd = c.rolling(20).mean(), c.rolling(20).std(ddof=0)
    d["bb_pct"] = (c - (mid - 2 * sd)) / (4 * sd).replace(0, np.nan)   # 0 = lower band, 1 = upper

    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d["atr"] = wilder(tr, 14)
    up, dn = h.diff(), -l.diff()
    plus_dm = up.where((up > dn) & (up > 0), 0.0)
    minus_dm = dn.where((dn > up) & (dn > 0), 0.0)
    plus_di = 100 * wilder(plus_dm, 14) / d["atr"]
    minus_di = 100 * wilder(minus_dm, 14) / d["atr"]
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    d["adx"] = wilder(dx.fillna(0), 14)

    d["obv"] = (np.sign(delta).fillna(0) * v).cumsum()
    d["vol_ratio"] = v / v.rolling(20).mean()
    return d


def component_scores(d: pd.DataFrame) -> pd.DataFrame:
    """Each component is a number from -1 (bearish) to +1 (bullish), per bar."""
    c = d["close"]
    out = pd.DataFrame(index=d.index)

    # Trend: where price and the moving averages sit, scaled by trend strength (ADX)
    raw = (0.4 * np.sign(d["ema50"] - d["ema200"])
           + 0.35 * np.sign(c - d["ema50"])
           + 0.25 * np.sign(d["ema20"] - d["ema20"].shift(5)))
    strength = (d["adx"] / 25).clip(0.4, 1.0)
    out["trend"] = (raw * strength).where(d["ema200"].notna())

    # MACD: histogram side, plus whether it is rising or falling
    hist = d["macd_hist"]
    out["macd"] = (0.6 * np.sign(hist) + 0.4 * np.sign(hist - hist.shift(1))).where(hist.notna())

    # RSI: oversold is bullish, overbought is bearish, mild momentum in between
    rsi = d["rsi"]
    out["rsi"] = np.select([rsi <= 30, rsi >= 70], [1.0, -1.0], default=(rsi - 50) / 40)
    out["rsi"] = out["rsi"].where(rsi.notna())

    # Bollinger: price outside or near the bands (mean reversion)
    bb = d["bb_pct"]
    out["bollinger"] = np.select([bb <= 0, bb >= 1, bb < 0.15, bb > 0.85],
                                 [1.0, -1.0, 0.5, -0.5], default=0.0)
    out["bollinger"] = out["bollinger"].where(bb.notna())

    # Volume: is money flowing in (OBV rising), louder on high-volume bars
    obv_dir = np.sign(d["obv"] - d["obv"].shift(20))
    surge = np.where(d["vol_ratio"] > 1.5, np.sign(c.diff()), 0.0)
    out["volume"] = (0.7 * obv_dir + 0.3 * surge).where(d["vol_ratio"].notna())
    return out.clip(-1, 1)


def tech_score(comp: pd.DataFrame, weights: dict) -> pd.Series:
    w = {k: weights[k] for k in comp.columns if weights.get(k, 0) > 0}
    total = sum(w.values())
    score = sum(comp[k] * wk for k, wk in w.items()) / total * 100
    return score


# --------------------------------------------------------------------------
# News and sentiment
# --------------------------------------------------------------------------
FIN_WORDS = {
    "surge": 2.5, "surges": 2.5, "soar": 2.8, "soars": 2.8, "rally": 2.3, "rallies": 2.3,
    "jump": 1.8, "jumps": 1.8, "beat": 1.8, "beats": 2.0, "upgrade": 2.2, "upgrades": 2.2,
    "upgraded": 2.2, "bullish": 2.5, "outperform": 2.0, "record": 1.5, "breakout": 1.8,
    "rebound": 1.6, "rebounds": 1.6, "approval": 1.8, "approves": 1.8, "inflows": 1.5,
    "buyback": 1.5, "raises": 1.2, "tops": 1.5, "gains": 1.6, "climbs": 1.6,
    "plunge": -2.8, "plunges": -2.8, "crash": -3.0, "crashes": -3.0, "slump": -2.3,
    "slumps": -2.3, "tumble": -2.4, "tumbles": -2.4, "miss": -1.8, "misses": -2.0,
    "downgrade": -2.2, "downgrades": -2.2, "downgraded": -2.2, "bearish": -2.5,
    "selloff": -2.4, "sell-off": -2.4, "lawsuit": -1.8, "probe": -1.6, "hack": -2.5,
    "hacked": -2.5, "exploit": -2.0, "fraud": -2.8, "bankruptcy": -3.0, "layoffs": -1.6,
    "outflows": -1.5, "liquidations": -1.8, "recession": -2.0, "cuts": -1.0, "falls": -1.6,
    "drops": -1.6, "sinks": -2.0, "slides": -1.6, "warning": -1.5, "ban": -1.8,
    "tariff": -1.2, "tariffs": -1.2, "delisting": -2.0, "investigation": -1.6,
}
_analyzer = None


def sentiment(text: str) -> float:
    """Headline sentiment from -1 to +1 (VADER plus finance vocabulary)."""
    global _analyzer
    if _analyzer is None:
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            _analyzer = SentimentIntensityAnalyzer()
            _analyzer.lexicon.update(FIN_WORDS)
        except ImportError:
            _analyzer = False
    if _analyzer:
        return _analyzer.polarity_scores(text)["compound"]
    words = [w.strip(".,:;!?'\"()").lower() for w in text.split()]
    s = sum(FIN_WORDS.get(w, 0) for w in words)
    return s / math.sqrt(s * s + 15)


def fetch_rss(url: str) -> list[dict]:
    r = requests.get(url, headers=UA, timeout=15)
    r.raise_for_status()
    items = []
    for it in ElementTree.fromstring(r.content).iter("item"):
        title = (it.findtext("title") or "").strip()
        try:
            when = parsedate_to_datetime(it.findtext("pubDate"))
            when = when if when.tzinfo else when.replace(tzinfo=UTC)
        except Exception:
            continue
        if title:
            items.append({"title": title, "link": (it.findtext("link") or "").strip(), "time": when})
    return items


def get_news(symbol: str, name: str, kind: str, cfg: dict) -> list[dict]:
    hours = cfg["news"]["lookback_hours"]
    urls = []
    if kind == "stock":
        urls.append(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US")
        q = f"{name} stock"
    else:
        q = f"{name} crypto"
    urls.append(f"https://news.google.com/rss/search?q={quote_plus(q)}+when:{max(1, hours // 24)}d"
                "&hl=en-US&gl=US&ceid=US:en")
    seen, items = set(), []
    for u in urls:
        try:
            for it in fetch_rss(u):
                key = it["title"].lower()[:60]
                if key not in seen:
                    seen.add(key)
                    items.append(it)
        except Exception as e:
            print(f"   news source failed for {symbol}: {e}")
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    items = sorted((i for i in items if i["time"] >= cutoff), key=lambda i: i["time"], reverse=True)
    items = items[: cfg["news"]["max_headlines"]]
    for it in items:
        it["sentiment"] = sentiment(it["title"])
    return items


def news_score(items: list[dict], hours: int) -> float | None:
    """Recency-weighted average sentiment, scaled to -1..+1. None if too few headlines."""
    if len(items) < 3:
        return None
    now = datetime.now(UTC)
    w = np.array([math.exp(-(now - i["time"]).total_seconds() / 3600 / (hours / 2)) for i in items])
    s = np.array([i["sentiment"] for i in items])
    return float(np.clip((w * s).sum() / w.sum() * 1.8, -1, 1))


def fear_greed() -> int | None:
    """Crypto Fear & Greed index (0 = extreme fear, 100 = extreme greed)."""
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", headers=UA, timeout=10)
        return int(r.json()["data"][0]["value"])
    except Exception:
        return None


def demo_news(name: str, seed: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    pool = [f"{name} rallies as buyers return", f"Analysts upgrade {name} after strong quarter",
            f"{name} slides on profit taking", f"Regulators open probe into {name}",
            f"{name} holds steady ahead of Fed decision", f"{name} jumps to a record high",
            f"Investors cautious on {name} amid selloff", f"What to watch for {name} this week"]
    now = datetime.now(UTC)
    items = [{"title": "[demo] " + pool[i], "link": "", "time": now - timedelta(hours=float(h))}
             for i, h in zip(rng.choice(len(pool), 5, replace=False), np.sort(rng.uniform(1, 60, 5)))]
    for it in items:
        it["sentiment"] = sentiment(it["title"])
    return items


# --------------------------------------------------------------------------
# Leverage and position size
# --------------------------------------------------------------------------
def leverage_plan(price: float, atr: float, kind: str, cfg: dict) -> dict:
    """Leverage does not create profit, it only decides how much margin a position
    ties up and how close liquidation is. So:
      - position size is set so that hitting the stop loses risk_per_trade_pct of the account
      - leverage is capped so liquidation is at least twice as far away as the stop
    """
    acct = cfg["account"]
    stop_frac = cfg["risk"]["stop_atr"] * atr / price
    cap = acct["max_leverage_crypto" if kind == "crypto" else "max_leverage_stocks"]
    lev = int(max(1, min(cap, math.floor(1 / (2 * stop_frac)))))
    notional = min(acct["size"] * acct["risk_per_trade_pct"] / 100 / stop_frac, acct["size"] * lev)
    return {"leverage": lev, "notional": notional, "margin": notional / lev,
            "risk_money": notional * stop_frac, "stop_pct": stop_frac * 100}


def live_price(symbol: str, kind: str, demo: bool, fallback: float) -> float:
    """Latest traded price (not just the last closed bar), for watching open positions."""
    try:
        if demo:
            return fallback * (1 + float(np.random.default_rng().normal(0, 0.004)))
        if kind == "crypto":
            r = requests.get("https://data-api.binance.vision/api/v3/ticker/price",
                             params={"symbol": symbol}, headers=UA, timeout=10)
            return float(r.json()["price"])
        import yfinance as yf
        return float(yf.Ticker(symbol).fast_info["last_price"])
    except Exception:
        return fallback


# --------------------------------------------------------------------------
# Backtest (technical score only: historical news is not available for free)
# --------------------------------------------------------------------------
def backtest(d: pd.DataFrame, score: pd.Series, th: dict, fee_pct: float, tf: str) -> dict | None:
    """Long-or-cash: buy when score >= buy threshold, sell when <= sell threshold.
    Trades execute on the bar AFTER the signal, with a fee on each side."""
    valid = score.dropna()
    if len(valid) < 150:
        return None
    close = d["close"].loc[valid.index]
    pos, state = np.zeros(len(valid)), 0
    for i, s in enumerate(valid.values):
        if state == 0 and s >= th["buy"]:
            state = 1
        elif state == 1 and s <= th["sell"]:
            state = 0
        pos[i] = state
    pos = pd.Series(pos, index=valid.index).shift(1).fillna(0)   # act on next bar
    ret = close.pct_change().fillna(0)
    fees = pos.diff().abs().fillna(0) * fee_pct / 100
    equity = (1 + pos * ret - fees).cumprod()
    hold = (1 + ret).cumprod()

    trades, entry = [], None
    for t, p in pos.diff().fillna(0).items():
        if p > 0:
            entry = equity.shift(1).fillna(1.0)[t]
        elif p < 0 and entry is not None:
            trades.append(equity[t] / entry - 1)
            entry = None
    dd = (equity / equity.cummax() - 1).min()
    hold_dd = (hold / hold.cummax() - 1).min()
    return {
        "bars": len(valid), "from": valid.index[0].strftime("%Y-%m-%d"),
        "strategy_pct": (equity.iloc[-1] - 1) * 100, "hold_pct": (hold.iloc[-1] - 1) * 100,
        "max_dd_pct": dd * 100, "hold_dd_pct": hold_dd * 100,
        "trades": len(trades),
        "win_rate": (100 * sum(t > 0 for t in trades) / len(trades)) if trades else None,
        "time_in_market_pct": pos.mean() * 100,
    }


# --------------------------------------------------------------------------
# Analysis of one asset
# --------------------------------------------------------------------------
def label_for(score: float, th: dict) -> str:
    if score >= th["strong_buy"]:
        return "STRONG BUY"
    if score >= th["buy"]:
        return "BUY"
    if score <= th["strong_sell"]:
        return "STRONG SELL"
    if score <= th["sell"]:
        return "SELL"
    return "HOLD"


def reasons(row: pd.Series, comp: pd.Series, nscore: float | None) -> list[str]:
    out = []
    out.append("Uptrend: EMA50 above EMA200" if row["ema50"] > row["ema200"]
               else "Downtrend: EMA50 below EMA200")
    out.append(f"Price {'above' if row['close'] > row['ema50'] else 'below'} EMA50, "
               f"ADX {row['adx']:.0f} ({'trending' if row['adx'] >= 25 else 'weak trend'})")
    out.append(f"MACD histogram {'positive' if row['macd_hist'] > 0 else 'negative'}"
               f"{' and rising' if comp['macd'] > 0.6 else ' and falling' if comp['macd'] < -0.6 else ''}")
    rsi = row["rsi"]
    out.append(f"RSI {rsi:.0f}" + (" (oversold)" if rsi <= 30 else " (overbought)" if rsi >= 70 else ""))
    if comp["bollinger"] != 0:
        out.append("Price at the lower Bollinger band" if comp["bollinger"] > 0
                   else "Price at the upper Bollinger band")
    if row["vol_ratio"] > 1.5:
        out.append(f"Volume {row['vol_ratio']:.1f}x the 20-bar average")
    if nscore is not None:
        out.append(f"News sentiment {'positive' if nscore > 0.15 else 'negative' if nscore < -0.15 else 'neutral'}"
                   f" ({nscore:+.2f})")
    return out


def analyze(symbol: str, name: str, kind: str, cfg: dict, demo: bool, seed: int, fng: int | None) -> dict:
    th, wts = cfg["thresholds"], cfg["weights"]
    res = {"symbol": symbol, "name": name, "kind": kind, "frames": {}, "news": [], "news_score": None}

    if cfg["news"]["enabled"]:
        res["news"] = demo_news(name, seed) if demo else get_news(symbol, name, kind, cfg)
        ns = news_score(res["news"], cfg["news"]["lookback_hours"])
        if kind == "crypto" and fng is not None:
            contrarian = (50 - fng) / 50 if (fng <= 25 or fng >= 75) else 0.0
            ns = contrarian if ns is None else 0.7 * ns + 0.3 * contrarian
            res["fear_greed"] = fng
        res["news_score"] = ns

    for tf in cfg["timeframes"]:
        try:
            if demo:
                df = demo_prices(seed, tf, start=50 + seed % 400)
            elif kind == "crypto":
                df = fetch_crypto(symbol, tf)
            else:
                df = fetch_stock(symbol, tf)
            if len(df) < 220:
                raise RuntimeError(f"only {len(df)} bars, need 220+")
            d = add_indicators(df)
            comp = component_scores(d)
            ts = tech_score(comp, wts)
            last, lcomp, tech = d.iloc[-1], comp.iloc[-1], float(ts.iloc[-1])
            ns, wn = res["news_score"], wts.get("news", 0)
            final = tech if ns is None else tech * (1 - wn) + ns * 100 * wn
            label = label_for(final, th)
            atr, price = float(last["atr"]), float(last["close"])
            side = 1 if final > 0 else -1
            res["frames"][tf] = {
                "time": d.index[-1], "price": price, "score": final, "tech_score": tech,
                "label": label, "components": {k: float(v) for k, v in lcomp.items()},
                "reasons": reasons(last, lcomp, ns), "atr": atr,
                "stop": price - side * cfg["risk"]["stop_atr"] * atr,
                "target": price + side * cfg["risk"]["target_atr"] * atr,
                "spark": d["close"].tail(120).tolist(), "spark_ema": d["ema50"].tail(120).tolist(),
                "backtest": backtest(d, ts, th, cfg["backtest"]["fee_pct"], tf),
                "lev": leverage_plan(price, atr, kind, cfg),
            }
        except Exception as e:
            res["frames"][tf] = {"error": str(e)}
            print(f"   {symbol} {tf}: {e}")
    return res


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------
def send_telegram(cfg: dict, text: str) -> None:
    tg = cfg["telegram"]
    if not (tg.get("bot_token") and tg.get("chat_id")):
        return
    try:
        requests.post(f"https://api.telegram.org/bot{tg['bot_token']}/sendMessage",
                      json={"chat_id": tg["chat_id"], "text": text}, timeout=15).raise_for_status()
    except Exception as e:
        print(f"Telegram failed: {e}")


def fmt_price(p: float) -> str:
    return f"{p:,.2f}" if p >= 1 else f"{p:.5f}"


def find_changes(results: list[dict], state_path: Path) -> list[str]:
    """Compare with the previous run and return a line for every changed signal."""
    old = json.loads(state_path.read_text()) if state_path.exists() else {}
    new, changes = {}, []
    for r in results:
        for tf, f in r["frames"].items():
            if "error" in f:
                continue
            key = f"{r['symbol']}|{tf}"
            new[key] = f["label"]
            if key in old and old[key] != f["label"]:
                line = (f"{r['symbol']} {tf}: {old[key]} -> {f['label']} "
                        f"(score {f['score']:+.0f}, price {fmt_price(f['price'])})")
                if f["label"] != "HOLD":
                    line += f", stop {fmt_price(f['stop'])}, target {fmt_price(f['target'])}"
                changes.append(line)
    state_path.write_text(json.dumps(new, indent=1))
    return changes


# --------------------------------------------------------------------------
# Open positions: what you actually bought or shorted
# --------------------------------------------------------------------------
def load_positions(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def save_positions(path: Path, positions: list[dict]) -> None:
    path.write_text(json.dumps(positions, indent=1), encoding="utf-8")


def evaluate_position(pos: dict, frame: dict | None, price: float, cfg: dict) -> dict:
    """Work out profit/loss and what to do with an open position right now."""
    side = 1 if pos["side"] == "long" else -1
    lev, entry = pos["leverage"], pos["entry"]
    pos["best"] = max(pos["best"], price) if side == 1 else min(pos["best"], price)
    atr = frame["atr"] if frame and "error" not in frame else pos["atr"]

    # trailing stop: follows the best price at stop_atr ATRs, never loosens past the first stop
    trail = pos["best"] - side * cfg["risk"]["stop_atr"] * atr
    stop = max(pos["stop"], trail) if side == 1 else min(pos["stop"], trail)
    move = side * (price / entry - 1)
    liq = entry * (1 - side / lev) if lev > 1 else None

    code, text = "hold", "Hold: nothing has changed against you"
    if (price - stop) * side <= 0:
        code, text = "close", "CLOSE: stop level hit"
    elif frame and "error" not in frame and (
            (side == 1 and "SELL" in frame["label"]) or (side == -1 and "BUY" in frame["label"])):
        code, text = "close", f"CLOSE: signal flipped to {frame['label']}"
    elif (price - pos["target"]) * side >= 0:
        code, text = "profit", "TAKE PROFIT: target reached (close, or keep it with the trailing stop)"
    elif liq is not None and abs(price - liq) < 1.5 * atr:
        code, text = "warn", "DANGER: price is close to liquidation, reduce or close"
    elif frame and "error" not in frame and frame["score"] * side < 0:
        code, text = "warn", f"Weakening: score turned against you ({frame['score']:+.0f}), consider reducing"
    return {"price": price, "move_pct": move * 100, "lev_pct": move * lev * 100,
            "money": pos["size"] * move if pos.get("size") else None,
            "stop": stop, "liq": liq, "code": code, "text": text}


# --------------------------------------------------------------------------
# HTML report
# --------------------------------------------------------------------------
CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#16202b;--mute:#667382;--line:#e3e7ec;--buy:#0f8a5f;
--sell:#c8323c;--hold:#7a8694;--accent:#2f6fde}
@media(prefers-color-scheme:dark){:root{--bg:#0f1318;--card:#181e26;--ink:#e8edf2;--mute:#93a0ae;
--line:#28313c;--buy:#3fcf96;--sell:#ff6b73;--hold:#93a0ae;--accent:#6ea3ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:24px;margin:0 0 4px}.sub{color:var(--mute);margin:0 0 20px}
.note{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);
padding:10px 14px;border-radius:8px;margin:0 0 20px;color:var(--mute);font-size:14px}
.demo{border-left-color:var(--sell);color:var(--ink);font-weight:600}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:8px 10px;
border-bottom:1px solid var(--line);font-size:14px}th{color:var(--mute);font-weight:600;font-size:12px;
text-transform:uppercase;letter-spacing:.04em}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin:0 0 18px}
.card h2{font-size:18px;margin:0}.card h2 small{color:var(--mute);font-weight:400;font-size:14px}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-weight:700;font-size:12px;
color:#fff;white-space:nowrap}.b-buy{background:var(--buy)}.b-sell{background:var(--sell)}
.b-hold{background:var(--hold)}
.frames{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:18px;margin-top:14px}
.frame{border:1px solid var(--line);border-radius:10px;padding:14px}
.frame h3{margin:0 0 8px;font-size:14px;display:flex;justify-content:space-between;align-items:center}
.big{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}
.meter{height:8px;background:var(--line);border-radius:4px;position:relative;margin:8px 0 4px}
.meter i{position:absolute;top:0;bottom:0;border-radius:4px}.meter b{position:absolute;left:50%;top:-3px;
bottom:-3px;width:2px;background:var(--mute)}
.comp{display:grid;grid-template-columns:78px 1fr 44px;gap:8px;align-items:center;font-size:13px;color:var(--mute)}
.comp .meter{height:6px;margin:0}ul{margin:8px 0 0;padding-left:18px;font-size:13.5px}
.kv{display:flex;gap:18px;flex-wrap:wrap;font-size:13.5px;margin-top:8px}.kv span{color:var(--mute)}
.bt{font-size:13px;color:var(--mute);margin-top:10px;border-top:1px dashed var(--line);padding-top:8px}
.news{margin-top:14px;font-size:13.5px}.news a{color:var(--ink);text-decoration:none}
.news a:hover{text-decoration:underline}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;
margin-right:7px}.pos{color:var(--buy)}.neg{color:var(--sell)}svg{display:block;width:100%;height:70px}
.err{color:var(--sell);font-size:13.5px}
.lev{font-size:13px;margin-top:10px;padding:8px 10px;background:var(--bg);border-radius:8px}
form.open{display:flex;gap:8px;flex-wrap:wrap;align-items:end;margin-top:10px;font-size:12px;color:var(--mute)}
form.open label{display:flex;flex-direction:column;gap:2px}
input{width:92px;padding:5px 7px;border:1px solid var(--line);border-radius:6px;background:var(--card);
color:var(--ink);font:inherit;font-size:13px}
button{padding:6px 12px;border:0;border-radius:6px;font:inherit;font-size:13px;font-weight:700;color:#fff;cursor:pointer;white-space:nowrap}
button.long{background:var(--buy)}button.short{background:var(--sell)}button.plain{background:var(--hold)}
.adv{font-weight:700}.a-close{color:var(--sell)}.a-profit{color:var(--buy)}.a-warn{color:#c98a00}
.card.mine{border-left:4px solid var(--accent)}form.inline{display:flex;gap:6px;align-items:center}
"""


def badge(label: str) -> str:
    cls = "b-buy" if "BUY" in label else "b-sell" if "SELL" in label else "b-hold"
    return f'<span class="badge {cls}">{label}</span>'


def meter(value: float, scale: float) -> str:
    """Bar from the centre: right (green) for bullish, left (red) for bearish."""
    pct = max(-1, min(1, value / scale)) * 50
    color = "var(--buy)" if pct >= 0 else "var(--sell)"
    left = 50 if pct >= 0 else 50 + pct
    return f'<div class="meter"><i style="left:{left:.1f}%;width:{abs(pct):.1f}%;background:{color}"></i><b></b></div>'


def sparkline(prices: list[float], ema50: list[float]) -> str:
    vals = [v for v in prices + ema50 if v == v]
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1

    def path(series):
        pts = [(i / (len(series) - 1) * 300, 66 - (v - lo) / rng * 62)
               for i, v in enumerate(series) if v == v]
        return " ".join(f"{'M' if j == 0 else 'L'}{x:.1f},{y:.1f}" for j, (x, y) in enumerate(pts))
    return (f'<svg viewBox="0 0 300 70" preserveAspectRatio="none" role="img" '
            f'aria-label="Last 120 bars: price and EMA50">'
            f'<path d="{path(ema50)}" fill="none" stroke="var(--mute)" stroke-width="1" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>'
            f'<path d="{path(prices)}" fill="none" stroke="var(--accent)" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>')


def render_frame(tf: str, f: dict, symbol: str = "", interactive: bool = False) -> str:
    if "error" in f:
        return f'<div class="frame"><h3>{tf}</h3><p class="err">No data: {html.escape(f["error"])}</p></div>'
    comps = "".join(f'<div class="comp"><span>{k}</span>{meter(v, 1)}<span>{v:+.2f}</span></div>'
                    for k, v in f["components"].items())
    lis = "".join(f"<li>{html.escape(x)}</li>" for x in f["reasons"])
    levels = ""
    if f["label"] != "HOLD":
        levels = (f'<div class="kv"><div><span>Stop</span> {fmt_price(f["stop"])}</div>'
                  f'<div><span>Target</span> {fmt_price(f["target"])}</div>'
                  f'<div><span>ATR</span> {fmt_price(f["atr"])}</div></div>')
    bt = f["backtest"]
    if bt:
        wr = f'{bt["win_rate"]:.0f}%' if bt["win_rate"] is not None else "n/a"
        bt_html = (f'<div class="bt">Backtest since {bt["from"]} ({bt["bars"]} bars, technical score only): '
                   f'strategy <b class="{"pos" if bt["strategy_pct"] >= 0 else "neg"}">{bt["strategy_pct"]:+.0f}%</b> '
                   f'vs buy and hold <b>{bt["hold_pct"]:+.0f}%</b>; max drawdown {bt["max_dd_pct"]:.0f}% '
                   f'vs {bt["hold_dd_pct"]:.0f}%; {bt["trades"]} trades, win rate {wr}, '
                   f'in the market {bt["time_in_market_pct"]:.0f}% of the time.</div>')
    else:
        bt_html = '<div class="bt">Not enough history to backtest.</div>'
    lv = f["lev"]
    lev_html = (f'<div class="lev"><b>Leverage: up to {lv["leverage"]}x.</b> Position size {lv["notional"]:,.0f} '
                f'(margin {lv["margin"]:,.0f} at {lv["leverage"]}x). If the stop {lv["stop_pct"]:.1f}% away is hit, '
                f'the loss is about {lv["risk_money"]:,.0f}.</div>')
    if interactive:
        lev_html += (f'<form class="open" method="post" action="/open">'
                     f'<input type="hidden" name="symbol" value="{symbol}"><input type="hidden" name="tf" value="{tf}">'
                     f'<label>Entry price<input name="entry" value="{f["price"]:.6g}"></label>'
                     f'<label>Leverage<input name="leverage" value="{lv["leverage"]}"></label>'
                     f'<label>Position size<input name="size" value="{lv["notional"]:.0f}"></label>'
                     f'<button class="long" name="side" value="long">I bought (long)</button>'
                     f'<button class="short" name="side" value="short">I went short</button></form>')
    bt_html = lev_html + bt_html
    return (f'<div class="frame"><h3><span>{tf} &middot; bar closed {f["time"]:%Y-%m-%d %H:%M} UTC</span>{badge(f["label"])}</h3>'
            f'<div class="big">{fmt_price(f["price"])} <small style="font-size:14px;color:var(--mute)">score {f["score"]:+.0f}</small></div>'
            f'{meter(f["score"], 100)}{sparkline(f["spark"], f["spark_ema"])}{comps}<ul>{lis}</ul>{levels}{bt_html}</div>')


def render_positions(positions: list[dict], evals: dict) -> str:
    open_rows = ""
    for p in positions:
        if p["status"] != "open":
            continue
        e = evals.get(p["id"])
        if not e:
            continue
        cls = "pos" if e["move_pct"] >= 0 else "neg"
        money = f'{e["money"]:+,.2f}' if e["money"] is not None else ""
        liq = fmt_price(e["liq"]) if e["liq"] else "none"
        open_rows += (
            f'<tr><td><b>{p["symbol"]}</b> {p["tf"]}</td><td>{p["side"].upper()} {p["leverage"]:g}x</td>'
            f'<td class="num">{fmt_price(p["entry"])}</td><td class="num">{fmt_price(e["price"])}</td>'
            f'<td class="num {cls}">{e["move_pct"]:+.2f}% <small>({e["lev_pct"]:+.1f}% on margin)</small></td>'
            f'<td class="num {cls}">{money}</td><td class="num">{fmt_price(e["stop"])}</td>'
            f'<td class="num">{fmt_price(p["target"])}</td><td class="num">{liq}</td>'
            f'<td class="adv a-{e["code"]}">{html.escape(e["text"])}</td>'
            f'<td><form class="inline" method="post" action="/close"><input type="hidden" name="id" value="{p["id"]}">'
            f'<input name="exit" value="{e["price"]:.6g}"><button class="plain">I closed it</button></form></td></tr>')
    out = ""
    if open_rows:
        out += ('<section class="card mine"><h2>My open positions</h2><table><thead><tr><th>Asset</th><th>Side</th>'
                '<th class="num">Entry</th><th class="num">Now</th><th class="num">Result</th><th class="num">Money</th>'
                '<th class="num">Stop (trailing)</th><th class="num">Target</th><th class="num">Liquidation</th>'
                f'<th>What to do</th><th>Exit price</th></tr></thead><tbody>{open_rows}</tbody></table></section>')
    else:
        out += ('<section class="card mine"><h2>My open positions</h2><p class="sub" style="margin:8px 0 0">None yet. '
                'When you really open a trade, press "I bought" or "I went short" under that asset and it will be watched here.</p></section>')
    closed = [p for p in positions if p["status"] == "closed"][-10:][::-1]
    if closed:
        rows = ""
        for p in closed:
            side = 1 if p["side"] == "long" else -1
            mv = side * (p["exit"] / p["entry"] - 1)
            money = f'{p["size"] * mv:+,.2f}' if p.get("size") else ""
            rows += (f'<tr><td><b>{p["symbol"]}</b> {p["tf"]}</td><td>{p["side"].upper()} {p["leverage"]:g}x</td>'
                     f'<td>{p["opened"][:16]}</td><td>{p["closed"][:16]}</td><td class="num">{fmt_price(p["entry"])}</td>'
                     f'<td class="num">{fmt_price(p["exit"])}</td>'
                     f'<td class="num {"pos" if mv >= 0 else "neg"}">{mv * 100:+.2f}% ({mv * p["leverage"] * 100:+.1f}% on margin)</td>'
                     f'<td class="num">{money}</td></tr>')
        out += ('<section class="card"><h2>Closed trades <small>last 10</small></h2><table><thead><tr><th>Asset</th><th>Side</th>'
                '<th>Opened (UTC)</th><th>Closed (UTC)</th><th class="num">Entry</th><th class="num">Exit</th>'
                f'<th class="num">Result</th><th class="num">Money</th></tr></thead><tbody>{rows}</tbody></table></section>')
    return out


def render_report(results: list[dict], changes: list[str], cfg: dict, demo: bool,
                  positions: list[dict] | None = None, evals: dict | None = None) -> str:
    interactive = positions is not None
    pos_html = render_positions(positions, evals or {}) if interactive else ""
    refresh_js = ("<script>setInterval(function(){var a=document.activeElement;"
                  "if(!a||a.tagName!=='INPUT')location.reload()},30000)</script>") if interactive else ""
    tfs = cfg["timeframes"]
    rows = ""
    for r in results:
        cells = ""
        for tf in tfs:
            f = r["frames"].get(tf, {})
            cells += ('<td class="err">no data</td><td></td>' if "error" in f or not f else
                      f'<td>{badge(f["label"])}</td><td class="num">{f["score"]:+.0f}</td>')
        first = next((f for f in r["frames"].values() if "error" not in f), None)
        ns = r["news_score"]
        rows += (f'<tr><td><b>{r["symbol"]}</b> <span style="color:var(--mute)">{html.escape(r["name"])}</span></td>'
                 f'<td class="num">{fmt_price(first["price"]) if first else ""}</td>{cells}'
                 f'<td class="num">{f"{ns:+.2f}" if ns is not None else "n/a"}</td></tr>')
    head = "".join(f"<th>{tf} signal</th><th class='num'>score</th>" for tf in tfs)

    cards = ""
    for r in results:
        news = ""
        for n in r["news"][:8]:
            s = n["sentiment"]
            col = "var(--buy)" if s > 0.15 else "var(--sell)" if s < -0.15 else "var(--hold)"
            title = html.escape(n["title"])
            link = f'<a href="{html.escape(n["link"])}" target="_blank" rel="noopener">{title}</a>' if n["link"] else title
            news += (f'<div><span class="dot" style="background:{col}"></span>{link} '
                     f'<span style="color:var(--mute)">{n["time"]:%d %b %H:%M} ({s:+.2f})</span></div>')
        extra = f' &middot; Fear &amp; Greed {r["fear_greed"]}' if "fear_greed" in r else ""
        news_block = (f'<div class="news"><b>Recent headlines</b>{extra}{news}</div>' if news else
                      '<div class="news" style="color:var(--mute)">No recent headlines found; score uses technicals only.</div>')
        frames = "".join(render_frame(tf, r["frames"][tf], r["symbol"], interactive) for tf in tfs if tf in r["frames"])
        cards += (f'<section class="card"><h2>{r["symbol"]} <small>{html.escape(r["name"])} &middot; {r["kind"]}</small></h2>'
                  f'<div class="frames">{frames}</div>{news_block}</section>')

    chg = ""
    if changes:
        chg = '<div class="note"><b>Changed since last run:</b><br>' + "<br>".join(html.escape(c) for c in changes) + "</div>"
    demo_note = ('<div class="note demo">DEMO RUN: prices and headlines on this page are randomly generated, '
                 'not real market data. Run without --demo for live signals.</div>') if demo else ""
    th = cfg["thresholds"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Trading signals</title>
<style>{CSS}</style></head><body><main>
<h1>Trading signals</h1>
<p class="sub">Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC &middot; closed bars only</p>
{demo_note}{chg}{pos_html}
<section class="card"><table><thead><tr><th>Asset</th><th class="num">Price</th>{head}<th class="num">News</th></tr></thead>
<tbody>{rows}</tbody></table></section>
{cards}
<div class="note">Score runs from -100 (bearish) to +100 (bullish). BUY at {th["buy"]:+d} or more, STRONG BUY at {th["strong_buy"]:+d},
SELL at {th["sell"]:+d} or less, STRONG SELL at {th["strong_sell"]:+d}. Stop and target are ATR-based suggestions.
Leverage is the highest that keeps liquidation at least twice as far away as the stop (capped in config.json); position size is set so a
stop-out costs {cfg["account"]["risk_per_trade_pct"]:g}% of a {cfg["account"]["size"]:,.0f} account. Liquidation prices are approximate.
Backtests cover the technical score only, include fees but not slippage or taxes, and past results do not predict future ones.
This is an information tool, not financial advice.</div>
</main>{refresh_js}</body></html>"""


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def compute_results(cfg: dict, demo: bool) -> list[dict]:
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Checking signals{' (DEMO data)' if demo else ''}...")
    fng = None if demo or not cfg["news"]["enabled"] else fear_greed()
    results, seed = [], 7
    for kind, key in (("crypto", "crypto"), ("stock", "stocks")):
        for symbol, name in cfg.get(key, {}).items():
            seed += 37
            print(f" - {symbol}")
            results.append(analyze(symbol, name, kind, cfg, demo, seed, fng))
    return results


class App:
    """Live dashboard: refreshes signals, watches open positions, serves the page."""

    def __init__(self, cfg: dict, demo: bool, outdir: Path):
        self.cfg, self.demo, self.outdir = cfg, demo, outdir
        self.lock = threading.RLock()
        self.results, self.changes, self.evals = [], [], {}
        self.pos_path = outdir / ("positions_demo.json" if demo else "positions.json")
        self.positions = load_positions(self.pos_path)

    def frame(self, symbol: str, tf: str):
        for r in self.results:
            if r["symbol"] == symbol:
                return r, r["frames"].get(tf)
        return None, None

    def refresh_signals(self) -> None:
        results = compute_results(self.cfg, self.demo)
        state = self.outdir / ("state_demo.json" if self.demo else "state.json")
        changes = find_changes(results, state)
        with self.lock:
            self.results = results
            if changes:
                self.changes = changes
        if changes:
            print("Changed signals:\n  " + "\n  ".join(changes))
            if not self.demo:
                send_telegram(self.cfg, "Signal changes\n" + "\n".join(changes))

    def check_positions(self) -> None:
        with self.lock:
            open_pos = [p for p in self.positions if p["status"] == "open"]
        for p in open_pos:
            r, f = self.frame(p["symbol"], p["tf"])
            fallback = f["price"] if f and "error" not in f else p["entry"]
            price = live_price(p["symbol"], p["kind"], self.demo, fallback)
            with self.lock:
                e = evaluate_position(p, f, price, self.cfg)
                self.evals[p["id"]] = e
                if e["code"] != p.get("last_code"):
                    p["last_code"] = e["code"]
                    if e["code"] != "hold":
                        msg = (f"{p['symbol']} {p['tf']} {p['side'].upper()} {p['leverage']:g}x: {e['text']}\n"
                               f"price {fmt_price(price)}, result {e['move_pct']:+.2f}% ({e['lev_pct']:+.1f}% on margin)")
                        print("POSITION ALERT: " + msg)
                        if not self.demo:
                            send_telegram(self.cfg, msg)
                save_positions(self.pos_path, self.positions)

    def open_position(self, form: dict) -> None:
        r, f = self.frame(form["symbol"], form["tf"])
        if not f or "error" in f or form.get("side") not in ("long", "short"):
            return
        side = 1 if form["side"] == "long" else -1
        entry = float(form.get("entry") or f["price"])
        lev = max(1.0, float(form.get("leverage") or 1))
        size = float(form["size"]) if form.get("size") else None
        risk = self.cfg["risk"]
        pos = {"id": uuid.uuid4().hex[:8], "symbol": r["symbol"], "kind": r["kind"], "tf": form["tf"],
               "side": form["side"], "entry": entry, "leverage": lev, "size": size, "atr": f["atr"],
               "stop": entry - side * risk["stop_atr"] * f["atr"],
               "target": entry + side * risk["target_atr"] * f["atr"],
               "best": entry, "opened": datetime.now(UTC).isoformat(timespec="minutes"), "status": "open"}
        with self.lock:
            self.positions.append(pos)
            save_positions(self.pos_path, self.positions)
        self.check_positions()

    def close_position(self, form: dict) -> None:
        with self.lock:
            for p in self.positions:
                if p["id"] == form.get("id") and p["status"] == "open":
                    last = self.evals.get(p["id"], {}).get("price", p["entry"])
                    p.update(status="closed", exit=float(form.get("exit") or last),
                             closed=datetime.now(UTC).isoformat(timespec="minutes"))
            save_positions(self.pos_path, self.positions)

    def page(self) -> str:
        with self.lock:
            if not self.results:
                return ("<meta http-equiv='refresh' content='5'><body style='font-family:system-ui;padding:40px'>"
                        "Loading market data, first run takes about a minute...</body>")
            return render_report(self.results, self.changes, self.cfg, self.demo, self.positions, self.evals)

    def worker(self) -> None:
        next_full = 0.0
        srv = self.cfg["server"]
        while True:
            try:
                if time.time() >= next_full:
                    self.refresh_signals()
                    next_full = time.time() + srv["signal_refresh_minutes"] * 60
                self.check_positions()
            except Exception as e:
                print(f"Check failed, will retry: {e}")
            time.sleep(srv["price_check_seconds"])


def serve(cfg: dict, demo: bool, outdir: Path) -> None:
    app = App(cfg, demo, outdir)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = app.page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8")
            form = {k: v[0].strip().replace(",", ".") for k, v in parse_qs(raw).items()}
            try:
                if self.path == "/open":
                    app.open_position(form)
                elif self.path == "/close":
                    app.close_position(form)
            except Exception as e:
                print(f"Could not save position: {e}")
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

    port = cfg["server"]["port"]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)   # this computer only
    threading.Thread(target=app.worker, daemon=True).start()
    url = f"http://localhost:{port}"
    print(f"Dashboard: {url}   (keep this window open; Ctrl+C to stop)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")


def run_once(cfg: dict, demo: bool, outdir: Path) -> None:
    results = compute_results(cfg, demo)

    state = outdir / ("state_demo.json" if demo else "state.json")
    changes = find_changes(results, state)
    report = outdir / cfg["report_file"]
    report.write_text(render_report(results, changes, cfg, demo), encoding="utf-8")

    print()
    for r in results:
        parts = [f"{tf}: {f['label']:<11} ({f['score']:+4.0f})" if "error" not in f else f"{tf}: no data"
                 for tf, f in r["frames"].items()]
        print(f"  {r['symbol']:<9} " + "   ".join(parts))
    if changes:
        print("\nChanged signals:\n  " + "\n  ".join(changes))
        if not demo:
            send_telegram(cfg, "Signal changes\n" + "\n".join(changes))
    print(f"\nReport: {report}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Buy/sell signal bot for crypto and stocks")
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--serve", action="store_true", help="live dashboard with position tracking")
    ap.add_argument("--loop", type=float, metavar="MINUTES", help="repeat every N minutes")
    ap.add_argument("--demo", action="store_true", help="use synthetic data, no internet")
    args = ap.parse_args()
    cfg_path = Path(args.config)
    cfg = load_config(cfg_path)
    if not args.demo and requests is None:
        sys.exit("Install dependencies first: pip install -r requirements.txt")
    if args.serve:
        serve(cfg, args.demo, cfg_path.parent)
        return
    while True:
        try:
            run_once(cfg, args.demo, cfg_path.parent)
        except Exception as e:
            print(f"Run failed: {e}")
            if not args.loop:
                raise
        if not args.loop:
            break
        time.sleep(args.loop * 60)


if __name__ == "__main__":
    main()
