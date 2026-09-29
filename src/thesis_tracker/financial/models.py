"""Validated, serializable types for reported and derived financial facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any


class FailureCode(StrEnum):
    """Canonical Stage 3 failure taxonomy."""

    NOT_APPLICABLE = "not_applicable"
    SOURCE_STALE = "source_stale"
    REGISTRY_GAP = "registry_gap"
    CUSTOM_CONCEPT_ONLY = "custom_concept_only"
    INVALID_CONTEXT = "invalid_context"
    PERIOD_UNAVAILABLE = "period_unavailable"
    DURATION_UNAVAILABLE = "duration_unavailable"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"
    TRUE_MISSING = "true_missing"
    # Restored from the historical Stage 3 taxonomy for metric-level failures
    # that the ten source/period codes cannot express: a ratio with a zero
    # denominator, and a metric that requires an explicitly supplied external
    # input (historical market cap) which is absent.
    ZERO_DENOMINATOR = "zero_denominator"
    MISSING_EXTERNAL_DATA = "missing_external_data"


class Unit(StrEnum):
    """Units supported by the Stage 3 fact model."""

    USD = "USD"
    SHARES = "shares"
    USD_PER_SHARE = "USD/shares"
    PURE = "pure"


class FactOrigin(StrEnum):
    REPORTED = "reported"
    DERIVED = "derived"
    AI_ASSISTED_VALIDATED = "ai_assisted_validated"


class FactKind(StrEnum):
    INSTANT = "instant"
    DURATION = "duration"


@dataclass(frozen=True, slots=True)
class AiValidationProvenance:
    """Immutable audit record for one Python-validated AI proposal."""

    target: str
    candidate_concept: str
    model_name: str
    prompt_version: str
    input_hash: str
    response_hash: str
    validator_path: tuple[str, ...]
    source_fact_ids: tuple[str, ...]
    validation_evidence: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        required = (
            self.target,
            self.candidate_concept,
            self.model_name,
            self.prompt_version,
            self.input_hash,
            self.response_hash,
        )
        if not all(value.strip() for value in required):
            raise ValueError("AI validation provenance fields must not be blank")
        if not self.validator_path or not self.source_fact_ids:
            raise ValueError("AI validation requires validator path and source facts")

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "candidate_concept": self.candidate_concept,
            "model_name": self.model_name,
            "prompt_version": self.prompt_version,
            "input_hash": self.input_hash,
            "response_hash": self.response_hash,
            "validator_path": list(self.validator_path),
            "source_fact_ids": list(self.source_fact_ids),
            "validation_evidence": dict(self.validation_evidence),
        }


@dataclass(frozen=True, slots=True)
class FilingBoundary:
    """A periodic filing's reporting boundary, independent of fact duration."""

    ticker: str
    accession: str
    filed_at: date
    form: str
    fiscal_year: int
    fiscal_period: str
    period_end: date
    source: str

    def __post_init__(self) -> None:
        if not all((self.ticker, self.accession, self.form, self.source)):
            raise ValueError("filing boundary provenance fields must not be blank")
        if self.fiscal_period not in {"Q1", "Q2", "Q3", "Q4"}:
            raise ValueError("filing boundary fiscal_period must be Q1 through Q4")

    @property
    def target_period(self) -> str:
        return f"{self.fiscal_year}-{self.fiscal_period}"


@dataclass(frozen=True, slots=True)
class FinancialFact:
    """A reported or derived fact with mandatory SEC provenance."""

    ticker: str
    accession: str
    concept: str
    value: Decimal
    unit: Unit
    period_start: date | None
    period_end: date
    filed_at: date
    form: str
    fiscal_year: int
    fiscal_period: str
    source: str
    extraction_path: str
    context_id: str
    dimensions: tuple[tuple[str, str], ...]
    origin: FactOrigin
    resolver_path: tuple[str, ...]
    fact_kind: FactKind = FactKind.DURATION
    derivation_method: str | None = None
    source_fact_ids: tuple[str, ...] = ()
    ai_validation: tuple[AiValidationProvenance, ...] = ()

    def __post_init__(self) -> None:
        required = (
            self.ticker,
            self.accession,
            self.concept,
            self.form,
            self.fiscal_period,
            self.source,
            self.extraction_path,
            self.context_id,
        )
        if not all(item.strip() for item in required):
            raise ValueError("financial fact provenance fields must not be blank")
        if self.fact_kind is FactKind.INSTANT and self.period_start is not None:
            raise ValueError("instant fact must not have period_start")
        if self.fact_kind is FactKind.DURATION and self.period_start is None:
            raise ValueError("duration fact requires period_start")
        if self.period_start is not None and self.period_start > self.period_end:
            raise ValueError("duration fact period_start must not exceed period_end")
        if not isinstance(self.value, Decimal):
            raise TypeError("financial fact value must be Decimal")
        if not self.resolver_path:
            raise ValueError("financial fact resolver_path must not be empty")
        if self.origin is FactOrigin.REPORTED:
            if (
                self.derivation_method is not None
                or self.source_fact_ids
                or self.ai_validation
            ):
                raise ValueError("reported fact cannot carry derived or AI metadata")
        elif self.origin is FactOrigin.AI_ASSISTED_VALIDATED:
            if self.derivation_method is not None or self.source_fact_ids:
                raise ValueError("AI-validated reported fact cannot be derived")
            if not self.ai_validation:
                raise ValueError("AI-assisted fact requires validation provenance")
        elif not self.derivation_method or len(self.source_fact_ids) < 2:
            raise ValueError(
                "derived fact requires derivation_method and at least two sources"
            )

    @property
    def fact_id(self) -> str:
        return f"{self.accession}|{self.concept}|{self.context_id}"

    def to_dict(self) -> dict[str, Any]:
        serialized = {
            "fact_id": self.fact_id,
            "ticker": self.ticker,
            "accession": self.accession,
            "concept": self.concept,
            "value": str(self.value),
            "unit": self.unit.value,
            "period_start": (
                self.period_start.isoformat() if self.period_start is not None else None
            ),
            "period_end": self.period_end.isoformat(),
            "filed_at": self.filed_at.isoformat(),
            "form": self.form,
            "fiscal_year": self.fiscal_year,
            "fiscal_period": self.fiscal_period,
            "source": self.source,
            "extraction_path": self.extraction_path,
            "context_id": self.context_id,
            "dimensions": dict(self.dimensions),
            "origin": self.origin.value,
            "fact_kind": self.fact_kind.value,
            "resolver_path": list(self.resolver_path),
            "derivation_method": self.derivation_method,
            "source_fact_ids": list(self.source_fact_ids),
        }
        if self.ai_validation:
            serialized["ai_validation"] = [
                item.to_dict() for item in self.ai_validation
            ]
        return serialized


@dataclass(frozen=True, slots=True)
class FailureDiagnostic:
    ticker: str
    concept: str
    metric: str
    target_period: str
    final_failure: FailureCode
    recovery_history: tuple[FailureCode, ...]
    source: str
    filing_accession: str
    candidate_count: int
    candidate_summary: tuple[dict[str, Any], ...]
    rejection_reason: str
    final_status: str = "failed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "concept": self.concept,
            "metric": self.metric,
            "target_period": self.target_period,
            "final_failure": self.final_failure.value,
            "recovery_history": [code.value for code in self.recovery_history],
            "source": self.source,
            "filing_accession": self.filing_accession,
            "candidate_count": self.candidate_count,
            "candidate_summary": list(self.candidate_summary),
            "rejection_reason": self.rejection_reason,
            "final_status": self.final_status,
        }


@dataclass(frozen=True, slots=True)
class ResolvedFact:
    fact: FinancialFact


@dataclass(frozen=True, slots=True)
class FailedFact:
    diagnostic: FailureDiagnostic


FactResult = ResolvedFact | FailedFact
