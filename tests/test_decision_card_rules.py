"""D11 action consistency, D12 stop/target rationale, D13 horizon, and 观望."""

from __future__ import annotations

import copy
import socket

import pytest

from thesis_tracker.decision.core import (
    ACTION_WORDS,
    NEGATION_WINDOW,
    NEGATIONS,
    VALIDATOR_VERSION,
    build_card,
    fact_index,
    foreign_action_words,
    validate_card,
)
from thesis_tracker.decision.evidence import prepare_evidence

TICKER = "AAPL"
AS_OF = "2026-10-04"


@pytest.fixture
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


@pytest.fixture
def snapshot(offline):
    prepared, _, _ = prepare_evidence(TICKER, AS_OF)
    return prepared


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
        "stop_loss": 290, "target_price": 400, "fact_ids": [close, rsi, sma, high],
        "reasons": [{"text": "收盘价 {fact:" + close + "}，RSI {fact:" + rsi + "}。",
                     "fact_ids": [close, rsi]}],
        "invalidations": [{"kind": "close_below", "price": 290, "text": "收盘价跌破止损位"}],
        "stop_rationale": "跌破 {fact:" + sma + "} 即离场。",
        "target_rationale": "上看 {fact:" + high + "} 一线。",
    }
    base.update(overrides)
    return build_card(base, snapshot)


def codes(card, snapshot, **kwargs):
    return {item["rule"] for item in validate_card(card, snapshot, **kwargs)}


# ---------------------------------------------------------------- D11


def test_action_word_table_and_negation_window_are_fixed():
    assert ACTION_WORDS == {
        "买入": ("买入",),
        "分批": ("分批", "分步", "加仓"),
        "持有": ("持有",),
        "减仓": ("减仓", "卖出", "清仓", "离场"),
        "回避": ("回避",),
        "观望": ("观望",),
    }
    assert NEGATIONS == ("而非", "不宜", "避免", "不要", "勿")
    assert NEGATION_WINDOW == 6


def test_foreign_action_words_ignores_own_action_and_negated_words():
    assert foreign_action_words("采用分批建仓", "买入") == ["分批"]
    assert foreign_action_words("采用分批而非一次性买入", "买入") == ["分批"]
    assert foreign_action_words("不宜分批，直接买入", "买入") == []
    assert foreign_action_words("避免加仓与减仓", "买入") == []
    assert foreign_action_words("买入并持有", "买入") == ["持有"]


@pytest.mark.parametrize(("action", "text", "expected"), [
    ("买入", "故采用分批建仓。", True),
    ("买入", "建议减仓观望。", True),
    ("分批", "直接买入更合适。", True),
    ("持有", "可以分批加仓。", True),
    ("减仓", "应继续持有等待。", True),
    ("回避", "转为观望。", True),
    ("买入", "不宜分批，而是一次性买入。", False),
    ("买入", "采用分批而非一次性买入。", True),
    ("买入", "不宜分批。", False),
    ("买入", "不要加仓。", False),
    ("买入", "勿减仓。", False),
    ("持有", "继续持有并观察。", False),
])
def test_d11_reason_body_action_consistency(snapshot, action, text, expected):
    card = draft(snapshot, action=action, bias={"买入": "看多", "分批": "看多",
                                                "持有": "看多", "减仓": "看空",
                                                "回避": "看空"}[action],
                 entry_range=None if action in {"减仓", "回避"} else [300, 320],
                 stop_loss=None if action in {"减仓", "回避"} else 290,
                 target_price=None if action in {"减仓", "回避"} else 400,
                 stop_rationale="" if action in {"减仓", "回避"} else None,
                 target_rationale="" if action in {"减仓", "回避"} else None,
                 reasons=[{"text": text, "fact_ids": [fact_id(snapshot, "close")]}])
    if action in {"减仓", "回避"}:
        card = draft(snapshot, action=action, bias="看空", entry_range=None,
                     stop_loss=None, target_price=None,
                     reasons=[{"text": text, "fact_ids": [fact_id(snapshot, "close")]}])
        card.pop("stop_rationale"), card.pop("target_rationale")
    assert ("D11" in codes(card, snapshot)) is expected


def test_d11_ignores_invalidation_conditional_actions(snapshot):
    """Conditional actions inside invalidations are legal and must not be rejected."""
    card = draft(snapshot, invalidations=[
        {"kind": "close_below", "price": 290, "text": "跌破则转为回避。"},
        {"kind": "close_above", "price": 400, "text": "突破可上调为分批买入。"},
    ])
    assert "D11" not in codes(card, snapshot)


# ---------------------------------------------------------------- 观望


def test_watch_action_pairing_and_prices(snapshot):
    card = draft(snapshot, bias="中性", action="观望", entry_range=None,
                 stop_loss=None, target_price=None)
    card.pop("stop_rationale"), card.pop("target_rationale")
    assert codes(card, snapshot) == set()


def test_watch_with_prices_is_rejected(snapshot):
    card = draft(snapshot, bias="中性", action="观望", entry_range=[300, 320],
                 stop_loss=None, target_price=None)
    card.pop("stop_rationale"), card.pop("target_rationale")
    assert "D05" in codes(card, snapshot)


def test_watch_still_needs_a_machine_checkable_invalidation(snapshot):
    card = draft(snapshot, bias="中性", action="观望", entry_range=None,
                 stop_loss=None, target_price=None,
                 invalidations=[{"kind": "text", "price": None, "text": "情况变化"}])
    card.pop("stop_rationale"), card.pop("target_rationale")
    assert "D07" in codes(card, snapshot)


def test_watch_is_not_allowed_for_bullish_bias(snapshot):
    card = draft(snapshot, bias="看多", action="观望", entry_range=None,
                 stop_loss=None, target_price=None)
    card.pop("stop_rationale"), card.pop("target_rationale")
    assert "D04" in codes(card, snapshot)


# ---------------------------------------------------------------- D12


def test_d12_requires_rationale_with_a_fact_placeholder(snapshot):
    missing = draft(snapshot)
    missing.pop("stop_rationale")
    assert "D12" in codes(missing, snapshot)
    blank = draft(snapshot, stop_rationale="   ")
    assert "D12" in codes(blank, snapshot)
    no_fact = draft(snapshot, target_rationale="看向上方压力位。")
    assert "D12" in codes(no_fact, snapshot)


def test_d12_rationale_naked_numbers_use_d02(snapshot):
    card = draft(snapshot, stop_rationale="跌破 {fact:" + fact_id(snapshot, "sma_50") + "} 且 290 离场。")
    current = codes(card, snapshot)
    assert "D02" in current and "D12" not in current


@pytest.mark.parametrize("action", ["观望", "减仓", "回避"])
def test_d12_rationale_must_be_empty_for_non_long_actions(snapshot, action):
    bias = "中性" if action == "观望" else "看空"
    card = draft(snapshot, bias=bias, action=action, entry_range=None,
                 stop_loss=None, target_price=None)
    assert "D12" in codes(card, snapshot)


def test_d12_valid_card_has_no_violations(snapshot):
    assert validate_card(draft(snapshot), snapshot) == []


# ---------------------------------------------------------------- D13


def test_d13_card_horizon_must_equal_the_requested_horizon(snapshot):
    requested = copy.deepcopy(snapshot)
    requested["requested_horizon"] = "短期"
    assert "D13" in codes(draft(requested, horizon="中期"), requested)
    assert "D13" not in codes(draft(requested, horizon="短期"), requested)


def test_d13_is_skipped_when_the_snapshot_has_no_requested_horizon(snapshot):
    legacy = copy.deepcopy(snapshot)
    legacy.pop("requested_horizon", None)
    assert "requested_horizon" not in legacy
    assert "D13" not in codes(draft(legacy), legacy)


# ---------------------------------------------------------------- versioning


def test_old_cards_are_validated_by_version_and_keep_their_result(snapshot):
    legacy = draft(snapshot)
    legacy.pop("stop_rationale")
    legacy.pop("target_rationale")
    legacy["reasons"][0]["text"] = "故采用分批建仓。"
    strict = codes(legacy, snapshot)
    assert {"D12", "D11"} <= strict
    replay = codes(legacy, snapshot, version="decision-validator-1")
    assert "D12" not in replay
    assert "D11" in replay
    assert VALIDATOR_VERSION != "decision-validator-1"
