"""Filing-local semantic metadata for non-authoritative concept proposals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from thesis_tracker.financial.models import FilingBoundary, Unit


def normalize_presentation_qname(qname: str) -> str:
    """Convert an XBRL QName to edgartools' presentation identifier."""

    return qname.replace(":", "_", 1)


def presentation_qname(identifier: str) -> str:
    """Convert an edgartools presentation identifier back to a QName."""

    return identifier.replace("_", ":", 1)


@dataclass(frozen=True, slots=True)
class PresentationMetadata:
    concept: str
    label: str
    documentation: str | None
    statement: str
    position: int
    level: int
    weight: Decimal | None
    parent_concept: str | None
    parent_abstract_concept: str | None
    is_abstract: bool
    is_breakdown: bool
    dimensions_present: bool
    period_type: str | None


@dataclass(frozen=True, slots=True)
class SemanticFactCandidate:
    ticker: str
    accession: str
    concept: str
    value: Decimal
    unit: Unit
    period_type: str
    period_start: date | None
    period_end: date
    fiscal_year: int
    fiscal_period: str
    context_id: str
    dimensions: tuple[tuple[str, str], ...]
    source: str
    extraction_path: str

    @property
    def fact_id(self) -> str:
        return f"{self.accession}|{self.concept}|{self.context_id}"


@dataclass(frozen=True, slots=True)
class FilingSemanticContext:
    boundary: FilingBoundary
    presentations: tuple[PresentationMetadata, ...]
    facts: tuple[SemanticFactCandidate, ...]


def _as_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _as_decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        candidate = Decimal(str(value).replace(",", "").strip())
        return candidate if candidate.is_finite() else None
    except InvalidOperation:
        return None


def _unit(row: dict[str, Any]) -> Unit | None:
    currency = str(row.get("currency") or "").upper()
    unit_ref = str(row.get("unit_ref") or "")
    if currency == "USD" or unit_ref.upper() == "USD":
        return Unit.USD
    return None


def _field(value: object, name: str) -> object | None:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _catalog_element(xbrl: Any, identifier: str, qname: str) -> object | None:
    catalog = getattr(xbrl, "element_catalog", {})
    getter = getattr(catalog, "get", None)
    if getter is None:
        return None
    return getter(identifier) or getter(qname)


def _optional_qname(value: object) -> str | None:
    if value in (None, ""):
        return None
    return presentation_qname(str(value))


def _has_metadata_value(value: object) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    try:
        return bool(value == value)
    except (TypeError, ValueError):
        return False


def extract_filing_semantic_context(
    boundary: FilingBoundary,
    xbrl: Any,
) -> FilingSemanticContext:
    """Extract exact numeric facts only from one filing's income statement.

    Presentation rows may describe abstract or dimensioned concepts. Numeric
    candidates are deliberately narrower: USD, consolidated facts ending at
    the filing boundary. No concept is selected or mapped here.
    """

    statement = xbrl.statements.income_statement()
    if statement is None:
        return FilingSemanticContext(boundary, (), ())
    rows = statement.to_dataframe().to_dict(orient="records")
    presentations: list[PresentationMetadata] = []
    presented: set[str] = set()
    for position, row in enumerate(rows):
        identifier = str(row.get("concept") or "")
        if not identifier:
            continue
        concept = presentation_qname(identifier)
        element = _catalog_element(xbrl, identifier, concept)
        weight = _as_decimal(row.get("weight"))
        presentations.append(
            PresentationMetadata(
                concept=concept,
                label=str(row.get("label") or concept),
                documentation=(
                    str(documentation)
                    if (documentation := _field(element, "documentation"))
                    not in (None, "")
                    else None
                ),
                statement="income_statement",
                position=position,
                level=int(row.get("level") or 0),
                weight=weight,
                parent_concept=_optional_qname(row.get("parent_concept")),
                parent_abstract_concept=_optional_qname(
                    row.get("parent_abstract_concept")
                ),
                is_abstract=bool(row.get("abstract")),
                is_breakdown=bool(row.get("is_breakdown")),
                dimensions_present=_has_metadata_value(row.get("dimension")),
                period_type=(
                    str(period_type)
                    if (period_type := _field(element, "period_type"))
                    not in (None, "")
                    else None
                ),
            )
        )
        presented.add(concept)

    facts: list[SemanticFactCandidate] = []
    for row in xbrl.facts.get_facts():
        concept = str(row.get("concept") or "")
        if concept not in presented:
            continue
        context_id = str(row.get("context_ref") or "")
        context = xbrl.contexts.get(context_id)
        if not context_id or context is None:
            continue
        raw_dimensions = getattr(context, "dimensions", None) or {}
        dimensions = tuple(
            sorted((str(axis), str(member)) for axis, member in raw_dimensions.items())
        )
        # Dimensioned rows remain presentation evidence only. A semantic model
        # must not promote segment facts into the consolidated equation.
        if dimensions:
            continue
        value = _as_decimal(row.get("value"))
        unit = _unit(row)
        period_end = _as_date(row.get("period_end"))
        if value is None or unit is None or period_end != boundary.period_end:
            continue
        candidate = SemanticFactCandidate(
            ticker=boundary.ticker,
            accession=boundary.accession,
            concept=concept,
            value=value,
            unit=unit,
            period_type=str(row.get("period_type") or ""),
            period_start=_as_date(row.get("period_start")),
            period_end=period_end,
            fiscal_year=int(row.get("fiscal_year") or boundary.fiscal_year),
            fiscal_period=str(row.get("fiscal_period") or boundary.fiscal_period),
            context_id=context_id,
            dimensions=dimensions,
            source="sec_filing_xbrl",
            extraction_path="filing.xbrl().facts.get_facts()",
        )
        if candidate not in facts:
            facts.append(candidate)
    return FilingSemanticContext(
        boundary=boundary,
        presentations=tuple(presentations),
        facts=tuple(facts),
    )


def build_ai_candidate_payload(
    context: FilingSemanticContext,
    target_concept: str,
) -> dict[str, object]:
    """Build a small, auditable prompt payload from one filing only."""

    boundary = context.boundary
    return {
        "ticker": boundary.ticker,
        "form": boundary.form,
        "accession": boundary.accession,
        "filed_at": boundary.filed_at.isoformat(),
        "fiscal_year": boundary.fiscal_year,
        "fiscal_period": boundary.fiscal_period,
        "period_end": boundary.period_end.isoformat(),
        "target": target_concept,
        "statement": "income_statement",
        "concepts": [
            {
                "concept": item.concept,
                "label": item.label,
                "documentation": item.documentation,
                "position": item.position,
                "level": item.level,
                "weight": str(item.weight) if item.weight is not None else None,
                "parent_concept": item.parent_concept,
                "parent_abstract_concept": item.parent_abstract_concept,
                "is_abstract": item.is_abstract,
                "is_breakdown": item.is_breakdown,
                "dimensions_present": item.dimensions_present,
                "period_type": item.period_type,
            }
            for item in context.presentations
        ],
        "equation_facts": [
            {
                "fact_id": fact.fact_id,
                "concept": fact.concept,
                "value": str(fact.value),
                "unit": fact.unit.value,
                "period_type": fact.period_type,
                "period_start": (
                    fact.period_start.isoformat()
                    if fact.period_start is not None
                    else None
                ),
                "period_end": fact.period_end.isoformat(),
                "fiscal_year": fact.fiscal_year,
                "fiscal_period": fact.fiscal_period,
                "context_id": fact.context_id,
                "dimensions": dict(fact.dimensions),
            }
            for fact in context.facts
        ],
    }
