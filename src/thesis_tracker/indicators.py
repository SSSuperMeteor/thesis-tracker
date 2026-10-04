"""Pure technical indicators computed only from adjusted daily price bars."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Bar:
    date: str
    high: float
    low: float
    close: float
    volume: float


def _sma(values: list[float], length: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    for index in range(length - 1, len(values)):
        result[index] = math.fsum(values[index - length + 1:index + 1]) / length
    return result


def _ema(values: list[float], length: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < length:
        return result
    average = math.fsum(values[:length]) / length
    result[length - 1] = average
    alpha = 2 / (length + 1)
    for index in range(length, len(values)):
        average = alpha * values[index] + (1 - alpha) * average
        result[index] = average
    return result


def _rsi(values: list[float]) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < 15:
        return result
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    gain = math.fsum(max(change, 0) for change in changes[:14]) / 14
    loss = math.fsum(max(-change, 0) for change in changes[:14]) / 14
    for index in range(14, len(values)):
        if index > 14:
            change = changes[index - 1]
            gain = (gain * 13 + max(change, 0)) / 14
            loss = (loss * 13 + max(-change, 0)) / 14
        if loss > 0:
            result[index] = 100 - 100 / (1 + gain / loss)
        elif gain > 0:
            result[index] = 100.0
    return result


def _atr(bars: list[Bar]) -> list[float | None]:
    result: list[float | None] = [None] * len(bars)
    if len(bars) < 15:
        return result
    ranges = [max(current.high - current.low,
                  abs(current.high - previous.close),
                  abs(current.low - previous.close))
              for previous, current in zip(bars, bars[1:])]
    average = math.fsum(ranges[:14]) / 14
    result[14] = average
    for index in range(15, len(bars)):
        average = (average * 13 + ranges[index - 1]) / 14
        result[index] = average
    return result


def _macd(closes: list[float]) -> tuple[list[float | None], list[float | None]]:
    short = _ema(closes, 12)
    long = _ema(closes, 26)
    line = [a - b if a is not None and b is not None else None
            for a, b in zip(short, long)]
    signal: list[float | None] = [None] * len(closes)
    if len(closes) >= 26:
        for index, value in enumerate(_ema([item for item in line if item is not None], 9), start=25):
            signal[index] = value
    return line, signal


def calculate_indicator_series(
    bars: list[Bar], spy_bars: list[Bar] | None = None, *, positions=None,
) -> list[dict]:
    """Return selected daily observations, with all formula state built from past bars only."""
    if not bars:
        return []
    if any(left.date >= right.date for left, right in zip(bars, bars[1:])):
        raise ValueError("bars must have unique increasing dates")
    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars]
    sma = {length: _sma(closes, length) for length in (20, 50, 200)}
    ema = {length: _ema(closes, length) for length in (12, 26)}
    rsi = _rsi(closes)
    macd_line, macd_signal = _macd(closes)
    atr = _atr(bars)
    average_volume = _sma(volumes, 20)
    spy_by_date = {bar.date: bar.close for bar in spy_bars or []}
    aligned: list[tuple[float, float]] = []
    aligned_at: list[int] = []
    for bar in bars:
        if bar.date in spy_by_date:
            aligned.append((bar.close, spy_by_date[bar.date]))
        aligned_at.append(len(aligned))

    def metric(name, value, unit, day, required, actual, *, reason=None):
        if value is None and reason is None:
            reason = {"code": "insufficient_history",
                      "message": f"{name} 需要 {required} 根，实际 {actual} 根。"}
        return {"name": name, "value": value, "unit": unit, "date": day,
                "bars_used": min(required, actual), "reason": reason}

    output = []
    for index in range(len(bars)) if positions is None else positions:
        bar = bars[index]
        day = bar.date
        count = index + 1
        values = {}
        for length in (20, 50, 200):
            values[f"sma_{length}"] = metric(f"SMA({length})", sma[length][index],
                                              "USD/share", day, length, count)
        for length in (12, 26):
            values[f"ema_{length}"] = metric(f"EMA({length})", ema[length][index],
                                              "USD/share", day, length, count)
        rsi_reason = ({"code": "no_price_change", "message": "价格无变化，RSI 无定义。"}
                      if count >= 15 and rsi[index] is None else None)
        values["rsi_14"] = metric("RSI(14)", rsi[index], "index (0-100)", day,
                                   15, count, reason=rsi_reason)
        line = macd_line[index]
        signal = macd_signal[index]
        values["macd_line"] = metric("MACD(12,26) line", line, "USD/share", day, 26, count)
        values["macd_signal"] = metric("MACD(12,26,9) signal", signal,
                                         "USD/share", day, 34, count)
        values["macd_histogram"] = metric("MACD line minus signal", line - signal
                                            if line is not None and signal is not None else None,
                                            "USD/share", day, 34, count)
        middle = sma[20][index]
        deviation = (math.sqrt(math.fsum((item - middle) ** 2 for item in closes[index - 19:index + 1]) / 20)
                     if middle is not None else None)
        for name, value in (("middle", middle),
                            ("upper", middle + 2 * deviation if deviation is not None else None),
                            ("lower", middle - 2 * deviation if deviation is not None else None)):
            values[f"bollinger_{name}_20"] = metric(f"Bollinger(20,2) {name}", value,
                                                       "USD/share", day, 20, count)
        values["atr_14"] = metric("ATR(14)", atr[index], "USD/share", day, 15, count)
        avg = average_volume[index]
        values["avg_volume_20"] = metric("Average adjusted volume(20)", avg,
                                                "adjusted shares", day, 20, count)
        ratio_reason = ({"code": "zero_denominator", "message": "近 20 根平均复权成交量为 0，无法计算比值。"}
                        if avg == 0 else None)
        values["volume_ratio_20"] = metric("Adjusted volume / average adjusted volume(20)",
                                              bar.volume / avg if avg else None,
                                              "ratio", day, 20, count, reason=ratio_reason)
        for length in (20, 60, 252):
            window = bars[index - length + 1:index + 1] if count >= length else []
            high = max((item.high for item in window), default=None)
            low = min((item.low for item in window), default=None)
            values[f"range_high_{length}"] = metric(f"Highest adjusted high({length})", high,
                                                      "USD/share", day, length, count)
            values[f"range_low_{length}"] = metric(f"Lowest adjusted low({length})", low,
                                                     "USD/share", day, length, count)
            for side, level in (("high", high), ("low", low)):
                zero_reason = ({"code": "zero_denominator", "message": "区间价位为 0，无法计算距离百分比。"}
                               if level == 0 else None)
                distance = (bar.close / level - 1) * 100 if level else None
                values[f"distance_to_{side}_{length}_percent"] = metric(
                    f"(adjusted close / {side}({length}) - 1) × 100", distance,
                    "percent", day, length, count, reason=zero_reason)
        matched_count = aligned_at[index]
        for length in (21, 63, 126, 252):
            relative = None
            relative_reason = None
            if matched_count >= length and day in spy_by_date:
                current = aligned[matched_count - 1]
                prior = aligned[matched_count - length]
                if prior[0] and prior[1]:
                    relative = ((current[0] / prior[0] - 1) - (current[1] / prior[1] - 1)) * 100
                else:
                    relative_reason = {"code": "zero_denominator",
                                       "message": "对齐窗口的起始复权收盘价为 0，无法计算涨幅。"}
            values[f"relative_spy_{length}"] = metric(
                f"Ticker return minus SPY return over {length} aligned bars", relative,
                "percentage points", day, length, matched_count, reason=relative_reason)
        output.append({"date": day, "values": values})
    return output
