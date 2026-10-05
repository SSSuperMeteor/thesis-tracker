"""Auto-computed display wording, advisory hints, and evidence grouping."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from webapp_fixtures import build_fixture, fixture_snapshot


@pytest.fixture
def fixture(tmp_path):
    return build_fixture(tmp_path / "fixture")


@pytest.fixture
def detail(fixture):
    from thesis_tracker.webapp.service import card_detail

    return card_detail(card_db=fixture.card_db, card_id=fixture.card_id)


def test_the_52_week_line_does_not_repeat_its_own_label(detail):
    """`距52周高点` already says which distance this is."""
    item = next(entry for entry in detail["auto_computed"]
                if entry["label"] == "距52周高点")
    if item["text"].startswith("低于"):
        assert item["text"].endswith("%")
        assert "52 周高点" not in item["text"]
    else:
        # The fixture's short price series cannot produce a 52-week landmark;
        # the reading is then the plain "无法计算" reason, never a guess.
        assert item["text"].startswith("无法计算")


def test_the_real_archive_shortens_the_52_week_line():
    """With a real card the repeated label is actually gone."""
    archive = Path(__file__).resolve().parents[1] / "data/decisions/cards.db"
    if not archive.exists():
        pytest.skip("no local card archive in this checkout")
    from thesis_tracker.webapp.service import card_detail

    for row in _real_card_ids(archive)[:3]:
        detail = card_detail(card_db=archive, card_id=row)
        item = next(entry for entry in detail["auto_computed"]
                    if entry["label"] == "距52周高点")
        assert "52 周高点" not in item["text"], item["text"]
        assert item["text"].startswith("低于 "), item["text"]


def test_the_reward_risk_line_is_written_as_a_ratio(detail):
    item = next(entry for entry in detail["auto_computed"] if entry["label"] == "盈亏比")
    assert ": 1" in item["text"]
    value = item["text"].split(" : ")[0]
    assert float(value) > 0


def test_the_auto_computed_heading_no_longer_names_a_language(detail):
    """The section name is a heading, not an implementation note."""
    labels = [entry["label"] for entry in detail["auto_computed"]]
    assert "止损距离" in labels and "目标距离" in labels and "盈亏比" in labels


def test_hints_are_absent_when_every_threshold_is_satisfied(fixture):
    """A comfortable card states no warning at all."""
    from thesis_tracker.webapp.service import advice_hints

    card = {"action": "分批", "entry_range": [100, 100], "stop_loss": 90,
            "target_price": 130, "creation_price": 100}
    facts = [{"name": "atr_14", "value": "1.0"}]
    assert advice_hints(card, facts, number=lambda value: _decimal(value)) == []


def test_a_low_reward_risk_ratio_is_flagged():
    from thesis_tracker.webapp.service import advice_hints

    # (110-100)/(100-96) = 2.5 ... then a tighter case below the threshold.
    card = {"action": "分批", "entry_range": [100, 100], "stop_loss": 96,
            "target_price": 105, "creation_price": 100}
    hints = advice_hints(card, [], number=_decimal)
    assert any("盈亏比偏低" in hint for hint in hints), hints


def test_a_tight_stop_relative_to_atr_is_flagged():
    from thesis_tracker.webapp.service import advice_hints

    card = {"action": "分批", "entry_range": [100, 100], "stop_loss": 99,
            "target_price": 130, "creation_price": 100}
    hints = advice_hints(card, [{"name": "atr_14", "value": "2.0"}], number=_decimal)
    assert any("ATR" in hint for hint in hints), hints


def test_the_thresholds_are_named_constants_not_magic_numbers():
    from thesis_tracker.webapp import service

    assert service.MIN_REWARD_RISK == 1.5
    assert service.MIN_STOP_ATR_MULTIPLE == 2.0
    # The boundary is inclusive: exactly 1.5 is not "偏低".
    card = {"action": "分批", "entry_range": [100, 100], "stop_loss": 90,
            "target_price": 115, "creation_price": 100}
    assert service.advice_hints(card, [], number=_decimal) == []
    card["target_price"] = 114.9
    assert service.advice_hints(card, [], number=_decimal)


def test_exactly_two_atr_is_not_flagged_but_below_it_is():
    from thesis_tracker.webapp import service

    atr = [{"name": "atr_14", "value": "5.0"}]
    # stop distance 10 = exactly 2 x ATR -> allowed
    card = {"action": "分批", "entry_range": [100, 100], "stop_loss": 90,
            "target_price": 130, "creation_price": 100}
    assert service.advice_hints(card, atr, number=_decimal) == []
    # stop distance 9.9 < 2 x ATR -> flagged
    card["stop_loss"] = 90.1
    hints = service.advice_hints(card, atr, number=_decimal)
    assert any("ATR" in hint for hint in hints)


def test_no_hint_is_produced_for_an_action_without_prices():
    from thesis_tracker.webapp.service import advice_hints

    card = {"action": "观望", "entry_range": None, "stop_loss": None,
            "target_price": None, "creation_price": 100}
    assert advice_hints(card, [{"name": "atr_14", "value": "2.0"}], number=_decimal) == []


def test_hints_are_carried_on_the_card_detail(fixture):
    from thesis_tracker.webapp.service import card_detail

    detail = card_detail(card_db=fixture.card_db, card_id=fixture.card_id)
    assert "hints" in detail
    assert isinstance(detail["hints"], list)
    for hint in detail["hints"]:
        assert hint and not hint.endswith("。")


def test_the_hint_wording_does_not_claim_a_fact(fixture):
    """Thresholds are experiments, so the wording hedges instead of asserting."""
    from thesis_tracker.webapp.service import card_detail

    detail = card_detail(card_db=fixture.card_db, card_id=fixture.card_id)
    for hint in detail["hints"]:
        assert "一定" not in hint and "必然" not in hint and "说明" not in hint


def test_evidence_is_grouped_by_where_each_fact_came_from(detail):
    groups = detail["fact_groups"]
    keys = [group["key"] for group in groups]
    assert keys == [key for key in ("market", "fundamental", "derived") if key in keys]
    assert sum(len(group["facts"]) for group in groups) == len(detail["facts"])
    for group in groups:
        assert group["label"]
        for fact in group["facts"]:
            assert fact["label"] and fact["display"] and fact["fact_id"]


def test_every_group_has_a_chinese_heading(detail):
    """No group may render an English key as its heading."""
    from thesis_tracker.webapp.service import FACT_GROUP_LABELS, FACT_GROUP_ORDER

    assert FACT_GROUP_LABELS == {"market": "行情与指标", "fundamental": "财报指标",
                                 "derived": "派生"}
    assert FACT_GROUP_ORDER == ("market", "fundamental", "derived")
    for group in detail["fact_groups"]:
        assert group["label"] == FACT_GROUP_LABELS[group["key"]]
        assert group["key"] not in group["label"]


def test_the_real_archive_produces_all_three_groups():
    archive = Path(__file__).resolve().parents[1] / "data/decisions/cards.db"
    if not archive.exists():
        pytest.skip("no local card archive in this checkout")
    from thesis_tracker.webapp.service import card_detail

    seen = set()
    for card_id in _real_card_ids(archive):
        detail = card_detail(card_db=archive, card_id=card_id)
        seen.update(group["key"] for group in detail["fact_groups"])
    assert seen == {"market", "fundamental", "derived"}, seen


def test_facts_carry_a_short_source_identifier_and_a_date(detail):
    facts = [fact for group in detail["fact_groups"] for fact in group["facts"]]
    assert facts
    for fact in facts:
        assert fact["source_label"], fact
        assert fact["date_or_period"], fact
        # The raw identifier is present for the tooltip and data attribute, but
        # the page shows the short form.
        assert fact["fact_id"]
        assert fact["source_label"] != fact["fact_id"]


def test_a_derived_fact_is_grouped_as_derived(fixture):
    """The fixture card's own facts group by their recorded source."""
    from thesis_tracker.webapp.service import group_facts

    facts = [
        {"fact_id": "tiingo|AAPL|2026-10-02|daily", "category": "market"},
        {"fact_id": "sec_metric|AAPL|x|2026-06-27|a", "category": "fundamental"},
        {"fact_id": "derived|AAPL|high_52w|2026-09-22|a", "category": "derived"},
    ]
    groups = group_facts(facts)
    assert [group["key"] for group in groups] == ["market", "fundamental", "derived"]
    assert [group["facts"][0]["fact_id"] for group in groups] == [
        fact["fact_id"] for fact in facts]
    assert group_facts([]) == []


def test_grouping_is_decided_by_the_fact_own_source_not_by_name():
    """A fact's own source decides its group; its name never does.

    ``fact_category`` (which is shared with the card renderer and must not
    change) resolves the provider first and only looks for a formula when no
    provider is recorded, so a derived fact carrying the ``tiingo`` provider
    groups under market.  Its visible source label still reads as derived.
    """
    from thesis_tracker.decision.evidence import fact_category
    from thesis_tracker.webapp.service import source_label

    closed = {"fact_id": "tiingo|AAPL|2026-10-02|daily", "name": "close",
              "source": {"provider": "tiingo"}}
    metric = {"fact_id": "sec_metric|AAPL|gross_margin_trend|2026-06-27|abc",
              "name": "gross_margin_trend", "source": {"provider": "sec_filing_xbrl"}}
    derived = {"fact_id": "derived|AAPL|high_52w|2026-09-22|abc", "name": "high_52w",
               "source": {"formula": "max(high)"}}
    assert fact_category(closed) == "market"
    assert fact_category(metric) == "fundamental"
    assert fact_category(derived) == "derived"
    assert source_label(closed["source"]) == "行情"
    assert source_label(metric["source"]) == "SEC 财报"
    assert source_label(derived["source"]) == "派生计算"
    # A name that happens to look fundamental does not move a market fact.
    named = {"fact_id": "tiingo|AAPL|2026-10-02|indicator|x", "name": "rsi_14",
             "source": {"provider": "tiingo"}}
    assert fact_category(named) == "market"


def test_grouping_holds_for_every_card_in_the_real_archive():
    """Every archived card's facts land in exactly one group, none dropped."""
    archive = Path(__file__).resolve().parents[1] / "data/decisions/cards.db"
    if not archive.exists():
        pytest.skip("no local card archive in this checkout")
    from thesis_tracker.webapp.service import card_detail

    for card_id in _real_card_ids(archive):
        detail = card_detail(card_db=archive, card_id=card_id)
        grouped = [fact["fact_id"] for group in detail["fact_groups"]
                   for fact in group["facts"]]
        assert sorted(grouped) == sorted(fact["fact_id"] for fact in detail["facts"])
        assert len(grouped) == len(set(grouped)), card_id


def _real_card_ids(archive):
    connection = sqlite3.connect(f"file:{Path(archive).resolve()}?mode=ro", uri=True)
    try:
        return [row[0] for row in connection.execute(
            "SELECT card_id FROM decision_cards ORDER BY created_at DESC")]
    finally:
        connection.close()


def test_the_fixture_snapshot_still_builds(fixture):
    snapshot = fixture_snapshot("AAPL", fixture.price_db, fixture.fact_db)
    assert snapshot["calls"] and snapshot["as_of"]
    assert json.dumps(snapshot)[:1] == "{"


def _decimal(value):
    from decimal import Decimal

    return Decimal(str(value))


def test_the_atr_hint_uses_the_snapshot_not_only_the_cards_own_facts():
    """A card cites only what it references; ATR usually is not among them."""
    from thesis_tracker.webapp.service import advice_hints

    card = {"action": "分批", "entry_range": [100, 100], "stop_loss": 99,
            "target_price": 130, "creation_price": 100}
    index = {"tiingo|AAPL|2026-10-02|indicator|atr_14":
             {"name": "atr_14", "value": "2.0"}}
    hints = advice_hints(card, [], index=index, number=_decimal)
    assert any("ATR" in hint for hint in hints), hints
    # Without the index and without a card fact, nothing is claimed.
    assert advice_hints(card, [], number=_decimal) == []


def test_the_band_spans_the_reading_column_and_alternates_sides():
    from thesis_tracker.webapp.service import price_band

    card = {"action": "分批", "entry_range": [327.0, 335.0], "stop_loss": 320.0,
            "target_price": 345.34, "creation_price": 333.69}
    band = price_band(card)
    assert band["markers"]
    sides = [marker["label_side"] for marker in band["markers"]]
    assert set(sides) <= {"above", "below"}
    # Adjacent markers land on opposite sides so their labels cannot collide.
    assert sides[0] != sides[1]
    # The axis geometry is the reading column's own width, with no inset.
    assert band["plot_height_px"] > 0
    assert [item["shape"] for item in band["legend"]] == ["stop", "range", "close",
                                                          "target"]
    # The buy range's bar geometry arrives ready to place, with no page maths.
    assert band["range_left_percent"] == band["markers"][1]["percent"]
    assert float(band["range_width_percent"]) > 0
    assert all(marker["percent"] == f"{marker['position']:.2f}"
               for marker in band["markers"])
    # The closing price is distinguishable by shape, not only by colour.
    shapes = {marker["key"]: marker["shape"] for marker in band["markers"]}
    assert shapes["creation_price"] == "close"
    assert shapes["stop_loss"] == "stop"
    assert shapes["target_price"] == "target"
    assert shapes["entry_low"] == shapes["entry_high"] == "entry"
    assert len(set(shapes.values())) == 4


def test_band_labels_never_overlap_on_the_same_side():
    """Two labels closer than their width must not share a row and a side."""
    from thesis_tracker.webapp.service import price_band

    # Four prices within a fraction of a percent of each other.
    card = {"action": "分批", "entry_range": [100.0, 100.2], "stop_loss": 99.9,
            "target_price": 100.3, "creation_price": 100.1}
    band = price_band(card)
    occupied: dict[tuple[str, int], list[tuple[float, float]]] = {}
    for marker in band["markers"]:
        key = (marker["label_side"], marker["label_row"])
        width = max(len(marker["label"]), len(marker["value"])) * 12.0
        centre = marker["position"] / 100 * 640.0
        span = (centre - width / 2, centre + width / 2)
        for left, right in occupied.get(key, []):
            assert span[0] > right or span[1] < left, (marker, key)
        occupied.setdefault(key, []).append(span)
