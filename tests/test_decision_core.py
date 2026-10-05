"""Decision cards are validated against the captured tool responses."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from datetime import date

import pytest
from webapp_fixtures import guard_offline

from thesis_tracker.decision.cli import main as snapshot_main
from thesis_tracker.decision.core import (
    DISCLAIMER,
    append_card,
    build_card,
    capture_snapshot,
    read_card,
    render_card,
    validate_card,
)


@pytest.fixture
def offline(monkeypatch):
    guard_offline(monkeypatch)


@pytest.fixture
def snapshot(offline):
    return capture_snapshot("AAPL", "2026-10-04")


@pytest.fixture
def card(snapshot):
    rsi = snapshot["fact_index"]["tiingo|AAPL|2026-10-02|indicator|rsi_14"]
    close = snapshot["fact_index"]["tiingo|AAPL|2026-10-02|daily"]
    assert rsi["value"] == 54.73864972417355
    return build_card({
        "ticker": "AAPL", "as_of": "2026-10-04", "horizon": "中期",
        "bias": "看多", "action": "买入", "confidence": "中",
        "entry_range": [324, 334], "stop_loss": 290, "target_price": 400,
        "fact_ids": [rsi["fact_id"], close["fact_id"]],
        "reasons": [{"text": "RSI 为 {fact:" + rsi["fact_id"] + "}，观察 3 个季度。",
                     "fact_ids": [rsi["fact_id"]]}],
        "invalidations": [{"kind": "close_below", "price": 290,
                           "text": "收盘价跌破止损位"}],
        "stop_rationale": "RSI {fact:" + rsi["fact_id"] + "} 走弱则离场。",
        "target_rationale": "上看 {fact:" + close["fact_id"] + "} 上方。",
    }, snapshot)


def codes(card, snapshot):
    return {item["rule"] for item in validate_card(card, snapshot)}


def test_valid_card_fills_exact_snapshot_facts_and_renders_display_values(card, snapshot):
    assert validate_card(card, snapshot) == []
    assert card["creation_price"] == 333.69
    assert card["disclaimer"] == DISCLAIMER
    assert card["confidence_calibration"] == "未校准"
    for fact in card["facts"]:
        assert fact["value"] == snapshot["fact_index"][fact["fact_id"]]["value"]
        assert str(fact["display"]) in render_card(card, snapshot)


@pytest.mark.parametrize(("mutation", "rule"), [
    (lambda c: c["facts"][0].update(value=0), "D01"),
    (lambda c: c["reasons"][0].update(text="RSI 为 54.73864972417355"), "D02"),
    (lambda c: c["fact_ids"].append("missing|fact"), "D01"),
    (lambda c: c["fact_ids"].append("tiingo|NVDA|2026-10-02|daily"), "D01"),
    (lambda c: c["fact_ids"].append("tiingo|AAPL|2026-05-01|daily"), "D01"),
    (lambda c: c.update(bias="看空"), "D04"),
    (lambda c: c.update(stop_loss=330), "D05"),
    (lambda c: c["gaps"].pop(), "D06"),
    (lambda c: c.update(invalidations=[{"kind": "text", "text": "情况变化"}]), "D07"),
    (lambda c: c.update(disclaimer="改过的声明"), "D10"),
    (lambda c: c.update(horizon="超长期"), "D08"),
    (lambda c: c.update(confidence="很高"), "D08"),
    (lambda c: c["reasons"][0].update(fact_ids=[]), "D09"),
])
def test_adversarial_mutations_are_rejected(card, snapshot, mutation, rule):
    changed = copy.deepcopy(card)
    mutation(changed)
    assert rule in codes(changed, snapshot)


def test_stale_or_future_tool_data_rejected(card, snapshot):
    stale = copy.deepcopy(snapshot)
    stale["calls"][0]["envelope"]["data"]["data_end_date"] = "2026-09-20"
    assert "D03" in codes(card, stale)
    future = copy.deepcopy(snapshot)
    future["calls"][1]["envelope"]["data"]["data_end_date"] = "2026-10-05"
    assert "D03" in codes(card, future)
    malformed = copy.deepcopy(snapshot)
    malformed["calls"][0]["envelope"]["data"]["data_end_date"] = "not-a-date"
    assert "D03" in codes(card, malformed)


def test_unavailable_price_rejected(card, snapshot):
    changed = copy.deepcopy(snapshot)
    changed["calls"][0]["envelope"]["status"] = "unavailable"
    assert "D03" in codes(card, changed)


def test_all_violations_have_rule_location_and_plain_message(card, snapshot):
    card["facts"][0]["value"] = 0
    card["bias"] = "看空"
    card["disclaimer"] = ""
    violations = validate_card(card, snapshot)
    assert {"D01", "D04", "D10"} <= {item["rule"] for item in violations}
    assert all(item["location"] and item["message"] for item in violations)


def test_archive_is_append_only_and_reproducible(card, snapshot, tmp_path):
    path = tmp_path / "cards.db"
    card_id = append_card(path, card, snapshot)
    loaded = read_card(path, card_id)
    assert loaded["snapshot_sha256"] == hashlib.sha256(
        loaded["snapshot_json"].encode("utf-8")
    ).hexdigest()
    assert loaded["creation_price"] == "333.69"
    assert validate_card(loaded["card"], json.loads(loaded["snapshot_json"])) == loaded["validation_result"]
    with pytest.raises(Exception, match="immutable"):
        with sqlite3.connect(path) as conn:
            conn.execute("DELETE FROM decision_cards WHERE card_id=?", (card_id,))
    with pytest.raises(Exception, match="immutable"):
        with sqlite3.connect(path) as conn:
            conn.execute("UPDATE decision_cards SET ticker='NVDA' WHERE card_id=?", (card_id,))
    assert read_card(path, card_id)["card"] == card


@pytest.mark.parametrize(("bias", "action", "valid"), [
    ("看多", "买入", True), ("看多", "分批", True), ("看多", "持有", True),
    ("看多", "减仓", False), ("看多", "回避", False),
    ("中性", "分批", True), ("中性", "持有", True), ("中性", "回避", True),
    ("中性", "观望", True), ("中性", "买入", False), ("中性", "减仓", False),
    ("看空", "减仓", True), ("看空", "回避", True), ("看空", "观望", False),
    ("看多", "观望", False),
    ("看空", "买入", False), ("看空", "分批", False), ("看空", "持有", False),
])
def test_bias_action_matrix(card, snapshot, bias, action, valid):
    card["bias"], card["action"] = bias, action
    if action == "持有":
        card["entry_range"] = None
        card["stop_loss"] = 290
        card["target_price"] = 400
    elif action in {"观望", "减仓", "回避"}:
        card["entry_range"] = None
        card["stop_loss"] = None
        card["target_price"] = None
    assert ("D04" not in codes(card, snapshot)) is valid


def test_hold_and_avoid_price_rules(card, snapshot):
    card.update(action="持有", entry_range=None, stop_loss=290, target_price=400)
    assert "D05" not in codes(card, snapshot)
    card["stop_loss"] = 400
    assert "D05" in codes(card, snapshot)
    card.update(bias="看空", action="回避", stop_loss=None, target_price=None)
    assert "D05" not in codes(card, snapshot)
    card["target_price"] = 1
    assert "D05" in codes(card, snapshot)


def test_every_invalidation_price_must_be_positive(card, snapshot):
    card["invalidations"].append({"kind": "close_above", "price": -1, "text": "突破"})
    assert "D07" in codes(card, snapshot)


def test_numeric_whitelist_allows_dates_periods_counts_but_not_fact_value(card, snapshot):
    card["reasons"][0]["text"] += "2026-10-04、2026-Q3、3 个季度。"
    assert "D02" not in codes(card, snapshot)
    card["reasons"][0]["text"] += "54.73864972417355。"
    assert "D02" in codes(card, snapshot)


def test_other_bare_number_is_rejected_even_if_not_a_snapshot_value(card, snapshot):
    card["reasons"][0]["text"] += "参考 42。"
    assert "D02" in codes(card, snapshot)


def test_snapshot_cli_prints_facts_and_gaps_without_judgment(offline, capsys):
    assert snapshot_main(["AAPL", "--as-of", "2026-10-04"]) == 0
    output = capsys.readouterr().out
    assert "rsi_14" in output and "net_buyback_yield" in output
    assert "数据缺口" in output and "事实表" in output
    assert "AI 判断" not in output


def test_snapshot_uses_only_local_tools_no_llm(monkeypatch, offline):
    import openai

    def denied(*args, **kwargs):
        raise AssertionError("LLM call attempted")

    monkeypatch.setattr(openai, "OpenAI", denied)
    result = capture_snapshot("NVDA", date(2026, 10, 4).isoformat())
    assert [call["tool"] for call in result["calls"]] == [
        "get_price_history", "get_indicators", "get_fundamental_metrics"]
