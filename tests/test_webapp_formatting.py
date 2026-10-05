"""Every timestamp and version a page shows is a backend-produced string."""

from __future__ import annotations

import re
from datetime import datetime

import pytest
from webapp_fixtures import build_fixture

# Stored timestamps carry an offset; the page must never show one.
ISO_WITH_ZONE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)")


@pytest.fixture
def fixture(tmp_path):
    return build_fixture(tmp_path / "fixture")


@pytest.fixture
def detail(fixture):
    from thesis_tracker.webapp.service import card_detail

    return card_detail(card_db=fixture.card_db, card_id=fixture.card_id)


def test_card_list_timestamps_are_local_minute_strings(fixture):
    from thesis_tracker.webapp.service import card_list

    cards = card_list(card_db=fixture.card_db)
    assert cards
    for card in cards:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", card["created_at"]), card
        assert not ISO_WITH_ZONE.search(card["created_at"])
    # And the conversion is real, not a truncation of the UTC string.
    stored = datetime.fromisoformat("2026-10-05T00:44:06+00:00")
    from thesis_tracker.webapp import display

    assert cards[0]["created_at"] == stored.astimezone(
        display.local_zone()).strftime("%Y-%m-%d %H:%M")


def test_card_detail_timestamps_are_local_minute_strings(detail):
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", detail["created_at"])
    for attempt in detail["attempts"]:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", attempt["created_at"]), attempt
        assert not ISO_WITH_ZONE.search(attempt["created_at"])


def test_card_list_carries_a_short_rule_mark_and_a_staleness_flag(fixture):
    from thesis_tracker.webapp.service import card_list

    card = card_list(card_db=fixture.card_db)[0]
    assert card["version_mark"] == "提示词 v5 / 校验 3"
    assert card["rules_current"] is True
    assert card["rules_label"] is None
    # The full strings stay available for the detail page and for debugging.
    assert card["validator_version"] == "decision-validator-3"
    assert card["prompt_version"].startswith("decision-agent-v5")


def test_a_card_from_older_rules_is_marked(fixture, tmp_path):
    import sqlite3

    from thesis_tracker.webapp.service import card_list

    copy = tmp_path / "old.db"
    copy.write_bytes(fixture.card_db.read_bytes())
    connection = sqlite3.connect(copy)
    with connection:
        connection.execute(
            "UPDATE decision_cards SET validator_version='decision-validator-1', "
            "prompt_version='decision-agent-v3-tool-contract-2026-10-04'")
    connection.close()
    card = card_list(card_db=copy)[0]
    assert card["version_mark"] == "提示词 v3 / 校验 1"
    assert card["rules_current"] is False
    assert card["rules_label"] == "旧规则"


def test_the_rules_mark_is_compared_with_the_code_constants(fixture):
    """The flags must follow the shipped constants, not a hardcoded version."""
    import sqlite3

    from thesis_tracker.decision.agent import PROMPT_VERSION
    from thesis_tracker.decision.core import VALIDATOR_VERSION
    from thesis_tracker.webapp.service import card_list

    current = card_list(card_db=fixture.card_db)[0]
    assert current["rules_current"] is (current["validator_version"] == VALIDATOR_VERSION
                                        and current["prompt_version"] == PROMPT_VERSION)
    assert PROMPT_VERSION.startswith("decision-agent-v5")
    assert VALIDATOR_VERSION == "decision-validator-3"
    del sqlite3


def test_a_job_view_formats_its_timestamps_and_labels(fixture, tmp_path):
    from thesis_tracker.webapp.jobs import JobStore
    from thesis_tracker.webapp.service import job_view

    store = JobStore(tmp_path / "jobs.db")
    job = store.create("analyze", {"ticker": "NVDA", "horizon": "short",
                                   "as_of": "2026-10-04"})
    store.mark_running(job["job_id"])
    store.append_progress(job["job_id"], {"event": "prefetch", "tool_calls": 18,
                                          "base_pack_bytes": 15259, "catalog_bytes": 3699})
    store.finish(job["job_id"], status="succeeded",
                 result={"card_id": "c" * 36, "ticker": "NVDA", "as_of": "2026-10-04",
                         "horizon": "短期", "stats": {"input_tokens": 9099,
                                                      "output_tokens": 5217}})
    view = job_view(store.get(job["job_id"]))
    for key in ("created_at", "started_at", "finished_at"):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", view[key]), (key, view[key])
    assert view["parameter_summary"] == "公司 NVDA｜周期 短期｜分析截至 2026-10-04"
    assert [item["label"] for item in view["parameters"]] == ["公司", "周期", "分析截至"]
    assert all(item["at_display"] for item in view["progress"])
    assert "T" not in view["progress"][0]["at_display"]


def test_a_job_with_no_timestamps_yet_shows_a_dash(tmp_path):
    from thesis_tracker.webapp.jobs import JobStore
    from thesis_tracker.webapp.service import job_view

    store = JobStore(tmp_path / "jobs.db")
    job = store.create("analyze", {"ticker": "AAPL", "horizon": "mid",
                                   "as_of": "2026-10-04"})
    view = job_view(store.get(job["job_id"]))
    assert view["started_at"] is None and view["finished_at"] is None
    assert view["created_at"]


def test_the_card_id_short_form_is_eight_characters(detail):
    assert detail["card_id_short"] == detail["card_id"][:8]
    assert len(detail["card_id_short"]) == 8


def test_a_job_view_exposes_the_short_card_id_and_the_full_one(tmp_path):
    from thesis_tracker.webapp.jobs import JobStore
    from thesis_tracker.webapp.service import job_view

    store = JobStore(tmp_path / "jobs.db")
    job = store.create("analyze", {"ticker": "NVDA", "horizon": "short",
                                   "as_of": "2026-10-04"})
    card_id = "48f0260e-9c9d-428b-a6e0-2b6099b58410"
    store.finish(job["job_id"], status="succeeded",
                 result={"card_id": card_id, "ticker": "NVDA", "as_of": "2026-10-04",
                         "horizon": "短期"})
    view = job_view(store.get(job["job_id"]))
    assert view["result"]["card_id"] == card_id
    assert view["result"]["card_id_short"] == "48f0260e"


def test_a_progress_event_that_ended_a_run_carries_a_plain_reason():
    """The job page must not print a raw code such as ``correction_limit``."""
    from thesis_tracker.webapp.service import job_view

    job = {"job_id": "j" * 36, "kind": "analyze", "kind_label": "生成建议卡",
           "status": "failed", "status_label": "失败",
           "parameters": {"ticker": "NVDA"}, "created_at": None, "started_at": None,
           "finished_at": None, "result": None, "error": "x",
           "progress": [{"event": "rejected", "reason": "correction_limit",
                         "rules": ["D02"], "at": "2026-10-05T00:00:00+00:00"},
                        {"event": "rejected", "reason": "brand_new_reason", "rules": []}]}
    first, second = job_view(job)["progress"]
    assert first["reason_label"] == "连续多次修正仍未通过校验"
    assert "correction_limit" not in first["reason_label"]
    # An unknown code is still reported, in words that say it is unknown.
    assert "brand_new_reason" in second["reason_label"]


def test_a_derived_fact_row_carries_its_formula_with_named_operands():
    """The hover text for a computed result: what was done, to which two numbers."""
    from thesis_tracker.webapp.service import fact_rows

    derived = {"fact_id": "derived|chat|abc", "name": "compare_difference",
               "label": "差值", "value": "1.5", "unit": "USD/share",
               "date_or_period": "2026-09-07", "category": "derived",
               "source": {"provider": "derived", "formula": "a - b",
                          "source_fact_ids": ["tiingo|AAPL|2026-09-07|daily",
                                              "card|c1|stop_loss"]}}
    plain = {"fact_id": "tiingo|AAPL|2026-09-07|daily", "name": "close", "value": "249.9",
             "unit": "USD/share", "date_or_period": "2026-09-07", "category": "market",
             "source": {"provider": "tiingo"}}
    rows = fact_rows([derived, plain])
    assert rows[0]["formula_text"] == ("a - b，a = tiingo|AAPL|2026-09-07|daily，"
                                       "b = card|c1|stop_loss")
    assert rows[0]["category"] == "derived"
    assert rows[1]["formula_text"] is None
