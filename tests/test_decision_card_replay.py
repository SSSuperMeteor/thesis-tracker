"""Replay the three archived real cards: D11 under v1 rules and the new display."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from thesis_tracker.decision.core import fact_index, validate_card
from thesis_tracker.decision.evidence import display_text

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
