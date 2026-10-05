"""The price chart's view model: coordinates, averages, levels and label room.

The chart is drawn by a page that does no arithmetic on prices, so every property
a reader relies on has to be true of the numbers the backend sends: a higher
price is higher on the page, an average that cannot be computed is not drawn, an
action without price levels draws none, and two labels never share space.
"""

from __future__ import annotations

import itertools
import random
from datetime import date, timedelta

import pytest
from webapp_fixtures import build_fixture

from thesis_tracker.decision.evidence import display_text
from thesis_tracker.indicators import Bar
from thesis_tracker.webapp import charts


def make_bars(count: int, *, base: float = 100.0, drift: float = 0.1,
              wobble: float = 3.0, seed: int = 7, start: str = "2020-01-01") -> list[Bar]:
    """Deterministic weekday bars with a gentle trend and some noise."""
    generator = random.Random(seed)
    day = date.fromisoformat(start)
    bars: list[Bar] = []
    close = base
    while len(bars) < count:
        if day.weekday() < 5:
            close = max(0.5, close + drift + generator.uniform(-wobble, wobble) * base / 100)
            bars.append(Bar(day.isoformat(), close * 1.01, close * 0.99, close, 1_000_000.0))
        day += timedelta(days=1)
    return bars


@pytest.fixture(scope="module")
def chart():
    return charts.build_chart(make_bars(1500))


def percent(text: str) -> float:
    return float(text)


# -- coordinates --------------------------------------------------------------

def test_there_are_three_ranges_each_with_its_own_series(chart):
    assert [item["key"] for item in chart["ranges"]] == ["3m", "1y", "5y"]
    assert [item["label"] for item in chart["ranges"]] == ["3 个月", "1 年", "5 年"]
    counts = [len(item["points"]) for item in chart["ranges"]]
    assert counts[0] < counts[1] < counts[2]
    assert all(count >= 2 for count in counts)


def test_x_runs_left_to_right_in_date_order(chart):
    for item in chart["ranges"]:
        xs = [percent(point["x"]) for point in item["points"]]
        dates = [point["date"] for point in item["points"]]
        assert xs == sorted(xs) and len(set(xs)) == len(xs)
        assert dates == sorted(dates)
        assert xs[0] == 0.0 and xs[-1] == 100.0


def test_a_higher_price_is_higher_on_the_page(chart):
    """y is measured from the top, so order by price must invert order by y."""
    bars = {bar.date: bar.close for bar in make_bars(1500)}
    for item in chart["ranges"]:
        points = item["points"]
        for first, second in itertools.combinations(points, 2):
            price_a, price_b = bars[first["date"]], bars[second["date"]]
            y_a, y_b = percent(first["y"]), percent(second["y"])
            # Positions are rounded to two decimals, so two prices a hair apart may
            # share a y; the order must never be reversed.
            if price_a > price_b + 1e-9:
                assert y_a <= y_b, (first, second)
            elif price_b > price_a + 1e-9:
                assert y_b <= y_a, (first, second)
        highest = max(points, key=lambda point: bars[point["date"]])
        lowest = min(points, key=lambda point: bars[point["date"]])
        assert percent(highest["y"]) == min(percent(point["y"]) for point in points)
        assert percent(lowest["y"]) == max(percent(point["y"]) for point in points)
        assert percent(highest["y"]) < percent(lowest["y"])


def test_every_coordinate_is_inside_the_plot(chart):
    for item in chart["ranges"]:
        for point in item["points"]:
            assert 0.0 <= percent(point["x"]) <= 100.0
            assert 0.0 <= percent(point["y"]) <= 100.0


def test_the_series_includes_the_newest_bar_and_never_exceeds_its_budget(chart):
    newest = make_bars(1500)[-1].date
    for item, limit in zip(chart["ranges"], (70, 130, 270)):
        assert item["points"][-1]["date"] == newest
        assert len(item["points"]) <= limit


def test_the_path_is_the_points_in_order(chart):
    for item in chart["ranges"]:
        coordinates = [f"{point['x']},{point['y']}" for point in item["points"]]
        assert item["path"] == "M" + " L".join(coordinates)


def test_hover_text_is_the_shared_display_of_the_adjusted_close(chart):
    bars = {bar.date: bar.close for bar in make_bars(1500)}
    for item in chart["ranges"]:
        for point in item["points"]:
            assert point["close"] == display_text(bars[point["date"]], "USD/share",
                                                  name="close")
    assert "美元/股" in chart["ranges"][0]["points"][0]["close"]


def test_the_tooltip_flips_to_the_left_near_the_right_edge(chart):
    points = chart["ranges"][1]["points"]
    assert points[0]["align"] == "start"
    assert points[-1]["align"] == "end"


def test_axis_labels_are_prepared_strings_in_order(chart):
    for item in chart["ranges"]:
        ys = [percent(tick["y"]) for tick in item["y_ticks"]]
        labels = [tick["label"] for tick in item["y_ticks"]]
        assert len(ys) >= 3 and ys == sorted(ys, reverse=True)
        assert all(isinstance(label, str) and label for label in labels)
        assert "美元" not in "".join(labels)
        assert [float(label) for label in labels] == sorted(
            (float(label) for label in labels))
        assert [tick["label"] for tick in item["x_ticks"]][0] == item["points"][0]["date"]
        assert [tick["label"] for tick in item["x_ticks"]][-1] == item["points"][-1]["date"]


# -- moving averages ---------------------------------------------------------

def test_the_averages_come_from_the_existing_indicator_function(chart):
    """Same numbers as the indicator tool, so the chart can never disagree with it."""
    from thesis_tracker.indicators import calculate_indicator_series

    bars = make_bars(1500)
    latest = calculate_indicator_series(bars, None, positions=[len(bars) - 1])[0]["values"]
    point = chart["ranges"][1]["points"][-1]
    assert point["sma50"] == display_text(latest["sma_50"]["value"], "USD/share",
                                          name="close")
    assert point["sma200"] == display_text(latest["sma_200"]["value"], "USD/share",
                                           name="close")


def test_both_averages_are_drawn_when_the_history_is_long_enough(chart):
    for item in chart["ranges"]:
        assert item["sma50"]["available"] is True
        assert item["sma50"]["path"].startswith("M")
        assert item["sma200"]["available"] is True
        assert item["notes"] == []


def test_a_short_history_draws_no_200_day_line_and_says_why():
    short = charts.build_chart(make_bars(120))
    for item in short["ranges"]:
        assert item["sma200"]["available"] is False
        assert item["sma200"]["path"] is None
        assert "200" in item["sma200"]["note"]
        assert "历史不足" in item["sma200"]["note"]
        assert item["sma200"]["note"] in item["notes"]
        assert all(point["sma200"] is None for point in item["points"])
    # 120 bars is still enough for the 50-day line.
    assert short["ranges"][0]["sma50"]["available"] is True


def test_a_very_short_history_draws_neither_average():
    tiny = charts.build_chart(make_bars(30))
    item = tiny["ranges"][0]
    assert item["sma50"]["available"] is False and item["sma200"]["available"] is False
    assert len(item["notes"]) == 2


def test_a_partial_average_starts_where_it_exists():
    """Bars 0-198 have no 200-day value; the line must not start at the left edge."""
    partial = charts.build_chart(make_bars(260))
    item = partial["ranges"][2]
    assert item["sma200"]["available"] is True
    first_with_value = next(point for point in item["points"] if point["sma200"])
    assert percent(first_with_value["x"]) > 0
    assert item["sma200"]["path"].startswith(f"M{first_with_value['x']},")


def test_a_one_bar_history_is_not_a_chart():
    result = charts.build_chart(make_bars(1))
    assert result["available"] is False
    assert "至少" in result["reason"]


# -- levels ---------------------------------------------------------------------

LEVELS = {"entry": (228.0, 236.0), "stop": 218.12, "target": 265.0}


def test_levels_are_drawn_where_their_prices_are():
    bars = make_bars(400, base=230.0, drift=0.0, wobble=1.0)
    result = charts.build_chart(bars, levels=LEVELS)
    item = result["ranges"][1]
    by_key = {level["key"]: level for level in item["levels"]}
    assert set(by_key) == {"entry", "stop", "target"}
    # target above entry above stop, so y runs the other way.
    assert (percent(by_key["target"]["y"]) < percent(by_key["entry"]["y"])
            < percent(by_key["stop"]["y"]))
    assert percent(by_key["entry"]["y"]) < percent(by_key["entry"]["y_bottom"])
    assert percent(by_key["entry"]["height"]) == pytest.approx(
        percent(by_key["entry"]["y_bottom"]) - percent(by_key["entry"]["y"]), abs=0.011)
    assert by_key["stop"]["value"] == "218.12 美元/股"
    assert by_key["entry"]["value"] == "228.00 – 236.00 美元/股"
    assert by_key["entry"]["kind"] == "band"
    assert by_key["stop"]["kind"] == by_key["target"]["kind"] == "line"


def test_the_domain_always_contains_every_level():
    bars = make_bars(400, base=230.0, drift=0.0, wobble=0.5)
    item = charts.build_chart(bars, levels={"entry": (100.0, 110.0), "stop": 90.0,
                                            "target": 400.0})["ranges"][1]
    for level in item["levels"]:
        assert 0.0 <= percent(level["y"]) <= 100.0


def test_an_action_without_levels_draws_none_and_says_so():
    result = charts.build_chart(make_bars(400), levels=None, levels_note="此动作没有价位。")
    for item in result["ranges"]:
        assert item["levels"] == []
    assert result["levels_note"] == "此动作没有价位。"


def test_a_missing_level_is_omitted_rather_than_zero():
    result = charts.build_chart(make_bars(400, base=230.0, drift=0, wobble=1.0),
                                levels={"entry": None, "stop": 218.12, "target": None})
    keys = [level["key"] for level in result["ranges"][1]["levels"]]
    assert keys == ["stop"]


# -- label room -----------------------------------------------------------------

def _boxes(item, plot_height):
    height = charts.LABEL_HEIGHT_PX
    return [(level["label_top_px"], level["label_top_px"] + height)
            for level in item["levels"]] + (
        [(item["close_marker"]["label_top_px"],
          item["close_marker"]["label_top_px"] + height)] if item["close_marker"] else [])


@pytest.mark.parametrize("base", [4.2, 233.95, 4_500.0])
@pytest.mark.parametrize("spread", [0.0004, 0.002, 0.01, 0.08])
def test_labels_never_overlap_at_any_price_magnitude(base, spread):
    """Four labels whose prices sit `spread` apart, from a penny stock to an index."""
    bars = make_bars(400, base=base, drift=0.0, wobble=0.3)
    close = bars[-1].close
    levels = {"entry": (close * (1 - spread), close * (1 + spread)),
              "stop": close * (1 - 2 * spread), "target": close * (1 + 2 * spread)}
    result = charts.build_chart(bars, levels=levels, close_price=close,
                                close_date=bars[-1].date)
    for item in result["ranges"]:
        boxes = sorted(_boxes(item, result["plot_height_px"]))
        assert len(boxes) == 4
        for (top_a, bottom_a), (top_b, _bottom_b) in zip(boxes, boxes[1:]):
            assert top_b >= bottom_a + charts.LABEL_GAP_PX - 1e-6, boxes
        assert boxes[0][0] >= 0
        assert boxes[-1][1] <= result["plot_height_px"] + 1e-6


def test_the_label_spreader_keeps_order_and_stays_inside_the_plot():
    tops = charts.spread_labels([100.0, 101.0, 102.0, 255.0], 260, 20, 4)
    assert tops == sorted(tops)
    assert all(b - a >= 24 - 1e-9 for a, b in zip(tops, tops[1:]))
    assert tops[0] >= 0 and tops[-1] + 20 <= 260


# -- the close marker -------------------------------------------------------------

def test_the_close_marker_sits_at_the_cards_date_when_it_is_in_range():
    bars = make_bars(400, base=230.0, drift=0.0, wobble=1.0)
    day = bars[-10].date
    result = charts.build_chart(bars, close_price=233.95, close_date=day,
                                levels=LEVELS)
    marker = result["ranges"][0]["close_marker"]
    assert marker["label"] == "收盘价 233.95 美元/股"
    assert marker["date"] == day
    assert 0.0 < percent(marker["x"]) < 100.0


def test_the_close_marker_is_absent_and_explained_outside_the_range():
    bars = make_bars(1500, base=230.0, drift=0.0, wobble=1.0)
    day = bars[-300].date  # well over a year back: inside five years, outside three months
    result = charts.build_chart(bars, close_price=233.95, close_date=day)
    three_months = result["ranges"][0]
    assert three_months["close_marker"] is None
    assert day in three_months["close_note"]
    assert result["ranges"][2]["close_marker"] is not None


# -- the sparkline ----------------------------------------------------------------

def test_the_sparkline_is_a_short_normalised_path_that_ends_on_the_last_bar():
    bars = make_bars(300)
    spark = charts.sparkline(bars)
    assert spark["available"] is True
    assert spark["path"].startswith("M")
    assert 2 <= spark["count"] <= charts.SPARK_POINTS
    assert spark["end"]["x"] == "100.00"
    values = [float(token.split(",")[1]) for token in spark["path"][1:].split(" L")]
    assert all(0.0 <= value <= 100.0 for value in values)
    assert spark["end"]["y"] == f"{values[-1]:.2f}"
    assert spark["start_date"] < spark["end_date"] == bars[-1].date
    assert spark["label"].startswith("近半年")


def test_a_company_with_one_bar_has_no_sparkline():
    spark = charts.sparkline(make_bars(1))
    assert spark["available"] is False and spark["path"] is None


def test_a_flat_series_is_a_flat_line_not_a_division_by_zero():
    flat = [Bar(f"2026-0{month}-01", 10, 10, 10, 1) for month in range(1, 8)]
    spark = charts.sparkline(flat)
    assert spark["available"] is True
    assert len({token.split(",")[1] for token in spark["path"][1:].split(" L")}) == 1


# -- from the real price database -------------------------------------------------

@pytest.fixture(scope="module")
def fixture(tmp_path_factory):
    return build_fixture(tmp_path_factory.mktemp("chart-db") / "fixture")


def test_the_loader_charts_a_fresh_ticker_from_the_price_database(fixture):
    result = charts.price_chart(fixture.price_db, "AAPL")
    assert result["available"] is True
    assert result["ranges"][0]["points"][-1]["date"] == "2026-09-07"
    assert result["ranges"][1]["sma200"]["available"] is True


def test_the_loader_reports_a_ticker_with_no_prices(fixture):
    result = charts.price_chart(fixture.price_db, "MSFT")
    assert result["available"] is False
    assert result["reason"]


def test_a_stale_short_history_draws_what_it_can(fixture):
    # NVDA's fixture has 100 bars: a 50-day line, no 200-day line.
    result = charts.price_chart(fixture.price_db, "NVDA")
    five_years = result["ranges"][2]
    assert five_years["sma50"]["available"] is True
    assert five_years["sma200"]["available"] is False
    assert "历史不足" in five_years["sma200"]["note"]
