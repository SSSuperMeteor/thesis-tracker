"""Independent formula checks for adjusted-price technical indicators."""

import math
from datetime import date, timedelta

import pytest

from thesis_tracker.indicators import Bar, calculate_indicator_series


def bars(count=300, *, flat=False, gap=False, slope=0.13):
    first = date(2024, 1, 1)
    result = []
    for index in range(count):
        day = first + timedelta(days=index + (1 if gap and index >= 60 else 0))
        close = 100 if flat else 100 + slope * index + 2 * math.sin(index / 7)
        result.append(Bar(day.isoformat(), close + 1, close - 1, close, 1000 + index))
    return result


def reference_ema(values, length):
    if len(values) < length:
        return None
    value = sum(values[:length]) / length
    for item in values[length:]:
        value = (2 / (length + 1)) * item + (1 - 2 / (length + 1)) * value
    return value


def reference_rsi(closes):
    if len(closes) < 15:
        return None
    changes = [right - left for left, right in zip(closes, closes[1:])]
    up = sum(max(change, 0) for change in changes[:14]) / 14
    down = sum(max(-change, 0) for change in changes[:14]) / 14
    for change in changes[14:]:
        up = (up * 13 + max(change, 0)) / 14
        down = (down * 13 + max(-change, 0)) / 14
    if down == 0:
        return 100 if up > 0 else None
    return 100 - 100 / (1 + up / down)


def reference_atr(items):
    if len(items) < 15:
        return None
    true_ranges = [max(current.high - current.low,
                       abs(current.high - previous.close),
                       abs(current.low - previous.close))
                   for previous, current in zip(items, items[1:])]
    value = sum(true_ranges[:14]) / 14
    for item in true_ranges[14:]:
        value = (value * 13 + item) / 14
    return value


def reference_macd(closes):
    if len(closes) < 26:
        return None, None, None
    macd_lines = [reference_ema(closes[:end], 12) - reference_ema(closes[:end], 26)
                  for end in range(26, len(closes) + 1)]
    signal = reference_ema(macd_lines, 9)
    return macd_lines[-1], signal, None if signal is None else macd_lines[-1] - signal


def value(record, name):
    return record["values"][name]["value"]


def test_all_formulas_match_independent_reference():
    items = bars()
    benchmark = bars(slope=0.07)
    record = calculate_indicator_series(items, benchmark, positions=[299])[0]
    closes = [item.close for item in items]
    for length in (20, 50, 200):
        assert value(record, f"sma_{length}") == pytest.approx(sum(closes[-length:]) / length, abs=1e-9)
    for length in (12, 26):
        assert value(record, f"ema_{length}") == pytest.approx(reference_ema(closes, length), abs=1e-9)
    assert value(record, "rsi_14") == pytest.approx(reference_rsi(closes), abs=1e-9)
    expected_macd = reference_macd(closes)
    for name, expected in zip(("macd_line", "macd_signal", "macd_histogram"), expected_macd):
        assert value(record, name) == pytest.approx(expected, abs=1e-9)
    mean = sum(closes[-20:]) / 20
    deviation = math.sqrt(sum((item - mean) ** 2 for item in closes[-20:]) / 20)
    for name, expected in (("bollinger_middle_20", mean), ("bollinger_upper_20", mean + 2 * deviation),
                           ("bollinger_lower_20", mean - 2 * deviation)):
        assert value(record, name) == pytest.approx(expected, abs=1e-9)
    assert value(record, "atr_14") == pytest.approx(reference_atr(items), abs=1e-9)
    avg_volume = sum(item.volume for item in items[-20:]) / 20
    assert value(record, "avg_volume_20") == pytest.approx(avg_volume, abs=1e-9)
    assert value(record, "volume_ratio_20") == pytest.approx(items[-1].volume / avg_volume, abs=1e-9)
    for length in (20, 60, 252):
        highest = max(item.high for item in items[-length:])
        lowest = min(item.low for item in items[-length:])
        assert value(record, f"range_high_{length}") == pytest.approx(highest, abs=1e-9)
        assert value(record, f"range_low_{length}") == pytest.approx(lowest, abs=1e-9)
        assert value(record, f"distance_to_high_{length}_percent") == pytest.approx(
            (items[-1].close / highest - 1) * 100, abs=1e-9)
        assert value(record, f"distance_to_low_{length}_percent") == pytest.approx(
            (items[-1].close / lowest - 1) * 100, abs=1e-9)
    for length in (21, 63, 126, 252):
        expected = ((items[-1].close / items[-length].close - 1) -
                    (benchmark[-1].close / benchmark[-length].close - 1)) * 100
        assert value(record, f"relative_spy_{length}") == pytest.approx(expected, abs=1e-9)


def test_properties_and_no_lookahead():
    items = bars(280)
    benchmark = bars(280, slope=0.07)
    full = calculate_indicator_series(items, benchmark)
    for index in (14, 19, 25, 33, 49, 199, 251, 279):
        truncated = calculate_indicator_series(items[:index + 1], benchmark[:index + 1])[-1]
        for name, metric in truncated["values"].items():
            expected = full[index]["values"][name]["value"]
            actual = metric["value"]
            assert actual == pytest.approx(expected, abs=1e-9) if actual is not None else expected is None
        if value(truncated, "rsi_14") is not None:
            assert 0 <= value(truncated, "rsi_14") <= 100
        if value(truncated, "bollinger_middle_20") is not None:
            assert value(truncated, "bollinger_lower_20") <= value(truncated, "bollinger_middle_20") <= value(truncated, "bollinger_upper_20")
        if value(truncated, "atr_14") is not None:
            assert value(truncated, "atr_14") >= 0
        if value(truncated, "macd_histogram") is not None:
            assert value(truncated, "macd_histogram") == pytest.approx(value(truncated, "macd_line") - value(truncated, "macd_signal"), abs=1e-9)


def test_insufficient_history_and_flat_rsi():
    short = calculate_indicator_series(bars(10), bars(10))[-1]
    rsi = short["values"]["rsi_14"]
    assert rsi["value"] is None
    assert rsi["reason"]["code"] == "insufficient_history"
    assert "15" in rsi["reason"]["message"] and "10" in rsi["reason"]["message"]
    assert value(short, "ema_12") is None
    flat = calculate_indicator_series(bars(40, flat=True), bars(40))[-1]
    assert value(flat, "rsi_14") is None
    assert "价格无变化" in flat["values"]["rsi_14"]["reason"]["message"]
    assert value(flat, "sma_20") == 100


def test_date_gap_aligns_spy_by_date():
    items = bars(70, gap=True)
    benchmark = bars(71)
    record = calculate_indicator_series(items, benchmark)[-1]
    common = {item.date: item.close for item in benchmark}
    aligned = [(item.close, common[item.date]) for item in items if item.date in common]
    expected = ((aligned[-1][0] / aligned[-21][0] - 1) -
                (aligned[-1][1] / aligned[-21][1] - 1)) * 100
    assert value(record, "relative_spy_21") == pytest.approx(expected, abs=1e-9)


def test_relative_strength_zero_start_price_is_not_misclassified_as_short_history():
    items = bars(21)
    items[0] = Bar(items[0].date, 1, 0, 0, items[0].volume)
    result = calculate_indicator_series(items, bars(21))[-1]["values"]["relative_spy_21"]
    assert result["value"] is None
    assert result["reason"]["code"] == "zero_denominator"
