#!/usr/bin/env python3
"""
Research round 3: does market context improve crypto trades?

Takes the daily crypto rules from round 2 on the bias-free coin universe (a
coin can only be traded while it was in the top 80 by volume over the previous
30 days) and splits their trades by what the world looked like the day before
entry: Fear & Greed, Bitcoin trend, S&P 500, VIX, dollar, yields, stablecoin
supply, futures funding (scanner/context.py).

Also tests each flag as a simple Bitcoin switch: hold Bitcoin only while the
flag is true (or false), otherwise cash.

Three periods by time: A (oldest 50%), B (next 25%), C (latest 25%, holdout).
Writes research/context_trades.csv and research/context_btc.csv.
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scanner"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import signal_bot as sb
import scan
import context
import crypto as cr
from backtest import klines_history

OUT = Path(__file__).resolve().parent
FEE = 0.10
KEEP = {"vol_breakout", "breakout20", "breakout55", "ema50_trend", "rsi2_dip", "random_long"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    rng = np.random.default_rng(5)
    t0 = time.time()

    if args.demo:
        got = {("BTCUSDT" if i == 0 else f"C{i}USDT"): sb.demo_prices(500 + i, "1d", n=1800, start=40 + i)
               for i in range(10)}
    else:
        cfg = sb.load_config(ROOT / "scanner" / "config.json")
        cfg["universe"] = {**cfg["universe"], "crypto_min_volume_usd": 0, "crypto_max": 2000}
        _, every = scan.crypto_universe(cfg)
        syms = [r["symbol"] for r in every]
        with ThreadPoolExecutor(6) as ex:
            got = dict(zip(syms, ex.map(lambda s: klines_history(s, "1d", cr.BARS["1d"]), syms)))
        got = {s: df for s, df in got.items() if df is not None and len(df) > 60}
    print(f"{len(got)} coins loaded, {time.time() - t0:.0f}s", flush=True)

    btc = got["BTCUSDT"]["close"].copy()
    btc.index = btc.index.normalize()
    if args.demo:
        flags = pd.DataFrame({k: (rng.random(len(btc)) < 0.5).astype(float) for k in list(context.LABELS)[:6]},
                             index=btc.index)
        failed = []
    else:
        flags, _, failed = context.build(btc)
    print("context sources that failed:", failed or "none", flush=True)
    known = flags.shift(1)                                    # what was known the day before entry

    dv = pd.DataFrame({s: df["close"] * df["volume"] for s, df in got.items()}).sort_index()
    dv.index = dv.index.normalize()
    allowed = dv.rolling(30).mean().shift(1).rank(axis=1, ascending=False) <= 80
    btc_up = (btc > sb.ema(btc, 200)).shift(1).fillna(False)
    sink: dict = {}
    for s, df in got.items():
        cr.run_asset(df, "1d", btc_up, rng, sink, allow=allowed[s])

    # ---- trades split by context
    all_t = np.concatenate([p[:, 0] for parts in sink.values() for p in parts])
    lo, hi = all_t.min(), all_t.max()
    c1, c2 = lo + cr.CUTS[0] * (hi - lo), lo + cr.CUTS[1] * (hi - lo)
    rows = []
    for (tf, fam, ex), parts in sink.items():
        if fam not in KEEP:
            continue
        a = np.concatenate(parts)
        net = a[:, 1] - 2 * FEE / 100 / a[:, 2]
        days = pd.to_datetime(a[:, 0], unit="s", utc=True).normalize()
        segs = (("A", a[:, 0] < c1), ("B", (a[:, 0] >= c1) & (a[:, 0] < c2)), ("C", a[:, 0] >= c2))

        def add(feature, state, mask):
            row = {"family": fam, "exit": ex, "feature": feature, "state": state}
            for seg, m in segs:
                row.update({f"{seg}_{k}": v for k, v in cr.seg_stats(net[mask & m]).items()})
            rows.append(row)

        add("all", "", np.ones(len(a), bool))
        for feat in known.columns:
            col = known[feat].reindex(days).values
            add(feat, "yes", col == 1)
            add(feat, "no", col == 0)
    trades = pd.DataFrame(rows)
    trades.insert(0, "periods", " | ".join(pd.to_datetime(x, unit="s").strftime("%Y-%m-%d") for x in (lo, c1, c2, hi)))
    tag = "_demo" if args.demo else ""
    trades.to_csv(OUT / f"context_trades{tag}.csv", index=False)

    # ---- each flag as a Bitcoin switch
    ret = btc.pct_change()
    idx = btc.index[200:]
    k1, k2 = idx[int(len(idx) * cr.CUTS[0])], idx[int(len(idx) * cr.CUTS[1])]
    rows = []

    def add_switch(name, w):
        w = w.fillna(0.0)
        net = (w * ret - w.diff().abs().fillna(0.0) * FEE / 100).loc[idx]
        row = {"rule": name, "share_of_time_in": round(float(w.loc[idx].mean()), 2),
               "switches_per_year": round(float(w.diff().abs().loc[idx].mean() * 365), 1)}
        for seg, part in (("A", net[:k1]), ("B", net[k1:k2]), ("C", net[k2:]), ("all", net)):
            row.update({f"{seg}_{k}": v for k, v in cr.port_metrics(part).items()})
        rows.append(row)

    add_switch("hold_btc", pd.Series(1.0, index=btc.index))
    for feat in known.columns:
        add_switch(f"btc_when_{feat}", (known[feat] == 1).astype(float))
        add_switch(f"btc_when_not_{feat}", (known[feat] == 0).astype(float))
    sw = pd.DataFrame(rows)
    sw.insert(0, "periods", f"{idx[0].date()} | {k1.date()} | {k2.date()} | {idx[-1].date()}")
    sw.to_csv(OUT / f"context_btc{tag}.csv", index=False)
    print(f"{len(trades)} trade rows, {len(sw)} switch rows, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
