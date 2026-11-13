#!/usr/bin/env python3
"""
Cloud scanner. Runs on a schedule in GitHub Actions.

1. Builds the universe: every liquid USDT pair on Binance, plus the US and
   European stock lists (data/universe.json and scanner/europe.txt).
2. Scores every asset on 1h, 4h and 1d bars (stocks: 1d for all, 1h/4h for
   the strongest candidates only).
3. Adds news sentiment for the strongest candidates.
4. Checks how past signals of the same kind worked out on that asset.
5. Sends the ranked result to the dashboard (Cloudflare Worker).

Environment:
    DASH_URL      e.g. https://signals.example.com   (optional: without it, only data/latest.json is written)
    INGEST_TOKEN  shared secret with the Worker
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import signal_bot as sb  # indicators, scoring, news, leverage (shared with the local version)
import paper

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
UTC = timezone.utc
UA = sb.UA
HORIZON = {"1h": 12, "4h": 12, "1d": 7}          # bars to look ahead when checking past signals
STABLE = {"USDC", "FDUSD", "TUSD", "DAI", "USDP", "BUSD", "EUR", "EURI", "AEUR", "USD1", "XUSD",
          "BFUSD", "USDE", "PYUSD", "GBP", "TRY", "BRL", "USDS", "FRAX", "UST", "USTC"}
COIN_NAMES = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "XRP": "XRP Ripple", "BNB": "BNB Binance",
              "DOGE": "Dogecoin", "ADA": "Cardano", "AVAX": "Avalanche", "LINK": "Chainlink", "DOT": "Polkadot",
              "LTC": "Litecoin", "TRX": "Tron", "TON": "Toncoin", "SHIB": "Shiba Inu", "SUI": "Sui",
              "NEAR": "NEAR Protocol", "UNI": "Uniswap", "PEPE": "Pepe coin", "APT": "Aptos", "ARB": "Arbitrum",
              "OP": "Optimism", "ATOM": "Cosmos", "XLM": "Stellar", "HBAR": "Hedera", "AAVE": "Aave",
              "BCH": "Bitcoin Cash", "ETC": "Ethereum Classic", "FIL": "Filecoin", "INJ": "Injective"}


# --------------------------------------------------------------------------
# Crypto data: Binance public market data, KuCoin as fallback
# --------------------------------------------------------------------------
def get_json(url: str, params: dict | None = None, timeout: int = 20):
    r = requests.get(url, params=params, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.json()


def crypto_universe(cfg: dict) -> tuple[str, list[dict]]:
    """Returns (source, [{symbol, base, volume}]) sorted by 24h volume."""
    u = cfg["universe"]
    rows, source = [], None
    for host in ("https://data-api.binance.vision", "https://api.binance.com"):
        try:
            for t in get_json(f"{host}/api/v3/ticker/24hr", timeout=30):
                if t["symbol"].endswith("USDT"):
                    rows.append({"symbol": t["symbol"], "base": t["symbol"][:-4],
                                 "volume": float(t["quoteVolume"])})
            source = "binance"
            break
        except Exception as e:
            print(f"Binance universe via {host} failed: {e}")
    if source is None:
        data = get_json("https://api.kucoin.com/api/v1/market/allTickers", timeout=30)["data"]["ticker"]
        for t in data:
            if t["symbol"].endswith("-USDT"):
                base = t["symbol"][:-5]
                rows.append({"symbol": base + "USDT", "base": base, "volume": float(t.get("volValue") or 0)})
        source = "kucoin"
    keep = [r for r in rows
            if r["volume"] >= u["crypto_min_volume_usd"] and r["base"] not in STABLE
            and not r["base"].endswith(("UP", "DOWN", "BULL", "BEAR"))]
    keep.sort(key=lambda r: -r["volume"])
    return source, keep[: u["crypto_max"]]


def kucoin_klines(base: str, tf: str) -> pd.DataFrame:
    typ = {"1h": "1hour", "4h": "4hour", "1d": "1day"}[tf]
    data = get_json("https://api.kucoin.com/api/v1/market/candles",
                    {"type": typ, "symbol": f"{base}-USDT"})["data"]
    df = pd.DataFrame(data, columns=["time", "open", "close", "high", "low", "volume", "turnover"])
    df["time"] = pd.to_datetime(df["time"].astype("int64"), unit="s", utc=True)
    df = df.set_index("time").sort_index()[["open", "high", "low", "close", "volume"]].astype(float)
    return sb.drop_unfinished(df, tf)


def crypto_frame(source: str, row: dict, tf: str) -> pd.DataFrame | None:
    for attempt in range(2):
        try:
            if source == "binance":
                return sb.fetch_crypto(row["symbol"], tf, limit=600)
            return kucoin_klines(row["base"], tf)
        except Exception as e:
            if attempt:
                print(f"   {row['symbol']} {tf}: {e}")
            time.sleep(1)
    return None


# --------------------------------------------------------------------------
# Stock data: Yahoo Finance in batches
# --------------------------------------------------------------------------
def load_stock_universe() -> dict[str, str]:
    """ticker -> name, from data/universe.json (US) and scanner/europe.txt."""
    names = dict(json.loads((ROOT / "data" / "universe.json").read_text(encoding="utf-8")).get("us", {}))
    for line in (HERE / "europe.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            t, _, n = line.partition(",")
            names[t.strip()] = n.strip() or t.strip()
    return names


def yahoo_batch(tickers: list[str], period: str, interval: str, chunk: int = 150) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    out = {}
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        try:
            raw = yf.download(part, period=period, interval=interval, group_by="ticker",
                              auto_adjust=True, threads=True, progress=False)
        except Exception as e:
            print(f"   Yahoo batch {i} failed: {e}")
            continue
        for t in part:
            try:
                df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
                if len(df):
                    df.columns = ["open", "high", "low", "close", "volume"]
                    df.index = pd.to_datetime(df.index, utc=True)
                    out[t] = df.astype(float)
            except Exception:
                pass
        time.sleep(1)
    return out


def to_4h(df: pd.DataFrame) -> pd.DataFrame:
    return df.resample("4h").agg({"open": "first", "high": "max", "low": "min",
                                  "close": "last", "volume": "sum"}).dropna()


# --------------------------------------------------------------------------
# Scoring one asset on one timeframe
# --------------------------------------------------------------------------
def past_signals(close: pd.Series, score: pd.Series, th: dict, horizon: int) -> dict:
    """How did earlier BUY and SELL signals on this asset work out?
    For each time the score crossed into BUY (or SELL) territory, measure the
    move over the next `horizon` bars, in the signal's direction."""
    fwd = (close.shift(-horizon) / close - 1) * 100
    prev = score.shift(1)
    res = {}
    for side, entries, sign in (("buy", (score >= th["buy"]) & (prev < th["buy"]), 1),
                                ("sell", (score <= th["sell"]) & (prev > th["sell"]), -1)):
        moves = (fwd[entries] * sign).dropna()
        res[side] = {"n": int(len(moves)),
                     "avg": round(float(moves.mean()), 2) if len(moves) else None,
                     "win": round(float((moves > 0).mean() * 100)) if len(moves) else None}
    return res


def score_frame(df: pd.DataFrame, tf: str, cfg: dict) -> dict | None:
    if df is None or len(df) < 220:
        return None
    d = sb.add_indicators(df)
    comp = sb.component_scores(d)
    ts = sb.tech_score(comp, cfg["weights"])
    if not np.isfinite(ts.iloc[-1]) or not np.isfinite(d["atr"].iloc[-1]) or d["atr"].iloc[-1] <= 0:
        return None
    return {"tf": tf, "d": d, "comp": comp.iloc[-1], "tech": float(ts.iloc[-1]),
            "hist": past_signals(d["close"], ts, cfg["thresholds"], HORIZON[tf])}


def sig(x: float, n: int = 6) -> float:
    return float(f"{x:.{n}g}")


def build_item(symbol: str, name: str, kind: str, fr: dict, news: dict | None, cfg: dict) -> dict:
    d, last = fr["d"], fr["d"].iloc[-1]
    ns, wn = (news or {}).get("score"), cfg["weights"].get("news", 0)
    score = fr["tech"] if ns is None else fr["tech"] * (1 - wn) + ns * 100 * wn
    label = sb.label_for(score, cfg["thresholds"])
    price, atr = float(last["close"]), float(last["atr"])
    side = 1 if score > 0 else -1
    h = fr["hist"]["buy" if side == 1 else "sell"]
    hc = cfg["history_check"]
    proven = bool(h["n"] >= hc["min_past_signals"] and h["avg"] is not None and h["avg"] >= hc["min_avg_move_pct"])
    lev = sb.leverage_plan(price, atr, "crypto" if kind == "crypto" else "stock", cfg)
    return {
        "symbol": symbol, "name": name, "kind": kind, "tf": fr["tf"],
        "time": d.index[-1].strftime("%Y-%m-%d %H:%M"), "price": sig(price), "atr": sig(atr),
        "score": round(score), "tech": round(fr["tech"]), "label": label,
        "stop": sig(price - side * cfg["risk"]["stop_atr"] * atr),
        "target": sig(price + side * cfg["risk"]["target_atr"] * atr),
        "lev": {"x": lev["leverage"], "size": round(lev["notional"]), "margin": round(lev["margin"]),
                "risk": round(lev["risk_money"], 1), "stop_pct": round(lev["stop_pct"], 1)},
        "hist": {**h, "bars": HORIZON[fr["tf"]]}, "proven": proven,
        "reasons": sb.reasons(last, fr["comp"], ns),
        "news": ns if ns is None else round(ns, 2),
        "headlines": [{"t": n["title"][:140], "l": n["link"], "s": round(n["sentiment"], 2)}
                      for n in (news or {}).get("items", [])[:4]],
        "spark": [sig(v, 5) for v in d["close"].tail(60).tolist()],
    }


def fetch_news(symbol: str, name: str, kind: str, cfg: dict, fng: int | None) -> dict:
    items = sb.get_news(symbol, name, "crypto" if kind == "crypto" else "stock", cfg)
    ns = sb.news_score(items, cfg["news"]["lookback_hours"])
    if kind == "crypto" and fng is not None:
        contrarian = (50 - fng) / 50 if (fng <= 25 or fng >= 75) else 0.0
        ns = contrarian if ns is None else 0.7 * ns + 0.3 * contrarian
    return {"score": ns, "items": items}


# --------------------------------------------------------------------------
# What the research backtest says about each kind of signal
# --------------------------------------------------------------------------
def load_verdicts() -> dict:
    """(market, tf, side, family) -> how signals of that kind did in research/results.csv
    (trailing-stop exit, no extra filter), in both halves of the history."""
    path = ROOT / "research" / "results.csv"
    if not path.exists():
        return {}
    r = pd.read_csv(path)
    r = r[(r["exit"] == "trail") & (r["filter"] == "none") & r["family"].isin(["score40", "score55"])]
    out = {}
    for _, x in r.iterrows():
        both_up = x.is_avg_r > 0 and x.oos_avg_r > 0
        if both_up and x.is_t >= 2 and x.oos_t >= 1.8:
            v = "good"
        elif x.oos_avg_r < 0 and (x.is_avg_r < 0 or x.oos_t <= -1.8):
            v = "bad"
        else:
            v = "unclear"
        out[(x.market, x.tf, x.side, x.family)] = {"v": v, "is": round(float(x.is_avg_r), 2),
                                                   "oos": round(float(x.oos_avg_r), 2),
                                                   "n": int(x.is_n + x.oos_n)}
    return out


def verdict_for(it: dict, verdicts: dict) -> dict | None:
    fam = "score55" if it["label"].startswith("STRONG") else "score40"
    return verdicts.get((paper.market_of(it), it["tf"], "long" if it["score"] > 0 else "short", fam))


# --------------------------------------------------------------------------
# Live prices and 5-minute bars (used by the paper trading test)
# --------------------------------------------------------------------------
def live_price(symbol: str, kind: str) -> float | None:
    try:
        if kind == "crypto":
            try:
                return float(get_json("https://data-api.binance.vision/api/v3/ticker/price",
                                       {"symbol": symbol}, 10)["price"])
            except Exception:
                base = symbol[:-4]
                return float(get_json("https://api.kucoin.com/api/v1/market/orderbook/level1",
                                      {"symbol": f"{base}-USDT"}, 10)["data"]["price"])
        import yfinance as yf
        return float(yf.Ticker(symbol).fast_info["last_price"])
    except Exception as e:
        print(f"   live price failed for {symbol}: {e}")
        return None


def bars_since(symbol: str, kind: str, since: datetime) -> pd.DataFrame:
    """Closed 5-minute bars that started at or after `since`."""
    now = datetime.now(UTC)
    if kind == "crypto":
        frames, start = [], int(since.timestamp() * 1000)
        for _ in range(10):                                   # up to ~35 days of 5m bars
            rows = get_json("https://data-api.binance.vision/api/v3/klines",
                            {"symbol": symbol, "interval": "5m", "startTime": start, "limit": 1000})
            if not rows:
                break
            frames.extend(rows)
            if len(rows) < 1000:
                break
            start = rows[-1][0] + 1
        df = pd.DataFrame([r[:5] for r in frames], columns=["time", "open", "high", "low", "close"])
        df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
        df = df.set_index("time").astype(float)
    else:
        import yfinance as yf
        period = "5d" if now - since < timedelta(days=4) else "60d"
        raw = yf.Ticker(symbol).history(period=period, interval="5m", auto_adjust=True)
        if raw is None or raw.empty:
            return pd.DataFrame()
        df = raw[["Open", "High", "Low", "Close"]].copy()
        df.columns = ["open", "high", "low", "close"]
        df.index = pd.to_datetime(df.index, utc=True)
    df = df[(df.index >= since) & (df.index + timedelta(minutes=5) <= now)]
    return df.astype(float)


_demo_last: dict[str, float] = {}


def demo_bars_since(symbol: str, kind: str, since: datetime) -> pd.DataFrame:
    now = datetime.now(UTC)
    n = int((now - since).total_seconds() // 300)
    if n <= 0:
        return pd.DataFrame()
    rng = np.random.default_rng()
    start = _demo_last.get(symbol, 100.0)
    close = start * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    open_ = np.r_[start, close[:-1]]
    spread = np.abs(rng.normal(0, 0.002, n))
    idx = pd.date_range(start=since, periods=n, freq="5min")
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * (1 + spread),
                         "low": np.minimum(open_, close) * (1 - spread), "close": close}, index=idx)


# --------------------------------------------------------------------------
# Demo data (for testing without internet)
# --------------------------------------------------------------------------
def demo_frames() -> tuple[dict, dict, dict]:
    crypto = {f"{b}USDT": b for b in ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LINK", "AVAX", "SUI", "PEPE",
                                      "NEAR", "ARB", "OP", "INJ", "APT"]}
    stocks = {"AAPL": "Apple", "NVDA": "Nvidia", "TSLA": "Tesla", "SAP.DE": "SAP", "ASML.AS": "ASML",
              "MC.PA": "LVMH", "AMD": "AMD", "META": "Meta", "SHEL.L": "Shell", "TKM1T.TL": "Tallinna Kaubamaja"}
    frames = {}
    for i, s in enumerate(list(crypto) + list(stocks)):
        for tf in ("1h", "4h", "1d"):
            frames[(s, tf)] = sb.demo_prices(100 + i * 13, tf, n=600, start=20 + i * 17)
    return crypto, stocks, frames


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    t0 = time.time()
    cfg = sb.load_config(HERE / "config.json")
    dash, token = os.environ.get("DASH_URL", "").rstrip("/"), os.environ.get("INGEST_TOKEN", "")
    if dash in ("https:", "http:", "https://", "http://"):      # DASH_DOMAIN variable not set yet
        dash = ""
        print("DASH_DOMAIN is not set: results are only written to data/latest.json")
    hdr = {"Authorization": f"Bearer {token}", **UA}

    # assets with open positions are always included, whatever their signal
    watch = []
    if dash:
        try:
            watch = requests.get(f"{dash}/api/watch", headers=hdr, timeout=20).json()
        except Exception as e:
            print(f"Could not read open positions from dashboard: {e}")
    paper_state = None
    if dash:
        try:
            paper_state = requests.get(f"{dash}/api/paper", headers=hdr, timeout=20).json()
        except Exception as e:
            print(f"Could not read the paper test from dashboard: {e}")
    elif (ROOT / "data" / "paper.json").exists():
        paper_state = json.loads((ROOT / "data" / "paper.json").read_text(encoding="utf-8"))
    watch_keys = {(w["symbol"], w["tf"]) for w in watch}
    watch_syms = {w["symbol"] for w in watch}

    scored: list[tuple[str, str, str, dict]] = []      # (symbol, name, kind, frame)
    stats = {"crypto": 0, "stocks": 0, "source": "demo" if args.demo else ""}
    fng = None

    if args.demo:
        crypto, stocks, frames = demo_frames()
        for (s, tf), df in frames.items():
            kind = "crypto" if s in crypto else "stock"
            fr = score_frame(df, tf, cfg)
            if fr:
                scored.append((s, crypto.get(s) or stocks[s], kind, fr))
        stats["crypto"], stats["stocks"] = len(crypto), len(stocks)
    else:
        # ---- crypto: every liquid USDT pair, all timeframes
        source, uni = crypto_universe(cfg)
        stats["source"], stats["crypto"] = source, len(uni)
        print(f"Crypto: {len(uni)} pairs from {source}")
        jobs = [(r, tf) for r in uni for tf in cfg["timeframes"]]
        with ThreadPoolExecutor(8 if source == "binance" else 3) as ex:
            dfs = list(ex.map(lambda j: crypto_frame(source, j[0], j[1]), jobs))
        for (r, tf), df in zip(jobs, dfs):
            fr = score_frame(df, tf, cfg)
            if fr:
                scored.append((r["symbol"], COIN_NAMES.get(r["base"], r["base"]), "crypto", fr))
        fng = sb.fear_greed()

        # ---- stocks: daily bars for the whole list, intraday for the strongest only
        names = load_stock_universe()
        print(f"Stocks: {len(names)} tickers")
        daily = yahoo_batch(list(names), "2y", "1d")
        stats["stocks"] = len(daily)
        day_frames = {}
        for t, df in daily.items():
            fr = score_frame(sb.drop_unfinished(df, "1d"), "1d", cfg)
            if fr:
                day_frames[t] = fr
                scored.append((t, names[t], "stock", fr))
        short = sorted(day_frames, key=lambda t: -abs(day_frames[t]["tech"]))[: cfg["universe"]["stock_intraday_shortlist"]]
        short = list(dict.fromkeys(short + [s for s in watch_syms if s in names]))
        hourly = yahoo_batch(short, "1y", "1h")
        for t, df in hourly.items():
            for tf, frame_df in (("1h", df), ("4h", to_4h(df))):
                if tf in cfg["timeframes"]:
                    fr = score_frame(sb.drop_unfinished(frame_df, tf), tf, cfg)
                    if fr:
                        scored.append((t, names[t], "stock", fr))

    # ---- news for the strongest candidates of each kind
    news: dict[str, dict] = {}
    if cfg["news"]["enabled"] and not args.demo:
        for kind in ("crypto", "stock"):
            best: dict[str, tuple[float, str]] = {}
            for s, n, k, fr in scored:
                if k == kind and abs(fr["tech"]) > best.get(s, (0, ""))[0]:
                    best[s] = (abs(fr["tech"]), n)
            cands = sorted(best, key=lambda s: -best[s][0])[: cfg["output"]["news_candidates_per_kind"]]
            with ThreadPoolExecutor(6) as ex:
                for s, res in zip(cands, ex.map(lambda s: fetch_news(s, best[s][1], kind, cfg, fng), cands)):
                    news[s] = res

    # ---- build the output: actionable signals, ranked, plus watched positions
    items, watched, labels, everything = [], {}, {}, []
    verdicts = load_verdicts()
    for s, n, k, fr in scored:
        it = build_item(s, n, k, fr, news.get(s), cfg)
        it["bt"] = verdict_for(it, verdicts) if it["label"] != "HOLD" else None
        everything.append(it)
        labels[(s, fr["tf"])] = it["label"]
        if args.demo:
            _demo_last[s] = it["price"]
        if (s, fr["tf"]) in watch_keys:
            watched[f"{s}|{fr['tf']}"] = {"label": it["label"], "score": it["score"], "atr": it["atr"],
                                          "price": it["price"], "time": it["time"]}
        if it["label"] != "HOLD":
            items.append(it)

    # ---- paper trading test
    try:
        pstate = paper.run(paper_state, everything, labels, paper.settings(cfg),
                           demo_bars_since if args.demo else bars_since,
                           (lambda s, k: _demo_last.get(s)) if args.demo else live_price)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Paper test skipped this scan: {e}")
        pstate = None
    out_items = []
    for kind in ("crypto", "stock"):
        ki = sorted((i for i in items if i["kind"] == kind), key=lambda i: ((i["bt"] or {}).get("v") != "good", not i["proven"], -abs(i["score"])))
        out_items += ki[: cfg["output"]["max_signals_per_kind"]]

    payload = {"generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"), "demo": args.demo,
               "stats": {**stats, "frames": len(scored), "signals": len(items), "fear_greed": fng,
                         "seconds": round(time.time() - t0)},
               "risk": cfg["risk"], "account": cfg["account"], "thresholds": cfg["thresholds"],
               "items": out_items, "watch": watched}
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    if pstate is not None:
        payload["paper"] = pstate
        if not dash:
            (ROOT / "data" / "paper.json").write_text(json.dumps(pstate), encoding="utf-8")
    print(f"Scored {len(scored)} asset/timeframe pairs, {len(items)} signals, "
          f"{sum(i['proven'] for i in out_items)} with a good track record, in {payload['stats']['seconds']}s")

    if stats["crypto"] == 0 and stats["stocks"] == 0:
        sys.exit("No market data at all: both sources failed.")
    if dash:
        r = requests.post(f"{dash}/api/ingest", headers=hdr, data=json.dumps(payload), timeout=60)
        print(f"Dashboard update: HTTP {r.status_code} {r.text[:200]}")
        r.raise_for_status()


if __name__ == "__main__":
    main()
