"""Card detail: structured segments that reproduce the CLI rendering byte for byte."""

from __future__ import annotations

import json
import sqlite3

import pytest
from webapp_fixtures import REPO_ROOT, build_fixture, fixture_snapshot

ARCHIVED_CARDS = REPO_ROOT / "data/decisions/cards.db"


@pytest.fixture
def fixture(tmp_path):
    return build_fixture(tmp_path / "fixture")


@pytest.fixture
def detail(fixture):
    from thesis_tracker.webapp.service import card_detail

    return card_detail(card_db=fixture.card_db, card_id=fixture.card_id,
                       price_db=fixture.price_db)


def shortened_ok(short, full):
    """True when the page's line is the renderer's, or a documented shortening.

    Two lines drop words the card already prints as a label: the 52-week line
    loses its repeated "52 周高点", and the reward/risk ratio gains the ": 1"
    the renderer leaves implicit.
    """
    short_label, _, short_text = short.partition(": ")
    full_label, _, full_text = full.partition(": ")
    if short_label != full_label:
        return False
    if short_text == full_text or short_text in full_text:
        return True
    # The 52-week line only removes the words the label already prints.
    without_repeat = full_text.replace("52 周高点 ", "").replace("52 周高点", "")
    if short_text == without_repeat:
        return True
    return short_text == f"{full_text} : 1"


def join(segments):
    """Render a segment array the way the page does: text plus fact displays."""
    return "".join(item["value"] if item["type"] == "text" else item["display"]
                   for item in segments)


def rendered_lines(card_db, card_id):
    """Re-render an archived card exactly as the command line does.

    The card's own stored validator version is used, the same way the archive
    replays old cards: rendering a v1 card with the current rules would report
    the display-rule and D11 differences documented in ``agent-loop.md``.
    """
    from thesis_tracker.decision.core import read_card, render_card

    archived = read_card(card_db, card_id)
    try:
        return render_card(archived["card"],
                           json.loads(_snapshot_text(card_db, card_id)),
                           version=archived["validator_version"])
    except ValueError:
        return None


def _snapshot_text(card_db, card_id):
    connection = sqlite3.connect(card_db)
    try:
        return connection.execute(
            "SELECT snapshot_json FROM decision_cards WHERE card_id=?", (card_id,)
        ).fetchone()[0]
    finally:
        connection.close()


def archived_cards(card_db):
    """(card_id, validator_version) for every archived card, oldest first."""
    connection = sqlite3.connect(f"file:{card_db.resolve()}?mode=ro", uri=True)
    try:
        return [tuple(row) for row in connection.execute(
            "SELECT card_id, validator_version FROM decision_cards ORDER BY created_at")]
    finally:
        connection.close()


def line(rendered, prefix):
    matches = [item for item in rendered.split("\n") if item.startswith(prefix)]
    assert len(matches) == 1, (prefix, matches)
    return matches[0]


def section(rendered, header):
    """The rendered lines belonging to one titled section, header excluded."""
    lines = rendered.split("\n")
    start = lines.index(header)
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if not lines[index].startswith(("- ", "  ")):
            end = index
            break
    return lines[start + 1:end]


def test_stop_and_target_rationales_reconstruct_their_rendered_lines(fixture, detail):
    rendered = rendered_lines(fixture.card_db, fixture.card_id)
    assert join(detail["stop_rationale"]) == line(rendered, "止损依据：")[len("止损依据："):]
    assert join(detail["target_rationale"]) == line(rendered, "目标依据：")[len("目标依据："):]


def test_reason_segments_reconstruct_the_rendered_reason_lines(fixture, detail):
    rendered = rendered_lines(fixture.card_db, fixture.card_id)
    expected = [item[len("- "):] for item in section(rendered, "理由：")]
    assert len(expected) == len(detail["reasons"])
    for body, segments in zip(expected, detail["reasons"], strict=True):
        assert segments, "every reason carries at least one segment"
        assert join(segments) == body
        assert any(item["type"] == "fact" for item in segments)


def test_invalidation_segments_reconstruct_check_and_explanation(fixture, detail):
    rendered = rendered_lines(fixture.card_db, fixture.card_id)
    checks = section(rendered, "失效条件：")
    assert len(detail["invalidations"]) == len(checks) // 2
    for index, item in enumerate(detail["invalidations"]):
        check_line = checks[index * 2]
        explanation_line = checks[index * 2 + 1]
        assert check_line.startswith("- 机器检查：")
        assert explanation_line.startswith("  说明：")
        assert join(item["machine_check"]) == check_line[len("- 机器检查："):]
        assert join(item["explanation"]) == explanation_line[len("  说明："):]


def test_fact_rows_carry_id_label_display_source_and_date(fixture, detail):
    rendered = rendered_lines(fixture.card_db, fixture.card_id)
    table = section(rendered, "事实表：")
    assert len(detail["facts"]) == len(table)
    for fact, row in zip(detail["facts"], table, strict=True):
        assert row.startswith(f"- {fact['label']}: {fact['display']} ({fact['fact_id']})")
        assert fact["unit"] and fact["date_or_period"]
        assert fact["provider"] in {"sec_filing_xbrl", "tiingo"} or fact["formula"]
    assert detail["facts"][0]["label"] == "收盘价"
    assert detail["facts"][0]["display"].endswith("美元/股")
    assert detail["facts"][0]["date_or_period"] == "2026-09-07"


def test_auto_computed_section_matches_the_rendered_lines(fixture, detail):
    """Each line is the renderer's own, or a shortened form of it.

    The card page prints the label above the value, so two lines drop the words
    they repeat (``距52周高点`` and the reward/risk ratio's implicit ": 1").
    Every other character is the renderer's, unchanged.
    """
    rendered = rendered_lines(fixture.card_db, fixture.card_id)
    expected = section(rendered, "自动计算（Python）：")
    produced = [f"- {item['label']}: {item['text']}" for item in detail["auto_computed"]]
    assert len(produced) == len(expected)
    for short, full in zip(produced, expected, strict=True):
        assert shortened_ok(short, full), (short, full)


def test_the_shortened_lines_lose_only_repeated_words(fixture):
    """The adjustment is a display rule, never a new number."""
    from thesis_tracker.decision.core import auto_computed
    from thesis_tracker.webapp.service import adjust_auto_computed

    items = [{"label": "距52周高点", "text": "低于 52 周高点 25.71%"},
             {"label": "盈亏比", "text": "2.2"},
             {"label": "止损距离", "text": "3.32%（1.56 倍 ATR）"},
             {"label": "目标距离", "text": "无法计算（缺少目标位或入场价）"}]
    adjusted = adjust_auto_computed(items)
    assert adjusted[0]["text"] == "低于 25.71%"
    assert adjusted[1]["text"] == "2.2 : 1"
    # Lines that repeat nothing are passed through untouched.
    assert adjusted[2] == items[2]
    assert adjusted[3] == items[3]
    assert auto_computed is not None


def test_gaps_and_disclaimer_match_the_rendered_card(fixture, detail):
    from thesis_tracker.decision.core import DISCLAIMER

    rendered = rendered_lines(fixture.card_db, fixture.card_id)
    gaps = section(rendered, "数据缺口：")
    assert len(gaps) == len(detail["gaps"])
    for gap, row in zip(detail["gaps"], gaps, strict=True):
        assert row.startswith(f"- {gap['label']}: {gap['status']} ")
    assert detail["disclaimer"] == DISCLAIMER
    assert rendered.split("\n")[-1] == DISCLAIMER


def test_verdict_header_matches_the_rendered_card(fixture, detail):
    rendered = rendered_lines(fixture.card_db, fixture.card_id)
    lines = rendered.split("\n")
    assert lines[0] == f"{detail['ticker']}｜{detail['as_of']}｜{detail['horizon']}"
    assert f"价格数据截至 {detail['price_data_end']}" in lines
    assert lines[2] == (f"AI 判断：{detail['bias']} / {detail['action']}｜"
                        f"置信度 {detail['confidence']}（{detail['confidence_calibration']}）")


def test_price_band_positions_are_ordered_like_the_prices(fixture, detail):
    band = detail["price_band"]
    assert band is not None
    markers = band["markers"]
    assert [item["key"] for item in markers] == ["stop_loss", "entry_low", "creation_price",
                                                 "entry_high", "target_price"]
    positions = [item["position"] for item in markers]
    assert positions == sorted(positions)
    assert positions[0] == 0.0 and positions[-1] == 100.0
    assert all(0.0 <= value <= 100.0 for value in positions)
    # The CSS fraction is derived here, so the page never divides a number.
    assert all(item["fraction"] == round(item["position"] / 100, 4) for item in markers)
    assert all(0.0 <= item["fraction"] <= 1.0 for item in markers)
    assert band["entry_low"] == detail["entry_range"][0]
    assert band["entry_high"] == detail["entry_range"][1]


def test_an_action_without_prices_has_no_band(fixture, tmp_path):
    """观望/减仓/回避 have no prices, so the page must not draw a band."""
    from thesis_tracker.webapp.service import card_detail, price_band

    assert price_band({"action": "观望", "entry_range": None, "stop_loss": None,
                       "target_price": None, "creation_price": 333.69}) is None
    assert price_band({"action": "回避"}) is None
    detail = card_detail(card_db=fixture.card_db, card_id=fixture.card_id)
    assert detail["price_band"] is not None


def test_attempts_belong_to_the_card_own_analysis_not_other_runs(fixture, detail):
    """A draft rejected by a different analysis of the same ticker is not this card's."""
    assert detail["attempts"], "the card's own analysis drafted at least once"
    assert {item["analysis_id"] for item in detail["attempts"]} == {detail["analysis_id"]}
    assert detail["analysis_id"] == fixture.passed_analysis_id
    assert fixture.rejected_analysis_id != detail["analysis_id"]


def test_versions_and_rejected_attempts_are_exposed(fixture, detail):
    assert detail["versions"]["validator_version"] == "decision-validator-3"
    assert detail["versions"]["prompt_version"] == (
        "decision-agent-v5-entry-stop-rules-2026-10-04")
    assert detail["versions"]["requested_model"] == "deepseek-flash"
    assert detail["versions"]["returned_model"] == "deepseek-flash"
    assert detail["versions"]["snapshot_sha256"]
    assert detail["versions"]["validation_result"] == []
    assert detail["usage"]["input_tokens"] == 22262
    assert [item["attempt_no"] for item in detail["attempts"]] == [1, 2]
    rejected = [item for item in detail["attempts"] if not item["passed"]]
    assert len(rejected) == 1, "the fixture must archive a rejected draft"
    rules = [violation["rule"] for item in rejected for violation in item["violations"]]
    assert "D02" in rules and "D11" in rules
    for item in rejected:
        assert item["headline"].startswith("被拒绝：")
        for violation in item["violations"]:
            assert violation["message"] and violation["location"]


def test_unknown_card_id_raises_key_error(fixture):
    from thesis_tracker.webapp.service import card_detail

    with pytest.raises(KeyError):
        card_detail(card_db=fixture.card_db, card_id="does-not-exist")


def test_displaying_an_archived_card_never_calls_a_model(fixture, monkeypatch):
    """Showing an archived card must not reach any model client."""
    import thesis_tracker.decision.agent as agent

    def forbidden(*args, **kwargs):
        raise AssertionError("displaying an archived card must not call a model")

    monkeypatch.setattr(agent.DeepSeekClient, "__init__", forbidden)
    from thesis_tracker.webapp.service import card_detail

    detail = card_detail(card_db=fixture.card_db, card_id=fixture.card_id)
    assert detail["reasons"]


@pytest.mark.skipif(not ARCHIVED_CARDS.exists(),
                    reason="no local card archive in this checkout")
def test_real_archived_cards_reconstruct_byte_for_byte():
    """Archived cards: joined segments equal the existing renderer output.

    Cards the current validator refuses to re-render are counted and reported,
    never silently skipped; every card stored under the newest version present
    in the archive must reproduce exactly.
    """
    from thesis_tracker.decision.core import VALIDATOR_VERSION
    from thesis_tracker.webapp.service import card_detail

    cards = archived_cards(ARCHIVED_CARDS)
    assert cards, "the local archive must contain at least one card"
    checked, refused, refused_ids = 0, 0, []
    for card_id, version in cards:
        rendered = rendered_lines(ARCHIVED_CARDS, card_id)
        if rendered is None:
            assert version != VALIDATOR_VERSION, card_id
            refused += 1
            refused_ids.append(card_id)
            continue
        detail = card_detail(card_db=ARCHIVED_CARDS, card_id=card_id)
        lines = rendered.split("\n")
        assert lines[0] == f"{detail['ticker']}｜{detail['as_of']}｜{detail['horizon']}"
        if detail["stop_rationale"]:
            assert join(detail["stop_rationale"]) == line(rendered, "止损依据：")[5:]
        if detail["target_rationale"]:
            assert join(detail["target_rationale"]) == line(rendered, "目标依据：")[5:]
        expected = [item[2:] for item in section(rendered, "理由：")]
        assert len(expected) == len(detail["reasons"])
        for body, segments in zip(expected, detail["reasons"], strict=True):
            assert join(segments) == body
        checks = section(rendered, "失效条件：")
        for index, item in enumerate(detail["invalidations"]):
            assert join(item["machine_check"]) == checks[index * 2][len("- 机器检查："):]
            assert join(item["explanation"]) == checks[index * 2 + 1][len("  说明："):]
        expected_auto = section(rendered, "自动计算（Python）：")
        produced_auto = [f"- {item['label']}: {item['text']}"
                         for item in detail["auto_computed"]]
        assert len(produced_auto) == len(expected_auto)
        for short, full in zip(produced_auto, expected_auto, strict=True):
            assert shortened_ok(short, full), (short, full)
        table = section(rendered, "事实表：")
        assert len(table) == len(detail["facts"])
        for row, fact in zip(table, detail["facts"], strict=True):
            assert row == f"- {fact['label']}: {fact['display']} ({fact['fact_id']})"
        assert detail["disclaimer"] == lines[-1]
        checked += 1
    assert checked >= 3, (checked, refused_ids)
    assert checked + refused == len(cards)


@pytest.mark.skipif(not ARCHIVED_CARDS.exists(),
                    reason="no local card archive in this checkout")
def test_real_archived_card_of_each_current_version_is_checked():
    """The newest validator version's cards are all byte-for-byte verified."""
    from thesis_tracker.decision.core import VALIDATOR_VERSION
    from thesis_tracker.webapp.service import card_detail

    current = [card_id for card_id, version in archived_cards(ARCHIVED_CARDS)
               if version == VALIDATOR_VERSION]
    assert current, "the archive must contain a current-version card"
    for card_id in current:
        detail = card_detail(card_db=ARCHIVED_CARDS, card_id=card_id)
        rendered = rendered_lines(ARCHIVED_CARDS, card_id)
        assert rendered is not None
        for key, prefix in (("stop_rationale", "止损依据："),
                            ("target_rationale", "目标依据：")):
            if detail[key]:
                assert join(detail[key]) == line(rendered, prefix)[len(prefix):]
            else:
                # 观望/减仓/回避 leave both rationales empty by rule (D12).
                assert not any(item.startswith(prefix) for item in rendered.split("\n"))


def test_fixture_snapshot_helpers_agree_with_the_fixture_card(fixture):
    """Guard: the fixture card really is the snapshot the page re-derives from."""
    from thesis_tracker.decision.core import fact_index

    snapshot = fixture_snapshot("AAPL", fixture.price_db, fixture.fact_db)
    index = fact_index(snapshot)[0]
    assert index, "the fixture snapshot must produce facts"


def test_the_band_legend_uses_the_same_shapes_as_the_chart(detail):
    """Every legend symbol must be the symbol the plot actually draws.

    The round-2 defect was a legend whose marks did not match the chart: a
    reader comparing them learns the wrong thing.  This asserts the sets are
    equal, so a rename on one side cannot pass.
    """
    band = detail["price_band"]
    legend_shapes = [item["shape"] for item in band["legend"]]
    chart_shapes = {marker["shape"] for marker in band["markers"]}
    assert legend_shapes == ["stop", "entry", "close", "target"]
    assert set(legend_shapes) <= chart_shapes | {"entry"}
    # The entry range's own symbol is the bar, and the entry markers carry it.
    entries = [marker for marker in band["markers"] if marker["shape"] == "entry"]
    assert entries, "the buy range must have its own markers"
    assert all(marker["key"].startswith("entry_") for marker in entries)


def test_each_invalidation_line_says_what_it_is(detail):
    """A machine-checkable threshold and an AI sentence must be told apart.

    Both used to be plain paragraphs, so a reader could not see which line the
    software enforces and which is explanation.
    """
    assert detail["invalidations"], "the fixture card has an invalidation"
    for item in detail["invalidations"]:
        assert item["machine_label"] == "机器检查"
        assert item["explanation_label"] == "说明"
        machine = "".join(segment["value"] for segment in item["machine_check"])
        assert machine.startswith("收盘价")
        assert item["explanation"], "the explanation is kept, just labelled"


def test_the_card_chart_draws_the_cards_own_levels_and_close(detail):
    chart = detail["chart"]
    assert chart["available"] is True
    levels = {level["key"]: level for level in chart["ranges"][1]["levels"]}
    assert set(levels) == {"entry", "stop", "target"}
    assert levels["stop"]["value"] == detail["stop_loss"]
    assert levels["target"]["value"] == detail["target_price"]
    assert levels["entry"]["value"].startswith(detail["entry_range"][0].split(" ")[0])
    marker = chart["ranges"][1]["close_marker"]
    assert marker["label"] == f"收盘价 {detail['creation_price']}"


@pytest.mark.parametrize("action, drawn", [("买入", True), ("分批", True), ("持有", True),
                                           ("观望", False), ("减仓", False),
                                           ("回避", False)])
def test_only_the_priced_actions_draw_levels(action, drawn):
    from thesis_tracker.webapp.service import chart_inputs

    card = {"action": action, "entry_range": [228, 236], "stop_loss": 218.12,
            "target_price": 265, "creation_price": 233.95, "as_of": "2026-09-07"}
    levels, note, _close, _day = chart_inputs(card)
    if drawn:
        assert levels is not None and note is None
    else:
        assert levels is None
        assert "没有价位" in note


def test_a_holding_card_has_no_entry_band():
    from thesis_tracker.webapp.service import chart_inputs

    card = {"action": "持有", "entry_range": None, "stop_loss": 200, "target_price": 260,
            "creation_price": 233.95, "as_of": "2026-09-07"}
    levels, _note, _close, _day = chart_inputs(card)
    assert levels["entry"] is None and levels["stop"] == 200
