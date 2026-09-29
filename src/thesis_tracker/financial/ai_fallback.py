"""Optional AI concept sidecar; Python remains the sole decision maker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Protocol

from thesis_tracker.financial.ai_cache import (
    ProposalCacheError,
    ProposalCacheKey,
    SqliteConceptProposalCache,
)
from thesis_tracker.financial.ai_concepts import (
    PROMPT_VERSION,
    ConceptProposal,
    ConceptProposalProvider,
    ConceptProposalResponseError,
    ProposalStatus,
    build_proposal_prompts,
    parse_concept_proposal,
    sha256_text,
)
from thesis_tracker.financial.ai_validation import (
    ConceptValidationResult,
    ValidatedConceptMapping,
    ValidationEvidence,
    ValidationStatus,
    validate_concept_proposal,
)
from thesis_tracker.financial.models import (
    AiValidationProvenance,
    FactKind,
    FactOrigin,
    FailureCode,
    FailureDiagnostic,
    FinancialFact,
)
from thesis_tracker.financial.registry import CONCEPT_REGISTRY
from thesis_tracker.financial.semantic_candidates import (
    FilingSemanticContext,
    SemanticFactCandidate,
    build_ai_candidate_payload,
)


@dataclass(frozen=True, slots=True)
class AiFallbackConfig:
    """Runtime switch for the optional concept sidecar."""

    enabled: bool = True


class AiAttemptStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NO_MATCH = "no_match"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PROVIDER_ERROR = "provider_error"
    INVALID_RESPONSE = "invalid_response"


@dataclass(frozen=True, slots=True)
class AiConceptAttempt:
    ticker: str
    accession: str
    target: str
    original_failure: FailureCode
    status: AiAttemptStatus
    cache_hit: bool
    model_name: str
    prompt_version: str
    input_hash: str
    response_hash: str | None
    created_at: datetime
    raw_response: str | None = None
    proposal: ConceptProposal | None = None
    validation: ConceptValidationResult | None = None
    error: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    @property
    def validation_reason_code(self) -> str | None:
        return self.validation.reason_code if self.validation is not None else None

    def to_dict(self) -> dict[str, Any]:
        validation = None
        if self.validation is not None:
            validation = {
                "status": self.validation.status.value,
                "reason_code": self.validation.reason_code,
                "reason": self.validation.reason,
                "validator_path": list(self.validation.validator_path),
                "mapping": (
                    {
                        "target": self.validation.mapping.target,
                        "concepts": list(self.validation.mapping.concepts),
                    }
                    if self.validation.mapping is not None
                    else None
                ),
                "evidence": [_evidence_dict(item) for item in self.validation.evidence],
            }
        return {
            "ticker": self.ticker,
            "accession": self.accession,
            "target": self.target,
            "original_failure": self.original_failure.value,
            "status": self.status.value,
            "cache_hit": self.cache_hit,
            "model_name": self.model_name,
            "prompt_version": self.prompt_version,
            "input_hash": self.input_hash,
            "response_hash": self.response_hash,
            "created_at": self.created_at.isoformat(),
            "raw_response": self.raw_response,
            "proposal": (
                self.proposal.model_dump(mode="json")
                if self.proposal is not None
                else None
            ),
            "validation": validation,
            "error": self.error,
            "usage": {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
            },
        }


@dataclass(frozen=True, slots=True)
class AiRecoveryResult:
    mapping: ValidatedConceptMapping | None
    mapped_facts: tuple[FinancialFact, ...]
    attempt: AiConceptAttempt


class AiFallback(Protocol):
    """Sidecar boundary used by the metric without changing its default path."""

    config: AiFallbackConfig

    def recover(
        self,
        *,
        diagnostic: FailureDiagnostic,
        context: FilingSemanticContext,
    ) -> AiRecoveryResult: ...


def is_ai_eligible(code: FailureCode) -> bool:
    """Return whether a canonical failure may enter semantic recovery."""

    return code in {FailureCode.REGISTRY_GAP, FailureCode.CUSTOM_CONCEPT_ONLY}


@dataclass(frozen=True, slots=True)
class SidecarApplicability:
    """Deterministic verdict on whether a semantic proposal is worth asking."""

    applicable: bool
    reason: str
    operands: tuple[str, ...] = ()


_GROSS_PROFIT_EQUATION_OPERANDS: tuple[str, ...] = (
    "gross_profit",
    "cost_of_revenue",
)


def cost_of_revenue_sidecar_applicability(
    context: FilingSemanticContext,
) -> SidecarApplicability:
    """Gate the cost-of-revenue sidecar behind a demonstrable equation operand.

    ``gross_margin_trend`` can only ever accept a semantic cost concept through
    ``revenue - cost_of_revenue == gross_profit``.  When the requested filing
    boundary discloses neither registered operand, no candidate proposal can be
    validated, so the observation is ``not_applicable`` rather than a semantic
    gap worth asking a model about.

    Duration is deliberately not filtered: a fact ending at the requested
    boundary proves the concept is disclosed, matching the resolver's
    period-existence ordering.  Only registered concepts qualify; a broad
    aggregate such as ``us-gaap:CostsAndExpenses`` is not a cost-of-revenue
    operand.
    """

    registered: set[str] = set()
    for canonical in _GROSS_PROFIT_EQUATION_OPERANDS:
        registered.update(CONCEPT_REGISTRY.get(canonical, ()))
    operands = tuple(
        dict.fromkeys(
            fact.concept
            for fact in context.facts
            if fact.concept in registered
            and fact.period_end == context.boundary.period_end
        )
    )
    if operands:
        return SidecarApplicability(
            applicable=True,
            reason=(
                "filing discloses registered gross-profit equation operands at "
                f"{context.boundary.period_end}: {', '.join(operands)}"
            ),
            operands=operands,
        )
    return SidecarApplicability(
        applicable=False,
        reason=(
            "filing discloses no registered GrossProfit or CostOfRevenue fact at "
            f"{context.boundary.period_end}; revenue - cost_of_revenue == "
            "gross_profit cannot be proven, so a semantic cost proposal is not "
            "applicable"
        ),
    )


def _evidence_dict(evidence: ValidationEvidence) -> dict[str, Any]:
    return {
        "candidate_concept": evidence.candidate_concept,
        "accession": evidence.accession,
        "equation": evidence.equation,
        "operand_concepts": list(evidence.operand_concepts),
        "context_ids": list(evidence.context_ids),
        "source_fact_ids": list(evidence.source_fact_ids),
        "unit": evidence.unit.value,
        "period_start": evidence.period_start.isoformat(),
        "period_end": evidence.period_end.isoformat(),
    }


def _provenance(
    *,
    candidate: SemanticFactCandidate,
    evidence: ValidationEvidence,
    attempt: AiConceptAttempt,
    validation: ConceptValidationResult,
) -> AiValidationProvenance:
    return AiValidationProvenance(
        target=attempt.target,
        candidate_concept=candidate.concept,
        model_name=attempt.model_name,
        prompt_version=attempt.prompt_version,
        input_hash=attempt.input_hash,
        response_hash=attempt.response_hash or "unavailable",
        validator_path=validation.validator_path,
        source_fact_ids=evidence.source_fact_ids,
        validation_evidence=(
            ("equation", evidence.equation),
            ("accession", evidence.accession),
            ("unit", evidence.unit.value),
            ("period_start", evidence.period_start.isoformat()),
            ("period_end", evidence.period_end.isoformat()),
            ("context_ids", ",".join(evidence.context_ids)),
        ),
    )


def _mapped_facts(
    context: FilingSemanticContext,
    validation: ConceptValidationResult,
    attempt: AiConceptAttempt,
) -> tuple[FinancialFact, ...]:
    assert validation.mapping is not None
    facts: list[FinancialFact] = []
    for candidate in context.facts:
        if candidate.concept not in validation.mapping.concepts:
            continue
        evidence = next(
            (
                item
                for item in validation.evidence
                if candidate.fact_id in item.source_fact_ids
            ),
            None,
        )
        if evidence is None or candidate.period_start is None:
            continue
        facts.append(
            FinancialFact(
                ticker=candidate.ticker,
                accession=candidate.accession,
                concept=candidate.concept,
                value=candidate.value,
                unit=candidate.unit,
                period_start=candidate.period_start,
                period_end=candidate.period_end,
                filed_at=context.boundary.filed_at,
                form=context.boundary.form,
                fiscal_year=candidate.fiscal_year,
                fiscal_period=candidate.fiscal_period,
                source=candidate.source,
                extraction_path=candidate.extraction_path,
                context_id=candidate.context_id,
                dimensions=candidate.dimensions,
                origin=FactOrigin.AI_ASSISTED_VALIDATED,
                resolver_path=(
                    "stage1_boundary",
                    "filing_xbrl",
                    "ai_candidate_proposal",
                    "python_exact_equation",
                ),
                fact_kind=FactKind.DURATION,
                ai_validation=(
                    _provenance(
                        candidate=candidate,
                        evidence=evidence,
                        attempt=attempt,
                        validation=validation,
                    ),
                ),
            )
        )
    return tuple(facts)


class AiConceptFallback:
    """Cached semantic proposal followed by mandatory deterministic validation."""

    def __init__(
        self,
        *,
        provider: ConceptProposalProvider,
        cache: SqliteConceptProposalCache,
        config: AiFallbackConfig | None = None,
    ) -> None:
        self.provider = provider
        self.cache = cache
        self.config = config or AiFallbackConfig()

    def _attempt(
        self,
        *,
        diagnostic: FailureDiagnostic,
        context: FilingSemanticContext,
        key: ProposalCacheKey,
        status: AiAttemptStatus,
        created_at: datetime,
        cache_hit: bool,
        raw_response: str | None = None,
        proposal: ConceptProposal | None = None,
        validation: ConceptValidationResult | None = None,
        error: str | None = None,
        response_hash: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> AiConceptAttempt:
        return AiConceptAttempt(
            ticker=context.boundary.ticker,
            accession=context.boundary.accession,
            target=diagnostic.concept,
            original_failure=diagnostic.final_failure,
            status=status,
            cache_hit=cache_hit,
            model_name=self.provider.model_name,
            prompt_version=PROMPT_VERSION,
            input_hash=key.input_hash,
            response_hash=response_hash,
            created_at=created_at,
            raw_response=raw_response,
            proposal=proposal,
            validation=validation,
            error=error,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )

    def recover(
        self,
        *,
        diagnostic: FailureDiagnostic,
        context: FilingSemanticContext,
    ) -> AiRecoveryResult:
        target = diagnostic.concept
        payload = build_ai_candidate_payload(context, target)
        key = ProposalCacheKey.build(
            accession=context.boundary.accession,
            target=target,
            input_payload=payload,
            prompt_version=PROMPT_VERSION,
            model_name=self.provider.model_name,
        )
        now = datetime.now(timezone.utc)
        try:
            cached = self.cache.get(key)
        except ProposalCacheError as error:
            attempt = self._attempt(
                diagnostic=diagnostic,
                context=context,
                key=key,
                status=AiAttemptStatus.INVALID_RESPONSE,
                created_at=now,
                cache_hit=True,
                error=f"{type(error).__name__}: {error}",
            )
            return AiRecoveryResult(None, (), attempt)

        if cached is not None:
            raw_response = cached.raw_response
            response_hash = cached.response_hash
            proposal = cached.proposal
            created_at = cached.created_at
            cache_hit = True
            prompt_tokens = cached.prompt_tokens
            completion_tokens = cached.completion_tokens
            total_tokens = cached.total_tokens
        else:
            system_prompt, user_prompt = build_proposal_prompts(payload)
            try:
                response = self.provider.propose(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
            except Exception as error:  # provider libraries expose varied errors
                attempt = self._attempt(
                    diagnostic=diagnostic,
                    context=context,
                    key=key,
                    status=AiAttemptStatus.PROVIDER_ERROR,
                    created_at=now,
                    cache_hit=False,
                    error=f"{type(error).__name__}: {error}",
                )
                return AiRecoveryResult(None, (), attempt)
            raw_response = response.raw_response
            response_hash = sha256_text(raw_response)
            try:
                proposal = parse_concept_proposal(
                    raw_response,
                    expected_target=target,
                )
            except ConceptProposalResponseError as error:
                attempt = self._attempt(
                    diagnostic=diagnostic,
                    context=context,
                    key=key,
                    status=AiAttemptStatus.INVALID_RESPONSE,
                    created_at=now,
                    cache_hit=False,
                    raw_response=raw_response,
                    response_hash=response_hash,
                    error=f"{type(error).__name__}: {error}",
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    total_tokens=response.total_tokens,
                )
                return AiRecoveryResult(None, (), attempt)
            self.cache.put(key, response, proposal, created_at=now)
            created_at = now
            cache_hit = False
            prompt_tokens = response.prompt_tokens
            completion_tokens = response.completion_tokens
            total_tokens = response.total_tokens

        base = dict(
            diagnostic=diagnostic,
            context=context,
            key=key,
            created_at=created_at,
            cache_hit=cache_hit,
            raw_response=raw_response,
            response_hash=response_hash,
            proposal=proposal,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
        if proposal.status is ProposalStatus.NO_MATCH:
            attempt = self._attempt(status=AiAttemptStatus.NO_MATCH, **base)
            return AiRecoveryResult(None, (), attempt)
        if proposal.status is ProposalStatus.INSUFFICIENT_EVIDENCE:
            attempt = self._attempt(
                status=AiAttemptStatus.INSUFFICIENT_EVIDENCE,
                **base,
            )
            return AiRecoveryResult(None, (), attempt)

        validation = validate_concept_proposal(
            proposal,
            context,
            context.boundary,
        )
        status = (
            AiAttemptStatus.ACCEPTED
            if validation.status is ValidationStatus.ACCEPTED
            else AiAttemptStatus.REJECTED
        )
        attempt = self._attempt(status=status, validation=validation, **base)
        if validation.status is ValidationStatus.REJECTED:
            return AiRecoveryResult(None, (), attempt)
        mapped = _mapped_facts(context, validation, attempt)
        if not mapped:
            failed_attempt = self._attempt(
                status=AiAttemptStatus.REJECTED,
                validation=ConceptValidationResult(
                    status=ValidationStatus.REJECTED,
                    mapping=None,
                    evidence=validation.evidence,
                    validator_path=validation.validator_path,
                    reason_code="validated_fact_materialization_failed",
                    reason="validated evidence did not identify a source candidate",
                ),
                **base,
            )
            return AiRecoveryResult(None, (), failed_attempt)
        return AiRecoveryResult(validation.mapping, mapped, attempt)
