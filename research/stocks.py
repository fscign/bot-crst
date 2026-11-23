#!/usr/bin/env python3
"""
Research round 4: can the stock rule be improved?

Daily bars, 10 years, US and European stocks. Compares the rule the bot trades now
(score crosses +40, stop 2 ATR, trail 3 ATR, 40-bar limit, exit on a SELL flip) with
longer holds, wider stops and other entries (breakouts, volatility breakouts,
moving-average trend, dip buying), with and without an S&P 500 uptrend filter.

Three periods by time: A (oldest 50%), B (next 25%), C (latest 25%, holdout).
Writes research/stock_trades.csv. Fee 0.05% per side.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scanner"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import signal_bot as sb
import scan
import crypto as cr

OUT = Path(__file__).resolve().parent
WEIGHTS = sb.DEFAULT_CONFIG["weights"]


def run_stock(df, market, gate, rng, sink):
    if df is None or len(df) < 400:
        return
    d = sb.add_indicators(df)
    score = sb.tech_score(sb.component_scores(d), WEIGHTS)
    cross = (score >= 40) & (score.shift(1) < 40)
    never = pd.Series(False, index=d.index)
    fams = [f for f in cr.families(d, rng, "1d") if f[1] == 1]
    fams.append(("score40_live", 1, cross, score <= -30, [("trail3_40bars", 2.0, 3.0, 0.0, 40)]))
    # 200-day trend variants: simple average (same value whatever history is loaded), and versions that need
    # 2 closes in a row on the other side before acting (fewer whipsaws)
    c = d["close"]
    sma = c.rolling(200).mean()
    above, below = c > sma, c < sma
    big = [("signal", 3.0, 0.0, 0.0, 100000)]
    fams.append(("sma200_trend", 1, above & ~above.shift(1, fill_value=False), below, big))
    fams.append(("sma200_confirm2", 1, above & above.shift(1, fill_value=False) & ~above.shift(2, fill_value=False),
                 below & below.shift(1, fill_value=False), big))
    fams.append(("sma200_buffer1pct", 1, (c > sma * 1.01) & ~(c.shift(1) > sma.shift(1) * 1.01), c < sma * 0.99, big))
    fams.append(("score40", 1, cross, never, [("trail3_40bars", 2.0, 3.0, 0.0, 40), ("trail3_120bars", 2.0, 3.0, 0.0, 120),
                                              ("wide5_120bars", 3.0, 5.0, 0.0, 120), ("wide5_250bars", 3.0, 5.0, 0.0, 250),
                                              ("wide8_250bars", 4.0, 8.0, 0.0, 250)]))
    o, h, l, c = (d[x].values.astype(np.float64) for x in ("open", "high", "low", "close"))
    atr = d["atr"].values.astype(np.float64)
    t = d.index.values.astype("datetime64[s]").astype(np.int64)
    g = gate.reindex(d.index.normalize(), method="ffill").fillna(False).values.astype(np.bool_)
    for fam, side, entry, exit_sig, exits in fams:
        ent = entry.fillna(False).values.astype(np.bool_)
        xs = exit_sig.fillna(False).values.astype(np.bool_)
        for name, stop_m, trail_m, target_r, max_hold in exits:
            ei, r, sf, held = cr.sim(o, h, l, c, atr, ent, xs, side, stop_m, trail_m, target_r, max_hold)
            if len(ei):
                sink.setdefault((market, fam, name), []).append(np.column_stack([t[ei], r, sf, g[ei], held]))


def main() -> None:
    demo = "--demo" in sys.argv
    rng = np.random.default_rng(3)
    t0 = time.time()
    sink: dict = {}
    if demo:
        data = {f"S{i}": sb.demo_prices(700 + i, "1d", n=2400, start=40 + i) for i in range(12)}
        spy = data["S0"]
    else:
        names = scan.load_stock_universe()
        data = scan.yahoo_batch(list(names), "10y", "1d")
        spy = scan.yahoo_batch(["SPY"], "10y", "1d").get("SPY")
    close = spy["close"]
    gate = (close > close.rolling(200).mean()).shift(1).fillna(False)
    gate.index = gate.index.normalize()
    for tkr, df in data.items():
        run_stock(df.iloc[:-1], "eu" if "." in tkr else "us", gate, rng, sink)
    print(f"{len(data)} stocks, {sum(len(p) for ps in sink.values() for p in ps):,} trades, {time.time() - t0:.0f}s", flush=True)
    tab = cr.trade_table(sink).rename(columns={"tf": "market"})
    tab["filter"] = tab["filter"].str.replace("btc_", "spx_")
    tab[tab.fee == 0.05].to_csv(OUT / ("stock_trades_demo.csv" if demo else "stock_trades.csv"), index=False)

    # Efficiency: rules with wider stops hold longer, so results per trade are not comparable.
    # Per period: average % per trade, average bars held, and % earned per bar in the market.
    rows = []
    for (market, fam, ex), parts in sink.items():
        a = np.concatenate(parts)
        pct = (a[:, 1] * a[:, 2] - 2 * 0.0005) * 100
        lo, hi = a[:, 0].min(), a[:, 0].max()
        c1, c2 = lo + cr.CUTS[0] * (hi - lo), lo + cr.CUTS[1] * (hi - lo)
        row = {"market": market, "family": fam, "exit": ex}
        for seg, m in (("A", a[:, 0] < c1), ("B", (a[:, 0] >= c1) & (a[:, 0] < c2)), ("C", a[:, 0] >= c2)):
            if m.sum() > 2:
                row.update({f"{seg}_pct_per_trade": round(pct[m].mean(), 3), f"{seg}_bars": round(a[m, 4].mean(), 1),
                            f"{seg}_pct_per_bar": round(pct[m].sum() / a[m, 4].sum(), 4),
                            f"{seg}_worst_trade_pct": round(pct[m].min(), 1)})
        rows.append(row)
    pd.DataFrame(rows).sort_values(["market", "family", "exit"]).to_csv(
        OUT / ("stock_efficiency_demo.csv" if demo else "stock_efficiency.csv"), index=False)


if __name__ == "__main__":
    main()
