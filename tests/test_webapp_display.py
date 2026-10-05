"""Display rules shared by every page: timestamps, parameter labels, version marks."""

from __future__ import annotations

from datetime import timedelta, timezone

import pytest

from thesis_tracker.webapp import display

# A fixed zone, injectable into every formatter, so these assertions never
# depend on the timezone of the machine running the tests.
FIXED = timezone(timedelta(hours=-7), "fixed-07")


@pytest.fixture(autouse=True)
def fixed_zone(monkeypatch):
    monkeypatch.setattr(display, "LOCAL_TIMEZONE", FIXED)


def test_timestamps_lose_seconds_microseconds_and_the_zone_suffix():
    assert display.format_timestamp("2026-10-05T02:48:14.071136+00:00") == "2026-10-04 19:48"
    assert display.format_timestamp("2026-10-05T00:44:06+00:00") == "2026-10-04 17:44"


def test_timestamps_are_converted_into_the_injected_zone():
    # 02:48 UTC is 19:48 the previous day at -07:00.
    assert display.format_timestamp("2026-10-05T02:48:00+00:00") == "2026-10-04 19:48"


def test_midnight_and_day_boundaries_use_the_local_date():
    assert display.format_timestamp("2026-10-04T07:00:00+00:00") == "2026-10-04 00:00"
    assert display.format_timestamp("2026-10-04T06:59:00+00:00") == "2026-10-03 23:59"


def test_a_naive_timestamp_is_read_as_utc_rather_than_shifted_silently():
    assert display.format_timestamp("2026-10-05T02:48:00") == "2026-10-04 19:48"


def test_an_unparsable_value_is_shown_unchanged_instead_of_hidden():
    assert display.format_timestamp("not a timestamp") == "not a timestamp"
    assert display.format_timestamp(None) is None


def test_a_non_default_zone_can_be_passed_explicitly():
    tokyo = timezone(timedelta(hours=9))
    assert display.format_timestamp("2026-10-05T02:48:00+00:00", zone=tokyo) == "2026-10-05 11:48"


def test_job_parameters_are_labelled_in_chinese():
    items = display.parameter_labels({"ticker": "NVDA", "horizon": "short",
                                      "as_of": "2026-10-04"})
    assert items == [
        {"key": "ticker", "label": "公司", "value": "NVDA"},
        {"key": "horizon", "label": "周期", "value": "短期"},
        {"key": "as_of", "label": "分析截至", "value": "2026-10-04"},
    ]


def test_parameter_summary_never_shows_a_raw_key():
    summary = display.parameter_summary({"ticker": "NVDA", "horizon": "short",
                                         "as_of": "2026-10-04"})
    assert summary == "公司 NVDA｜周期 短期｜分析截至 2026-10-04"
    for raw in ("as_of=", "horizon=", "ticker="):
        assert raw not in summary


@pytest.mark.parametrize(("key", "label"), [("short", "短期"), ("mid", "中期"),
                                            ("long", "长期"), ("weekly", "weekly")])
def test_horizon_labels_come_from_the_single_existing_mapping(key, label):
    assert display.horizon_label(key) == label


def test_an_unexpected_parameter_key_is_still_shown_with_its_own_name():
    items = display.parameter_labels({"ticker": "AAPL", "depth": 3})
    assert items[-1] == {"key": "depth", "label": "depth", "value": "3"}


def test_the_short_version_mark_parses_both_stored_strings():
    assert display.version_mark("decision-agent-v5-entry-stop-rules-2026-10-04",
                                "decision-validator-3") == "提示词 v5 / 校验 3"
    assert display.version_mark("decision-agent-v3-tool-contract-2026-10-04",
                                "decision-validator-1") == "提示词 v3 / 校验 1"


def test_an_unparsable_version_falls_back_to_the_original_string():
    assert display.version_mark("handwritten-prompt", "decision-validator-3") == (
        "校验 3（提示词 handwritten-prompt）")
    assert display.version_mark(None, None) == "—"
    # With nothing to pair it with, an unparsable string is shown as it is --
    # never abbreviated into a number that would be wrong.
    assert display.version_mark("handwritten", None) == "handwritten"
    assert display.version_mark(None, "experimental-rules") == "experimental-rules"


def test_the_version_mark_carries_no_long_hash_or_date_suffix():
    mark = display.version_mark("decision-agent-v5-entry-stop-rules-2026-10-04",
                                "decision-validator-3")
    assert "2026-10-04" not in mark and "entry-stop-rules" not in mark
    assert len(mark) <= 20


def test_current_rules_detection_compares_both_versions():
    from thesis_tracker.decision.agent import PROMPT_VERSION as current_prompt
    from thesis_tracker.decision.core import VALIDATOR_VERSION as current_validator

    assert display.is_current_rules(current_prompt, current_validator,
                                    current_validator=current_validator,
                                    current_prompt=current_prompt)
    assert not display.is_current_rules("decision-agent-v4-horizon-rules-2026-10-04",
                                        "decision-validator-2",
                                        current_validator=current_validator,
                                        current_prompt=current_prompt)
    assert not display.is_current_rules(current_prompt, "decision-validator-2",
                                        current_validator=current_validator,
                                        current_prompt=current_prompt)


def test_a_chat_turn_names_its_conversation_by_a_short_id_and_hides_the_message_id():
    parameters = {"ticker": "NVDA",
                  "conversation_id": "55328c51-845d-49c5-9c53-6e7000bb3a61",
                  "message_id": "81b8fe37-1694-44f9-9833-70dd687739da"}
    assert display.parameter_summary(parameters) == "公司 NVDA｜对话 55328c51"
    items = display.parameter_labels(parameters)
    assert [item["key"] for item in items] == ["ticker", "conversation_id"]


def test_no_job_summary_contains_a_full_identifier():
    summary = display.parameter_summary({
        "ticker": "NVDA", "conversation_id": "55328c51-845d-49c5-9c53-6e7000bb3a61",
        "message_id": "81b8fe37-1694-44f9-9833-70dd687739da"})
    assert "55328c51-" not in summary
    assert "message_id" not in summary and "conversation_id" not in summary
