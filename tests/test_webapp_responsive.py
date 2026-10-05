"""Layout rules that keep the page free of whole-page horizontal scrolling.

There is no headless browser in this checkout, so this asserts the structural
rules a browser would need rather than pixels: every wide element has its own
scroll container, grid children can shrink, and no rule pins a width that could
outgrow a 360px viewport.  The visual check is recorded separately as
not-verified.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src/thesis_tracker/webapp/static"
CSS = (STATIC / "app.css").read_text(encoding="utf-8")


def rules(selector: str) -> list[str]:
    return re.findall(rf"^{re.escape(selector)}\s*\{{(.*?)\}}", CSS, re.S | re.M)


def body(selector: str, index: int = 0) -> str:
    found = rules(selector)
    assert found, f"missing rule for {selector}"
    return found[index]


@pytest.mark.parametrize("width", [360, 390, 768, 900, 1280, 1920])
def test_no_rule_pins_a_page_level_width(width):
    """No fixed page width may exceed the narrowest supported viewport."""
    for match in re.finditer(r"^\s*(?:width|min-width)\s*:\s*(\d+)px", CSS, re.M):
        value = int(match.group(1))
        assert value <= width, f"a fixed {value}px width would overflow a {width}px viewport"


def test_the_shell_columns_can_shrink_below_their_content():
    shell = body(".shell")
    assert "minmax(0, 1fr)" in shell, shell
    assert "min-width: 0" in body("main")
    assert "min-width: 0" in body("a", 0) or "min-width: 0" in CSS


def test_wide_tables_scroll_inside_their_own_container():
    scroll = body(".table-scroll")
    assert "overflow: auto" in scroll
    assert "max-width: 100%" in scroll
    # The coverage table is not allowed to stretch the page: the wrapper owns it.
    assert "overflow-x: visible" not in CSS


def test_the_reading_column_has_a_measure_and_the_evidence_column_collapses():
    card_layout = body(".card-layout")
    assert "grid-template-columns: minmax(0, 640px) minmax(300px, 440px)" in card_layout
    narrow = CSS[CSS.index("@media (max-width: 1099px)"):]
    assert "grid-template-columns: minmax(0, 1fr)" in narrow
    assert "position: static" in narrow or "position:static" in narrow


def test_the_sidebar_becomes_a_wrapping_top_bar_on_narrow_screens():
    narrow = CSS[CSS.index("@media (max-width: 899px)"):]
    assert "flex-direction: row" in narrow
    assert "flex-wrap: wrap" in narrow


def test_sticky_positions_are_used_only_where_the_design_calls_for_them():
    """The header rows, the company column, three summary columns, the evidence panel,
    and the copy-confirmation toast (the one element that follows the viewport)."""
    sticky = set(re.findall(r"^([^{}]+?)\s*\{[^}]*position:\s*sticky", CSS, re.S | re.M))
    normalised = {selector.strip().split("\n")[-1].strip() for selector in sticky}
    # Five: the sticky coverage header, the company column, the three pinned
    # summary columns, the evidence panel, and the toast.  The toast is sticky
    # rather than fixed on purpose (see test_nothing_is_positioned_against_the_
    # viewport): it needs the viewport's bottom edge and nothing else.
    assert len(sticky) == 5, sticky
    assert ".toast" in normalised
    assert any("thead th" in selector for selector in normalised)
    assert ".stick" in normalised and ".tail" in normalised and ".evidence" in normalised
    # The pinned summary columns are offset by their neighbours' widths.
    assert "right: 0" in CSS
    assert ":nth-last-child(2)" in CSS and "right: 53px" in CSS
    assert ":nth-last-child(3)" in CSS and "right: 168px" in CSS


def test_nothing_is_positioned_against_the_viewport():
    """Fixed positioning plus a nested scroller is the usual cause of page overflow."""
    assert "float:" not in CSS
    assert "position: fixed" not in CSS
    # Absolutely positioned things (the skip link, band marks) sit in a
    # relatively positioned parent, so they cannot escape their own container.
    assert "position: relative" in CSS


def test_touch_targets_are_not_smaller_than_the_control_radius_allows():
    button = body("button", 0)
    padding = re.search(r"padding:\s*(\d+)px\s+(\d+)px", button)
    assert padding is not None
    assert int(padding.group(1)) >= 2
