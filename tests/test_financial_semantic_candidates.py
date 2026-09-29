from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from thesis_tracker.financial.models import FilingBoundary, Unit
from thesis_tracker.financial.semantic_candidates import (
    build_ai_candidate_payload,
    extract_filing_semantic_context,
    normalize_presentation_qname,
    presentation_qname,
)


class FakeFrame:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def to_dict(self, orient: str) -> list[dict[str, object]]:
        assert orient == "records"
        return self._rows


class FakeFacts:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def get_facts(self) -> list[dict[str, object]]:
        return self._rows


class FakeStatement:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def to_dataframe(self) -> FakeFrame:
        return FakeFrame(self._rows)


class FakeStatements:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def income_statement(self) -> FakeStatement:
        return FakeStatement(self._rows)


def _boundary(accession: str = "acc-current") -> FilingBoundary:
    return FilingBoundary(
        ticker="TEST",
        accession=accession,
        filed_at=date(2026, 5, 1),
        form="10-Q",
        fiscal_year=2026,
        fiscal_period="Q1",
        period_end=date(2026, 3, 31),
        source="sec_filing_metadata",
    )


def _raw_fact(
    concept: str,
    value: str,
    *,
    context_ref: str = "consolidated",
    period_end: str = "2026-03-31",
) -> dict[str, object]:
    return {
        "concept": concept,
        "context_ref": context_ref,
        "value": value,
        "unit_ref": "USD",
        "currency": "USD",
        "period_type": "duration",
        "period_start": "2026-01-01",
        "period_end": period_end,
        "fiscal_period": "Q1",
        "fiscal_year": 2026,
    }


def _xbrl() -> SimpleNamespace:
    statement_rows = [
        {
            "concept": "us-gaap_Revenues",
            "label": "Revenue",
            "level": 0,
            "weight": 1.0,
            "parent_concept": None,
            "parent_abstract_concept": "us-gaap_IncomeStatementAbstract",
            "abstract": False,
            "dimension": None,
            "is_breakdown": False,
        },
        {
            "concept": "test_CustomCloudCost",
            "label": "Cloud cost",
            "level": 1,
            "weight": -1.0,
            "parent_concept": "us-gaap_Revenues",
            "parent_abstract_concept": "test_CostsAbstract",
            "abstract": False,
            "dimension": None,
            "is_breakdown": False,
        },
        {
            "concept": "test_CustomCloudCost",
            "label": "Cloud cost by segment",
            "level": 2,
            "weight": -1.0,
            "parent_concept": "test_CustomCloudCost",
            "parent_abstract_concept": "test_CostsAbstract",
            "abstract": False,
            "dimension": "test_CloudMember",
            "is_breakdown": True,
        },
        {
            "concept": "test_CostsAbstract",
            "label": "Costs",
            "level": 0,
            "weight": None,
            "parent_concept": None,
            "parent_abstract_concept": None,
            "abstract": True,
            "dimension": None,
            "is_breakdown": False,
        },
    ]
    facts = [
        _raw_fact("us-gaap:Revenues", "100"),
        _raw_fact("test:CustomCloudCost", "60"),
        _raw_fact("test:CustomCloudCost", "20", context_ref="segment"),
        _raw_fact("test:BalanceSheetConcept", "999"),
        _raw_fact("test:CustomCloudCost", "66", period_end="2026-06-30"),
    ]
    return SimpleNamespace(
        statements=FakeStatements(statement_rows),
        facts=FakeFacts(facts),
        contexts={
            "consolidated": SimpleNamespace(dimensions={}),
            "segment": SimpleNamespace(
                dimensions={"us-gaap:SegmentAxis": "test:CloudMember"}
            ),
        },
        element_catalog={
            "us-gaap_Revenues": SimpleNamespace(
                documentation="Income from customers", period_type="duration"
            ),
            "test_CustomCloudCost": SimpleNamespace(
                documentation="Costs attributable to cloud revenue",
                period_type="duration",
            ),
            "test_CostsAbstract": SimpleNamespace(
                documentation="Cost presentation group", period_type="duration"
            ),
        },
    )


def test_qname_normalization_is_reversible() -> None:
    assert normalize_presentation_qname("test:CustomCloudCost") == (
        "test_CustomCloudCost"
    )
    assert presentation_qname("test_CustomCloudCost") == "test:CustomCloudCost"


def test_extracts_only_income_statement_exact_consolidated_facts() -> None:
    context = extract_filing_semantic_context(_boundary(), _xbrl())

    assert {item.concept for item in context.facts} == {
        "us-gaap:Revenues",
        "test:CustomCloudCost",
    }
    custom = next(item for item in context.facts if item.concept.startswith("test:"))
    assert custom.value == Decimal("60")
    assert isinstance(custom.value, Decimal)
    assert custom.unit is Unit.USD
    assert custom.period_start == date(2026, 1, 1)
    assert custom.period_end == date(2026, 3, 31)
    assert custom.context_id == "consolidated"
    assert custom.dimensions == ()
    assert custom.accession == "acc-current"


def test_presentation_metadata_retains_structure_and_dimension_presence() -> None:
    context = extract_filing_semantic_context(_boundary(), _xbrl())
    custom_rows = [
        item for item in context.presentations if item.concept == "test:CustomCloudCost"
    ]

    assert custom_rows[0].label == "Cloud cost"
    assert custom_rows[0].documentation == (
        "Costs attributable to cloud revenue"
    )
    assert custom_rows[0].parent_concept == "us-gaap:Revenues"
    assert custom_rows[0].parent_abstract_concept == "test:CostsAbstract"
    assert custom_rows[0].level == 1
    assert custom_rows[0].weight == Decimal("-1.0")
    assert custom_rows[0].statement == "income_statement"
    assert custom_rows[1].dimensions_present is True
    assert custom_rows[1].is_breakdown is True
    assert next(
        item for item in context.presentations if item.concept == "test:CostsAbstract"
    ).is_abstract is True


def test_prompt_payload_is_filing_local_and_excludes_future_and_unrelated_facts() -> None:
    context = extract_filing_semantic_context(_boundary(), _xbrl())

    payload = build_ai_candidate_payload(context, "cost_of_revenue")
    serialized = str(payload)

    assert payload["accession"] == "acc-current"
    assert payload["target"] == "cost_of_revenue"
    assert "test:CustomCloudCost" in serialized
    assert "test:BalanceSheetConcept" not in serialized
    assert "2026-06-30" not in serialized
    assert "acc-future" not in serialized


def test_conflicting_values_for_same_fact_identity_are_not_silently_deduplicated() -> None:
    xbrl = _xbrl()
    xbrl.facts._rows.append(_raw_fact("test:CustomCloudCost", "61"))

    context = extract_filing_semantic_context(_boundary(), xbrl)

    conflicts = [
        fact for fact in context.facts if fact.concept == "test:CustomCloudCost"
    ]
    assert [fact.value for fact in conflicts] == [Decimal("60"), Decimal("61")]


def test_dataframe_nan_is_not_treated_as_dimension_or_numeric_weight() -> None:
    xbrl = _xbrl()
    first = xbrl.statements._rows[0]
    first["dimension"] = float("nan")
    first["weight"] = float("nan")

    context = extract_filing_semantic_context(_boundary(), xbrl)
    revenue = context.presentations[0]

    assert revenue.dimensions_present is False
    assert revenue.weight is None
