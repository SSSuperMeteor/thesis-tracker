"""Chat answer validation, rules C01-C03.

A chat answer is a template: the model writes prose and refers to numbers only
through placeholders.  That is what makes the three rules checkable without
parsing a model's arithmetic:

* **C01 naked numbers** — every digit in the body must be a placeholder, a date,
  a fiscal period, a time count, or a number the user themselves typed in the
  message being answered.  The rule itself is ``core.free_numbers``, the same
  one the card validator applies, so the two can never disagree.
* **C02 action words** — a trade instruction may only appear through a card
  placeholder.  The word list is ``core.chat_action_words`` and there is
  deliberately no negation escape: a simpler rule is a more provable one, and a
  chat answer has no reason to name an action it is not issuing.
* **C03 placeholder validity** — every ``{fact:...}`` and ``{card:...}`` must
  resolve inside *this conversation's* evidence set.  Another conversation, or
  another company, is a violation, not a lookup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from thesis_tracker.decision.core import chat_action_words

# A card placeholder adds the field: {card:<card_id>:<field>}.
FACT_PLACEHOLDER = re.compile(r"\{fact:([^{}]*)\}")
CARD_PLACEHOLDER = re.compile(r"\{card:([^{}]*)\}")
# Anything that looks like a placeholder but does not match the two exact forms.
LOOSE_PLACEHOLDER = re.compile(r"\{[^{}]*\}")

CARD_FIELDS = ("action", "tendency", "horizon", "entry_low", "entry_high",
               "stop_loss", "target_price", "created_at")


@dataclass(frozen=True)
class Evidence:
    """What one conversation may cite, and what counts as current for it."""

    facts: dict[str, dict] = field(default_factory=dict)
    cards: dict[str, dict] = field(default_factory=dict)
    latest_price_date: str | None = None

    def fact(self, fact_id: str) -> dict | None:
        return self.facts.get(fact_id)

    def card(self, card_id: str) -> dict | None:
        return self.cards.get(card_id)


def _violation(rule: str, location: str, message: str) -> dict:
    return {"rule": rule, "location": location, "message": message}


def _numbers_typed_by_the_user(user_text: str | None) -> set[str]:
    """The literal digit runs the user wrote, so an answer may quote them."""
    if not user_text:
        return set()
    return set(re.findall(r"\d+(?:[.,]\d+)?", user_text))


def _spans(pattern: re.Pattern[str], text: str) -> list[tuple[int, int]]:
    return [match.span() for match in pattern.finditer(text)]


def scan_placeholders(text: str) -> tuple[list[tuple[int, int, str]], list[dict]]:
    """Split the body into valid placeholder spans and rule-C03 violations.

    A ``{`` that never closes, or that opens before the previous one closed, is a
    malformed placeholder rather than prose: the model was trying to cite
    something and failed, and a partially typed fact id must not reach the reader
    as text.
    """
    spans: list[tuple[int, int, str]] = []
    violations: list[dict] = []
    cursor = 0
    while cursor < len(text):
        opening = text.find("{", cursor)
        if opening == -1:
            break
        closing = text.find("}", opening)
        if closing == -1:
            violations.append(_violation(
                "C03", f"offset {opening}", "占位符没有闭合的右花括号。"))
            break
        inner = text.find("{", opening + 1)
        if inner != -1 and inner < closing:
            violations.append(_violation(
                "C03", f"offset {opening}", "占位符里又出现了左花括号。"))
            cursor = opening + 1
            continue
        spans.append((opening, closing + 1, text[opening + 1:closing]))
        cursor = closing + 1
    return spans, violations


def _allowed_spans(text: str, placeholders: list[tuple[int, int, str]]
                   ) -> list[tuple[int, int]]:
    """Ranges the shared naked-number rule removes before it looks for numbers.

    This is the same set ``core.free_numbers`` strips, read from the same
    patterns: placeholders, ISO dates, fiscal periods and time counts.
    """
    spans = [(start, end) for start, end, _ in placeholders]
    spans += _spans(re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), text)
    spans += _spans(re.compile(r"\b(?:20\d{2}-)?Q[1-4]\b", re.I), text)
    spans += _spans(re.compile(r"\d+\s*个(?:季度|月|交易日|工作日|年度|年)"), text)
    return spans


def _free_number_spans(text: str, placeholders: list[tuple[int, int, str]]
                       ) -> list[tuple[int, int]]:
    """Bare numbers with their positions, decided by the shared rule."""
    from thesis_tracker.decision.core import NUMBER

    allowed = _allowed_spans(text, placeholders)
    spans: list[tuple[int, int]] = []
    for match in NUMBER.finditer(text):
        start, end = match.span()
        if any(start >= left and end <= right for left, right in allowed):
            continue
        spans.append((start, end))
    return spans


def validate_answer(body: str, *, evidence: Evidence,
                    user_text: str | None = None) -> list[dict]:
    """Every violation in one drafted answer.  Empty means it may be shown."""
    text = body or ""
    violations: list[dict] = []

    # -- C03: every citation must resolve inside this conversation -----------
    placeholders, malformed = scan_placeholders(text)
    violations.extend(malformed)
    for start, end, inner in placeholders:
        if inner.startswith("fact:"):
            fact_id = inner[len("fact:"):]
            if not fact_id:
                violations.append(_violation(
                    "C03", f"offset {start}", "事实占位符没有编号。"))
            elif evidence.fact(fact_id) is None:
                violations.append(_violation(
                    "C03", f"offset {start}",
                    f"编号 {fact_id} 不在这个对话的证据里；"
                    "只能引用本对话工具返回过的事实。"))
        elif inner.startswith("card:"):
            card_id, _, field_name = inner[len("card:"):].partition(":")
            if not card_id or not field_name:
                violations.append(_violation(
                    "C03", f"offset {start}",
                    "卡占位符必须写成 {card:<卡编号>:<字段>}。"))
                continue
            card = evidence.card(card_id)
            if card is None:
                violations.append(_violation(
                    "C03", f"offset {start}",
                    f"卡编号 {card_id} 不在这个对话读过的卡里。"))
            elif field_name not in CARD_FIELDS:
                violations.append(_violation(
                    "C03", f"offset {start}",
                    f"卡里没有字段 {field_name}；可用字段：{', '.join(CARD_FIELDS)}。"))
        else:
            violations.append(_violation(
                "C03", f"offset {start}",
                "无法识别的占位符；事实用 {fact:<编号>}，"
                "卡字段用 {card:<卡编号>:<字段>}。"))

    # -- C01: no naked numbers, except dates, periods, counts and the user's --
    typed = _numbers_typed_by_the_user(user_text)
    for start, end in _free_number_spans(text, placeholders):
        literal = text[start:end]
        if literal in typed or literal.replace(",", "") in typed:
            continue
        violations.append(_violation(
            "C01", f"offset {start}",
            f"正文里的数字 {literal} 必须写成占位符；"
            "事实用 {fact:<编号>}，卡字段用 {card:<卡编号>:<字段>}。"))

    # -- C02: a trade instruction may only be quoted from a card -------------
    for words in chat_action_words.values():
        for word in words:
            found = text.find(word)
            while found != -1:
                violations.append(_violation(
                    "C02", f"offset {found}",
                    f"正文里不能直接出现动作词“{word}”。"
                    "要引用卡上的动作请用 {card:<卡编号>:action}；"
                    "这个对话不自己发出交易指令。"))
                found = text.find(word, found + len(word))

    violations.sort(key=lambda item: (int(item["location"].split()[1]), item["rule"]))
    return violations
