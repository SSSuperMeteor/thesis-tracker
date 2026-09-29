from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from thesis_tracker.financial.ai_concepts import (
    CandidateConceptProposal,
    ConceptProposal,
    ProposalStatus,
    SemanticRelation,
)
from thesis_tracker.financial.ai_validation import (
    ValidationStatus,
    validate_concept_proposal,
)
from thesis_tracker.financial.models import FilingBoundary, Unit
from thesis_tracker.financial.semantic_candidates import (
    FilingSemanticContext,
    PresentationMetadata,
    SemanticFactCandidate,
)

BOUNDARY = FilingBoundary(
    ticker="TEST",
    accession="acc-1",
    filed_at=date(2026, 5, 1),
    form="10-Q",
    fiscal_year=2026,
    fiscal_period="Q1",
    period_end=date(2026, 3, 31),
    source="sec_filing_metadata",
)


def _fact(
    concept: str,
    value: str,
    *,
    accession: str = "acc-1",
    unit: Unit = Unit.USD,
    period_start: date = date(2026, 1, 1),
    period_end: date = date(2026, 3, 31),
    period_type: str = "duration",
    context_id: str = "consolidated",
    dimensions: tuple[tuple[str, str], ...] = (),
) -> SemanticFactCandidate:
    return SemanticFactCandidate(
        ticker="TEST",
        accession=accession,
        concept=concept,
        value=Decimal(value),
        unit=unit,
        period_type=period_type,
        period_start=period_start,
        period_end=period_end,
        fiscal_year=2026,
        fiscal_period="Q1",
        context_id=context_id,
        dimensions=dimensions,
        source="sec_filing_xbrl",
        extraction_path="filing.xbrl().facts.get_facts()",
    )


def _presentation(concept: str) -> PresentationMetadata:
    return PresentationMetadata(
        concept=concept,
        label=concept,
        documentation="definition",
        statement="income_statement",
        position=0,
        level=1,
        weight=Decimal("-1"),
        parent_concept="us-gaap:Revenues",
        parent_abstract_concept="test:CostsAbstract",
        is_abstract=False,
        is_breakdown=False,
        dimensions_present=False,
        period_type="duration",
    )


def _context(cost: str = "60") -> FilingSemanticContext:
    facts = (
        _fact("us-gaap:Revenues", "100"),
        _fact("us-gaap:GrossProfit", "40"),
        _fact("test:CustomCost", cost),
    )
    return FilingSemanticContext(
        boundary=BOUNDARY,
        presentations=tuple(_presentation(fact.concept) for fact in facts),
        facts=facts,
    )


def _proposal(
    concept: str = "test:CustomCost",
    relation: SemanticRelation = SemanticRelation.EQUIVALENT,
) -> ConceptProposal:
    return ConceptProposal(
        target="cost_of_revenue",
        status=ProposalStatus.CANDIDATE,
        candidate_concepts=(
            CandidateConceptProposal(
                concept=concept,
                semantic_relation=relation,
                reason="Filing labels it as the aggregate cost",
                evidence_fields=("label", "documentation", "equation"),
            ),
        ),
    )


def test_hallucinated_qname_is_rejected() -> None:
    result = validate_concept_proposal(_proposal("test:Invented"), _context(), BOUNDARY)

    assert result.status is ValidationStatus.REJECTED
    assert result.reason_code == "candidate_not_filing_local"


def test_same_accession_candidate_with_failed_equation_is_rejected() -> None:
    result = validate_concept_proposal(_proposal(), _context("61"), BOUNDARY)

    assert result.status is ValidationStatus.REJECTED
    assert result.reason_code == "equation_not_proven"


def test_exact_equation_and_matching_context_is_accepted_with_audit_evidence() -> None:
    result = validate_concept_proposal(_proposal(), _context(), BOUNDARY)

    assert result.status is ValidationStatus.ACCEPTED
    assert result.mapping is not None
    assert result.mapping.concepts == ("test:CustomCost",)
    evidence = result.evidence[0]
    assert evidence.equation == "100-60=40"
    assert evidence.accession == "acc-1"
    assert evidence.candidate_concept == "test:CustomCost"
    assert evidence.context_ids == (
        "consolidated",
        "consolidated",
        "consolidated",
    )
    assert evidence.source_fact_ids == (
        "acc-1|us-gaap:Revenues|consolidated",
        "acc-1|test:CustomCost|consolidated",
        "acc-1|us-gaap:GrossProfit|consolidated",
    )
    assert result.validator_path == (
        "filing_local",
        "semantic_relation",
        "context_compatibility",
        "decimal_strict_equation",
        "unique_mapping",
    )


def test_component_never_becomes_aggregate_from_numeric_coincidence() -> None:
    result = validate_concept_proposal(
        _proposal(relation=SemanticRelation.COMPONENT), _context(), BOUNDARY
    )

    assert result.status is ValidationStatus.REJECTED
    assert result.reason_code == "non_aggregate_relation"


@pytest.mark.parametrize(
    ("changed", "reason"),
    [
        ({"accession": "other-acc"}, "candidate_not_filing_local"),
        ({"unit": Unit.PURE}, "context_mismatch"),
        ({"period_start": date(2025, 1, 1)}, "context_mismatch"),
        ({"period_end": date(2026, 3, 30)}, "context_mismatch"),
        ({"period_type": "instant"}, "context_mismatch"),
        ({"context_id": "other-context"}, "context_mismatch"),
        (
            {"dimensions": (("us-gaap:SegmentAxis", "test:CloudMember"),)},
            "context_mismatch",
        ),
    ],
)
def test_context_or_accession_mismatch_is_rejected(
    changed: dict[str, object], reason: str
) -> None:
    context = _context()
    candidate = replace(context.facts[2], **changed)
    context = replace(context, facts=(*context.facts[:2], candidate))

    result = validate_concept_proposal(_proposal(), context, BOUNDARY)

    assert result.status is ValidationStatus.REJECTED
    assert result.reason_code == reason


def test_target_boundary_mismatch_is_rejected() -> None:
    other = replace(BOUNDARY, accession="other")

    result = validate_concept_proposal(_proposal(), _context(), other)

    assert result.status is ValidationStatus.REJECTED
    assert result.reason_code == "filing_boundary_mismatch"


def test_not_equivalent_relation_is_rejected() -> None:
    result = validate_concept_proposal(
        _proposal(relation=SemanticRelation.NOT_EQUIVALENT), _context(), BOUNDARY
    )

    assert result.status is ValidationStatus.REJECTED
    assert result.reason_code == "non_aggregate_relation"


def test_two_proven_equivalent_candidates_are_rejected_as_ambiguous() -> None:
    context = _context()
    second = _fact("test:OtherCost", "60")
    context = replace(
        context,
        facts=(*context.facts, second),
        presentations=(*context.presentations, _presentation(second.concept)),
    )
    proposal = ConceptProposal(
        target="cost_of_revenue",
        status=ProposalStatus.CANDIDATE,
        candidate_concepts=(
            _proposal().candidate_concepts[0],
            _proposal().candidate_concepts[0].model_copy(
                update={"concept": "test:OtherCost"}
            ),
        ),
    )

    result = validate_concept_proposal(proposal, context, BOUNDARY)

    assert result.status is ValidationStatus.REJECTED
    assert result.reason_code == "ambiguous_validated_mapping"


def test_component_proposals_do_not_hide_a_separately_proven_aggregate() -> None:
    context = _context()
    component = _fact("test:CostComponent", "10")
    context = replace(
        context,
        facts=(*context.facts, component),
        presentations=(*context.presentations, _presentation(component.concept)),
    )
    proposal = ConceptProposal(
        target="cost_of_revenue",
        status=ProposalStatus.CANDIDATE,
        candidate_concepts=(
            CandidateConceptProposal(
                concept=component.concept,
                semantic_relation=SemanticRelation.COMPONENT,
                reason="one disclosed component",
                evidence_fields=("label",),
            ),
            _proposal().candidate_concepts[0],
        ),
    )

    result = validate_concept_proposal(proposal, context, BOUNDARY)

    assert result.status is ValidationStatus.ACCEPTED
    assert result.mapping is not None
    assert result.mapping.concepts == ("test:CustomCost",)


def test_missing_gross_profit_is_not_misclassified_as_context_mismatch() -> None:
    context = _context()
    context = replace(
        context,
        facts=tuple(
            fact for fact in context.facts if fact.concept != "us-gaap:GrossProfit"
        ),
    )

    result = validate_concept_proposal(_proposal(), context, BOUNDARY)

    assert result.status is ValidationStatus.REJECTED
    assert result.reason_code == "equation_inputs_unavailable"


def test_conflicting_values_for_same_fact_identity_fail_closed() -> None:
    context = _context()
    conflict = replace(context.facts[2], value=Decimal("61"))
    context = replace(context, facts=(*context.facts, conflict))

    result = validate_concept_proposal(_proposal(), context, BOUNDARY)

    assert result.status is ValidationStatus.REJECTED
    assert result.reason_code == "ambiguous_filing_fact"
