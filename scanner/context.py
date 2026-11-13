"""
Market context: the things outside a coin's own chart that people say move crypto.

Used twice:
- research/context_research.py tests on history whether each of these would
  have improved crypto trades (so we only act on what held up);
- the scanner takes a snapshot each run for the dashboard and for the rules of
  the experimental crypto account.

Every flag is a plain yes/no question about yesterday's close, e.g. "is
Bitcoin above its 100-day average?". Sources are free and need no key:
Binance (Bitcoin, funding), alternative.me (Fear & Greed), Yahoo Finance
(S&P 500, VIX, dollar index, 10-year yield), DefiLlama (stablecoin supply),
ForexFactory's weekly feed (economic calendar).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

import signal_bot as sb

UTC = timezone.utc
UA = sb.UA

LABELS = {
    "btc_above_ema100": "Bitcoin above its 100-day average",
    "btc_above_ema200": "Bitcoin above its 200-day average",
    "btc_30d_up": "Bitcoin higher than 30 days ago",
    "fng_extreme_fear": "Fear & Greed: extreme fear (25 or less)",
    "fng_fear": "Fear & Greed below 45",
    "fng_greed": "Fear & Greed above 55",
    "fng_extreme_greed": "Fear & Greed: extreme greed (75 or more)",
    "fng_rising": "Fear & Greed higher than a week ago",
    "spx_above_200d": "S&P 500 above its 200-day average",
    "spx_20d_up": "S&P 500 higher than 20 days ago",
    "vix_below_20": "VIX (stock market fear) below 20",
    "vix_above_25": "VIX above 25",
    "vix_falling": "VIX below its 20-day average",
    "dollar_weakening": "US dollar index below its 50-day average",
    "yields_falling": "US 10-year yield below its 50-day average",
    "stables_growing": "Stablecoin supply growing over 30 days",
    "stables_growing_fast": "Stablecoin supply up more than 2% in 30 days",
    "funding_hot": "Bitcoin futures funding high (crowded longs)",
    "funding_negative": "Bitcoin futures funding negative (crowded shorts)",
}


def _get(url: str, params: dict | None = None, timeout: int = 30):
    r = requests.get(url, params=params, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _day(ts, unit: str) -> pd.Timestamp:
    return pd.to_datetime(int(ts), unit=unit, utc=True).normalize()


def fear_greed(limit: int = 0) -> pd.Series:
    data = _get("https://api.alternative.me/fng/", {"limit": limit, "format": "json"})["data"]
    return pd.Series({_day(x["timestamp"], "s"): float(x["value"]) for x in data}).sort_index()


def yahoo_close(ticker: str, period: str) -> pd.Series:
    import yfinance as yf
    h = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
    s = h["Close"].dropna()
    s.index = pd.to_datetime(s.index, utc=True).normalize()
    return s[~s.index.duplicated(keep="last")]


def stablecoin_supply() -> pd.Series:
    data = _get("https://stablecoins.llama.fi/stablecoincharts/all", timeout=60)
    s = pd.Series({_day(x["date"], "s"): float((x.get("totalCirculatingUSD") or {}).get("peggedUSD") or np.nan)
                   for x in data})
    return s.dropna().sort_index()


def btc_funding(start: pd.Timestamp) -> pd.Series:
    """Average daily funding rate of the BTCUSDT perpetual (per 8 hours)."""
    rows, t = [], int(start.timestamp() * 1000)
    for _ in range(15):
        part = _get("https://fapi.binance.com/fapi/v1/fundingRate", {"symbol": "BTCUSDT", "startTime": t, "limit": 1000})
        if not part:
            break
        rows += part
        if len(part) < 1000:
            break
        t = part[-1]["fundingTime"] + 1
    s = pd.Series({pd.to_datetime(r["fundingTime"], unit="ms", utc=True): float(r["fundingRate"]) for r in rows})
    return s.resample("1D").mean().dropna()


def _flag(cond: pd.Series, valid: pd.Series) -> pd.Series:
    """1.0 / 0.0, and NaN where the answer is not known yet."""
    return cond.astype(float).where(valid.notna())


def build(btc: pd.Series, period: str = "10y", fng_limit: int = 0, funding: bool = True):
    """btc: daily closes indexed by UTC day. Returns (flags, values, failed sources).
    flags[day] describes the world at that day's close (not shifted)."""
    idx = btc.index
    flags, values, failed = pd.DataFrame(index=idx), pd.DataFrame(index=idx), []

    def on_days(s: pd.Series) -> pd.Series:
        return s.reindex(idx, method="ffill")

    e100, e200 = sb.ema(btc, 100), sb.ema(btc, 200)
    flags["btc_above_ema100"] = _flag(btc > e100, e100)
    flags["btc_above_ema200"] = _flag(btc > e200, e200)
    flags["btc_30d_up"] = _flag(btc > btc.shift(30), btc.shift(30))

    try:
        f = fear_greed(fng_limit)
        flags["fng_extreme_fear"] = on_days(_flag(f <= 25, f))
        flags["fng_fear"] = on_days(_flag(f < 45, f))
        flags["fng_greed"] = on_days(_flag(f > 55, f))
        flags["fng_extreme_greed"] = on_days(_flag(f >= 75, f))
        flags["fng_rising"] = on_days(_flag(f > f.shift(7), f.shift(7)))
        values["fear_greed"] = on_days(f)
    except Exception as e:
        failed.append(f"fear & greed: {e}")

    for name, ticker, fn in (
        ("spx", "^GSPC", lambda s: {"spx_above_200d": _flag(s > s.rolling(200).mean(), s.rolling(200).mean()),
                                    "spx_20d_up": _flag(s > s.shift(20), s.shift(20))}),
        ("vix", "^VIX", lambda s: {"vix_below_20": _flag(s < 20, s), "vix_above_25": _flag(s > 25, s),
                                   "vix_falling": _flag(s < s.rolling(20).mean(), s.rolling(20).mean())}),
        ("dollar", "DX-Y.NYB", lambda s: {"dollar_weakening": _flag(s < s.rolling(50).mean(), s.rolling(50).mean())}),
        ("yield10y", "^TNX", lambda s: {"yields_falling": _flag(s < s.rolling(50).mean(), s.rolling(50).mean())}),
    ):
        try:
            s = yahoo_close(ticker, period)
            if len(s) < 60:
                raise RuntimeError("no data")
            for k, v in fn(s).items():
                flags[k] = on_days(v)
            values[name] = on_days(s)
        except Exception as e:
            failed.append(f"{name}: {e}")

    try:
        st = stablecoin_supply()
        g = st.pct_change(30)
        flags["stables_growing"] = on_days(_flag(g > 0, g))
        flags["stables_growing_fast"] = on_days(_flag(g > 0.02, g))
        values["stables_30d_pct"] = on_days(g * 100)
    except Exception as e:
        failed.append(f"stablecoins: {e}")

    if funding:
        try:
            fr = btc_funding(idx[0])
            m = fr.rolling(7).mean()
            flags["funding_hot"] = on_days(_flag(m > 0.0002, m))
            flags["funding_negative"] = on_days(_flag(m < 0, m))
            values["funding_7d_pct"] = on_days(m * 100)
        except Exception as e:
            failed.append(f"funding: {e}")
    return flags, values, failed


def economic_events(days: int = 7) -> list[dict]:
    """High-impact US and euro-area releases still to come this week."""
    data = _get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=20)
    now, out = datetime.now(UTC), []
    for e in data:
        if e.get("impact") != "High" or e.get("country") not in ("USD", "EUR"):
            continue
        t = pd.to_datetime(e["date"], utc=True).to_pydatetime()
        if now - timedelta(hours=2) <= t <= now + timedelta(days=days):
            out.append({"time": t.strftime("%Y-%m-%d %H:%M"), "country": e["country"], "title": e.get("title", ""),
                        "forecast": e.get("forecast", ""), "previous": e.get("previous", "")})
    return sorted(out, key=lambda x: x["time"])


def snapshot() -> dict:
    """Today's context for the scanner: flags, a few numbers, upcoming events."""
    df = sb.fetch_crypto("BTCUSDT", "1d", limit=600)
    btc = df["close"].copy()
    btc.index = btc.index.normalize()
    flags, values, failed = build(btc, period="2y", fng_limit=400)
    last_f, last_v = flags.iloc[-1], values.iloc[-1]
    out = {"day": btc.index[-1].strftime("%Y-%m-%d"),
           "flags": {k: (None if pd.isna(x) else bool(x)) for k, x in last_f.items()},
           "values": {k: (None if pd.isna(x) else round(float(x), 3)) for k, x in last_v.items()},
           "labels": LABELS, "failed": failed, "events": []}
    try:
        out["events"] = economic_events()
    except Exception as e:
        out["failed"].append(f"calendar: {e}")
    return out
