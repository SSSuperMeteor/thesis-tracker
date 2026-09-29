from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from thesis_tracker.financial.models import (
    AiValidationProvenance,
    FactKind,
    FactOrigin,
    FailedFact,
    FailureCode,
    FilingBoundary,
    FinancialFact,
    ResolvedFact,
    Unit,
)
from thesis_tracker.financial.registry import CONCEPT_REGISTRY
from thesis_tracker.financial.resolver import resolve_quarterly_fact


def boundary(
    accession: str,
    fiscal_year: int,
    fiscal_period: str,
    period_end: date,
    *,
    form: str = "10-Q",
) -> FilingBoundary:
    return FilingBoundary(
        ticker="TEST",
        accession=accession,
        filed_at=date(period_end.year, min(period_end.month + 1, 12), 15),
        form=form,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        period_end=period_end,
        source="sec_filing_metadata",
    )


def fact(
    accession: str,
    value: str,
    start: date,
    end: date,
    *,
    concept: str = "us-gaap:Revenues",
    context_id: str = "ctx-1",
    fiscal_year: int = 2026,
    fiscal_period: str = "Q2",
    dimensions: tuple[tuple[str, str], ...] = (),
    unit: Unit = Unit.USD,
    form: str = "10-Q",
    resolver_path: tuple[str, ...] = ("filing_xbrl",),
) -> FinancialFact:
    return FinancialFact(
        ticker="TEST",
        accession=accession,
        concept=concept,
        value=Decimal(value),
        unit=unit,
        period_start=start,
        period_end=end,
        filed_at=date(end.year, min(end.month + 1, 12), 15),
        form=form,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        source="sec_filing_xbrl",
        extraction_path="filing.xbrl().facts",
        context_id=context_id,
        dimensions=dimensions,
        origin=FactOrigin.REPORTED,
        resolver_path=resolver_path,
    )


def ai_fact(
    accession: str,
    value: str,
    start: date,
    end: date,
    *,
    concept: str = "test:CustomCost",
    context_id: str = "ctx-1",
    fiscal_year: int = 2026,
    fiscal_period: str = "Q2",
) -> FinancialFact:
    reported = fact(
        accession,
        value,
        start,
        end,
        concept=concept,
        context_id=context_id,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
    )
    return replace(
        reported,
        origin=FactOrigin.AI_ASSISTED_VALIDATED,
        resolver_path=("filing_xbrl", "ai_assisted_validated"),
        ai_validation=(
            AiValidationProvenance(
                target="cost_of_revenue",
                candidate_concept=concept,
                model_name="fake-model",
                prompt_version="test-v1",
                input_hash="input-hash",
                response_hash="response-hash",
                validator_path=("decimal_strict_equation",),
                source_fact_ids=(
                    f"{accession}|us-gaap:Revenues|ctx-1",
                    f"{accession}|{concept}|{context_id}",
                    f"{accession}|us-gaap:GrossProfit|ctx-1",
                ),
                validation_evidence=(("equation", "100-60=40"),),
            ),
        ),
    )


def test_resolves_normal_single_quarter_fact() -> None:
    q1 = boundary("q1", 2026, "Q1", date(2026, 3, 31))
    q2 = boundary("q2", 2026, "Q2", date(2026, 6, 30))
    candidate = fact("q2", "125", date(2026, 4, 1), date(2026, 6, 30))

    result = resolve_quarterly_fact(
        facts=(candidate,),
        boundaries=(q1, q2),
        target=q2,
        canonical_concept="revenue",
    )

    assert isinstance(result, ResolvedFact)
    assert result.fact == candidate


def test_annual_duration_cannot_masquerade_as_quarter() -> None:
    q1 = boundary("q1", 2026, "Q1", date(2026, 3, 31))
    q2 = boundary("q2", 2026, "Q2", date(2026, 6, 30))
    annual = fact("q2", "250", date(2025, 7, 1), date(2026, 6, 30))

    result = resolve_quarterly_fact(
        facts=(annual,),
        boundaries=(q1, q2),
        target=q2,
        canonical_concept="revenue",
    )

    assert isinstance(result, FailedFact)
    assert result.diagnostic.final_failure is FailureCode.DURATION_UNAVAILABLE


@pytest.mark.parametrize(
    ("target_period", "prior_end", "target_end", "prior_value", "ytd_value", "expected"),
    [
        ("Q2", date(2026, 3, 31), date(2026, 6, 30), "100", "230", "130"),
        ("Q3", date(2026, 6, 30), date(2026, 9, 30), "230", "390", "160"),
    ],
)
def test_q2_and_q3_ytd_are_differenced_when_boundaries_are_proven(
    target_period: str,
    prior_end: date,
    target_end: date,
    prior_value: str,
    ytd_value: str,
    expected: str,
) -> None:
    period_number = int(target_period[-1])
    prior_period = f"Q{period_number - 1}"
    prior = boundary("prior", 2026, prior_period, prior_end)
    target = boundary("current", 2026, target_period, target_end)
    fiscal_start = date(2026, 1, 1)
    candidates = (
        fact(
            "prior",
            prior_value,
            fiscal_start,
            prior_end,
            context_id="prior-ytd",
            fiscal_period=prior_period,
        ),
        fact(
            "current",
            ytd_value,
            fiscal_start,
            target_end,
            context_id="current-ytd",
            fiscal_period=target_period,
        ),
    )

    result = resolve_quarterly_fact(
        facts=candidates,
        boundaries=(prior, target),
        target=target,
        canonical_concept="revenue",
    )

    assert isinstance(result, ResolvedFact)
    assert result.fact.value == Decimal(expected)
    assert result.fact.origin is FactOrigin.DERIVED
    assert result.fact.derivation_method == "ytd_difference"
    assert result.fact.source_fact_ids == (
        candidates[1].fact_id,
        candidates[0].fact_id,
    )


def test_duration_mismatch_is_rejected() -> None:
    q1 = boundary("q1", 2026, "Q1", date(2026, 3, 31))
    q2 = boundary("q2", 2026, "Q2", date(2026, 6, 30))
    wrong_start = fact("q2", "125", date(2026, 3, 15), date(2026, 6, 30))

    result = resolve_quarterly_fact(
        facts=(wrong_start,),
        boundaries=(q1, q2),
        target=q2,
        canonical_concept="revenue",
    )

    assert isinstance(result, FailedFact)
    assert result.diagnostic.final_failure is FailureCode.DURATION_UNAVAILABLE
    assert result.diagnostic.candidate_count == 1


def test_multiple_surviving_candidates_fail_closed_as_ambiguous() -> None:
    q1 = boundary("q1", 2026, "Q1", date(2026, 3, 31))
    q2 = boundary("q2", 2026, "Q2", date(2026, 6, 30))
    candidates = (
        fact("q2", "125", date(2026, 4, 1), date(2026, 6, 30), context_id="a"),
        fact("q2", "126", date(2026, 4, 1), date(2026, 6, 30), context_id="b"),
    )

    result = resolve_quarterly_fact(
        facts=candidates,
        boundaries=(q1, q2),
        target=q2,
        canonical_concept="revenue",
    )

    assert isinstance(result, FailedFact)
    assert result.diagnostic.final_failure is FailureCode.AMBIGUOUS
    assert result.diagnostic.candidate_count == 2
    assert len(result.diagnostic.to_dict()["candidate_summary"]) == 2


def test_equivalent_registered_aliases_in_same_context_are_coalesced() -> None:
    q1 = boundary("q1", 2026, "Q1", date(2026, 3, 31))
    q2 = boundary("q2", 2026, "Q2", date(2026, 6, 30))
    candidates = (
        fact(
            "q2",
            "125",
            date(2026, 4, 1),
            date(2026, 6, 30),
            concept="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
            context_id="same-context",
        ),
        fact(
            "q2",
            "125",
            date(2026, 4, 1),
            date(2026, 6, 30),
            concept="us-gaap:Revenues",
            context_id="same-context",
        ),
    )

    result = resolve_quarterly_fact(
        facts=candidates,
        boundaries=(q1, q2),
        target=q2,
        canonical_concept="revenue",
    )

    assert isinstance(result, ResolvedFact)
    assert result.fact.value == Decimal("125")
    assert result.fact.concept == candidates[0].concept
    assert "equivalent_registered_aliases_coalesced" in result.fact.resolver_path


def test_fact_from_different_accession_is_not_silently_used() -> None:
    q1 = boundary("q1", 2026, "Q1", date(2026, 3, 31))
    q2 = boundary("q2", 2026, "Q2", date(2026, 6, 30))
    other_filing = fact(
        "other",
        "125",
        date(2026, 4, 1),
        date(2026, 6, 30),
    )

    result = resolve_quarterly_fact(
        facts=(other_filing,),
        boundaries=(q1, q2),
        target=q2,
        canonical_concept="revenue",
    )

    assert isinstance(result, FailedFact)
    assert result.diagnostic.final_failure is FailureCode.INVALID_CONTEXT
    assert result.diagnostic.filing_accession == "q2"


def test_q4_is_derived_from_fy_and_proven_nine_month_context() -> None:
    q3 = boundary("q3", 2026, "Q3", date(2026, 9, 30))
    q4 = boundary("fy", 2026, "Q4", date(2026, 12, 31), form="10-K")
    nine_month = fact(
        "q3",
        "390",
        date(2026, 1, 1),
        date(2026, 9, 30),
        context_id="nine-month",
        fiscal_period="Q3",
    )
    annual = fact(
        "fy",
        "550",
        date(2026, 1, 1),
        date(2026, 12, 31),
        context_id="annual",
        fiscal_period="FY",
        form="10-K",
        resolver_path=(
            "stage1_boundary",
            "amendment_without_registered_facts",
            "filing_xbrl",
        ),
    )

    result = resolve_quarterly_fact(
        facts=(nine_month, annual),
        boundaries=(q3, q4),
        target=q4,
        canonical_concept="revenue",
    )

    assert isinstance(result, ResolvedFact)
    assert result.fact.value == Decimal("160")
    assert result.fact.period_start == date(2026, 10, 1)
    assert result.fact.derivation_method == "fy_minus_nine_months"
    assert result.fact.source_fact_ids == (annual.fact_id, nine_month.fact_id)
    assert "amendment_without_registered_facts" in result.fact.resolver_path


def test_q4_is_unavailable_when_composition_cannot_be_proven() -> None:
    q3 = boundary("q3", 2026, "Q3", date(2026, 9, 30))
    q4 = boundary("fy", 2026, "Q4", date(2026, 12, 31), form="10-K")
    nine_month = fact(
        "q3",
        "390",
        date(2026, 1, 2),
        date(2026, 9, 30),
        context_id="misaligned-nine-month",
        fiscal_period="Q3",
    )
    annual = fact(
        "fy",
        "550",
        date(2026, 1, 1),
        date(2026, 12, 31),
        context_id="annual",
        fiscal_period="FY",
        form="10-K",
    )

    result = resolve_quarterly_fact(
        facts=(nine_month, annual),
        boundaries=(q3, q4),
        target=q4,
        canonical_concept="revenue",
    )

    assert isinstance(result, FailedFact)
    assert result.diagnostic.final_failure is FailureCode.DURATION_UNAVAILABLE


def test_dimensioned_candidate_is_invalid_context() -> None:
    q1 = boundary("q1", 2026, "Q1", date(2026, 3, 31))
    q2 = boundary("q2", 2026, "Q2", date(2026, 6, 30))
    segment = fact(
        "q2",
        "125",
        date(2026, 4, 1),
        date(2026, 6, 30),
        dimensions=(("us-gaap:StatementBusinessSegmentsAxis", "test:CloudMember"),),
    )

    result = resolve_quarterly_fact(
        facts=(segment,),
        boundaries=(q1, q2),
        target=q2,
        canonical_concept="revenue",
    )

    assert isinstance(result, FailedFact)
    assert result.diagnostic.final_failure is FailureCode.INVALID_CONTEXT


def test_conflicting_fiscal_metadata_is_invalid_context() -> None:
    q1 = boundary("q1", 2026, "Q1", date(2026, 3, 31))
    q2 = boundary("q2", 2026, "Q2", date(2026, 6, 30))
    conflict = fact(
        "q2",
        "125",
        date(2026, 4, 1),
        date(2026, 6, 30),
        fiscal_year=2025,
    )

    result = resolve_quarterly_fact(
        facts=(conflict,),
        boundaries=(q1, q2),
        target=q2,
        canonical_concept="revenue",
    )

    assert isinstance(result, FailedFact)
    assert result.diagnostic.final_failure is FailureCode.INVALID_CONTEXT


def test_unknown_registry_key_returns_registry_gap() -> None:
    q2 = boundary("q2", 2026, "Q2", date(2026, 6, 30))

    result = resolve_quarterly_fact(
        facts=(),
        boundaries=(q2,),
        target=q2,
        canonical_concept="not_registered",
    )

    assert isinstance(result, FailedFact)
    assert result.diagnostic.final_failure is FailureCode.REGISTRY_GAP


def test_registered_concept_without_candidates_does_not_claim_true_missing() -> None:
    q2 = boundary("q2", 2026, "Q2", date(2026, 6, 30))

    result = resolve_quarterly_fact(
        facts=(),
        boundaries=(q2,),
        target=q2,
        canonical_concept="revenue",
    )

    assert isinstance(result, FailedFact)
    assert result.diagnostic.final_failure is FailureCode.REGISTRY_GAP


def test_q1_requires_previous_fiscal_year_boundary() -> None:
    q1 = boundary("q1", 2026, "Q1", date(2026, 3, 31))
    candidate = fact(
        "q1",
        "100",
        date(2026, 1, 1),
        date(2026, 3, 31),
        fiscal_period="Q1",
    )

    result = resolve_quarterly_fact(
        facts=(candidate,),
        boundaries=(q1,),
        target=q1,
        canonical_concept="revenue",
    )

    assert isinstance(result, FailedFact)
    assert result.diagnostic.final_failure is FailureCode.PERIOD_UNAVAILABLE


def test_instant_fact_requires_no_period_start() -> None:
    instant = FinancialFact(
        ticker="TEST",
        accession="balance-sheet",
        concept="us-gaap:CashAndCashEquivalentsAtCarryingValue",
        value=Decimal("10"),
        unit=Unit.USD,
        period_start=None,
        period_end=date(2026, 6, 30),
        filed_at=date(2026, 8, 1),
        form="10-Q",
        fiscal_year=2026,
        fiscal_period="Q2",
        source="sec_filing_xbrl",
        extraction_path="filing.xbrl().facts",
        context_id="instant",
        dimensions=(),
        origin=FactOrigin.REPORTED,
        resolver_path=("filing_xbrl",),
        fact_kind=FactKind.INSTANT,
    )

    assert instant.period_start is None
    assert instant.to_dict()["fact_kind"] == "instant"


def test_duration_fact_rejects_missing_period_start() -> None:
    with pytest.raises(ValueError, match="duration fact requires period_start"):
        FinancialFact(
            ticker="TEST",
            accession="income-statement",
            concept="us-gaap:Revenues",
            value=Decimal("10"),
            unit=Unit.USD,
            period_start=None,
            period_end=date(2026, 6, 30),
            filed_at=date(2026, 8, 1),
            form="10-Q",
            fiscal_year=2026,
            fiscal_period="Q2",
            source="sec_filing_xbrl",
            extraction_path="filing.xbrl().facts",
            context_id="duration",
            dimensions=(),
            origin=FactOrigin.REPORTED,
            resolver_path=("filing_xbrl",),
            fact_kind=FactKind.DURATION,
        )


def test_ephemeral_overlay_resolves_custom_fact_without_mutating_registry() -> None:
    q1 = boundary("q1", 2026, "Q1", date(2026, 3, 31))
    q2 = boundary("q2", 2026, "Q2", date(2026, 6, 30))
    candidate = ai_fact("q2", "60", date(2026, 4, 1), date(2026, 6, 30))
    original_registry = CONCEPT_REGISTRY["cost_of_revenue"]

    without = resolve_quarterly_fact(
        facts=(candidate,),
        boundaries=(q1, q2),
        target=q2,
        canonical_concept="cost_of_revenue",
    )
    with_overlay = resolve_quarterly_fact(
        facts=(candidate,),
        boundaries=(q1, q2),
        target=q2,
        canonical_concept="cost_of_revenue",
        concept_overlay=("test:CustomCost",),
    )

    assert isinstance(without, FailedFact)
    assert without.diagnostic.final_failure is FailureCode.REGISTRY_GAP
    assert isinstance(with_overlay, ResolvedFact)
    assert with_overlay.fact == candidate
    assert CONCEPT_REGISTRY["cost_of_revenue"] == original_registry


def test_overlay_cannot_rescue_different_accession_or_ambiguity() -> None:
    q1 = boundary("q1", 2026, "Q1", date(2026, 3, 31))
    q2 = boundary("q2", 2026, "Q2", date(2026, 6, 30))
    other = ai_fact("other", "60", date(2026, 4, 1), date(2026, 6, 30))
    ambiguous = (
        ai_fact("q2", "60", date(2026, 4, 1), date(2026, 6, 30), context_id="a"),
        ai_fact("q2", "61", date(2026, 4, 1), date(2026, 6, 30), context_id="b"),
    )

    wrong_accession = resolve_quarterly_fact(
        facts=(other,),
        boundaries=(q1, q2),
        target=q2,
        canonical_concept="cost_of_revenue",
        concept_overlay=("test:CustomCost",),
    )
    multiple = resolve_quarterly_fact(
        facts=ambiguous,
        boundaries=(q1, q2),
        target=q2,
        canonical_concept="cost_of_revenue",
        concept_overlay=("test:CustomCost",),
    )

    assert isinstance(wrong_accession, FailedFact)
    assert wrong_accession.diagnostic.final_failure is FailureCode.INVALID_CONTEXT
    assert isinstance(multiple, FailedFact)
    assert multiple.diagnostic.final_failure is FailureCode.AMBIGUOUS


def test_ai_validation_provenance_survives_quarter_derivation() -> None:
    q1 = boundary("q1", 2026, "Q1", date(2026, 3, 31))
    q2 = boundary("q2", 2026, "Q2", date(2026, 6, 30))
    prior = ai_fact(
        "q1",
        "40",
        date(2026, 1, 1),
        date(2026, 3, 31),
        context_id="q1-ytd",
        fiscal_period="Q1",
    )
    current = ai_fact(
        "q2",
        "100",
        date(2026, 1, 1),
        date(2026, 6, 30),
        context_id="q2-ytd",
    )

    result = resolve_quarterly_fact(
        facts=(prior, current),
        boundaries=(q1, q2),
        target=q2,
        canonical_concept="cost_of_revenue",
        concept_overlay=("test:CustomCost",),
    )

    assert isinstance(result, ResolvedFact)
    assert result.fact.origin is FactOrigin.DERIVED
    assert result.fact.value == Decimal("60")
    assert len(result.fact.ai_validation) == 2
