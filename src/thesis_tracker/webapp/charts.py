"""View models for the price chart and the overview sparkline.

The page that draws these does no arithmetic on prices and formats nothing, so
this module sends it everything ready to place:

* positions are percentages of the plot box, as two-decimal strings, with ``y``
  measured from the top, so a higher price has a *smaller* ``y``;
* every price a reader can see (hover text, axis labels, level labels) is a string
  produced by the shared ``display_text`` rule;
* the moving averages come from ``indicators.calculate_indicator_series``, the same
  function the indicator tool uses, so the chart cannot disagree with it;
* label room is decided here, in pixels, because only here are the plot height and
  the label height both known.

The line is the *adjusted* close, since a split would otherwise show up as a
cliff.  Levels from a card are raw prices at the card's date.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

from thesis_tracker.decision.evidence import display_text
from thesis_tracker.indicators import Bar, calculate_indicator_series
from thesis_tracker.webapp import data

# key, label, calendar days covered, maximum points drawn.
RANGES = (("3m", "3 个月", 92, 70), ("1y", "1 年", 366, 130), ("5y", "5 年", 1827, 270))
PLOT_HEIGHT_PX = 260
# One label is a single 13px line plus padding; the gap keeps two labels apart.
LABEL_HEIGHT_PX = 20
LABEL_GAP_PX = 4
SMA_LINES = ((50, "sma_50", "sma50", "50 日均线"), (200, "sma_200", "sma200", "200 日均线"))
SPARK_DAYS = 183
SPARK_POINTS = 26
MIN_BARS = 2
# Share of the vertical span kept free above and below the data.
PADDING = 0.06
TOOLTIP_FLIP_AT = 60.0


def _fmt(value: float) -> str:
    return f"{value:.2f}"


def _price(value: float) -> str:
    return display_text(value, "USD/share", name="close")


def _bare(value: float) -> str:
    """A price without its unit, for axis labels."""
    return _price(value).split(" ")[0]


# -- sampling -----------------------------------------------------------------

def sample_positions(count: int, limit: int) -> list[int]:
    """Evenly spaced indices into ``count`` bars, always ending on the newest one."""
    if count <= limit:
        return list(range(count))
    stride = math.ceil(count / limit)
    positions = list(range(count - 1, -1, -stride))
    positions.reverse()
    return positions


def _range_start(newest: str, days: int) -> str:
    return (date.fromisoformat(newest) - timedelta(days=days)).isoformat()


def _nice_ticks(low: float, high: float, minimum: int = 4) -> list[float]:
    """Round-number ticks inside [low, high]: the coarsest step that gives at least
    ``minimum`` of them, so the axis is readable but never crowded."""
    span = high - low
    if span <= 0:
        return [low]
    top = math.ceil(math.log10(span))
    best: list[float] = []
    for exponent in range(top, top - 8, -1):
        for factor in (5, 2.5, 2, 1):
            step = factor * 10 ** exponent
            value = math.ceil(low / step - 1e-9) * step
            ticks = []
            while value <= high + step * 1e-9:
                ticks.append(round(value, 10))
                value += step
            if len(ticks) >= minimum:
                return ticks
            best = ticks if len(ticks) > len(best) else best
    return best


def spread_labels(wanted: list[float], limit: float, height: float, gap: float) -> list[float]:
    """Top positions for labels that want to sit at ``wanted``, without overlap.

    Labels keep their vertical order.  They are pushed down until each clears the
    one above, then pulled back up from the bottom if the last one would leave
    the plot.  The result is returned in the order of ``wanted``.
    """
    if not wanted:
        return []
    order = sorted(range(len(wanted)), key=lambda index: wanted[index])
    tops = [max(0.0, wanted[index]) for index in order]
    for position in range(1, len(tops)):
        tops[position] = max(tops[position], tops[position - 1] + height + gap)
    overflow = tops[-1] + height - limit
    if overflow > 0:
        tops[-1] -= overflow
        for position in range(len(tops) - 2, -1, -1):
            tops[position] = min(tops[position], tops[position + 1] - height - gap)
    result = [0.0] * len(wanted)
    for rank, index in enumerate(order):
        result[index] = round(tops[rank], 2)
    return result


# -- one range ----------------------------------------------------------------

def _level_inputs(levels: dict | None) -> list[tuple[str, str, float, float | None]]:
    """(key, label, price, upper price for a band) for the levels that exist."""
    found: list[tuple[str, str, float, float | None]] = []
    if not levels:
        return found
    entry = levels.get("entry")
    if entry and entry[0] is not None and entry[1] is not None:
        low, high = sorted((float(entry[0]), float(entry[1])))
        found.append(("entry", "买点区间", low, high))
    for key, label in (("stop", "止损"), ("target", "目标")):
        value = levels.get(key)
        if value is not None:
            found.append((key, label, float(value), None))
    return found


def _range_view(key: str, label: str, bars: list[Bar], positions: list[int],
                indicators: dict[int, dict], *, levels: dict | None,
                close_price: float | None, close_date: str | None) -> dict:
    closes = [bars[index].close for index in positions]
    sma_values = {name: [indicators[index]["values"][metric]["value"]
                         for index in positions]
                  for _length, metric, name, _label in SMA_LINES}
    level_inputs = _level_inputs(levels)
    every = list(closes)
    for values in sma_values.values():
        every.extend(value for value in values if value is not None)
    for _key, _label, low, high in level_inputs:
        every.append(low)
        if high is not None:
            every.append(high)
    if close_price is not None and close_date is not None:
        every.append(close_price)
    low, high = min(every), max(every)
    if high - low <= 0:
        pad = abs(high) * 0.01 or 1.0
        low, high = low - pad, high + pad
    pad = (high - low) * PADDING
    low, high = low - pad, high + pad

    def y_of(value: float) -> float:
        return (high - value) / (high - low) * 100

    count = len(positions)
    xs = [50.0] if count == 1 else [index / (count - 1) * 100 for index in range(count)]
    points = []
    for slot, index in enumerate(positions):
        point = {
            "x": _fmt(xs[slot]), "y": _fmt(y_of(closes[slot])),
            "date": bars[index].date, "close": _price(closes[slot]),
            "align": "end" if xs[slot] > TOOLTIP_FLIP_AT else "start",
        }
        for _length, _metric, name, _label in SMA_LINES:
            value = sma_values[name][slot]
            point[name] = None if value is None else _price(value)
        points.append(point)

    lines = {}
    notes = []
    for length, _metric, name, text in SMA_LINES:
        pieces = [f"{_fmt(xs[slot])},{_fmt(y_of(value))}"
                  for slot, value in enumerate(sma_values[name]) if value is not None]
        if pieces:
            lines[name] = {"available": True, "label": text, "path": "M" + " L".join(pieces),
                           "note": None}
        else:
            note = f"历史不足 {length} 个交易日，没有{text}。"
            lines[name] = {"available": False, "label": text, "path": None, "note": note}
            notes.append(note)

    level_views = []
    wanted: list[float] = []
    for level_key, level_label, bottom, upper in level_inputs:
        anchor = y_of(bottom if upper is None else (bottom + upper) / 2)
        view = {"key": level_key, "label": level_label,
                "kind": "band" if upper is not None else "line",
                "shape": {"entry": "entry", "stop": "stop", "target": "target"}[level_key]}
        if upper is None:
            view["value"] = _price(bottom)
            view["y"] = _fmt(y_of(bottom))
        else:
            view["value"] = f"{_bare(bottom)} – {_price(upper)}"
            view["y"] = _fmt(y_of(upper))
            view["y_bottom"] = _fmt(y_of(bottom))
            # The band's height, so the page draws a rectangle without subtracting.
            view["height"] = _fmt(y_of(bottom) - y_of(upper))
        level_views.append(view)
        wanted.append(anchor / 100 * PLOT_HEIGHT_PX - LABEL_HEIGHT_PX / 2)

    marker = None
    marker_note = None
    if close_price is not None and close_date is not None:
        within = [slot for slot, index in enumerate(positions)
                  if bars[index].date <= close_date]
        if close_date < bars[positions[0]].date or not within:
            marker_note = f"建卡当天（{close_date}）不在这个范围内。"
        else:
            slot = within[-1]
            marker = {"x": _fmt(xs[slot]), "y": _fmt(y_of(close_price)),
                      "date": close_date, "label": f"收盘价 {_price(close_price)}",
                      "shape": "close"}
            wanted.append(y_of(close_price) / 100 * PLOT_HEIGHT_PX - LABEL_HEIGHT_PX / 2)
    tops = spread_labels(wanted, PLOT_HEIGHT_PX, LABEL_HEIGHT_PX, LABEL_GAP_PX)
    for view, top in zip(level_views, tops):
        view["label_top_px"] = top
    if marker is not None:
        marker["label_top_px"] = tops[len(level_views)]

    ticks = _nice_ticks(low + pad, high - pad)
    x_slots = sorted({0, count // 4, count // 2, (3 * count) // 4, count - 1})
    x_ticks = [{"x": _fmt(xs[slot]), "label": bars[positions[slot]].date,
                "align": "start" if slot == 0 else "end" if slot == count - 1 else "middle"}
               for slot in x_slots]
    legend = [{"key": "price", "label": "复权收盘价"}]
    legend += [{"key": name, "label": text}
               for _length, _metric, name, text in SMA_LINES if lines[name]["available"]]
    return {
        "key": key, "label": label,
        "start_date": bars[positions[0]].date, "end_date": bars[positions[-1]].date,
        "points": points, "path": "M" + " L".join(f"{p['x']},{p['y']}" for p in points),
        "sma50": lines["sma50"], "sma200": lines["sma200"],
        "y_ticks": [{"y": _fmt(y_of(tick)), "label": _bare(tick)} for tick in ticks],
        "x_ticks": x_ticks, "levels": level_views, "close_marker": marker,
        "close_note": marker_note, "notes": notes, "legend": legend,
    }


# -- the whole chart --------------------------------------------------------------

def build_chart(bars: list[Bar], *, levels: dict | None = None,
                levels_note: str | None = None, close_price: float | None = None,
                close_date: str | None = None) -> dict:
    """The three ranges of one ticker's chart, from bars in date order."""
    if len(bars) < MIN_BARS:
        return {"available": False, "ranges": [], "plot_height_px": PLOT_HEIGHT_PX,
                "reason": f"价格历史至少需要 {MIN_BARS} 个交易日才能画图。"}
    newest = bars[-1].date
    windows = []
    for key, label, days, limit in RANGES:
        start = _range_start(newest, days)
        first = next((index for index, bar in enumerate(bars) if bar.date >= start), 0)
        chosen = [first + offset for offset in sample_positions(len(bars) - first, limit)]
        if len(chosen) < 2:
            chosen = [max(0, len(bars) - 2), len(bars) - 1]
        windows.append((key, label, chosen))
    wanted = sorted({index for _key, _label, chosen in windows for index in chosen})
    indicators = {item_index: item for item_index, item in zip(
        wanted, calculate_indicator_series(bars, None, positions=wanted))}
    return {
        "available": True, "reason": None, "plot_height_px": PLOT_HEIGHT_PX,
        "label_height_px": LABEL_HEIGHT_PX, "default_range": "1y",
        "caption": ("折线是复权收盘价（已按分红和拆股调整），所以更早的价格可能与当时的"
                    "原始收盘价不同；建议卡的价位按建卡当天的原始价格画。"),
        "levels_note": levels_note,
        "ranges": [_range_view(key, label, bars, chosen, indicators, levels=levels,
                               close_price=close_price, close_date=close_date)
                   for key, label, chosen in windows],
    }


def read_bars(price_db: Path | str, symbol: str) -> tuple[list[Bar], str | None]:
    """Adjusted bars from the read-only price database, or (``[]``, reason)."""
    try:
        connection = data.read_only(price_db)
    except FileNotFoundError:
        return [], "本地没有价格数据。"
    try:
        if not data._has_table(connection, "daily_prices"):
            return [], "本地没有价格数据。"
        if not data.single_batch(connection, symbol):
            return [], "这家公司的价格来自多次不一致的抓取，不画图。"
        rows = connection.execute(
            "SELECT date, adj_high, adj_low, adj_close, adj_volume FROM daily_prices "
            "WHERE symbol=? ORDER BY date", (symbol,)).fetchall()
    finally:
        connection.close()
    if not rows:
        return [], "本地没有这家公司的价格数据。"
    return [Bar(row["date"], row["adj_high"], row["adj_low"], row["adj_close"],
                row["adj_volume"]) for row in rows], None


def price_chart(price_db: Path | str, symbol: str, **options) -> dict:
    """One ticker's chart straight from the price database."""
    bars, reason = read_bars(price_db, symbol.upper())
    if reason is not None:
        return {"available": False, "reason": reason, "ranges": [],
                "plot_height_px": PLOT_HEIGHT_PX}
    return build_chart(bars, **options)


# -- sparkline ----------------------------------------------------------------------

def sparkline(bars: list[Bar]) -> dict:
    """About six months of adjusted closes as a normalised 100 x 100 path."""
    off = {"available": False, "path": None, "end": None, "count": 0,
           "start_date": None, "end_date": None, "label": "没有足够的价格画走势"}
    if len(bars) < MIN_BARS:
        return off
    start = _range_start(bars[-1].date, SPARK_DAYS)
    window = [bar for bar in bars if bar.date >= start]
    if len(window) < MIN_BARS:
        return off
    chosen = [window[index] for index in sample_positions(len(window), SPARK_POINTS)]
    closes = [bar.close for bar in chosen]
    low, high = min(closes), max(closes)
    span = high - low
    pad = span * PADDING
    count = len(chosen)

    def y_of(value: float) -> float:
        if span == 0:
            return 50.0
        return (high + pad - value) / (span + 2 * pad) * 100

    pieces = [f"{_fmt(index / (count - 1) * 100)},{_fmt(y_of(value))}"
              for index, value in enumerate(closes)]
    return {"available": True, "path": "M" + " L".join(pieces), "count": count,
            "end": {"x": "100.00", "y": _fmt(y_of(closes[-1]))},
            "start_date": chosen[0].date, "end_date": chosen[-1].date,
            "label": f"近半年复权收盘价走势，{chosen[0].date} 至 {chosen[-1].date}"}


def sparklines(price_db: Path | str, symbols: list[str]) -> dict[str, dict]:
    """Sparklines for many tickers over one read-only connection."""
    if not symbols:
        return {}
    try:
        connection = data.read_only(price_db)
    except FileNotFoundError:
        return {}
    try:
        result = {}
        for symbol in symbols:
            rows = connection.execute(
                "SELECT date, adj_high, adj_low, adj_close, adj_volume FROM daily_prices "
                "WHERE symbol=? AND date >= date((SELECT MAX(date) FROM daily_prices "
                "WHERE symbol=?), ?) ORDER BY date",
                (symbol, symbol, f"-{SPARK_DAYS} days")).fetchall()
            result[symbol] = sparkline([Bar(row["date"], row["adj_high"], row["adj_low"],
                                            row["adj_close"], row["adj_volume"])
                                        for row in rows])
        return result
    finally:
        connection.close()
