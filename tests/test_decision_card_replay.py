"""Replay the three archived real cards: D11 under v1 rules and the new display."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from thesis_tracker.decision.core import fact_index, render_card, validate_card
from thesis_tracker.decision.evidence import display_text, metric_label

ARCHIVE = Path("data/decisions/cards.db")
CARDS = {
    "AAPL": ("68405fbc-8bb9-4b03-8885-fa82c88338a8", "买入", True),
    "NVDA": ("f904ecd9-ce6f-412c-bd8f-95aa5c4b7cca", "买入", True),
    "TSLA": ("241be83d-8cf9-46e8-9daf-d58abce445e1", "持有", False),
}


def archived(ticker):
    if not ARCHIVE.is_file():
        pytest.skip(f"{ARCHIVE} is local runtime data and is not present")
    connection = sqlite3.connect(f"file:{ARCHIVE}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    card_id, _, _ = CARDS[ticker]
    row = connection.execute(
        "SELECT card_json, snapshot_json, validator_version FROM decision_cards WHERE card_id=?",
        (card_id,)).fetchone()
    if row is None:
        pytest.skip(f"archived card {card_id} is not present")
    return (json.loads(row["card_json"]), json.loads(row["snapshot_json"]),
            row["validator_version"])


@pytest.mark.parametrize("ticker", sorted(CARDS))
def test_archived_cards_were_written_by_validator_v1(ticker):
    _, _, version = archived(ticker)
    assert version == "decision-validator-1"


@pytest.mark.parametrize("ticker", sorted(CARDS))
def test_replay_d11_on_archived_cards_matches_the_real_violations(ticker):
    card, snapshot, _ = archived(ticker)
    _, action, expected = CARDS[ticker]
    assert card["action"] == action
    rules = {item["rule"] for item in validate_card(card, snapshot,
                                                    version="decision-validator-1")}
    assert ("D11" in rules) is expected, sorted(rules)


def test_archived_cards_render_with_the_new_display_rules():
    card, _, _ = archived("AAPL")
    facts = {item["name"]: item for item in card["facts"]}
    assert display_text(facts["close"]["value"], facts["close"]["unit"],
                        name="close") == "333.69 美元/股"
    assert display_text(facts["gross_margin_trend"]["value"],
                        facts["gross_margin_trend"]["unit"],
                        name="gross_margin_trend").endswith("%")
    assert display_text(facts["net_debt_to_ebitda"]["value"],
                        facts["net_debt_to_ebitda"]["unit"],
                        name="net_debt_to_ebitda").endswith("倍")
    assert display_text(facts["rsi_14"]["value"], facts["rsi_14"]["unit"],
                        name="rsi_14") == "54.7"


def test_archived_cards_keep_their_exact_values_and_fact_ids():
    card, _, _ = archived("NVDA")
    for fact in card["facts"]:
        assert isinstance(fact["fact_id"], str) and fact["fact_id"]
        assert fact["value"] is not None


@pytest.mark.parametrize("ticker", sorted(CARDS))
def test_display_change_never_touches_archived_values_or_fact_ids(ticker):
    card, snapshot, _ = archived(ticker)
    index, _ = fact_index(snapshot)
    for fact in card["facts"]:
        current = index[fact["fact_id"]]
        assert current["value"] == fact["value"]
        assert current["name"] == fact["name"]
        assert current["unit"] == fact["unit"]


# ---------------------------------------------------------------------------
# v4 cards (prompt v4, validator v2): D14/D15/D11 and the new display only.
# D01 is not required to pass: the display strings written by v2 differ from the
# v3 formatter, which is the documented consequence of a display-rule change.

V4_CARDS = {
    "AAPL": ("7b557755-5d0a-41af-876d-1cb002a51e71", "分批",
             {"D14": False, "D15": False, "D11": False}),
    "NVDA": ("5705ef99-7b95-4289-a8e9-ac7923155d27", "买入",
             {"D14": True, "D15": False, "D11": False}),
    "TSLA": ("a200943e-3c33-45d6-a7d3-43319810ef31", "观望",
             {"D14": False, "D15": False, "D11": False}),
}


def archived_v4(ticker):
    if not ARCHIVE.is_file():
        pytest.skip(f"{ARCHIVE} is local runtime data and is not present")
    connection = sqlite3.connect(f"file:{ARCHIVE}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT card_json, snapshot_json FROM decision_cards WHERE card_id=?",
        (V4_CARDS[ticker][0],)).fetchone()
    if row is None:
        pytest.skip(f"v4 card {V4_CARDS[ticker][0]} is not present")
    return json.loads(row["card_json"]), json.loads(row["snapshot_json"])


@pytest.mark.parametrize("ticker", sorted(V4_CARDS))
def test_v4_cards_replay_matches_the_expected_d11_d14_d15(ticker):
    card, snapshot = archived_v4(ticker)
    _, action, expected = V4_CARDS[ticker]
    assert card["action"] == action
    rules = {item["rule"] for item in validate_card(card, snapshot,
                                                    version="decision-validator-3")}
    for rule, should_fire in expected.items():
        assert (rule in rules) is should_fire, (ticker, rule, sorted(rules))


@pytest.mark.parametrize("ticker", sorted(V4_CARDS))
def test_v4_cards_render_with_the_v3_display(ticker):
    card, snapshot = archived_v4(ticker)
    # The facts table is Python-owned: re-derive it from the snapshot with the
    # current formatter (that is what "the new render" means).  Model-authored
    # fields are untouched.
    index, _ = fact_index(snapshot)
    card = dict(card, facts=[index[fact_id] for fact_id in card["fact_ids"] if fact_id in index])
    rendered = render_card(card, snapshot, version="decision-validator-2")
    assert "价格数据截至 " in rendered
    assert "机器检查：" in rendered
    assert "自动计算（Python）" in rendered
    for fact in card["facts"]:
        assert metric_label(fact["name"]) in rendered, fact["name"]
    for line in rendered.splitlines():
        if line.startswith("- ") and ": " in line and "(" in line and "|" in line:
            label = line[2:].split(":", 1)[0]
            assert "_" not in label, line
