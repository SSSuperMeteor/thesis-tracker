"""The design tokens must keep every text/background pair readable.

This reads ``app.css`` the way the browser would: the ``:root`` block is the
light theme and the ``prefers-color-scheme: dark`` block overrides it.  Nothing
here is hand-copied, so a token edit that breaks contrast fails the suite.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS_PATH = (Path(__file__).resolve().parents[1]
            / "src/thesis_tracker/webapp/static/app.css")

# WCAG 2.1: 4.5:1 for normal text, 3:1 for control boundaries and focus rings.
TEXT_MIN = 4.5
UI_MIN = 3.0

# Every pair the interface actually renders.  Reading text sits on the page or
# on a panel; muted text (--ink-2) is the quietest text colour that exists, and
# --none is decorative only (asserted below).
TEXT_PAIRS = [
    ("--ink", "--paper"), ("--ink", "--surface"), ("--ink", "--surface-2"),
    ("--ink-2", "--paper"), ("--ink-2", "--surface"), ("--ink-2", "--surface-2"),
    ("--accent", "--paper"), ("--accent", "--surface"), ("--accent", "--surface-2"),
    ("--accent", "--accent-soft"),
    ("--ok", "--paper"), ("--ok", "--surface"), ("--ok", "--surface-2"),
    ("--warn", "--paper"), ("--warn", "--surface"), ("--warn", "--surface-2"),
    ("--bad", "--paper"), ("--bad", "--surface"), ("--bad", "--surface-2"),
]

# Non-text marks that carry information or bound an interactive control need
# 3:1: the form-control outline, the hollow "missing" mark, and the focus ring.
# --rule is deliberately excluded: hairline dividers between table rows and
# panels are pure structure, and forcing them to 3:1 would make every panel a
# heavy box.  That exclusion is a decision, recorded here.
UI_PAIRS = [
    ("--ctl-rule", "--paper"), ("--ctl-rule", "--surface"), ("--ctl-rule", "--surface-2"),
    ("--accent", "--paper"), ("--accent", "--surface"), ("--accent", "--surface-2"),
    ("--none", "--paper"), ("--none", "--surface"), ("--none", "--surface-2"),
]


def _tokens(block: str) -> dict[str, str]:
    return {name: value.lower() for name, value in re.findall(
        r"(--[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;", block)}


def _theme_blocks(css: str) -> tuple[str, str]:
    root = re.search(r":root\s*\{(.*?)\}", css, re.S)
    assert root is not None, "app.css must define :root tokens"
    dark_marker = css.index("prefers-color-scheme: dark")
    dark = re.search(r":root\s*\{(.*?)\}", css[dark_marker:], re.S)
    assert dark is not None, "app.css must define dark-mode token overrides"
    return root.group(1), dark.group(1)


def themes() -> dict[str, dict[str, str]]:
    css = CSS_PATH.read_text(encoding="utf-8")
    light_block, dark_block = _theme_blocks(css)
    light = _tokens(light_block)
    dark = dict(light)
    dark.update(_tokens(dark_block))
    assert set(light) == set(dark), "both themes must define the same token names"
    return {"light": light, "dark": dark}


def _channel(value: int) -> float:
    srgb = value / 255
    return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4


def luminance(colour: str) -> float:
    text = colour.lstrip("#")
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    assert len(text) == 6, colour
    red, green, blue = (int(text[index:index + 2], 16) for index in (0, 2, 4))
    return (0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue))


def contrast(foreground: str, background: str) -> float:
    first, second = luminance(foreground), luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


@pytest.fixture(scope="module")
def theme() -> dict[str, dict[str, str]]:
    return themes()


@pytest.mark.parametrize("name", ["light", "dark"])
def test_every_text_pair_reaches_4_5_to_1(theme, name):
    tokens = theme[name]
    failures = []
    for foreground, background in TEXT_PAIRS:
        ratio = contrast(tokens[foreground], tokens[background])
        if ratio < TEXT_MIN:
            failures.append(f"{name}: {foreground} on {background} = {ratio:.2f}:1")
    assert not failures, failures


@pytest.mark.parametrize("name", ["light", "dark"])
def test_control_boundaries_and_focus_rings_reach_3_to_1(theme, name):
    tokens = theme[name]
    failures = []
    for foreground, background in UI_PAIRS:
        ratio = contrast(tokens[foreground], tokens[background])
        if ratio < UI_MIN:
            failures.append(f"{name}: {foreground} on {background} = {ratio:.2f}:1")
    assert not failures, failures


def test_the_page_background_and_surface_stay_distinguishable(theme):
    for name, tokens in theme.items():
        assert tokens["--paper"] != tokens["--surface"], name
        assert contrast(tokens["--paper"], tokens["--surface"]) > 1.0


def test_the_decorative_missing_colour_is_never_used_for_text():
    """--none is documented as decoration only; prove nothing paints text with it."""
    css = CSS_PATH.read_text(encoding="utf-8")
    offenders = [line.strip() for line in css.splitlines()
                 if "var(--none)" in line and re.search(r"(^|[;{\s])color\s*:", line)]
    assert not offenders, offenders


def test_no_page_rule_hardcodes_a_colour_outside_the_token_block():
    css = CSS_PATH.read_text(encoding="utf-8")
    body = css[css.index("/* ---------------------------------------------------------------\n   base"):]
    hexes = [value for value in re.findall(r"#[0-9a-fA-F]{3,8}\b", body)]
    # Only the two dialogs' shadow may name a colour outside the token blocks,
    # and it is expressed as rgb() rather than a hex literal.
    assert not hexes, hexes


def test_the_design_tokens_cover_every_colour_the_pages_need(theme):
    for tokens in theme.values():
        for required in ("--paper", "--surface", "--surface-2", "--ink", "--ink-2",
                         "--rule", "--ctl-rule", "--accent", "--accent-soft", "--ok",
                         "--warn", "--bad", "--none"):
            assert required in tokens, required
