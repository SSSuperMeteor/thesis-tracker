"""Chat answer validation C01-C03 and the placeholder renderer.

These are the rules that keep a conversation from becoming the one place the
project guesses: every number must arrive through a placeholder the conversation
can actually resolve, every trade instruction must be quoted from a card, and
nothing may be cited that this conversation never saw.
"""

from __future__ import annotations

import pytest

from thesis_tracker.webapp.chat.render import render_answer, render_display
from thesis_tracker.webapp.chat.validate import validate_answer

USER_TEXT = "现在能买吗？"

FACTS = {
    "tiingo|NVDA|2026-10-02|daily": {
        "fact_id": "tiingo|NVDA|2026-10-02|daily", "name": "close", "value": "233.95",
        "unit": "USD/share", "display": "233.95 美元/股", "date_or_period": "2026-10-02",
        "ticker": "NVDA", "category": "market",
        "source": {"provider": "tiingo"}},
    "tiingo|NVDA|2026-09-25|daily": {
        "fact_id": "tiingo|NVDA|2026-09-25|daily", "name": "close", "value": "230.10",
        "unit": "USD/share", "display": "230.10 美元/股", "date_or_period": "2026-09-25",
        "ticker": "NVDA", "category": "market",
        "source": {"provider": "tiingo"}},
    "derived|chat|abc123": {
        "fact_id": "derived|chat|abc123", "name": "compare_pct_change", "value": "0.0388",
        "unit": "percent", "display": "3.88%", "date_or_period": "2026-10-02",
        "ticker": "NVDA", "category": "derived",
        "source": {"provider": "derived", "formula": "(a - b) / b",
                   "source_fact_ids": ["a", "b"]}},
}

CARD_ID = "48f0260e-9c9d-428b-a6e0-2b6099b58410"
CARDS = {
    CARD_ID: {
        "card_id": CARD_ID, "action": "买入", "tendency": "看多", "horizon": "短期",
        "created_at": "2026-10-05T02:48:14+00:00",
        "fields": {
            "action": "买入", "tendency": "看多", "horizon": "短期",
            "entry_low": "228.00 美元/股", "entry_high": "236.00 美元/股",
            "stop_loss": "223.00 美元/股", "target_price": "252.00 美元/股",
            "created_at": "2026-10-04 19:48",
        }},
}


@pytest.fixture
def evidence():
    from thesis_tracker.webapp.chat.validate import Evidence

    return Evidence(facts=dict(FACTS), cards=dict(CARDS), latest_price_date="2026-10-02")


def rules(violations):
    return [item["rule"] for item in violations]


def test_a_clean_answer_passes(evidence):
    body = ("收盘价 {fact:tiingo|NVDA|2026-10-02|daily}，距离止损 "
            "{fact:derived|chat|abc123}。")
    assert validate_answer(body, evidence=evidence, user_text=USER_TEXT) == []


# -- C01 naked numbers -------------------------------------------------------

def test_a_bare_fact_number_is_refused(evidence):
    violations = validate_answer("收盘价是 233.95 美元/股。", evidence=evidence,
                                 user_text=USER_TEXT)
    assert rules(violations) == ["C01"]
    assert "占位符" in violations[0]["message"]


def test_a_number_the_user_typed_is_allowed(evidence):
    """The user's own words are not evidence, and must not be blocked.

    The answer may quote it back, but the user's number is not a fact: when the
    rendered text is built it carries no fact marker (see the render tests).
    """
    assert validate_answer("你问的 300 我无法确认。", evidence=evidence,
                           user_text="把目标价改成 300") == []
    assert validate_answer("你说的 300 我不知道。", evidence=evidence,
                           user_text="300 是多少") == []


def test_a_number_the_user_did_not_type_is_refused_even_if_not_a_fact(evidence):
    violations = validate_answer("大概还有 12 天。", evidence=evidence, user_text=USER_TEXT)
    assert rules(violations) == ["C01"]


def test_dates_fiscal_periods_and_time_counts_are_allowed(evidence):
    body = ("截至 2026-10-02，2026-Q3 的数据显示已经过去 3 个季度。")
    assert validate_answer(body, evidence=evidence, user_text=USER_TEXT) == []


def test_a_placeholder_is_not_a_naked_number(evidence):
    body = "止损距离 {fact:derived|chat|abc123}。"
    assert validate_answer(body, evidence=evidence, user_text=USER_TEXT) == []


def test_every_naked_number_is_reported_not_just_the_first(evidence):
    violations = validate_answer("233.95 和 230.10 都不对。", evidence=evidence,
                                 user_text=USER_TEXT)
    assert len(violations) == 2
    assert {item["rule"] for item in violations} == {"C01"}


# -- C02 action words --------------------------------------------------------

@pytest.mark.parametrize("word", ["买入", "分批", "分步", "加仓", "持有", "减仓",
                                  "卖出", "清仓", "离场", "回避", "观望"])
def test_a_bare_action_word_is_refused(evidence, word):
    violations = validate_answer(f"建议{word}。", evidence=evidence, user_text=USER_TEXT)
    assert "C02" in rules(violations)
    assert word in violations[0]["message"]


def test_an_action_word_inside_a_card_placeholder_is_allowed(evidence):
    body = "这张卡的动作是 {card:" + CARD_ID + ":action}。"
    assert validate_answer(body, evidence=evidence, user_text=USER_TEXT) == []


def test_a_negated_action_word_is_refused_too(evidence):
    """C02 keeps no negation escape: the rule stays simple and provable."""
    for phrase in ("不宜买入", "不要减仓", "避免观望", "分批而非一次性买入"):
        violations = validate_answer(f"现在{phrase}。", evidence=evidence,
                                     user_text=USER_TEXT)
        assert "C02" in rules(violations), phrase


def test_a_card_placeholder_carrying_an_action_word_covers_the_body(evidence):
    body = "卡上写 {card:" + CARD_ID + ":action}，我不替你决定。"
    assert validate_answer(body, evidence=evidence, user_text=USER_TEXT) == []


# -- C03 placeholder validity ------------------------------------------------

def test_an_unknown_fact_id_is_refused(evidence):
    violations = validate_answer("收盘价 {fact:tiingo|NVDA|2001-01-01|daily}。",
                                 evidence=evidence, user_text=USER_TEXT)
    assert rules(violations) == ["C03"]
    assert "证据" in violations[0]["message"]


def test_a_fact_from_another_conversation_is_refused(evidence):
    """This conversation's own evidence decides, not a bag of everything."""
    from thesis_tracker.webapp.chat.validate import Evidence

    other_fact = {"fact_id": "tiingo|AAPL|2026-10-02|daily", "name": "close",
                  "value": "333.69", "unit": "USD/share", "display": "333.69 美元/股",
                  "date_or_period": "2026-10-02", "ticker": "AAPL", "category": "market"}
    body = "AAPL 收盘价 {fact:tiingo|AAPL|2026-10-02|daily}。"
    assert rules(validate_answer(body, evidence=evidence, user_text=USER_TEXT)) == ["C03"]
    # The same body resolves once that fact is part of this conversation.
    widened = Evidence(facts={**evidence.facts, other_fact["fact_id"]: other_fact},
                       cards=dict(CARDS), latest_price_date="2026-10-02")
    assert validate_answer(body, evidence=widened, user_text=USER_TEXT) == []


def test_an_unknown_card_id_is_refused(evidence):
    violations = validate_answer("卡上写 {card:00000000-0000:action}。", evidence=evidence,
                                 user_text=USER_TEXT)
    assert "C03" in rules(violations)


def test_an_unknown_card_field_is_refused(evidence):
    violations = validate_answer("卡上写 {card:" + CARD_ID + ":buy_point}。",
                                 evidence=evidence, user_text=USER_TEXT)
    assert "C03" in rules(violations)
    assert "字段" in violations[0]["message"]


def test_a_malformed_placeholder_is_refused(evidence):
    for body in ("{fact:}", "{card:" + CARD_ID + "}", "{card::action}",
                 "{fact:tiingo|NVDA|2026-10-02|daily"):
        violations = validate_answer(body, evidence=evidence, user_text=USER_TEXT)
        assert violations, body
        assert "C03" in rules(violations), body


def test_all_three_rules_report_together(evidence):
    body = "建议买入，收盘价 233.95，另见 {fact:nope}。"
    found = set(rules(validate_answer(body, evidence=evidence, user_text=USER_TEXT)))
    assert found == {"C01", "C02", "C03"}


# -- rendering ---------------------------------------------------------------

def test_rendering_marks_a_card_value_as_a_judgment(evidence):
    body = "卡上动作是 {card:" + CARD_ID + ":action}，止损 {card:" + CARD_ID
    body += ":stop_loss}。"
    text, segments = render_answer(body, evidence=evidence)
    assert "AI 判断" in text
    assert CARD_ID[:8] in text
    assert "2026-10-04" in text
    assert [item["type"] for item in segments] == ["text", "card", "text", "card", "text"]
    assert segments[1]["card_id"] == CARD_ID
    assert segments[1]["field"] == "action"
    assert segments[1]["annotation"] == "AI 判断"


def test_rendering_annotates_a_fact_that_is_not_the_latest_price_day(evidence):
    body = "{fact:tiingo|NVDA|2026-09-25|daily}"
    text, _ = render_answer(body, evidence=evidence)
    assert "230.10 美元/股" in text
    assert "截至 2026-09-25" in text


def test_rendering_leaves_the_latest_price_day_unannotated(evidence):
    text, _ = render_answer("{fact:tiingo|NVDA|2026-10-02|daily}", evidence=evidence)
    assert text == "233.95 美元/股"
    assert "截至" not in text


def test_rendering_annotates_a_fundamental_fact_by_its_fiscal_period():
    from thesis_tracker.webapp.chat.validate import Evidence

    evidence = Evidence(facts={"sec_metric|NVDA|gross_margin_trend|2026-07-26|abc": {
        "fact_id": "sec_metric|NVDA|gross_margin_trend|2026-07-26|abc",
        "name": "gross_margin_trend", "value": "0.7498", "unit": "ratio",
        "display": "74.98%", "date_or_period": "2026-07-26", "ticker": "NVDA",
        "category": "fundamental"}}, cards={}, latest_price_date="2026-10-02")
    text, _ = render_answer("{fact:sec_metric|NVDA|gross_margin_trend|2026-07-26|abc}",
                            evidence=evidence)
    assert "74.98%" in text
    assert "截至 2026-07-26" in text


def test_the_rendered_segments_concatenate_to_the_rendered_text(evidence):
    body = ("最新收盘价 {fact:tiingo|NVDA|2026-10-02|daily}，"
            "较早收盘价 {fact:tiingo|NVDA|2026-09-25|daily}，"
            "卡上动作 {card:" + CARD_ID + ":action}。")
    text, segments = render_answer(body, evidence=evidence)
    # The segments are the whole answer: rendering each one reproduces the text.
    assert "".join(render_display(item) for item in segments) == text
    # The annotation also travels as its own field, so the page can style it
    # quietly instead of parsing it back out of the sentence.
    facts = [item for item in segments if item["type"] == "fact"]
    assert [item["display"] for item in facts] == ["233.95 美元/股", "230.10 美元/股"]
    assert facts[0]["annotation"] is None
    assert facts[1]["annotation"] == "截至"
    assert facts[1]["annotation_date"] == "2026-09-25"


def test_rendering_never_touches_a_number_it_was_not_given(evidence):
    text, segments = render_answer("没有占位符的句子。", evidence=evidence)
    assert text == "没有占位符的句子。"
    assert segments == [{"type": "text", "value": "没有占位符的句子。"}]
