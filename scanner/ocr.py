"""
Reading text from pictures (signal screenshots posted in Telegram).

Uses RapidOCR (pip package rapidocr-onnxruntime, no system install). The recognised
words are rebuilt into lines, top to bottom and left to right, so a screenshot reads
like the app looks, for example:
    SOLUSDT PnL (USDT)
    Short Isolated 10x +1,180.17 (+8.09%)
    Entry Price Mark Price Est. Liq. Price
    72.625 72.041 79.737
    TP/SL Entire position: 71.000/--
"""
from __future__ import annotations

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
    return _engine


def boxes_to_lines(result) -> str:
    """result: [(box, text, confidence), ...] with box = 4 corner points."""
    items = []
    for box, text, _conf in result or []:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        items.append({"y": (min(ys) + max(ys)) / 2, "h": max(ys) - min(ys), "x": min(xs), "t": text})
    if not items:
        return ""
    items.sort(key=lambda i: i["y"])
    tol = 0.6 * sorted(i["h"] for i in items)[len(items) // 2]
    lines, row = [], [items[0]]
    for it in items[1:]:
        if abs(it["y"] - row[-1]["y"]) <= tol:
            row.append(it)
        else:
            lines.append(row)
            row = [it]
    lines.append(row)
    return "\n".join(" ".join(i["t"] for i in sorted(r, key=lambda i: i["x"])) for r in lines)


def read_image(image) -> str:
    """image: a file path or the raw bytes of a picture. Returns its text as lines."""
    result, _ = _get_engine()(image)
    return boxes_to_lines(result)
