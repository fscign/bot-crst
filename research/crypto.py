#!/usr/bin/env python3
"""
Crypto research, round 2: is there ANY simple rule that made money in crypto?

Part 1, trade rules (per coin, per timeframe): breakouts, moving-average trend,
buying sharp dips, volatility breakouts, short rules, each with several exits,
with and without a "Bitcoin is in an uptrend" filter, at three fee levels.

Part 2, portfolio rules (daily, across all coins): hold only coins in an
uptrend, hold the recent winners, hold the recent losers, hold the basket only
when Bitcoin is in an uptrend. Compared with simply holding Bitcoin and with
holding all coins equally.

History is cut into three periods by time: A (oldest 50%), B (next 25%) and
C (latest 25%). Rules are chosen on A and B. C is a holdout: it is only looked
at to judge the rules already chosen.

Writes research/crypto_trades.csv and research/crypto_portfolio.csv.
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
import signal_bot as sb
import scan
from backtest import klines_history

OUT = Path(__file__).resolve().parent
FEES = [0.10, 0.05, 0.02]                 # % per side: spot taker, futures taker, maker
BARS = {"1h": 26280, "4h": 8760, "1d": 2200}      # about 3, 4 and 6 years
HOLD = {"1h": 48, "4h": 42, "1d": 20}
CUTS = (0.50, 0.75)


@njit(cache=True)
def sim(o, h, l, c, atr, entry, exit_sig, side, stop_m, trail_m, target_r, max_hold):
    """One position at a time; a signal on bar i enters at the open of bar i+1.
    stop_m: stop distance in ATR. trail_m: trailing distance in ATR (0 = fixed stop).
    target_r: profit target in R (0 = none). exit_sig: close the trade at that bar's close."""
    n = len(c)
    e_idx = np.empty(n, np.int64)
    r_out = np.empty(n, np.float64)
    sf_out = np.empty(n, np.float64)
    k = 0
    free = 0
    for i in range(n - 1):
        if not entry[i] or i < free:
            continue
        a = atr[i]
        e = i + 1
        ep = o[e]
        if not (a > 0.0) or not (ep > 0.0):
            continue
        S = stop_m * a
        stop = ep - side * S
        best = ep
        tp = ep + side * target_r * S
        exitp = c[n - 1]
        last = min(n - 1, e + max_hold)
        j = e
        while j <= last:
            worst = l[j] if side == 1 else h[j]
            bestp = h[j] if side == 1 else l[j]
            if (o[j] - stop) * side <= 0.0:
                exitp = o[j]
                break
            if (worst - stop) * side <= 0.0:
                exitp = stop
                break
            if target_r > 0.0 and (bestp - tp) * side >= 0.0:
                exitp = tp
                break
            if (bestp - best) * side > 0.0:
                best = bestp
                if trail_m > 0.0:
                    ns = best - side * trail_m * a
                    if (ns - stop) * side > 0.0:
                        stop = ns
            if exit_sig[j] or j == last:
                exitp = c[j]
                break
            j += 1
        if j > last:
            j = last
        e_idx[k] = e
        r_out[k] = side * (exitp - ep) / S
        sf_out[k] = S / ep
        k += 1
        free = j
    return e_idx[:k], r_out[:k], sf_out[:k]


def families(d: pd.DataFrame, rng, tf: str) -> list:
    """(family, side, entry, exit_signal, [(exit name, stop, trail, target, max_hold)])"""
    c, h, l, o = d["close"], d["high"], d["low"], d["open"]
    ema = {n: sb.ema(c, n) for n in (20, 50, 100, 200)}
    sma5, sma20 = c.rolling(5).mean(), c.rolling(20).mean()
    sd20 = c.rolling(20).std(ddof=0)
    delta = c.diff()
    g2 = delta.clip(lower=0).ewm(alpha=0.5, adjust=False, min_periods=2).mean()
    l2 = (-delta.clip(upper=0)).ewm(alpha=0.5, adjust=False, min_periods=2).mean()
    rsi2 = 100 - 100 / (1 + g2 / l2.replace(0, np.nan))
    up, dn = c > ema[200], c < ema[200]
    never = pd.Series(False, index=d.index)
    H = HOLD[tf]
    trend_exits = [("trail3", 2.0, 3.0, 0.0, 2 * H), ("wide5", 3.0, 5.0, 0.0, 6 * H)]
    dip_exits = [("signal", 3.0, 0.0, 0.0, 15), ("signal_wide", 5.0, 0.0, 0.0, 30)]
    rnd = pd.Series(rng.random(len(d)) < 1 / 40, index=d.index) & ema[200].notna()
    rng_bar = h - l
    out = []
    for n in (20, 55):
        out.append((f"breakout{n}", 1, (c > h.rolling(n).max().shift(1)) & up, never, trend_exits))
    for n in (50, 200):
        cross_up = (c > ema[n]) & (c.shift(1) <= ema[n].shift(1))
        out.append((f"ema{n}_trend", 1, cross_up, c < ema[n], [("signal", 3.0, 0.0, 0.0, 100000)]))
    out.append(("rsi2_dip", 1, (rsi2 < 10) & up, c > sma5, dip_exits))
    out.append(("band_dip", 1, (c < sma20 - 2.5 * sd20) & up, c > sma20, dip_exits))
    out.append(("vol_breakout", 1, (rng_bar > 2 * d["atr"].shift(1)) & (c > l + 0.75 * rng_bar) & (c > ema[50]),
                never, trend_exits))
    out.append(("breakdown20_short", -1, (c < l.rolling(20).min().shift(1)) & dn, never, trend_exits))
    out.append(("rsi2_rally_short", -1, (rsi2 > 90) & dn, c < sma5, dip_exits))
    out.append(("random_long", 1, rnd, never, trend_exits + dip_exits[:1]))
    out.append(("random_short", -1, rnd, never, trend_exits + dip_exits[:1]))
    return out


def run_asset(df, tf, btc_up, rng, sink, allow=None):
    """allow: optional daily True/False series; entries are only taken on days when it is True."""
    if df is None or len(df) < 400:
        return
    d = sb.add_indicators(df)
    o, h, l, c = (d[x].values.astype(np.float64) for x in ("open", "high", "low", "close"))
    atr = d["atr"].values.astype(np.float64)
    t = d.index.values.astype("datetime64[s]").astype(np.int64)
    gate = btc_up.reindex(d.index.normalize(), method="ffill").fillna(False).values.astype(np.bool_)
    for fam, side, entry, exit_sig, exits in families(d, rng, tf):
        ent = entry.fillna(False).values.astype(np.bool_)
        if allow is not None:
            ent &= allow.reindex(d.index.normalize()).fillna(False).values.astype(np.bool_)
        xs = exit_sig.fillna(False).values.astype(np.bool_)
        for name, stop_m, trail_m, target_r, max_hold in exits:
            ei, r, sf = sim(o, h, l, c, atr, ent, xs, side, stop_m, trail_m, target_r, max_hold)
            if len(ei):
                sink.setdefault((tf, fam, name), []).append(np.column_stack([t[ei], r, sf, gate[ei]]))


def seg_stats(r: np.ndarray) -> dict:
    n = len(r)
    if n < 3:
        return {"n": n, "avg": np.nan, "t": np.nan, "win": np.nan}
    return {"n": n, "avg": round(r.mean(), 4), "win": round((r > 0).mean() * 100, 1),
            "t": round(r.mean() / (r.std(ddof=1) / np.sqrt(n)), 2) if r.std() > 0 else np.nan}


# --------------------------------------------------------------------------
# Portfolio rules (daily)
# --------------------------------------------------------------------------
def port_metrics(ret: pd.Series) -> dict:
    ret = ret.dropna()
    if len(ret) < 30:
        return {"cagr": np.nan, "sharpe": np.nan, "maxdd": np.nan}
    eq = (1 + ret).cumprod()
    years = len(ret) / 365
    return {"cagr": round((eq.iloc[-1] ** (1 / years) - 1) * 100, 1),
            "sharpe": round(ret.mean() / ret.std() * np.sqrt(365), 2) if ret.std() > 0 else np.nan,
            "maxdd": round((eq / eq.cummax() - 1).min() * 100, 1)}


def portfolio(close: pd.DataFrame, btc: pd.Series) -> pd.DataFrame:
    ret = close.pct_change()
    live = close.notna() & close.shift(200).notna()            # coin has at least 200 days of history
    n_live = live.sum(axis=1).replace(0, np.nan)
    ema = {n: close.apply(lambda s: sb.ema(s, n)) for n in (20, 50, 100, 200)}
    btc_ema = {n: sb.ema(btc, n) for n in (50, 100, 200)}
    strategies = {}
    w_btc = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    if "BTCUSDT" in w_btc:
        w_btc["BTCUSDT"] = 1.0
    strategies["hold_btc"] = w_btc
    basket = live.div(n_live, axis=0).fillna(0.0)
    strategies["hold_all_coins"] = basket
    for n in (20, 50, 100, 200):
        intrend = (close > ema[n]) & live
        strategies[f"trend{n}_part"] = intrend.div(n_live, axis=0).fillna(0.0)          # cash for coins out of trend
        strategies[f"trend{n}_full"] = intrend.div(intrend.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    for n in (50, 100, 200):
        on = (btc > btc_ema[n]).astype(float)
        strategies[f"btc_only_when_btc_above_ema{n}"] = w_btc.mul(on, axis=0)
        strategies[f"all_coins_when_btc_above_ema{n}"] = basket.mul(on, axis=0)
    weekly = pd.Series(close.index.dayofweek == 0, index=close.index)
    for look in (7, 14, 30, 90):
        past = close.pct_change(look).where(live)
        rank = past.rank(axis=1, pct=True)
        top = (rank >= 0.8)
        w = top.div(top.sum(axis=1).replace(0, np.nan), axis=0).where(weekly, np.nan).ffill().fillna(0.0)
        strategies[f"winners{look}d_weekly"] = w
        strategies[f"winners{look}d_weekly_btc_above_ema100"] = w.mul((btc > btc_ema[100]).astype(float), axis=0)
        if look <= 14:
            bot = (rank <= 0.2)
            wl = bot.div(bot.sum(axis=1).replace(0, np.nan), axis=0).where(weekly, np.nan).ffill().fillna(0.0)
            strategies[f"losers{look}d_weekly"] = wl
        if look >= 14:
            bot = (rank <= 0.2)
            ws = bot.div(bot.sum(axis=1).replace(0, np.nan), axis=0).where(weekly, np.nan).ffill().fillna(0.0)
            strategies[f"winners_minus_losers{look}d"] = 0.5 * w - 0.5 * ws
    for look in (1, 3):
        past = close.pct_change(look).where(live)
        bot = past.rank(axis=1, pct=True) <= 0.2
        strategies[f"losers{look}d_daily"] = bot.div(bot.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)

    idx = close.index[200:]
    cut1 = idx[int(len(idx) * CUTS[0])]
    cut2 = idx[int(len(idx) * CUTS[1])]
    rows = []
    for name, w in strategies.items():
        held = w.shift(1).fillna(0.0)                           # decided at yesterday's close
        gross = (held * ret).sum(axis=1)
        turnover = (w - w.shift(1)).abs().sum(axis=1).shift(1).fillna(0.0)
        for fee in (0.10, 0.05):
            net = (gross - turnover * fee / 100).loc[idx]
            row = {"strategy": name, "fee": fee, "avg_exposure": round(held.loc[idx].abs().sum(axis=1).mean(), 2),
                   "turnover_per_year": round(turnover.loc[idx].mean() * 365, 1)}
            for seg, part in (("A", net[:cut1]), ("B", net[cut1:cut2]), ("C", net[cut2:]), ("all", net)):
                row.update({f"{seg}_{k}": v for k, v in port_metrics(part).items()})
            rows.append(row)
    res = pd.DataFrame(rows)
    res.attrs["cuts"] = (str(idx[0].date()), str(cut1.date()), str(cut2.date()), str(idx[-1].date()))
    return res


def trade_table(sink: dict) -> pd.DataFrame:
    """Statistics per rule, period (A, B, C), filter and fee level."""
    span = {}
    for (tf, _, _), parts in sink.items():
        tt = np.concatenate([p[:, 0] for p in parts])
        lo, hi = span.get(tf, (np.inf, -np.inf))
        span[tf] = (min(lo, tt.min()), max(hi, tt.max()))
    rows = []
    for (tf, fam, ex), parts in sink.items():
        a = np.concatenate(parts)
        lo, hi = span[tf]
        c1, c2 = lo + CUTS[0] * (hi - lo), lo + CUTS[1] * (hi - lo)
        for fname, mask in (("none", np.ones(len(a), bool)), ("btc_uptrend", a[:, 3] == 1), ("btc_downtrend", a[:, 3] == 0)):
            for fee in FEES:
                net = a[:, 1] - 2 * fee / 100 / a[:, 2]
                row = {"tf": tf, "family": fam, "exit": ex, "filter": fname, "fee": fee,
                       "cutA": pd.to_datetime(c1, unit="s").strftime("%Y-%m-%d"),
                       "cutB": pd.to_datetime(c2, unit="s").strftime("%Y-%m-%d"),
                       "avg_stop_pct": round(a[mask, 2].mean() * 100, 2) if mask.any() else np.nan}
                for seg, m in (("A", a[:, 0] < c1), ("B", (a[:, 0] >= c1) & (a[:, 0] < c2)), ("C", a[:, 0] >= c2)):
                    row.update({f"{seg}_{k}": v for k, v in seg_stats(net[mask & m]).items()})
                rows.append(row)
    return pd.DataFrame(rows).sort_values(["tf", "family", "exit", "filter", "fee"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--coins", type=int, default=80)
    args = ap.parse_args()
    rng = np.random.default_rng(11)
    t0 = time.time()
    sink: dict = {}
    daily: dict = {}

    if args.demo:
        syms = [f"C{i}USDT" for i in range(9)] + ["BTCUSDT"]
        for i, s in enumerate(syms):
            daily[s] = sb.demo_prices(300 + i, "1d", n=1500, start=30 + i)
        btc_d = daily["BTCUSDT"]["close"]
        btc_up = (btc_d > sb.ema(btc_d, 200)).shift(1).fillna(False)
        btc_up.index = btc_up.index.normalize()
        for i, s in enumerate(syms):
            run_asset(daily[s], "1d", btc_up, rng, sink)
            run_asset(sb.demo_prices(400 + i, "4h", n=3000, start=30 + i), "4h", btc_up, rng, sink)
    else:
        cfg = sb.load_config(ROOT / "scanner" / "config.json")
        _, uni = scan.crypto_universe(cfg)
        uni = uni[: args.coins]
        syms = [r["symbol"] for r in uni]
        if "BTCUSDT" not in syms:
            syms.insert(0, "BTCUSDT")
        with ThreadPoolExecutor(6) as ex:
            for s, df in zip(syms, ex.map(lambda s: klines_history(s, "1d", BARS["1d"]), syms)):
                if df is not None:
                    daily[s] = df
        btc_d = daily["BTCUSDT"]["close"]
        btc_up = (btc_d > sb.ema(btc_d, 200)).shift(1).fillna(False)
        btc_up.index = btc_up.index.normalize()
        for s, df in daily.items():
            run_asset(df, "1d", btc_up, rng, sink)
        print(f"1d: {len(daily)} coins, {time.time() - t0:.0f}s", flush=True)
        for tf in ("4h", "1h"):
            with ThreadPoolExecutor(6) as ex:
                dfs = list(ex.map(lambda s: klines_history(s, tf, BARS[tf]), syms))
            for df in dfs:
                run_asset(df, tf, btc_up, rng, sink)
            print(f"{tf}: {sum(d is not None for d in dfs)} coins, {time.time() - t0:.0f}s", flush=True)

    rows = trade_table(sink)
    tag = "_demo" if args.demo else ""
    rows.to_csv(OUT / f"crypto_trades{tag}.csv", index=False)

    # ---- portfolio rules
    close = pd.DataFrame({s: df["close"] for s, df in daily.items()}).sort_index()
    close.index = close.index.normalize()
    port = portfolio(close, close["BTCUSDT"])
    port.insert(0, "periods", " | ".join(port.attrs["cuts"]))
    port.to_csv(OUT / f"crypto_portfolio{tag}.csv", index=False)

    # ---- robustness: daily rules on a point-in-time universe
    # The main test uses the 80 coins that are most traded TODAY, which favours coins that did well recently.
    # Here every USDT pair is loaded and a coin may only be entered on days when it was among the 80 most
    # traded over the PREVIOUS 30 days.
    if not args.demo:
        wide = dict(cfg)
        wide["universe"] = {**cfg["universe"], "crypto_min_volume_usd": 0, "crypto_max": 2000}
        _, every = scan.crypto_universe(wide)
        all_syms = [r["symbol"] for r in every]
        with ThreadPoolExecutor(6) as ex:
            got = dict(zip(all_syms, ex.map(lambda s: klines_history(s, "1d", BARS["1d"]), all_syms)))
        got = {s: df for s, df in got.items() if df is not None and len(df) > 60}
        dv = pd.DataFrame({s: (df["close"] * df["volume"]) for s, df in got.items()}).sort_index()
        dv.index = dv.index.normalize()
        allowed = dv.rolling(30).mean().shift(1).rank(axis=1, ascending=False) <= 80
        pit: dict = {}
        for s, df in got.items():
            run_asset(df, "1d", btc_up, rng, pit, allow=allowed[s])
        trade_table(pit).to_csv(OUT / "crypto_trades_pit.csv", index=False)
        print(f"point-in-time universe: {len(got)} coins loaded, {time.time() - t0:.0f}s")
    print(f"{sum(len(p) for ps in sink.values() for p in ps):,} trades, {len(rows)} trade groups, "
          f"{len(port)} portfolio rows, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
