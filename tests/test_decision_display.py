"""Card display rules: Chinese units, per-metric ratio formatting, auto-computed block."""

from __future__ import annotations

import json

import pytest
from webapp_fixtures import guard_offline

from thesis_tracker.decision.core import (
    auto_computed,
    build_card,
    fact_index,
    render_card,
    validate_card,
)
from thesis_tracker.decision.evidence import (
    METRIC_LABELS,
    MULTIPLE_METRICS,
    PERCENT_METRICS,
    POINTS_METRICS,
    display_label,
    display_text,
    display_value,
    fact_category,
    metric_label,
    prepare_evidence,
)

TICKER = "AAPL"
AS_OF = "2026-10-04"


def fact_id(snapshot, name):
    index, _ = fact_index(snapshot)
    return next(item["fact_id"] for item in index.values() if item["name"] == name)


def draft(snapshot, **overrides):
    close = fact_id(snapshot, "close")
    rsi = fact_id(snapshot, "rsi_14")
    sma = fact_id(snapshot, "sma_50")
    high = fact_id(snapshot, "high_52w")
    base = {
        "ticker": TICKER, "as_of": AS_OF, "horizon": "中期", "bias": "看多",
        "action": "买入", "confidence": "中", "entry_range": [324, 334],
        "stop_loss": 319, "target_price": 400, "fact_ids": [close, rsi, sma, high],
        "reasons": [{"text": "收盘价 {fact:" + close + "}，RSI {fact:" + rsi + "}。",
                     "fact_ids": [close, rsi]}],
        "invalidations": [{"kind": "close_below", "price": 319, "text": "收盘价跌破止损位"}],
        "stop_rationale": "跌破 {fact:" + sma + "} 即离场。",
        "target_rationale": "上看 {fact:" + high + "} 一线。",
    }
    base.update(overrides)
    return build_card(base, snapshot)


@pytest.fixture
def offline(monkeypatch):
    guard_offline(monkeypatch)


@pytest.fixture(scope="module")
def snapshot():
    prepared, _, _ = prepare_evidence(TICKER, AS_OF)
    return prepared


# ------------------------------------------------------------ unit + ratio rules


@pytest.mark.parametrize(("value", "unit", "name", "expected"), [
    (333.69, "USD/share", "close", "333.69 美元/股"),
    (-3.373486998320485, "percent", "distance_to_high_252_percent", "-3.37%"),
    (14.610869088811684, "percentage points", "relative_spy_126", "14.61 个百分点"),
    (54.73864972417355, "index (0-100)", "rsi_14", "54.7"),
    (0.5005620698794520047159033788, "ratio", "gross_margin_trend", "50.06%"),
    (-0.01214293668174370449712864620, "ratio", "accruals_ratio", "-1.21%"),
    (-0.01562083247732048164528936936, "ratio", "diluted_share_count_yoy", "-1.56%"),
    (1.153748027795494981368961697, "ratio", "cash_conversion", "1.15 倍"),
    (1.095886197616301422529796232, "ratio", "net_debt_to_ebitda", "1.10 倍"),
    (280.7665198237885462555066079, "ratio", "interest_coverage", "280.77 倍"),
    (1.4514546982965382, "ratio", "volume_ratio_20", "1.45 倍"),
])
def test_display_text_formats_units_and_ratio_classes(value, unit, name, expected):
    assert display_text(value, unit, name=name) == expected


def test_unknown_metric_and_unit_fall_back_to_the_original_name():
    assert display_text("1.234567890123456789", "ratio", name="unknown_metric") == "1.2346"
    assert display_label("unknown_metric") == "unknown_metric"
    assert display_text(1.5, "widgets", name="unknown_metric") == "1.5000 widgets"


def test_metric_label_mapping_is_chinese_with_unknown_fallback():
    assert METRIC_LABELS["gross_margin_trend"] == "毛利率趋势"
    assert METRIC_LABELS["accruals_ratio"] == "应计比率"
    assert METRIC_LABELS["cash_conversion"] == "现金转换"
    assert METRIC_LABELS["net_debt_to_ebitda"] == "净债务/EBITDA"
    assert METRIC_LABELS["interest_coverage"] == "利息保障倍数"
    assert METRIC_LABELS["diluted_share_count_yoy"] == "摊薄股本同比"
    assert metric_label("sma_200") == "200日均线"
    assert metric_label("totally_unknown_metric") == "totally_unknown_metric"


def test_same_metric_across_periods_appends_its_own_period():
    assert display_label("gross_margin_trend", "2026-06-27", multiple=True) == \
        "毛利率趋势（财期截止 2026-06-27）"
    assert display_label("gross_margin_trend", "2026-06-27", multiple=False) == "毛利率趋势"
    assert display_label("sma_200", "2026-10-02", multiple=True) == "200日均线（日期 2026-10-02）"
    assert display_label("sma_200", "2026-10-02", multiple=True,
                         category="market") == "200日均线（日期 2026-10-02）"
    assert display_label("gross_margin_trend", "2026-06-27", multiple=True,
                         category="fundamental") == "毛利率趋势（财期截止 2026-06-27）"


def test_ratio_class_sets_are_exactly_the_documented_ones():
    assert PERCENT_METRICS == {"gross_margin_trend", "accruals_ratio", "diluted_share_count_yoy"}
    assert MULTIPLE_METRICS == {"cash_conversion", "net_debt_to_ebitda",
                                "interest_coverage", "volume_ratio_20"}


def test_number_only_display_stays_the_model_facing_primitive(snapshot):
    assert display_value(333.695, "USD/share") == "333.70"
    assert display_value("1.234567890123456789", "ratio") == "1.2346"
    assert display_value(None, "ratio") is None


# ------------------------------------------------------------ naked numbers


@pytest.mark.parametrize("text", [
    "毛利率 50.06% 已经改善。",
    "净债务/EBITDA 为 1.10 倍。",
    "收盘价 333.69 美元/股。",
    "RSI 为 54.7。",
])
def test_naked_display_numbers_are_rejected(snapshot, text):
    card = draft(snapshot, reasons=[{"text": text, "fact_ids": [fact_id(snapshot, "close")]}])
    assert "D02" in {item["rule"] for item in validate_card(card, snapshot)}


def test_placeholders_render_with_the_same_formatter(snapshot):
    close = fact_id(snapshot, "close")
    gross = fact_id(snapshot, "gross_margin_trend")
    card = draft(snapshot, fact_ids=[close, gross, fact_id(snapshot, "sma_50"),
                                    fact_id(snapshot, "high_52w")],
                 reasons=[{"text": "收盘 {fact:" + close + "}，毛利率 {fact:" + gross + "}。",
                           "fact_ids": [close, gross]}])
    assert validate_card(card, snapshot) == []
    rendered = render_card(card, snapshot)
    assert "333.69 美元/股" in rendered
    assert "50.06%" in rendered


# ------------------------------------------------------------ auto-computed block


def test_auto_computed_uses_entry_midpoint_for_buy(snapshot):
    card = draft(snapshot, action="买入", entry_range=[300, 320], stop_loss=290, target_price=400)
    items = {item["label"]: item["text"] for item in auto_computed(card, snapshot)}
    assert items["止损距离"].startswith("6.45%")
    assert "2.84 倍 ATR" in items["止损距离"]
    assert items["目标距离"].startswith("29.03%")
    assert "倍 ATR" in items["目标距离"]
    assert items["盈亏比"] == "4.5"
    assert items["距52周高点"] == "低于 52 周高点 3.37%"


def test_auto_computed_uses_close_for_hold(snapshot):
    card = draft(snapshot, action="持有", entry_range=None, stop_loss=290, target_price=400)
    items = {item["label"]: item["text"] for item in auto_computed(card, snapshot)}
    assert items["止损距离"].startswith("13.09%")
    assert items["盈亏比"] == "1.5"


def price_only_snapshot():
    """A snapshot with a close price but no ATR and no derived landmarks."""
    envelope = {"status": "ok", "as_of": AS_OF, "fact_id": "test|close",
                "data": {"data_end_date": AS_OF,
                         "latest_close": {"value": 333.69, "unit": "USD/share"},
                         "rows": [{"date": AS_OF, "fact_id": "test|close",
                                   "close": {"value": 333.69, "unit": "USD/share"}}]}}
    return {"ticker": TICKER, "as_of": AS_OF,
            "calls": [{"tool": "get_price_history",
                       "args": {"symbol": TICKER, "as_of": AS_OF}, "envelope": envelope}]}


def test_auto_computed_says_why_when_a_fact_is_missing():
    snapshot = price_only_snapshot()
    card = {"action": "买入", "entry_range": [300, 320], "stop_loss": 290,
            "target_price": 400, "creation_price": 333.69}
    items = {item["label"]: item["text"] for item in auto_computed(card, snapshot)}
    assert items["止损距离"] == "6.45%"
    assert "ATR" not in items["止损距离"]
    assert items["距52周高点"].startswith("无法计算")


@pytest.mark.parametrize("action", ["观望", "减仓", "回避"])
def test_auto_computed_reports_no_entry_for_non_long_actions(snapshot, action):
    bias = "中性" if action == "观望" else "看空"
    card = draft(snapshot, bias=bias, action=action, entry_range=None,
                 stop_loss=None, target_price=None)
    items = {item["label"]: item["text"] for item in auto_computed(card, snapshot)}
    assert list(items) == ["距52周高点"], (action, items)
    assert items["距52周高点"] == "低于 52 周高点 3.37%"


def test_auto_computed_is_display_only_and_does_not_rescue_a_bad_card(snapshot):
    card = draft(snapshot, action="买入", entry_range=[300, 320], stop_loss=400, target_price=400)
    rules = {item["rule"] for item in validate_card(card, snapshot)}
    assert "D05" in rules
    with pytest.raises(ValueError):
        render_card(card, snapshot)


def test_render_card_contains_the_auto_computed_section(snapshot):
    card = draft(snapshot)
    rendered = render_card(card, snapshot)
    assert "自动计算（Python）" in rendered
    assert "止损距离" in rendered and "盈亏比" in rendered


def test_rendered_card_lists_each_fact_with_its_chinese_label(snapshot):
    index, _ = fact_index(snapshot)
    gross = [item["fact_id"] for item in index.values() if item["name"] == "gross_margin_trend"]
    close = fact_id(snapshot, "close")
    card = draft(snapshot, fact_ids=[close, *gross, fact_id(snapshot, "sma_50"),
                                     fact_id(snapshot, "high_52w")],
                 reasons=[{"text": "收盘 {fact:" + close + "}", "fact_ids": [close]}])
    rendered = render_card(card, snapshot)
    assert "毛利率趋势" in rendered
    if len(gross) > 1:
        assert "财期截止" in rendered
    assert json.dumps(card, ensure_ascii=False)  # card stays JSON-serializable


# ------------------------------------------------------------ v3 label coverage


ALL_NAMES = [
    "accruals_ratio", "adjusted_close", "ar_growth_vs_rev_growth", "atr_14",
    "avg_volume_20", "bollinger_lower_20", "bollinger_middle_20",
    "bollinger_upper_20", "cash_conversion", "close",
    "diluted_share_count_yoy", "distance_to_high_20_percent",
    "distance_to_high_252_percent", "distance_to_high_60_percent",
    "distance_to_low_20_percent", "distance_to_low_252_percent",
    "distance_to_low_60_percent", "ema_12", "ema_26", "gross_margin_trend",
    "high_3y", "high_52w", "high_5y", "interest_coverage", "low_3y", "low_52w",
    "low_5y", "macd_histogram", "macd_line", "macd_signal",
    "net_buyback_yield", "net_debt_to_ebitda", "position_52w", "range_high_20",
    "range_high_252", "range_high_60", "range_low_20", "range_low_252",
    "range_low_60", "relative_spy_126", "relative_spy_21", "relative_spy_252",
    "relative_spy_63", "return_1m", "return_1y", "return_3m", "return_6m",
    "rsi_14", "sma_20", "sma_200", "sma_50", "volume_ratio_20",
]


@pytest.mark.parametrize("name", ALL_NAMES)
def test_every_known_fact_name_has_a_label_without_snake_case(name):
    label = metric_label(name)
    assert label != name, name
    assert "_" not in label, (name, label)


def test_unknown_names_still_fall_back_to_the_raw_name():
    assert metric_label("brand_new_metric") == "brand_new_metric"
    assert display_label("brand_new_metric") == "brand_new_metric"


def test_ar_growth_is_a_difference_of_two_growth_rates_in_percentage_points():
    assert POINTS_METRICS == {"ar_growth_vs_rev_growth"}
    assert display_text(0.209145354545865676348356080, "ratio",
                        name="ar_growth_vs_rev_growth") == "20.91 个百分点"
    # a ratio whose percentage is not covered still falls back to the raw number
    assert display_text(-0.0121, "ratio", name="cash_conversion") == "-0.01 倍"


def test_fact_category_reads_metadata_before_falling_back_to_the_id():
    assert fact_category({"fact_id": "sec_metric|X|gross_margin_trend|2026-06-27|aa",
                          "source": {"provider": "sec_filing_xbrl"}}) == "fundamental"
    assert fact_category({"fact_id": "tiingo|X|2026-10-02|daily",
                          "source": {"provider": "tiingo"}}) == "market"
    assert fact_category({"fact_id": "derived|X|high_52w|2026-09-22|aa",
                          "source": {"formula": "max(high)"}}) == "derived"
    assert fact_category({"fact_id": "sec_metric|X|accruals_ratio|2026-06-27|aa"}) == "fundamental"
    assert fact_category({"fact_id": "tiingo|X|2026-10-02|indicator|sma_20"}) == "market"
    assert fact_category({"fact_id": "derived|X|position_52w|2026-10-02|aa"}) == "derived"
    assert fact_category({"fact_id": "unknown|X"}) == "market"


def test_auto_computed_adds_the_target_atr_multiple(snapshot):
    card = draft(snapshot)
    items = {item["label"]: item["text"] for item in auto_computed(card, snapshot)}
    assert items["目标距离"].startswith("21.58%")
    assert "倍 ATR" in items["目标距离"]


@pytest.mark.parametrize("action", ["观望", "减仓", "回避"])
def test_auto_computed_for_no_entry_actions_only_shows_the_landmark(snapshot, action):
    bias = "中性" if action == "观望" else "看空"
    card = draft(snapshot, bias=bias, action=action, entry_range=None,
                 stop_loss=None, target_price=None)
    labels = [item["label"] for item in auto_computed(card, snapshot)]
    assert labels == ["距52周高点"]
