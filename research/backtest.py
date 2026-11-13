#!/usr/bin/env python3
"""
Research backtest: which rules actually made money on history?

Runs in GitHub Actions (it needs Binance and Yahoo data). For every asset and
timeframe it simulates several ENTRY rules, each with several EXIT rules, long
and short, net of fees, and writes the per-group statistics to
research/results.csv.

Results are split by time into an in-sample part (first 60%) and an
out-of-sample part (last 40%). A rule only counts if it worked in BOTH, and
did clearly better than the "random" entry family, which shows what the exit
rules and the market's own drift give you with no skill at all.

Unit of result: R = the initial stop distance (2 x ATR). +1R means the trade
made as much as it risked.
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from numba import njit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scanner"))
import signal_bot as sb
import scan

OUT = Path(__file__).resolve().parent
WEIGHTS = sb.DEFAULT_CONFIG["weights"]
FEE = {"crypto": 0.001, "us": 0.0005, "eu": 0.0005}       # per side, includes a little slippage
MAX_HOLD = {"1h": 48, "4h": 42, "1d": 20}
EXITS = ["base", "trail", "partial", "quick"]
FAMILIES = ["score40", "score55", "score70", "breakout", "pullback", "random"]
IS_SHARE = 0.6


# --------------------------------------------------------------------------
# Trade simulation
# --------------------------------------------------------------------------
@njit(cache=True)
def simulate(o, h, l, c, atr, score, sig, side, mode, max_hold, flip_thr):
    """One position at a time. A signal on bar i enters at the open of bar i+1.

    mode 0 base:    stop 2 ATR, trails 2 ATR behind the best price, target +3 ATR
    mode 1 trail:   stop 2 ATR, trails 3 ATR, no target, double time limit
    mode 2 partial: half off at +1R, stop to break-even, rest trails 3 ATR, double time limit
    mode 3 quick:   stop 2 ATR, target +1R (2 ATR), no trailing
    All modes also exit when the composite score flips past flip_thr, or at the time limit.
    Inside a bar the stop is assumed to be hit before the target (conservative).
    """
    n = len(c)
    e_idx = np.empty(n, np.int64)
    x_idx = np.empty(n, np.int64)
    r_out = np.empty(n, np.float64)
    sf_out = np.empty(n, np.float64)
    k = 0
    free = 0
    hold = max_hold if (mode == 0 or mode == 3) else 2 * max_hold
    for i in range(n - 1):
        if not sig[i] or i < free:
            continue
        a = atr[i]
        e = i + 1
        entry = o[e]
        if not (a > 0.0) or not (entry > 0.0):
            continue
        S = 2.0 * a
        stop = entry - side * S
        best = entry
        size = 1.0
        realized = 0.0
        partial_done = False
        has_target = mode == 0 or mode == 3
        target = entry + side * (1.5 * S if mode == 0 else S)
        trail = 2.0 * a if mode == 0 else (3.0 * a if mode != 3 else 0.0)
        exitp = c[n - 1]
        j = e
        last = min(n - 1, e + hold)
        while j <= last:
            oj = o[j]; hj = h[j]; lj = l[j]; cj = c[j]
            worst = lj if side == 1 else hj
            bestp = hj if side == 1 else lj
            if (oj - stop) * side <= 0.0:
                exitp = oj
                break
            if (worst - stop) * side <= 0.0:
                exitp = stop
                break
            if mode == 2 and not partial_done and (bestp - (entry + side * S)) * side >= 0.0:
                realized += 0.5
                size = 0.5
                partial_done = True
                if (entry - stop) * side > 0.0:
                    stop = entry
            if has_target and (bestp - target) * side >= 0.0:
                exitp = target
                break
            if (bestp - best) * side > 0.0:
                best = bestp
                if trail > 0.0:
                    ns = best - side * trail
                    if (ns - stop) * side > 0.0:
                        stop = ns
            if score[j] * side <= -flip_thr or j == last:
                exitp = cj
                break
            j += 1
        if j > last:
            j = last
        e_idx[k] = e
        x_idx[k] = j
        r_out[k] = realized + size * side * (exitp - entry) / S
        sf_out[k] = S / entry
        k += 1
        free = j
    return e_idx[:k], x_idx[:k], r_out[:k], sf_out[:k]


def entry_signals(d: pd.DataFrame, score: pd.Series, rng) -> dict:
    """family -> (long signal array, short signal array)"""
    prev = score.shift(1)
    c, hi, lo = d["close"], d["high"], d["low"]
    up = (d["ema50"] > d["ema200"])
    dn = (d["ema50"] < d["ema200"])
    out = {}
    for thr in (40, 55, 70):
        out[f"score{thr}"] = ((score >= thr) & (prev < thr), (score <= -thr) & (prev > -thr))
    hh, ll = hi.rolling(20).max().shift(1), lo.rolling(20).min().shift(1)
    trend_ok = d["adx"] > 20
    out["breakout"] = ((c > hh) & up & trend_ok, (c < ll) & dn & trend_ok)
    rsi, rprev = d["rsi"], d["rsi"].shift(1)
    out["pullback"] = ((rsi < 35) & (rprev >= 35) & up & (c > d["ema200"]),
                       (rsi > 65) & (rprev <= 65) & dn & (c < d["ema200"]))
    rnd = rng.random(len(d)) < 1 / 30
    rnd &= d["ema200"].notna().values
    out["random"] = (pd.Series(rnd, index=d.index), pd.Series(rnd, index=d.index))
    return {k: (a.fillna(False).values.astype(np.bool_), b.fillna(False).values.astype(np.bool_))
            for k, (a, b) in out.items()}


def run_asset(df: pd.DataFrame, tf: str, market: str, regime: pd.Series | None, rng, sink: dict) -> None:
    if df is None or len(df) < 300:
        return
    d = sb.add_indicators(df)
    score = sb.tech_score(sb.component_scores(d), WEIGHTS)
    sigs = entry_signals(d, score, rng)
    o, h, l, c = (d[x].values.astype(np.float64) for x in ("open", "high", "low", "close"))
    atr = d["atr"].values.astype(np.float64)
    sc = np.nan_to_num(score.values.astype(np.float64))
    t = d.index.values.astype("datetime64[s]").astype(np.int64)
    if regime is not None:      # yesterday's market trend, known before the bar opens
        reg = regime.reindex(d.index.normalize(), method="ffill").values.astype(np.float64)
    else:
        reg = np.full(len(d), np.nan)
    fee = FEE[market]
    for fam, (sl, ss) in sigs.items():
        for side, sig, sname in ((1, sl, "long"), (-1, ss, "short")):
            for mode, ex in enumerate(EXITS):
                ei, xi, r, sf = simulate(o, h, l, c, atr, sc, sig, side, mode, MAX_HOLD[tf], 30.0)
                if not len(ei):
                    continue
                r_net = r - 2 * fee / sf
                # "proven": at least 5 earlier trades of this exact kind on this asset, average above zero
                prior_n = np.arange(len(r_net))
                prior_mean = np.r_[0.0, np.cumsum(r_net)[:-1]] / np.maximum(prior_n, 1)
                proven = (prior_n >= 5) & (prior_mean > 0)
                with_regime = reg[ei] == side
                sink.setdefault((market, tf, fam, sname, ex), []).append(
                    np.column_stack([t[ei], r_net, sf, proven, with_regime, t[xi] - t[ei]]))


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
def klines_history(symbol: str, tf: str, bars: int) -> pd.DataFrame | None:
    rows, end = [], None
    for _ in range((bars + 999) // 1000):
        for attempt in range(4):
            try:
                params = {"symbol": symbol, "interval": tf, "limit": 1000}
                if end:
                    params["endTime"] = end
                part = scan.get_json("https://data-api.binance.vision/api/v3/klines", params)
                break
            except Exception as e:
                time.sleep(2 + 3 * attempt)
                part = None
        if not part:
            break
        rows = part + rows
        end = part[0][0] - 1
        if len(part) < 1000:
            break
    if not rows:
        return None
    df = pd.DataFrame([r[:6] for r in rows], columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df = df.drop_duplicates("time").set_index("time").astype(float)
    return df.iloc[:-1]


def trend_regime(daily: pd.DataFrame) -> pd.Series:
    """+1 when the market closed above its 200-day average, else -1. Shifted one day (no look-ahead)."""
    close = daily["close"]
    reg = np.sign(close - sb.ema(close, 200)).shift(1)
    reg.index = reg.index.normalize()
    return reg


def stats(a: np.ndarray) -> dict:
    n = len(a)
    if n == 0:
        return {"n": 0, "win": np.nan, "avg_r": np.nan, "pf": np.nan, "t": np.nan, "sum_r": 0.0, "avg_pct": np.nan,
                "hold_h": np.nan}
    r = a[:, 1]
    gains, losses = r[r > 0].sum(), -r[r <= 0].sum()
    return {"n": n, "win": round((r > 0).mean() * 100, 1), "avg_r": round(r.mean(), 4),
            "pf": round(gains / losses, 3) if losses > 0 else np.nan,
            "t": round(r.mean() / (r.std(ddof=1) / np.sqrt(n)), 2) if n > 2 and r.std() > 0 else np.nan,
            "sum_r": round(r.sum(), 1), "avg_pct": round((r * a[:, 2]).mean() * 100, 3),
            "hold_h": round(a[:, 5].mean() / 3600, 1)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="synthetic data, for testing the code only")
    ap.add_argument("--crypto", type=int, default=80)
    ap.add_argument("--stocks_1h", type=int, default=100)
    args = ap.parse_args()
    rng = np.random.default_rng(7)
    sink: dict = {}
    t0 = time.time()

    if args.demo:
        for i in range(12):
            for tf in ("1h", "4h", "1d"):
                df = sb.demo_prices(100 + i, tf, n=3000, start=50 + i)
                run_asset(df, tf, "crypto" if i < 6 else "us", None, rng, sink)
    else:
        cfg = sb.load_config(ROOT / "scanner" / "config.json")
        # ---- crypto
        _, uni = scan.crypto_universe(cfg)
        uni = uni[: args.crypto]
        btc = klines_history("BTCUSDT", "1d", 2000)
        creg = trend_regime(btc)
        plan = {"1h": 8760, "4h": 4380, "1d": 1500}       # about 1 year, 2 years, 4 years
        for tf, bars in plan.items():
            with ThreadPoolExecutor(6) as ex:
                dfs = list(ex.map(lambda r: klines_history(r["symbol"], tf, bars), uni))
            for r, df in zip(uni, dfs):
                run_asset(df, tf, "crypto", creg, rng, sink)
            print(f"crypto {tf}: {sum(d is not None for d in dfs)} pairs, {time.time() - t0:.0f}s", flush=True)
        # ---- stocks
        names = scan.load_stock_universe()
        spy = scan.yahoo_batch(["SPY"], "10y", "1d").get("SPY")
        sreg = trend_regime(spy) if spy is not None else None
        market_of = lambda t: "eu" if "." in t else "us"
        daily = scan.yahoo_batch(list(names), "5y", "1d")
        for t, df in daily.items():
            run_asset(df.iloc[:-1], "1d", market_of(t), sreg, rng, sink)
        print(f"stocks 1d: {len(daily)} tickers, {time.time() - t0:.0f}s", flush=True)
        liquid = [t for t in names if "." not in t][: args.stocks_1h]
        hourly = scan.yahoo_batch(liquid, "730d", "1h", chunk=50)
        for t, df in hourly.items():
            run_asset(df.iloc[:-1], "1h", "us", sreg, rng, sink)
            run_asset(scan.to_4h(df).iloc[:-1], "4h", "us", sreg, rng, sink)
        print(f"stocks 1h/4h: {len(hourly)} tickers, {time.time() - t0:.0f}s", flush=True)

    # ---- statistics per group, in-sample vs out-of-sample
    span: dict = {}
    for (market, tf, *_), parts in sink.items():
        tt = np.concatenate([p[:, 0] for p in parts])
        lo, hi = span.get((market, tf), (np.inf, -np.inf))
        span[(market, tf)] = (min(lo, tt.min()), max(hi, tt.max()))
    rows = []
    for (market, tf, fam, side, ex), parts in sink.items():
        a = np.concatenate(parts)
        lo, hi = span[(market, tf)]
        cut = lo + IS_SHARE * (hi - lo)
        filters = {"none": np.ones(len(a), bool), "regime": a[:, 4] == 1, "proven": a[:, 3] == 1,
                   "regime+proven": (a[:, 4] == 1) & (a[:, 3] == 1)}
        for fname, mask in filters.items():
            ins, oos = a[mask & (a[:, 0] < cut)], a[mask & (a[:, 0] >= cut)]
            row = {"market": market, "tf": tf, "family": fam, "side": side, "exit": ex, "filter": fname,
                   "split": pd.to_datetime(cut, unit="s").strftime("%Y-%m-%d")}
            row.update({f"is_{k}": v for k, v in stats(ins).items()})
            row.update({f"oos_{k}": v for k, v in stats(oos).items()})
            rows.append(row)
    res = pd.DataFrame(rows).sort_values(["market", "tf", "family", "side", "exit", "filter"])
    name = "results_demo.csv" if args.demo else "results.csv"
    res.to_csv(OUT / name, index=False)
    total = sum(len(p) for parts in sink.values() for p in parts)
    print(f"{total:,} simulated trades, {len(res)} groups, {time.time() - t0:.0f}s -> {OUT / name}")


if __name__ == "__main__":
    main()
