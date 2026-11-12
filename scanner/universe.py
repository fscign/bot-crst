#!/usr/bin/env python3
"""
Daily job: rebuild the US stock list (data/universe.json).

Takes every stock listed on NASDAQ, NYSE and NYSE American from the official
NASDAQ Trader symbol files, downloads 3 months of daily bars and keeps the
most liquid ones (price and dollar-volume filters in scanner/config.json).
European stocks are listed by hand in scanner/europe.txt.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import signal_bot as sb
from scan import yahoo_batch

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "data" / "universe.json"
FILES = {"https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt": ("Symbol", "Security Name"),
         "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt": ("ACT Symbol", "Security Name")}
SKIP = re.compile(r"warrant|right|unit|preferred|depositary sh|notes due|debenture|%", re.I)


def listed() -> dict[str, str]:
    names = {}
    for url, (sym_col, name_col) in FILES.items():
        lines = requests.get(url, headers=sb.UA, timeout=60).text.strip().splitlines()
        head = lines[0].split("|")
        for line in lines[1:]:
            row = dict(zip(head, line.split("|")))
            sym, name = row.get(sym_col, ""), row.get(name_col, "")
            if (not sym or row.get("Test Issue") == "Y" or row.get("ETF") == "Y"
                    or not re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", sym) or SKIP.search(name)):
                continue
            short = re.split(r" - | Common| Class| Ordinary|, Inc| Inc\.| Corp| Holdings| plc| Ltd", name)[0].strip()
            names[sym.replace(".", "-")] = short or sym
    return names


def main() -> None:
    cfg = sb.load_config(HERE / "config.json")["universe"]
    names = listed()
    print(f"{len(names)} listed US stocks, checking liquidity...")
    data = yahoo_batch(sorted(names), "3mo", "1d", chunk=300)
    liquid = []
    for t, df in data.items():
        tail = df.tail(20)
        if len(tail) < 15:
            continue
        price, adv = float(tail["close"].iloc[-1]), float((tail["close"] * tail["volume"]).mean())
        if price >= cfg["us_min_price"] and adv >= cfg["us_min_dollar_volume"]:
            liquid.append((adv, t))
    liquid.sort(reverse=True)
    keep = [t for _, t in liquid[: cfg["us_max"]]]
    print(f"{len(data)} with data, {len(liquid)} liquid, keeping {len(keep)}")
    if len(keep) < 100:
        sys.exit("Too few stocks passed (data source probably throttled). Keeping the previous list.")
    OUT.write_text(json.dumps({"built": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                               "us": {t: names[t] for t in keep}}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
