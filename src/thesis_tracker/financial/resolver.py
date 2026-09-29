"""Fail-closed resolver for reported and deterministically derived quarter facts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta

from thesis_tracker.financial.models import (
    FactKind,
    FactOrigin,
    FactResult,
    FailedFact,
    FailureCode,
    FailureDiagnostic,
    FilingBoundary,
    FinancialFact,
    ResolvedFact,
)
from thesis_tracker.financial.registry import (
    CONCEPT_REGISTRY,
    NON_DERIVABLE_CONCEPTS,
)


def _summary(fact: FinancialFact) -> dict[str, object]:
    return {
        "fact_id": fact.fact_id,
        "accession": fact.accession,
        "concept": fact.concept,
        "value": str(fact.value),
        "unit": fact.unit.value,
        "period_start": (
            fact.period_start.isoformat() if fact.period_start is not None else None
        ),
        "period_end": fact.period_end.isoformat(),
        "context_id": fact.context_id,
        "dimensions": dict(fact.dimensions),
    }


def _failure(
    *,
    target: FilingBoundary,
    canonical_concept: str,
    code: FailureCode,
    candidates: Sequence[FinancialFact],
    reason: str,
) -> FailedFact:
    return FailedFact(
        FailureDiagnostic(
            ticker=target.ticker,
            concept=canonical_concept,
            metric="financial_fact",
            target_period=target.target_period,
            final_failure=code,
            recovery_history=(),
            source="sec_filing_xbrl",
            filing_accession=target.accession,
            candidate_count=len(candidates),
            candidate_summary=tuple(_summary(item) for item in candidates),
            rejection_reason=reason,
        )
    )


def _coalesce_registered_aliases(
    candidates: Sequence[FinancialFact],
    registered: tuple[str, ...],
) -> tuple[FinancialFact, ...]:
    """Collapse aliases only when they identify the same reported context/value."""

    groups: dict[tuple[object, ...], list[FinancialFact]] = {}
    for item in candidates:
        identity = (
            item.ticker,
            item.accession,
            item.value,
            item.unit,
            item.period_start,
            item.period_end,
            item.filed_at,
            item.form,
            item.fiscal_year,
            item.fiscal_period,
            item.source,
            item.extraction_path,
            item.context_id,
            item.dimensions,
            item.origin,
            item.fact_kind,
            item.derivation_method,
            item.source_fact_ids,
            item.ai_validation,
            item.resolver_path,
        )
        groups.setdefault(identity, []).append(item)

    priority = {concept: index for index, concept in enumerate(registered)}
    coalesced: list[FinancialFact] = []
    for group in groups.values():
        preferred = min(group, key=lambda item: priority[item.concept])
        if len(group) > 1:
            preferred = replace(
                preferred,
                resolver_path=(
                    *preferred.resolver_path,
                    "equivalent_registered_aliases_coalesced",
                ),
            )
        coalesced.append(preferred)
    return tuple(coalesced)


def _prior_boundary(
    boundaries: Sequence[FilingBoundary],
    target: FilingBoundary,
) -> FilingBoundary | None:
    target_number = int(target.fiscal_period[-1])
    if target_number == 1:
        expected_year = target.fiscal_year - 1
        expected_period = "Q4"
    else:
        expected_year = target.fiscal_year
        expected_period = f"Q{target_number - 1}"
    matches = [
        item
        for item in boundaries
        if item.ticker == target.ticker
        and item.fiscal_year == expected_year
        and item.fiscal_period == expected_period
        and item.period_end < target.period_end
        and item.filed_at <= target.filed_at
    ]
    return max(matches, key=lambda item: item.period_end) if matches else None


def prior_fiscal_boundary(
    boundaries: Sequence[FilingBoundary],
    target: FilingBoundary,
) -> FilingBoundary | None:
    """Return the immediately preceding fiscal boundary, if it is known."""

    return _prior_boundary(boundaries, target)


def _derived_fact(
    *,
    minuend: FinancialFact,
    subtrahend: FinancialFact,
    target: FilingBoundary,
    period_start,
    method: str,
) -> FinancialFact:
    if minuend.unit is not subtrahend.unit:
        raise ValueError("cannot subtract financial facts with incompatible units")
    return FinancialFact(
        ticker=target.ticker,
        accession=target.accession,
        concept=minuend.concept,
        value=minuend.value - subtrahend.value,
        unit=minuend.unit,
        period_start=period_start,
        period_end=target.period_end,
        filed_at=target.filed_at,
        form=target.form,
        fiscal_year=target.fiscal_year,
        fiscal_period=target.fiscal_period,
        source="derived_sec_filing_xbrl",
        extraction_path=method,
        context_id=f"derived:{minuend.context_id}-{subtrahend.context_id}",
        dimensions=(),
        origin=FactOrigin.DERIVED,
        resolver_path=tuple(
            dict.fromkeys(
                (*minuend.resolver_path, *subtrahend.resolver_path, method)
            )
        ),
        derivation_method=method,
        source_fact_ids=(minuend.fact_id, subtrahend.fact_id),
        ai_validation=tuple(
            dict.fromkeys((*minuend.ai_validation, *subtrahend.ai_validation))
        ),
    )


def resolve_quarterly_fact(
    *,
    facts: Sequence[FinancialFact],
    boundaries: Sequence[FilingBoundary],
    target: FilingBoundary,
    canonical_concept: str,
    concept_overlay: tuple[str, ...] = (),
) -> FactResult:
    """Resolve one consolidated quarterly flow fact without heuristic picking."""

    permanent = CONCEPT_REGISTRY.get(canonical_concept)
    if permanent is None and not concept_overlay:
        return _failure(
            target=target,
            canonical_concept=canonical_concept,
            code=FailureCode.REGISTRY_GAP,
            candidates=(),
            reason="canonical concept is absent from the concept registry",
        )
    registered = tuple(dict.fromkeys((*(permanent or ()), *concept_overlay)))

    concept_facts = tuple(item for item in facts if item.concept in registered)
    if not concept_facts:
        return _failure(
            target=target,
            canonical_concept=canonical_concept,
            code=FailureCode.REGISTRY_GAP,
            candidates=(),
            reason=(
                "no fact uses a registered concept; custom and unregistered "
                "concepts have not been ruled out"
            ),
        )

    relevant_end = tuple(
        item for item in concept_facts if item.period_end == target.period_end
    )
    if not relevant_end:
        return _failure(
            target=target,
            canonical_concept=canonical_concept,
            code=FailureCode.PERIOD_UNAVAILABLE,
            candidates=concept_facts,
            reason="no registered fact ends at the filing reporting boundary",
        )

    target_accession = tuple(
        item for item in relevant_end if item.accession == target.accession
    )
    if not target_accession:
        return _failure(
            target=target,
            canonical_concept=canonical_concept,
            code=FailureCode.INVALID_CONTEXT,
            candidates=relevant_end,
            reason="facts ending at the target boundary belong to another accession",
        )

    compatible_context = tuple(
        item
        for item in target_accession
        if item.ticker == target.ticker
        and item.filed_at == target.filed_at
        and item.form == target.form
        and item.fiscal_year == target.fiscal_year
        and item.fact_kind is FactKind.DURATION
    )
    if not compatible_context:
        return _failure(
            target=target,
            canonical_concept=canonical_concept,
            code=FailureCode.INVALID_CONTEXT,
            candidates=target_accession,
            reason="fact metadata conflicts with the filing reporting context",
        )

    consolidated = _coalesce_registered_aliases(
        tuple(item for item in compatible_context if not item.dimensions),
        registered,
    )
    if not consolidated:
        return _failure(
            target=target,
            canonical_concept=canonical_concept,
            code=FailureCode.INVALID_CONTEXT,
            candidates=target_accession,
            reason="only dimensional facts survive at the target boundary",
        )

    prior = _prior_boundary(boundaries, target)
    if prior is None:
        return _failure(
            target=target,
            canonical_concept=canonical_concept,
            code=FailureCode.PERIOD_UNAVAILABLE,
            candidates=consolidated,
            reason="the immediately preceding fiscal boundary is unavailable",
        )
    else:
        expected_start = prior.period_end + timedelta(days=1)
        direct = tuple(
            item for item in consolidated if item.period_start == expected_start
        )

    if len(direct) == 1:
        return ResolvedFact(direct[0])
    if len(direct) > 1:
        return _failure(
            target=target,
            canonical_concept=canonical_concept,
            code=FailureCode.AMBIGUOUS,
            candidates=direct,
            reason="multiple facts survive concept, context, period, and duration checks",
        )

    if canonical_concept in NON_DERIVABLE_CONCEPTS:
        return _failure(
            target=target,
            canonical_concept=canonical_concept,
            code=FailureCode.DURATION_UNAVAILABLE,
            candidates=consolidated,
            reason=(
                f"{canonical_concept} is not additive and does not support "
                f"{target.fiscal_period} YTD/Q4 derivation"
            ),
        )

    previous = _coalesce_registered_aliases(
        tuple(
            item
            for item in concept_facts
            if item.accession == prior.accession
            and item.period_end == prior.period_end
            and item.ticker == prior.ticker
            and item.filed_at == prior.filed_at
            and item.form == prior.form
            and item.fiscal_year == prior.fiscal_year
            and item.fact_kind is FactKind.DURATION
            and not item.dimensions
        ),
        registered,
    )
    method = (
        "fy_minus_nine_months"
        if target.fiscal_period == "Q4"
        else "ytd_difference"
    )
    pairs = tuple(
        (current, earlier)
        for current in consolidated
        for earlier in previous
        if current.concept == earlier.concept
        and current.unit is earlier.unit
        and current.period_start is not None
        and current.period_start == earlier.period_start
        and current.period_start < prior.period_end
    )
    if len(pairs) == 1:
        current, earlier = pairs[0]
        return ResolvedFact(
            _derived_fact(
                minuend=current,
                subtrahend=earlier,
                target=target,
                period_start=prior.period_end + timedelta(days=1),
                method=method,
            )
        )
    if len(pairs) > 1:
        material = tuple(item for pair in pairs for item in pair)
        return _failure(
            target=target,
            canonical_concept=canonical_concept,
            code=FailureCode.AMBIGUOUS,
            candidates=material,
            reason="multiple derivation pairs satisfy all hard constraints",
        )
    return _failure(
        target=target,
        canonical_concept=canonical_concept,
        code=FailureCode.DURATION_UNAVAILABLE,
        candidates=(*consolidated, *previous),
        reason="single-quarter duration is absent and derivation boundaries do not align",
    )


def resolve_instant_fact(
    *,
    facts: Sequence[FinancialFact],
    target: FilingBoundary,
    canonical_concept: str,
    concept_overlay: tuple[str, ...] = (),
) -> FactResult:
    """Resolve one consolidated instant fact at the filing reporting boundary.

    Instant facts are anchored by the as-of date plus the filing reporting
    context; no duration match is required.  Concept, context, dimensions and
    ambiguity still have to pass.
    """

    permanent = CONCEPT_REGISTRY.get(canonical_concept)
    if permanent is None and not concept_overlay:
        return _failure(
            target=target,
            canonical_concept=canonical_concept,
            code=FailureCode.REGISTRY_GAP,
            candidates=(),
            reason="canonical concept is absent from the concept registry",
        )
    registered = tuple(dict.fromkeys((*(permanent or ()), *concept_overlay)))

    concept_facts = tuple(item for item in facts if item.concept in registered)
    if not concept_facts:
        return _failure(
            target=target,
            canonical_concept=canonical_concept,
            code=FailureCode.REGISTRY_GAP,
            candidates=(),
            reason=(
                "no fact uses a registered concept; custom and unregistered "
                "concepts have not been ruled out"
            ),
        )

    relevant_end = tuple(
        item for item in concept_facts if item.period_end == target.period_end
    )
    if not relevant_end:
        return _failure(
            target=target,
            canonical_concept=canonical_concept,
            code=FailureCode.PERIOD_UNAVAILABLE,
            candidates=concept_facts,
            reason="no registered fact is reported at the filing reporting boundary",
        )

    target_accession = tuple(
        item for item in relevant_end if item.accession == target.accession
    )
    if not target_accession:
        return _failure(
            target=target,
            canonical_concept=canonical_concept,
            code=FailureCode.INVALID_CONTEXT,
            candidates=relevant_end,
            reason="facts at the target boundary belong to another accession",
        )

    compatible_context = tuple(
        item
        for item in target_accession
        if item.ticker == target.ticker
        and item.filed_at == target.filed_at
        and item.form == target.form
        and item.fiscal_year == target.fiscal_year
        and item.fact_kind is FactKind.INSTANT
    )
    if not compatible_context:
        return _failure(
            target=target,
            canonical_concept=canonical_concept,
            code=FailureCode.INVALID_CONTEXT,
            candidates=target_accession,
            reason="fact metadata conflicts with the filing reporting context",
        )

    consolidated = _coalesce_registered_aliases(
        tuple(item for item in compatible_context if not item.dimensions),
        registered,
    )
    if not consolidated:
        return _failure(
            target=target,
            canonical_concept=canonical_concept,
            code=FailureCode.INVALID_CONTEXT,
            candidates=target_accession,
            reason="only dimensional facts survive at the target boundary",
        )
    if len(consolidated) == 1:
        return ResolvedFact(consolidated[0])
    return _failure(
        target=target,
        canonical_concept=canonical_concept,
        code=FailureCode.AMBIGUOUS,
        candidates=consolidated,
        reason="multiple instant facts survive concept, context, and period checks",
    )
