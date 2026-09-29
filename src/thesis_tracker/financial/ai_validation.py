"""Deterministic validation for non-authoritative AI concept proposals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from thesis_tracker.financial.ai_concepts import (
    ConceptProposal,
    ProposalStatus,
    SemanticRelation,
)
from thesis_tracker.financial.models import FilingBoundary, Unit
from thesis_tracker.financial.registry import CONCEPT_REGISTRY
from thesis_tracker.financial.semantic_candidates import (
    FilingSemanticContext,
    SemanticFactCandidate,
)


class ValidationStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ValidationEvidence:
    candidate_concept: str
    accession: str
    equation: str
    operand_concepts: tuple[str, str, str]
    context_ids: tuple[str, str, str]
    source_fact_ids: tuple[str, str, str]
    unit: Unit
    period_start: date
    period_end: date


@dataclass(frozen=True, slots=True)
class ValidatedConceptMapping:
    target: str
    concepts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConceptValidationResult:
    status: ValidationStatus
    mapping: ValidatedConceptMapping | None
    evidence: tuple[ValidationEvidence, ...]
    validator_path: tuple[str, ...]
    reason_code: str
    reason: str


_VALIDATOR_PATH = (
    "filing_local",
    "semantic_relation",
    "context_compatibility",
    "decimal_strict_equation",
    "unique_mapping",
)


def _rejected(
    reason_code: str,
    reason: str,
    *,
    evidence: tuple[ValidationEvidence, ...] = (),
) -> ConceptValidationResult:
    return ConceptValidationResult(
        status=ValidationStatus.REJECTED,
        mapping=None,
        evidence=evidence,
        validator_path=_VALIDATOR_PATH,
        reason_code=reason_code,
        reason=reason,
    )


def _filing_local_facts(
    context: FilingSemanticContext,
    concept: str,
) -> tuple[SemanticFactCandidate, ...]:
    return tuple(
        fact
        for fact in context.facts
        if fact.concept == concept
        and fact.accession == context.boundary.accession
        and fact.ticker == context.boundary.ticker
    )


def _same_valid_context(
    revenue: SemanticFactCandidate,
    cost: SemanticFactCandidate,
    gross_profit: SemanticFactCandidate,
    boundary: FilingBoundary,
) -> bool:
    facts = (revenue, cost, gross_profit)
    starts = {fact.period_start for fact in facts}
    ends = {fact.period_end for fact in facts}
    contexts = {fact.context_id for fact in facts}
    return (
        all(fact.accession == boundary.accession for fact in facts)
        and all(fact.ticker == boundary.ticker for fact in facts)
        and all(fact.unit is Unit.USD for fact in facts)
        and all(fact.period_type == "duration" for fact in facts)
        and all(not fact.dimensions for fact in facts)
        and len(starts) == 1
        and None not in starts
        and len(ends) == 1
        and boundary.period_end in ends
        and len(contexts) == 1
        and all(fact.fiscal_year == boundary.fiscal_year for fact in facts)
        and all(fact.fiscal_period == boundary.fiscal_period for fact in facts)
    )


def _equation_evidence(
    candidate: SemanticFactCandidate,
    context: FilingSemanticContext,
) -> tuple[ValidationEvidence, ...]:
    revenues = tuple(
        fact
        for concept in CONCEPT_REGISTRY["gross_profit_revenue"]
        for fact in _filing_local_facts(context, concept)
    )
    gross_profits = tuple(
        fact
        for concept in CONCEPT_REGISTRY["gross_profit"]
        for fact in _filing_local_facts(context, concept)
    )
    evidence: list[ValidationEvidence] = []
    for revenue in revenues:
        for gross_profit in gross_profits:
            if not _same_valid_context(
                revenue, candidate, gross_profit, context.boundary
            ):
                continue
            if revenue.value - candidate.value != gross_profit.value:
                continue
            assert revenue.period_start is not None
            evidence.append(
                ValidationEvidence(
                    candidate_concept=candidate.concept,
                    accession=candidate.accession,
                    equation=(
                        f"{revenue.value}-{candidate.value}={gross_profit.value}"
                    ),
                    operand_concepts=(
                        revenue.concept,
                        candidate.concept,
                        gross_profit.concept,
                    ),
                    context_ids=(
                        revenue.context_id,
                        candidate.context_id,
                        gross_profit.context_id,
                    ),
                    source_fact_ids=(
                        revenue.fact_id,
                        candidate.fact_id,
                        gross_profit.fact_id,
                    ),
                    unit=Unit.USD,
                    period_start=revenue.period_start,
                    period_end=revenue.period_end,
                )
            )
    return tuple(evidence)


def validate_concept_proposal(
    proposal: ConceptProposal,
    context: FilingSemanticContext,
    target_boundary: FilingBoundary,
) -> ConceptValidationResult:
    """Prove a proposed mapping using exact, same-context filing equations."""

    if context.boundary != target_boundary:
        return _rejected(
            "filing_boundary_mismatch",
            "semantic context and requested filing boundary differ",
        )
    facts_by_id: dict[str, list[SemanticFactCandidate]] = {}
    for fact in context.facts:
        facts_by_id.setdefault(fact.fact_id, []).append(fact)
    if any(len(set(group)) > 1 for group in facts_by_id.values()):
        return _rejected(
            "ambiguous_filing_fact",
            "conflicting values share a filing-local concept and context identity",
        )
    if proposal.target != "cost_of_revenue":
        return _rejected(
            "unsupported_target",
            f"validator does not support target {proposal.target!r}",
        )
    if proposal.status is not ProposalStatus.CANDIDATE:
        return _rejected(
            "proposal_has_no_candidate",
            f"proposal status is {proposal.status.value}",
        )

    presentation_concepts = {item.concept for item in context.presentations}
    proven: dict[str, tuple[ValidationEvidence, ...]] = {}
    saw_context_mismatch = False
    saw_equation_failure = False
    saw_non_aggregate = False
    saw_missing_equation_inputs = False
    for proposed in proposal.candidate_concepts:
        if proposed.semantic_relation not in {
            SemanticRelation.EQUIVALENT,
            SemanticRelation.POSSIBLE_AGGREGATE,
        }:
            saw_non_aggregate = True
            continue
        candidates = _filing_local_facts(context, proposed.concept)
        if proposed.concept not in presentation_concepts or not candidates:
            return _rejected(
                "candidate_not_filing_local",
                f"{proposed.concept} is not a numeric fact in the target filing",
            )
        revenues = tuple(
            fact
            for fact in context.facts
            if fact.concept in CONCEPT_REGISTRY["gross_profit_revenue"]
            and fact.accession == target_boundary.accession
        )
        gross_profits = tuple(
            fact
            for fact in context.facts
            if fact.concept in CONCEPT_REGISTRY["gross_profit"]
            and fact.accession == target_boundary.accession
        )
        if not revenues or not gross_profits:
            saw_missing_equation_inputs = True
            continue
        for candidate in candidates:
            evidence = _equation_evidence(candidate, context)
            if evidence:
                proven[proposed.concept] = (*proven.get(proposed.concept, ()), *evidence)
                continue
            compatible_pair_exists = any(
                _same_valid_context(revenue, candidate, gross_profit, target_boundary)
                for revenue in context.facts
                if revenue.concept in CONCEPT_REGISTRY["gross_profit_revenue"]
                for gross_profit in context.facts
                if gross_profit.concept in CONCEPT_REGISTRY["gross_profit"]
            )
            if compatible_pair_exists:
                saw_equation_failure = True
            else:
                saw_context_mismatch = True

    if len(proven) > 1:
        all_evidence = tuple(
            evidence for values in proven.values() for evidence in values
        )
        return _rejected(
            "ambiguous_validated_mapping",
            "more than one proposed concept satisfies the exact equation",
            evidence=all_evidence,
        )
    if len(proven) == 1:
        concept, evidence = next(iter(proven.items()))
        return ConceptValidationResult(
            status=ValidationStatus.ACCEPTED,
            mapping=ValidatedConceptMapping(
                target=proposal.target,
                concepts=(concept,),
            ),
            evidence=evidence,
            validator_path=_VALIDATOR_PATH,
            reason_code="validated_exact_equation",
            reason="one filing-local concept satisfies the exact gross-profit equation",
        )
    if saw_equation_failure:
        return _rejected(
            "equation_not_proven",
            "compatible filing-local facts do not satisfy Decimal strict equality",
        )
    if saw_missing_equation_inputs:
        return _rejected(
            "equation_inputs_unavailable",
            "filing lacks revenue or gross-profit facts required by the equation",
        )
    if saw_context_mismatch:
        return _rejected(
            "context_mismatch",
            "no revenue, cost, and gross-profit facts share the required context",
        )
    if saw_non_aggregate:
        return _rejected(
            "non_aggregate_relation",
            "proposal contains only component or non-equivalent relationships",
        )
    return _rejected(
        "equation_inputs_unavailable",
        "filing lacks a complete filing-local equation neighborhood",
    )
