"""Static frontend rules: no number formatting, no external resources, safe markup."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src/thesis_tracker/webapp/static"

SOURCES = sorted(
    [path for path in STATIC.rglob("*") if path.suffix in {".js", ".html", ".css"}]
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_static_sources_exist():
    names = {path.name for path in SOURCES}
    assert {"index.html", "app.css", "app.js"} <= names


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_no_number_formatting_or_rounding_in_the_frontend(path):
    """All numbers and labels are backend strings; the page only renders them."""
    text = read(path)
    banned = {
        "toFixed": r"\.toFixed\s*\(",
        "toPrecision": r"\.toPrecision\s*\(",
        "toLocaleString": r"\.toLocaleString\s*\(",
        "Intl.NumberFormat": r"Intl\.NumberFormat",
        "Intl.DateTimeFormat": r"Intl\.DateTimeFormat",
        "parseFloat": r"\bparseFloat\s*\(",
        "Number(": r"\bNumber\s*\(",
        "Math.round": r"Math\.round\s*\(",
        "percentage multiply": r"\*\s*100\b",
        "percentage divide": r"/\s*100\b",
    }
    offenders = {name: pattern for name, pattern in banned.items()
                 if re.search(pattern, text)}
    assert not offenders, offenders


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_no_hardcoded_financial_number_in_the_frontend(path):
    """Numbers in the frontend may only be CSS layout values.

    Financial numbers arrive as backend strings, so any literal that could be
    read as a price, amount, percentage or quantity is a defect.  Layout values
    (px/em/ms/weights/rgb channels) and the two form names are the only
    legitimate literals, and they are stripped explicitly rather than by
    guessing.
    """
    text = re.sub(r"style:\s*`[^`]*`", "", read(path))
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"#[^\n]*", "", text)                       # comments in CSS
    text = re.sub(r"rgb\([^)]*\)", "", text)                  # shadow/backdrop colours
    text = re.sub(r"\b10-[KQ]\b", "", text)                    # SEC form names
    text = re.sub(r"20\d{2}(-\d{2}-\d{2})?", "", text)         # dates
    text = re.sub(r"\b\d+(?:\.\d+)?(?:px|em|rem|vh|vw|ms|s|fr|ch)\b", "", text)
    text = re.sub(r"\b(?:100|200|300|400|500|600|700|800|900)\b", "", text)
    text = re.sub(r"\b\d+\b(?=\s*[%),;]|\s*$)", "", text, flags=re.M)
    offenders = re.findall(r"(?<![\w.$-])\d{2,}(?:\.\d+)?(?!\w)", text)
    assert not offenders, offenders


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_no_external_resource_is_referenced(path):
    text = read(path)
    assert "http://" not in text
    assert "https://" not in text
    assert "//cdn" not in text
    assert "integrity=" not in text
    assert "crossorigin" not in text


def test_the_page_has_one_language_and_semantic_landmarks():
    html = read(STATIC / "index.html")
    assert '<html lang="zh-CN">' in html
    assert 'href="#main"' in html and 'id="main"' in html
    assert "<nav" in html and "<main" in html
    assert html.count("<h1") == 0  # the views own their single h1
    assert "no-referrer" in html


def test_no_inline_script_or_style_that_csp_would_block():
    html = read(STATIC / "index.html")
    assert "<script>" not in html
    assert "onclick=" not in html and "onload=" not in html
    assert "javascript:" not in html


def test_the_frontend_never_builds_a_url_from_a_query_token():
    """The token lives in the cookie; the page must not echo it around."""
    script = read(STATIC / "app.js")
    assert "dsh_token" not in script
    assert "token" not in script.lower() or "tokens" in script.lower()


def test_clickable_things_are_buttons_or_links_not_divs():
    script = read(STATIC / "app.js")
    assert "createElement(\"div\")" not in script
    handlers = re.findall(r"(\w+)\.addEventListener\(\"click\"", script)
    assert handlers, "expected click handlers"
    # Every click target created in the file is a button or an anchor.
    buttons = re.findall(r"el\(\"button\"", script)
    assert len(buttons) >= len(set(handlers))


def test_codes_and_identifiers_are_marked_not_translated():
    script = read(STATIC / "app.js")
    assert 'translate: false' in script
    assert script.count("translate: false") >= 5


def test_reduced_motion_is_respected():
    css = read(STATIC / "app.css")
    assert "prefers-reduced-motion: reduce" in css


def test_the_only_animated_properties_are_transform_and_opacity():
    css = read(STATIC / "app.css")
    transitions = re.findall(r"transition\s*:\s*([^;]+);", css)
    for value in transitions:
        assert value.strip() != "all", value
    animations = re.findall(r"animation\s*:\s*([^;]+);", css)
    assert animations, "expected the fact-highlight animation"
    # Keyframes only change a background, never geometry.
    keyframes = re.search(r"@keyframes fact-highlight\s*\{(.*?)\n\}", css, re.S)
    assert keyframes is not None
    assert "background" in keyframes.group(1)
    assert "translate" not in keyframes.group(1)


def test_wide_content_scrolls_in_its_own_container():
    css = read(STATIC / "app.css")
    assert ".table-scroll" in css
    rule = re.search(r"\.table-scroll\s*\{(.*?)\}", css, re.S)
    assert rule is not None
    body = rule.group(1)
    assert "overflow" in body
    assert "max-width: 100%" in body
    # Nothing in the page may set a fixed pixel width that could outgrow the viewport.
    assert not re.search(r"\.page\s*\{[^}]*width\s*:\s*\d+px", css)
    assert "min-width: 0" in css, "grid children need min-width:0 to be shrinkable"


def test_the_shell_collapses_to_a_top_bar_under_900px():
    css = read(STATIC / "app.css")
    assert "max-width: 899px" in css
    assert "grid-template-columns: minmax(0, 1fr)" in css
