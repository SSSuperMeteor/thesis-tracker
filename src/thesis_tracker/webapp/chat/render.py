"""Turn a validated answer template into text and display segments.

The model writes ``{fact:<id>}`` and ``{card:<id>:<field>}``; this module renders
them.  Two annotations are added, and both exist to stop a judgment being read as
a fact:

* a card's fields are labelled ``（AI 判断，建议卡 <8 位>，YYYY-MM-DD）``;
* a fact whose own date is not the newest stored price day is labelled
  ``（截至 YYYY-MM-DD）``.

The frontend receives segments and renders them; it never parses a placeholder.
"""

from __future__ import annotations

from thesis_tracker.webapp.chat.validate import (
    CARD_PLACEHOLDER,
    FACT_PLACEHOLDER,
    Evidence,
)
from thesis_tracker.webapp.display import format_date, format_timestamp

JUDGMENT_ANNOTATION = "AI 判断"
ALSO_KNOWN_AS = "建议卡"


def render_answer(body: str, *, evidence: Evidence) -> tuple[str, list[dict]]:
    """Return (rendered text, segments).  Concatenating the segments gives the text."""
    text = body or ""
    segments: list[dict] = []
    cursor = 0
    for match in _placeholders(text):
        if match.start() > cursor:
            segments.append({"type": "text", "value": text[cursor:match.start()]})
        segments.append(_segment(match, evidence))
        cursor = match.end()
    if cursor < len(text):
        segments.append({"type": "text", "value": text[cursor:]})
    return "".join(_segment_text(item) for item in segments), segments


def _placeholders(text: str):
    found = sorted([*FACT_PLACEHOLDER.finditer(text), *CARD_PLACEHOLDER.finditer(text)],
                   key=lambda match: match.start())
    return found


def _segment(match, evidence: Evidence) -> dict:
    raw = match.group(1)
    if match.re is CARD_PLACEHOLDER:
        card_id, _, field_name = raw.partition(":")
        card = evidence.card(card_id) or {}
        fields = card.get("fields") or {}
        date = format_date(card.get("as_of")) or (fields.get("created_at") or "")[:10]
        return {"type": "card", "card_id": card_id, "field": field_name,
                "display": fields.get(field_name) or "",
                "annotation": JUDGMENT_ANNOTATION,
                "annotation_date": date,
                "annotation_source": f"{ALSO_KNOWN_AS} {card_id[:8]}"}
    fact_id = raw
    fact = evidence.fact(fact_id) or {}
    annotation_date = _annotation_date(fact, evidence)
    return {"type": "fact", "fact_id": fact_id,
            "display": fact.get("display") or "",
            "annotation": None if annotation_date is None else "截至",
            "annotation_date": annotation_date,
            "name": fact.get("name"),
            "unit": fact.get("unit")}


def _annotation_date(fact: dict, evidence: Evidence) -> str | None:
    """The date to annotate this fact with, or None when it is current."""
    day = fact.get("date_or_period")
    if not day:
        return None
    if evidence.latest_price_date and day == evidence.latest_price_date:
        return None
    return str(day)


def _segment_text(item: dict) -> str:
    if item["type"] == "text":
        return item["value"]
    if item["type"] == "card":
        return (f"{item['display']}（{item['annotation']}，{item['annotation_source']}"
                f"，{item['annotation_date']}）")
    if item["annotation"]:
        return f"{item['display']}（{item['annotation']} {item['annotation_date']}）"
    return item["display"]


def render_display(segment: dict) -> str:
    """The visible text of one segment; the frontend renders the same way."""
    return _segment_text(segment)


def card_id_from_message(segments: list[dict] | None) -> str | None:
    """The card a system message is about, taken from its own segments."""
    for item in segments or []:
        if item.get("type") == "card_link":
            return item.get("card_id")
    return None


def format_created(created_at: str | None) -> str | None:
    """Kept here so callers do not reach for the display module directly."""
    return format_timestamp(created_at)
