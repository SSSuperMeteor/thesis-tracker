"""The interface never explains how it is built, and never formats a timestamp."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src/thesis_tracker/webapp/static"
SOURCES = sorted(path for path in STATIC.rglob("*")
                 if path.suffix in {".js", ".html", ".css"})


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# Wording that describes the implementation instead of helping the reader.
# Each of these was visible in the shipped interface before this round.
BANNED_COPY = (
    "不做假进度条",
    "位置由后端按价格算出",
    "（Python）",
    "只读路径",
    "后端",
    "前端",
    "字段",
    "只做显示",
    "占位",
    "由 Python",
    "接口",
    "复用",
)


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_no_internal_or_implementation_copy_is_shipped(path):
    text = read(path)
    offenders = [phrase for phrase in BANNED_COPY if phrase in text]
    assert not offenders, (path.name, offenders)


def test_the_two_named_phrases_are_gone_from_the_whole_frontend():
    blob = "\n".join(read(path) for path in SOURCES)
    assert "不做假进度条" not in blob
    assert "位置由后端按价格算出" not in blob


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_the_frontend_never_parses_or_formats_a_timestamp(path):
    text = read(path)
    banned = {
        "new Date": r"\bnew\s+Date\b",
        "Date.parse": r"Date\.parse",
        "toISOString": r"toISOString",
        "getHours": r"getHours\s*\(",
        "getMinutes": r"getMinutes\s*\(",
        "toLocaleDateString": r"toLocaleDateString",
        "toLocaleTimeString": r"toLocaleTimeString",
        "substring on a timestamp": r"created_at\.(slice|substring|substr)",
        "manual split of a timestamp": r"created_at\.split",
    }
    offenders = {name: pattern for name, pattern in banned.items()
                 if re.search(pattern, text)}
    assert not offenders, offenders


def test_timestamps_come_from_backend_strings_only():
    script = read(STATIC / "app.js")
    # Every timestamp the pages render is a plain backend field.
    for field in ("created_at", "started_at", "finished_at", "at_display"):
        assert field in script, field
    assert "formatTimestamp" not in script
    assert "timeZone" not in script


def test_the_job_page_uses_the_backend_parameter_labels():
    script = read(STATIC / "app.js")
    assert "parameter_summary" in script
    # The raw key=value renderer is gone; tool arguments inside a step line are
    # printed as "key value" pairs, never as "key=value".
    assert "parameterText" not in script
    assert '`${key}=${' not in script
    step_lines = script.split("function stepLine")[1].split("async function viewJob")[0]
    assert '`${key}=' not in step_lines and "key}=" not in step_lines


def test_the_card_list_uses_the_short_rule_mark_and_the_old_rules_flag():
    script = read(STATIC / "app.js")
    assert "version_mark" in script
    assert "rules_label" in script
    # The two long version columns are gone from the list view.
    card_list = script.split("async function viewCards")[1].split("async function viewCard")[0]
    assert "validator_version" not in card_list
    assert "prompt_version" not in card_list
    assert "规则版本" in card_list


def test_the_detail_page_prints_the_short_id_once_and_offers_the_full_one():
    script = read(STATIC / "app.js")
    assert "card_id_short" in script
    assert "复制完整编号" in script
    assert "打开建议卡" in script


def test_the_overview_ships_a_legend():
    script = read(STATIC / "app.js")
    for phrase in ("年报 10-K", "季报 10-Q", "修订申报", "没有财报"):
        assert phrase in script, phrase


def test_a_card_cell_with_filings_is_visually_distinct_from_a_missing_one():
    css = read(STATIC / "app.css")
    filled = re.search(r"\.cell\.has-filing\s*\{([^}]*)\}", css)
    assert filled is not None, "a filled coverage cell needs its own rule"
    assert "background" in filled.group(1)
    missing = re.search(r"\.cell\.is-missing[^{]*\{([^}]*)\}", css)
    assert missing is not None
    assert "dashed" in missing.group(1)


def test_only_clickable_text_uses_the_accent_colour():
    """Plain labels such as the bias column must not read as links."""
    css = read(STATIC / "app.css")
    for selector in ("td.bias", "td.action", "td.plain"):
        rule = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", css)
        if rule is not None:
            assert "color: var(--accent)" not in rule.group(1), selector
    # The ticker link keeps the accent, because it is a link.
    assert re.search(r"\.stick \.ticker\s*\{[^}]*\}", css)
    assert ".ticker" not in [
        match.group(1).strip()
        for match in re.finditer(r"([^{}]+)\{[^}]*color:\s*var\(--ink\)", css)
    ]
