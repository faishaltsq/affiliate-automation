"""
Market chart renderer — draws a real index chart from live Yahoo Finance data.

Why this exists: news-portal stock photos of a trading floor are stale by
definition (they were shot years ago), so a card about "IHSG 5.185" ends up
showing a trading floor from 2019. For market topics the image has to *be* the
data: the actual index line, the actual level, the actual drop.

PIL-only — no matplotlib in the runtime venv, and adding a 60MB plotting stack
for one line chart is not worth it.
"""
import io
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFont

from src.logger import get_logger

log = get_logger("market_chart")

CHART_W = 1080
CHART_H = 1282  # matches PHOTO_HEIGHT in card_generator

BG = (10, 12, 16)
GRID = (34, 40, 52)
TEXT = (229, 231, 235)
MUTED = (140, 150, 168)
RED = (239, 68, 68)
GREEN = (34, 197, 94)
WHITE = (255, 255, 255)

SYMBOLS = {
    "ihsg": "^JKSE",
    "jkse": "^JKSE",
    "ihsg trading halt": "^JKSE",
    "bursa": "^JKSE",
    "saham": "^JKSE",
    "wall street": "^GSPC",
    "s&p": "^GSPC",
    "nasdaq": "^IXIC",
    "dow jones": "^DJI",
    "dolar": "IDR=X",
    "rupiah": "IDR=X",
    "bitcoin": "BTC-USD",
    "crypto": "BTC-USD",
    "emas": "GC=F",
    "minyak": "CL=F",
}

DISPLAY_NAME = {
    "^JKSE": "IHSG",
    "^GSPC": "S&P 500",
    "^IXIC": "NASDAQ",
    "^DJI": "Dow Jones",
    "IDR=X": "USD/IDR",
    "BTC-USD": "Bitcoin",
    "GC=F": "Emas",
    "CL=F": "Minyak",
}


def symbol_for_topic(topic: str) -> str | None:
    """Maps a headline to the index/instrument it is actually about."""
    lower = topic.lower()
    # longest keyword first so "ihsg trading halt" wins over "saham"
    for key in sorted(SYMBOLS, key=len, reverse=True):
        if key in lower:
            return SYMBOLS[key]
    return None


def _fetch(symbol: str, rng: str, interval: str) -> dict | None:
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
        f"?range={rng}&interval={interval}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        return data["chart"]["result"][0]
    except Exception as e:
        log.warning(f"chart fetch failed for {symbol} {rng}/{interval}: {e}")
        return None


def _font(size: int, bold: bool = False):
    name = "ariblk.ttf" if bold else "arial.ttf"
    for path in (f"C:/Windows/Fonts/{name}", "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _num(v: float) -> str:
    return f"{v:,.0f}".replace(",", ".")


def render_market_chart(topic: str, symbol: str | None = None) -> bytes | None:
    """
    Renders a 1080x1282 dark editorial chart for the instrument in `topic`.
    Returns JPEG bytes, or None when the topic is not a market story / fetch fails.
    """
    sym = symbol or symbol_for_topic(topic)
    if not sym:
        return None

    # Intraday first (shows today's crash), then daily as fallback.
    result = _fetch(sym, "5d", "15m")
    intraday = bool(result)
    if not result:
        result = _fetch(sym, "1mo", "1d")
    if not result:
        return None

    meta = result["meta"]
    ts = result.get("timestamp") or []
    quote = result["indicators"]["quote"][0]
    closes_raw = quote.get("close") or []
    pairs = [(t, c) for t, c in zip(ts, closes_raw) if c is not None]
    if len(pairs) < 5:
        return None

    closes = [c for _, c in pairs]
    price = meta.get("regularMarketPrice") or closes[-1]
    prev = meta.get("previousClose") or meta.get("chartPreviousClose") or closes[0]
    change = price - prev
    pct = (change / prev * 100) if prev else 0.0
    up = change >= 0
    accent = GREEN if up else RED

    img = Image.new("RGB", (CHART_W, CHART_H), BG)
    d = ImageDraw.Draw(img)

    # ---- Header ----
    f_sym = _font(64, bold=True)
    f_price = _font(104, bold=True)
    f_chg = _font(44, bold=True)
    f_lbl = _font(26)
    f_small = _font(24)

    name = DISPLAY_NAME.get(sym, sym)
    d.text((60, 60), name, font=f_sym, fill=TEXT)
    d.text((60, 148), _num(price), font=f_price, fill=WHITE)

    arrow = "▲" if up else "▼"
    pts_s = f"{abs(change):,.0f}".replace(",", ".")
    pct_s = f"{abs(pct):.2f}%".replace(".", ",")
    chg_txt = f"{arrow} {'+' if up else '-'}{pts_s}  ({'+' if up else '-'}{pct_s})"
    d.text((60, 268), chg_txt, font=f_chg, fill=accent)

    # Session timestamp — the time of the LAST data bar, not card generation
    # time, so a weekend card doesn't claim a live session.
    last_ts = pairs[-1][0]
    wib_last = datetime.fromtimestamp(last_ts, tz=timezone.utc) + timedelta(hours=7)
    d.text((60, 330), f"Sesi {wib_last.strftime('%d/%m/%Y %H:%M')} WIB  ·  Yahoo Finance",
           font=f_small, fill=MUTED)

    # ---- Chart area ----
    pad_l, pad_r = 60, 60
    top, bottom = 420, CHART_H - 150
    plot_w = CHART_W - pad_l - pad_r
    plot_h = bottom - top

    lo, hi = min(closes), max(closes)
    if hi == lo:
        hi, lo = hi + 1, lo - 1
    span = hi - lo
    lo -= span * 0.10
    hi += span * 0.10
    span = hi - lo

    # Include the 52W range in the padding decision so the axis never shows a
    # gridline below the stated 52-week low.
    hi52 = meta.get("fiftyTwoWeekHigh")
    lo52 = meta.get("fiftyTwoWeekLow")

    def y_of(v):
        return bottom - (v - lo) / span * plot_h

    # Grid + price axis labels
    for i in range(5):
        v = lo + span * i / 4
        y = y_of(v)
        d.line([(pad_l, y), (CHART_W - pad_r, y)], fill=GRID, width=1)
        lbl = _num(v)
        tw = d.textlength(lbl, font=f_small)
        d.text((CHART_W - pad_r - tw - 12, y - 14), lbl, font=f_small, fill=MUTED)

    # Prev-close reference line (dashed) + label
    if lo < prev < hi:
        yp = y_of(prev)
        for x in range(pad_l, CHART_W - pad_r, 18):
            d.line([(x, yp), (x + 9, yp)], fill=(90, 98, 114), width=2)
        d.text((pad_l + 4, yp - 30), f"prev close {_num(prev)}", font=f_small, fill=(120, 130, 148))

    # Index line
    n = len(closes)
    pts = [
        (pad_l + plot_w * i / (n - 1), y_of(c))
        for i, c in enumerate(closes)
    ]
    d.line(pts, fill=accent, width=5, joint="curve")

    # Soft area fill under the line
    area = pts + [(pts[-1][0], bottom), (pts[0][0], bottom)]
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.polygon(area, fill=(*accent, 46))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    d = ImageDraw.Draw(img)

    # Last-price marker
    lx, ly = pts[-1]
    d.ellipse([lx - 10, ly - 10, lx + 10, ly + 10], fill=accent)
    d.ellipse([lx - 5, ly - 5, lx + 5, ly + 5], fill=WHITE)

    # ---- X axis: day boundaries for multi-day ranges ----
    days = {}
    for t, _ in pairs:
        dwib = datetime.fromtimestamp(t, tz=timezone.utc) + timedelta(hours=7)
        days.setdefault(dwib.strftime("%d/%m"), []).append(t)
    if len(days) > 1:
        first_t = pairs[0][0]
        last_t = pairs[-1][0]
        span_t = max(1, last_t - first_t)
        for label, stamps in days.items():
            mid = (min(stamps) + max(stamps)) // 2
            x = pad_l + plot_w * (mid - first_t) / span_t
            d.text((x - 26, bottom + 8), label, font=f_small, fill=MUTED)

    # ---- Bottom context strip ----
    d.line([(pad_l, bottom + 40), (CHART_W - pad_r, bottom + 40)], fill=GRID, width=1)
    d.text((pad_l, bottom + 62), f"Range 5 hari  ·  {'intraday 15 menit' if intraday else 'harian'}",
           font=f_lbl, fill=MUTED)

    if hi52 and lo52:
        right = f"52W  {_num(lo52)} — {_num(hi52)}"
        tw = d.textlength(right, font=f_lbl)
        d.text((CHART_W - pad_r - tw, bottom + 62), right, font=f_lbl, fill=MUTED)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=94)
    log.info(f"Market chart rendered: {name} {_num(price)} ({pct:+.2f}%)")
    return buf.getvalue()


if __name__ == "__main__":
    b = render_market_chart("IHSG trading halt anjlok 8%")
    if b:
        out = "market_chart_test.jpg"
        open(out, "wb").write(b)
        im = Image.open(io.BytesIO(b))
        print(f"OK {out} {im.size} {len(b)} bytes")
