"""D14 entry must contain the current close, D15 stop must equal the close_below threshold."""

from __future__ import annotations

import pytest

from thesis_tracker.decision.core import (
    build_card,
    fact_index,
    render_card,
    validate_card,
)
from thesis_tracker.decision.evidence import prepare_evidence

TICKER = "AAPL"
AS_OF = "2026-10-04"          # latest original close for AAPL is 333.69 on 2026-10-02
CLOSE = 333.69


@pytest.fixture(scope="module")
def snapshot():
    prepared, _, _ = prepare_evidence(TICKER, AS_OF)
    return prepared


def fact_id(snapshot, name):
    index, _ = fact_index(snapshot)
    return next(item["fact_id"] for item in index.values() if item["name"] == name)


def draft(snapshot, **overrides):
    close = fact_id(snapshot, "close")
    sma = fact_id(snapshot, "sma_50")
    high = fact_id(snapshot, "high_52w")
    base = {
        "ticker": TICKER, "as_of": AS_OF, "horizon": "中期", "bias": "看多",
        "action": "买入", "confidence": "中", "entry_range": [324, 334],
        "stop_loss": 319, "target_price": 400,
        "fact_ids": [close, sma, high],
        "reasons": [{"text": "收盘价 {fact:" + close + "}", "fact_ids": [close]}],
        "invalidations": [{"kind": "close_below", "price": 319, "text": "跌破止损位"}],
        "stop_rationale": "跌破 {fact:" + sma + "} 离场。",
        "target_rationale": "上看 {fact:" + high + "} 一线。",
    }
    base.update(overrides)
    return build_card(base, snapshot)


def codes(card, snapshot, **kwargs):
    return {item["rule"] for item in validate_card(card, snapshot, **kwargs)}


# ------------------------------------------------------------------ D14


def test_d14_applies_to_buy_and_scale_in(snapshot):
    for action in ("买入", "分批"):
        card = draft(snapshot, action=action, entry_range=[330, 340])
        assert "D14" not in codes(card, snapshot), action
        assert "D15" not in codes(card, snapshot), action


@pytest.mark.parametrize("entry_range", [[333.69, 340], [300, 333.69]])
def test_d14_accepts_both_endpoints_inclusively(snapshot, entry_range):
    assert "D14" not in codes(draft(snapshot, entry_range=entry_range), snapshot)


@pytest.mark.parametrize(("entry_range", "why"), [
    ([310, 320], "close above the upper bound"),
    ([340, 350], "close below the lower bound"),
])
def test_d14_rejects_a_close_outside_the_entry_range(snapshot, entry_range, why):
    assert "D14" in codes(draft(snapshot, entry_range=entry_range), snapshot), why


@pytest.mark.parametrize(("action", "bias"), [("持有", "看多"), ("观望", "中性"),
                                              ("减仓", "看空"), ("回避", "看空")])
def test_d14_does_not_apply_without_an_entry_range(snapshot, action, bias):
    card = draft(snapshot, action=action, bias=bias, entry_range=None,
                 stop_loss=319 if action == "持有" else None,
                 target_price=400 if action == "持有" else None)
    if action != "持有":
        card.pop("stop_rationale"), card.pop("target_rationale")
        card["invalidations"] = [{"kind": "close_below", "price": 319, "text": "跌破"}]
    assert "D14" not in codes(card, snapshot)


# ------------------------------------------------------------------ D15


def test_d15_requires_a_close_below_equal_to_stop_loss(snapshot):
    good = draft(snapshot, stop_loss=319,
                 invalidations=[{"kind": "close_below", "price": 319, "text": "跌破"}])
    assert "D15" not in codes(good, snapshot)
    mismatched = draft(snapshot, stop_loss=319,
                       invalidations=[{"kind": "close_below", "price": 315, "text": "跌破"}])
    assert "D15" in codes(mismatched, snapshot)
    above_only = draft(snapshot, stop_loss=319,
                       invalidations=[{"kind": "close_above", "price": 319, "text": "站上"}])
    assert "D15" in codes(above_only, snapshot)


def test_d15_matches_any_one_close_below_among_several(snapshot):
    card = draft(snapshot, stop_loss=319, invalidations=[
        {"kind": "close_above", "price": 380, "text": "站上压力位"},
        {"kind": "close_below", "price": 300, "text": "更深的支撑"},
        {"kind": "close_below", "price": 319, "text": "跌破止损位"},
    ])
    assert "D15" not in codes(card, snapshot)


@pytest.mark.parametrize(("action", "bias"), [("持有", "看多"), ("分批", "看多")])
def test_d15_applies_to_hold_and_scale_in(snapshot, action, bias):
    card = draft(snapshot, action=action, bias=bias,
                 entry_range=None if action == "持有" else [324, 334],
                 stop_loss=319,
                 invalidations=[{"kind": "close_below", "price": 300, "text": "跌破"}])
    assert "D15" in codes(card, snapshot)


def test_d15_does_not_apply_to_watch_reduce_or_avoid(snapshot):
    for action, bias in (("观望", "中性"), ("减仓", "看空"), ("回避", "看空")):
        card = draft(snapshot, action=action, bias=bias, entry_range=None,
                     stop_loss=None, target_price=None)
        card.pop("stop_rationale"), card.pop("target_rationale")
        card["invalidations"] = [{"kind": "close_below", "price": 300, "text": "跌破"}]
        assert "D15" not in codes(card, snapshot), action


# ------------------------------------------------------------------ versioning


def test_d14_and_d15_are_skipped_for_v1_and_v2_replay(snapshot):
    card = draft(snapshot, entry_range=[310, 320])
    assert {"D14"} <= codes(card, snapshot)
    for version in ("decision-validator-1", "decision-validator-2"):
        replayed = codes(card, snapshot, version=version)
        assert "D14" not in replayed and "D15" not in replayed, version


# ------------------------------------------------------------------ rendering


def test_invalidation_renders_machine_check_then_explanation(snapshot):
    card = draft(snapshot, stop_loss=319, invalidations=[
        {"kind": "close_below", "price": 319, "text": "跌破止损位。"},
        {"kind": "close_above", "price": 380, "text": "站上压力位。"},
    ])
    rendered = render_card(card, snapshot)
    assert "机器检查：收盘价跌破 319.00 美元/股" in rendered
    assert "机器检查：收盘价站上 380.00 美元/股" in rendered
    assert "说明：跌破止损位。" in rendered
    assert "说明：站上压力位。" in rendered


def test_header_shows_the_price_data_date(snapshot):
    assert "价格数据截至 2026-10-02" in render_card(draft(snapshot), snapshot)
