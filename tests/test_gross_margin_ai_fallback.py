from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from thesis_tracker.financial.ai_cache import SqliteConceptProposalCache
from thesis_tracker.financial.ai_concepts import ConceptProviderResponse
from thesis_tracker.financial.ai_fallback import (
    AiAttemptStatus,
    AiConceptFallback,
    AiFallbackConfig,
    cost_of_revenue_sidecar_applicability,
    is_ai_eligible,
)
from thesis_tracker.financial.models import (
    FactOrigin,
    FailureCode,
    FilingBoundary,
    FinancialFact,
    Unit,
)
from thesis_tracker.financial.semantic_candidates import (
    FilingSemanticContext,
    PresentationMetadata,
    SemanticFactCandidate,
)
from thesis_tracker.metrics.financial import compute_gross_margin_trend


class FailIfCalledFallback:
    def __init__(self, *, enabled: bool = True) -> None:
        self.config = AiFallbackConfig(enabled=enabled)

    def recover(self, **_: object) -> object:
        raise AssertionError("AI fallback must not be called")


def make_boundary(
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


def make_fact(
    boundary: FilingBoundary,
    concept: str,
    value: str,
    context_id: str,
) -> FinancialFact:
    return FinancialFact(
        ticker=boundary.ticker,
        accession=boundary.accession,
        concept=concept,
        value=Decimal(value),
        unit=Unit.USD,
        period_start=date(2026, 1, 1),
        period_end=boundary.period_end,
        filed_at=boundary.filed_at,
        form=boundary.form,
        fiscal_year=boundary.fiscal_year,
        fiscal_period=boundary.fiscal_period,
        source="sec_filing_xbrl",
        extraction_path="filing.xbrl().facts",
        context_id=context_id,
        dimensions=(),
        origin=FactOrigin.REPORTED,
        resolver_path=("filing_xbrl",),
    )


def deterministic_fixture() -> tuple[
    tuple[FinancialFact, ...], tuple[FilingBoundary, ...]
]:
    prior = make_boundary("fy-2025", 2025, "Q4", date(2025, 12, 31), form="10-K")
    target = make_boundary("q1-2026", 2026, "Q1", date(2026, 3, 31))
    return (
        (
            make_fact(target, "us-gaap:Revenues", "200", "revenue"),
            make_fact(target, "us-gaap:CostOfRevenue", "80", "cost"),
        ),
        (prior, target),
    )


class FakeProvider:
    model_name = "fake-deepseek"

    def __init__(self, raw_response: str | Exception) -> None:
        self.raw_response = raw_response
        self.calls = 0

    def propose(self, *, system_prompt: str, user_prompt: str) -> ConceptProviderResponse:
        self.calls += 1
        assert "stage3-concept-v1" in system_prompt
        assert "acc-current" in user_prompt
        if isinstance(self.raw_response, Exception):
            raise self.raw_response
        return ConceptProviderResponse(
            raw_response=self.raw_response,
            model_name=self.model_name,
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
        )


def proposal_json(
    *,
    status: str = "candidate",
    concept: str = "test:CustomCost",
    relation: str = "equivalent",
) -> str:
    candidates = (
        [
            {
                "concept": concept,
                "semantic_relation": relation,
                "reason": "aggregate cost disclosed in this statement",
                "evidence_fields": ["label", "documentation", "equation"],
            }
        ]
        if status == "candidate"
        else []
    )
    return json.dumps(
        {
            "target": "cost_of_revenue",
            "status": status,
            "candidate_concepts": candidates,
        }
    )


def semantic_context(
    target: FilingBoundary,
    *,
    cost: str = "80",
    gross_profit: str = "120",
    candidate_accession: str | None = None,
) -> FilingSemanticContext:
    def semantic_fact(concept: str, value: str, context_id: str) -> SemanticFactCandidate:
        return SemanticFactCandidate(
            ticker=target.ticker,
            accession=candidate_accession or target.accession,
            concept=concept,
            value=Decimal(value),
            unit=Unit.USD,
            period_type="duration",
            period_start=date(2026, 1, 1),
            period_end=target.period_end,
            fiscal_year=target.fiscal_year,
            fiscal_period=target.fiscal_period,
            context_id=context_id,
            dimensions=(),
            source="sec_filing_xbrl",
            extraction_path="filing.xbrl().facts.get_facts()",
        )

    concepts = (
        "us-gaap:Revenues",
        "test:CustomCost",
        "us-gaap:GrossProfit",
    )
    return FilingSemanticContext(
        boundary=target,
        presentations=tuple(
            PresentationMetadata(
                concept=concept,
                label=concept,
                documentation="filing-local definition",
                statement="income_statement",
                position=index,
                level=1,
                weight=Decimal("-1") if index else Decimal("1"),
                parent_concept=None,
                parent_abstract_concept="test:IncomeStatementAbstract",
                is_abstract=False,
                is_breakdown=False,
                dimensions_present=False,
                period_type="duration",
            )
            for index, concept in enumerate(concepts)
        ),
        facts=(
            semantic_fact("us-gaap:Revenues", "200", "same-context"),
            semantic_fact("test:CustomCost", cost, "same-context"),
            semantic_fact("us-gaap:GrossProfit", gross_profit, "same-context"),
        ),
    )


def semantic_fixture() -> tuple[
    tuple[FinancialFact, ...], tuple[FilingBoundary, ...], FilingSemanticContext
]:
    prior = make_boundary("fy-prior", 2025, "Q4", date(2025, 12, 31), form="10-K")
    target = make_boundary("acc-current", 2026, "Q1", date(2026, 3, 31))
    return (
        (make_fact(target, "us-gaap:Revenues", "200", "same-context"),),
        (prior, target),
        semantic_context(target),
    )


def sidecar(
    tmp_path: Path,
    response: str | Exception,
    *,
    enabled: bool = True,
) -> tuple[AiConceptFallback, FakeProvider]:
    provider = FakeProvider(response)
    return (
        AiConceptFallback(
            provider=provider,
            cache=SqliteConceptProposalCache(tmp_path / "proposals.sqlite3"),
            config=AiFallbackConfig(enabled=enabled),
        ),
        provider,
    )


def test_deterministic_success_never_calls_ai_fallback() -> None:
    facts, boundaries = deterministic_fixture()

    result = compute_gross_margin_trend(
        ticker="TEST",
        facts=facts,
        boundaries=boundaries,
        max_periods=1,
        ai_fallback=FailIfCalledFallback(),
    )

    assert result.to_dict() == {
        "ticker": "TEST",
        "metric": "gross_margin_trend",
        "observations": [
            {
                "ticker": "TEST",
                "period_end": "2026-03-31",
                "fiscal_year": 2026,
                "fiscal_period": "Q1",
                "value": "0.6",
                "provenance": {
                    "formula": "(revenue - cost_of_revenue) / revenue",
                    "source_fact_ids": [
                        "q1-2026|us-gaap:Revenues|revenue",
                        "q1-2026|us-gaap:CostOfRevenue|cost",
                    ],
                    "source_facts": [
                        facts[0].to_dict(),
                        facts[1].to_dict(),
                    ],
                },
            }
        ],
        "failures": [],
    }


def test_disabled_ai_fallback_matches_deterministic_serialization() -> None:
    facts, boundaries = deterministic_fixture()
    baseline = compute_gross_margin_trend(
        ticker="TEST", facts=facts, boundaries=boundaries, max_periods=1
    )
    disabled = compute_gross_margin_trend(
        ticker="TEST",
        facts=facts,
        boundaries=boundaries,
        max_periods=1,
        ai_fallback=FailIfCalledFallback(enabled=False),
    )

    canonical = lambda value: json.dumps(  # noqa: E731
        value, sort_keys=True, separators=(",", ":")
    )
    assert canonical(disabled.to_dict()) == canonical(baseline.to_dict())


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (FailureCode.NOT_APPLICABLE, False),
        (FailureCode.SOURCE_STALE, False),
        (FailureCode.REGISTRY_GAP, True),
        (FailureCode.CUSTOM_CONCEPT_ONLY, True),
        (FailureCode.INVALID_CONTEXT, False),
        (FailureCode.PERIOD_UNAVAILABLE, False),
        (FailureCode.DURATION_UNAVAILABLE, False),
        (FailureCode.AMBIGUOUS, False),
        (FailureCode.UNRESOLVED, False),
        (FailureCode.TRUE_MISSING, False),
    ],
)
def test_ai_eligibility_is_limited_to_semantic_failures(
    code: FailureCode, expected: bool
) -> None:
    assert is_ai_eligible(code) is expected


def test_registry_gap_invokes_provider_and_records_no_match(tmp_path: Path) -> None:
    facts, boundaries, context = semantic_fixture()
    fallback, provider = sidecar(tmp_path, proposal_json(status="no_match"))

    result = compute_gross_margin_trend(
        ticker="TEST",
        facts=facts,
        boundaries=boundaries,
        semantic_contexts=(context,),
        max_periods=1,
        ai_fallback=fallback,
    )

    assert provider.calls == 1
    assert result.observations == ()
    assert result.failures[0].final_failure is FailureCode.REGISTRY_GAP
    assert result.ai_attempts[0].status is AiAttemptStatus.NO_MATCH


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (proposal_json(concept="test:Invented"), "candidate_not_filing_local"),
        (proposal_json(relation="component"), "non_aggregate_relation"),
    ],
)
def test_hallucination_and_component_are_rejected(
    tmp_path: Path, response: str, expected: str
) -> None:
    facts, boundaries, context = semantic_fixture()
    fallback, _ = sidecar(tmp_path, response)

    result = compute_gross_margin_trend(
        ticker="TEST",
        facts=facts,
        boundaries=boundaries,
        semantic_contexts=(context,),
        max_periods=1,
        ai_fallback=fallback,
    )

    assert result.observations == ()
    assert result.ai_attempts[0].status is AiAttemptStatus.REJECTED
    assert result.ai_attempts[0].validation_reason_code == expected


def test_failed_equation_is_rejected(tmp_path: Path) -> None:
    facts, boundaries, context = semantic_fixture()
    context = semantic_context(context.boundary, cost="81")
    fallback, _ = sidecar(tmp_path, proposal_json())

    result = compute_gross_margin_trend(
        ticker="TEST",
        facts=facts,
        boundaries=boundaries,
        semantic_contexts=(context,),
        max_periods=1,
        ai_fallback=fallback,
    )

    assert result.observations == ()
    assert result.ai_attempts[0].status is AiAttemptStatus.REJECTED
    assert result.ai_attempts[0].validation_reason_code == "equation_not_proven"


def test_valid_proposal_adds_observation_with_complete_ai_provenance(
    tmp_path: Path,
) -> None:
    facts, boundaries, context = semantic_fixture()
    fallback, provider = sidecar(tmp_path, proposal_json())

    result = compute_gross_margin_trend(
        ticker="TEST",
        facts=facts,
        boundaries=boundaries,
        semantic_contexts=(context,),
        max_periods=1,
        ai_fallback=fallback,
    )

    assert provider.calls == 1
    assert result.failures == ()
    assert result.observations[0].value == Decimal("0.6")
    cost = result.observations[0].provenance.source_facts[1]
    assert cost.origin is FactOrigin.AI_ASSISTED_VALIDATED
    audit = cost.ai_validation[0]
    assert audit.candidate_concept == "test:CustomCost"
    assert audit.model_name == "fake-deepseek"
    assert audit.prompt_version == "stage3-concept-v1"
    assert audit.input_hash
    assert audit.response_hash
    assert audit.validator_path[-2:] == (
        "decimal_strict_equation",
        "unique_mapping",
    )
    assert len(audit.source_fact_ids) == 3
    assert dict(audit.validation_evidence)["equation"] == "200-80=120"
    assert result.ai_attempts[0].status is AiAttemptStatus.ACCEPTED


def test_cross_accession_candidate_is_rejected(tmp_path: Path) -> None:
    facts, boundaries, context = semantic_fixture()
    context = semantic_context(context.boundary, candidate_accession="other-accession")
    fallback, _ = sidecar(tmp_path, proposal_json())

    result = compute_gross_margin_trend(
        ticker="TEST",
        facts=facts,
        boundaries=boundaries,
        semantic_contexts=(context,),
        max_periods=1,
        ai_fallback=fallback,
    )

    assert result.observations == ()
    assert result.ai_attempts[0].validation_reason_code == (
        "candidate_not_filing_local"
    )


def test_period_unavailable_never_calls_provider(tmp_path: Path) -> None:
    target = make_boundary("acc-current", 2026, "Q1", date(2026, 3, 31))
    facts = (
        make_fact(target, "us-gaap:Revenues", "200", "revenue"),
        make_fact(target, "us-gaap:CostOfRevenue", "80", "cost"),
    )
    fallback, provider = sidecar(tmp_path, proposal_json())

    result = compute_gross_margin_trend(
        ticker="TEST",
        facts=facts,
        boundaries=(target,),
        semantic_contexts=(semantic_context(target),),
        max_periods=1,
        ai_fallback=fallback,
    )

    assert provider.calls == 0
    assert result.ai_attempts == ()
    assert result.failures[0].final_failure is FailureCode.PERIOD_UNAVAILABLE


def test_provider_failure_preserves_original_diagnostic(tmp_path: Path) -> None:
    facts, boundaries, context = semantic_fixture()
    fallback, provider = sidecar(tmp_path, RuntimeError("network unavailable"))
    baseline = compute_gross_margin_trend(
        ticker="TEST", facts=facts, boundaries=boundaries, max_periods=1
    )

    result = compute_gross_margin_trend(
        ticker="TEST",
        facts=facts,
        boundaries=boundaries,
        semantic_contexts=(context,),
        max_periods=1,
        ai_fallback=fallback,
    )

    assert provider.calls == 1
    assert result.failures == baseline.failures
    assert result.ai_attempts[0].status is AiAttemptStatus.PROVIDER_ERROR
    assert "RuntimeError" in result.ai_attempts[0].error


def test_cached_proposal_avoids_second_call_and_is_revalidated(tmp_path: Path) -> None:
    facts, boundaries, context = semantic_fixture()
    fallback, provider = sidecar(tmp_path, proposal_json())

    first = compute_gross_margin_trend(
        ticker="TEST",
        facts=facts,
        boundaries=boundaries,
        semantic_contexts=(context,),
        max_periods=1,
        ai_fallback=fallback,
    )
    second = compute_gross_margin_trend(
        ticker="TEST",
        facts=facts,
        boundaries=boundaries,
        semantic_contexts=(
            semantic_context(context.boundary, cost="81"),
        ),
        max_periods=1,
        ai_fallback=fallback,
    )

    assert provider.calls == 2  # changed metadata/value has a different cache key
    assert first.ai_attempts[0].status is AiAttemptStatus.ACCEPTED
    assert second.ai_attempts[0].status is AiAttemptStatus.REJECTED

    third = compute_gross_margin_trend(
        ticker="TEST",
        facts=facts,
        boundaries=boundaries,
        semantic_contexts=(context,),
        max_periods=1,
        ai_fallback=fallback,
    )
    assert provider.calls == 2
    assert third.ai_attempts[0].cache_hit is True
    assert third.ai_attempts[0].status is AiAttemptStatus.ACCEPTED


@pytest.mark.parametrize("status", ["no_match", "insufficient_evidence"])
def test_negative_proposals_are_cached(
    tmp_path: Path, status: str
) -> None:
    facts, boundaries, context = semantic_fixture()
    fallback, provider = sidecar(tmp_path, proposal_json(status=status))

    results = [
        compute_gross_margin_trend(
            ticker="TEST",
            facts=facts,
            boundaries=boundaries,
            semantic_contexts=(context,),
            max_periods=1,
            ai_fallback=fallback,
        )
        for _ in range(2)
    ]

    assert provider.calls == 1
    assert results[1].ai_attempts[0].cache_hit is True
    assert results[1].ai_attempts[0].status.value == status


def test_invalid_model_json_preserves_failure(tmp_path: Path) -> None:
    facts, boundaries, context = semantic_fixture()
    fallback, _ = sidecar(tmp_path, "not JSON")

    result = compute_gross_margin_trend(
        ticker="TEST",
        facts=facts,
        boundaries=boundaries,
        semantic_contexts=(context,),
        max_periods=1,
        ai_fallback=fallback,
    )

    assert result.observations == ()
    assert result.ai_attempts[0].status is AiAttemptStatus.INVALID_RESPONSE


# ----------------------------------------------------------------
# Deterministic applicability gate in front of the cost sidecar
# ----------------------------------------------------------------


def operand_context(
    target: FilingBoundary,
    pairs: tuple[tuple[str, str], ...],
    *,
    period_end: date | None = None,
) -> FilingSemanticContext:
    """Build a filing-local context whose facts carry explicit operands."""

    return FilingSemanticContext(
        boundary=target,
        presentations=(),
        facts=tuple(
            SemanticFactCandidate(
                ticker=target.ticker,
                accession=target.accession,
                concept=concept,
                value=Decimal(value),
                unit=Unit.USD,
                period_type="duration",
                period_start=date(2026, 1, 1),
                period_end=period_end or target.period_end,
                fiscal_year=target.fiscal_year,
                fiscal_period=target.fiscal_period,
                context_id=f"ctx-{index}",
                dimensions=(),
                source="sec_filing_xbrl",
                extraction_path="filing.xbrl().facts.get_facts()",
            )
            for index, (concept, value) in enumerate(pairs)
        ),
    )


def test_applicability_gate_blocks_when_no_registered_operand_is_disclosed() -> None:
    target = make_boundary("acc-current", 2026, "Q1", date(2026, 3, 31))
    context = operand_context(
        target,
        (
            ("us-gaap:Revenues", "200"),
            # A broad cost aggregate is not a registered cost-of-revenue operand.
            ("us-gaap:CostsAndExpenses", "80"),
            ("test:CustomCost", "70"),
        ),
    )

    applicability = cost_of_revenue_sidecar_applicability(context)

    assert applicability.applicable is False
    assert applicability.operands == ()
    assert "GrossProfit" in applicability.reason
    assert "CostOfRevenue" in applicability.reason


def test_applicability_gate_allows_a_disclosed_gross_profit() -> None:
    target = make_boundary("acc-current", 2026, "Q1", date(2026, 3, 31))
    context = operand_context(
        target,
        (("us-gaap:Revenues", "200"), ("us-gaap:GrossProfit", "120")),
    )

    applicability = cost_of_revenue_sidecar_applicability(context)

    assert applicability.applicable is True
    assert applicability.operands == ("us-gaap:GrossProfit",)


def test_applicability_gate_allows_a_disclosed_cost_of_revenue() -> None:
    target = make_boundary("acc-current", 2026, "Q1", date(2026, 3, 31))
    context = operand_context(
        target,
        (("us-gaap:CostOfGoodsAndServicesSold", "80"),),
    )

    assert cost_of_revenue_sidecar_applicability(context).applicable is True


def test_applicability_gate_ignores_operands_from_another_period() -> None:
    target = make_boundary("acc-current", 2026, "Q1", date(2026, 3, 31))
    context = operand_context(
        target,
        (("us-gaap:GrossProfit", "120"),),
        period_end=date(2025, 12, 31),
    )

    assert cost_of_revenue_sidecar_applicability(context).applicable is False


def test_gate_returns_not_applicable_without_calling_provider(
    tmp_path: Path,
) -> None:
    facts, boundaries, context = semantic_fixture()
    gated = operand_context(
        context.boundary,
        (
            ("us-gaap:Revenues", "200"),
            ("us-gaap:CostsAndExpenses", "80"),
            ("test:CustomCost", "70"),
        ),
    )
    fallback, provider = sidecar(tmp_path, proposal_json())

    result = compute_gross_margin_trend(
        ticker="TEST",
        facts=facts,
        boundaries=boundaries,
        semantic_contexts=(gated,),
        max_periods=1,
        ai_fallback=fallback,
    )

    assert provider.calls == 0
    assert result.observations == ()
    assert result.ai_attempts == ()
    assert result.failures[0].final_failure is FailureCode.NOT_APPLICABLE
    assert result.failures[0].final_status == "not_applicable"
    assert result.failures[0].concept == "cost_of_revenue"
    assert "revenue - cost_of_revenue == gross_profit" in (
        result.failures[0].rejection_reason
    )


def test_gate_still_enters_sidecar_when_gross_profit_is_disclosed(
    tmp_path: Path,
) -> None:
    facts, boundaries, context = semantic_fixture()
    fallback, provider = sidecar(tmp_path, proposal_json(status="no_match"))

    result = compute_gross_margin_trend(
        ticker="TEST",
        facts=facts,
        boundaries=boundaries,
        semantic_contexts=(context,),
        max_periods=1,
        ai_fallback=fallback,
    )

    assert provider.calls == 1
    assert result.failures[0].final_failure is FailureCode.REGISTRY_GAP


def test_gate_leaves_deterministic_observations_untouched(
    tmp_path: Path,
) -> None:
    facts, boundaries = deterministic_fixture()
    fallback, provider = sidecar(tmp_path, proposal_json())

    result = compute_gross_margin_trend(
        ticker="TEST",
        facts=facts,
        boundaries=boundaries,
        max_periods=1,
        ai_fallback=fallback,
    )

    assert provider.calls == 0
    assert len(result.observations) == 1
    assert result.failures == ()
    assert result.ai_attempts == ()


def test_gate_does_not_change_the_sic_not_applicable_path(
    tmp_path: Path,
) -> None:
    facts, boundaries = deterministic_fixture()
    fallback, provider = sidecar(tmp_path, proposal_json())

    result = compute_gross_margin_trend(
        ticker="TEST",
        facts=facts,
        boundaries=boundaries,
        issuer_sic="6021",
        max_periods=1,
        ai_fallback=fallback,
    )

    assert provider.calls == 0
    assert result.failures[0].final_failure is FailureCode.NOT_APPLICABLE
    assert result.failures[0].source == "sec_company_metadata"
    assert result.failures[0].concept == "gross_margin"
