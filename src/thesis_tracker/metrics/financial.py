"""Deterministic Python financial metrics."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any

from thesis_tracker.financial.ai_fallback import (
    AiConceptAttempt,
    AiFallback,
    cost_of_revenue_sidecar_applicability,
    is_ai_eligible,
)
from thesis_tracker.financial.models import (
    FailedFact,
    FailureCode,
    FailureDiagnostic,
    FilingBoundary,
    FinancialFact,
    ResolvedFact,
)
from thesis_tracker.financial.resolver import (
    prior_fiscal_boundary,
    resolve_instant_fact,
    resolve_quarterly_fact,
)
from thesis_tracker.financial.semantic_candidates import FilingSemanticContext


@dataclass(frozen=True, slots=True)
class MetricProvenance:
    formula: str
    source_facts: tuple[FinancialFact, ...]

    @property
    def source_fact_ids(self) -> tuple[str, ...]:
        return tuple(fact.fact_id for fact in self.source_facts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "formula": self.formula,
            "source_fact_ids": list(self.source_fact_ids),
            "source_facts": [fact.to_dict() for fact in self.source_facts],
        }


@dataclass(frozen=True, slots=True)
class GrossMarginObservation:
    ticker: str
    period_end: date
    fiscal_year: int
    fiscal_period: str
    value: Decimal
    provenance: MetricProvenance

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "period_end": self.period_end.isoformat(),
            "fiscal_year": self.fiscal_year,
            "fiscal_period": self.fiscal_period,
            "value": str(self.value),
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class GrossMarginTrend:
    ticker: str
    observations: tuple[GrossMarginObservation, ...]
    failures: tuple[FailureDiagnostic, ...]
    ai_attempts: tuple[AiConceptAttempt, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = {
            "ticker": self.ticker,
            "metric": "gross_margin_trend",
            "observations": [item.to_dict() for item in self.observations],
            "failures": [item.to_dict() for item in self.failures],
        }
        if self.ai_attempts:
            result["ai_attempts"] = [item.to_dict() for item in self.ai_attempts]
        return result


def _leaf_accessions(fact: FinancialFact) -> tuple[str, ...]:
    if not fact.source_fact_ids:
        return (fact.accession,)
    return tuple(fact_id.split("|", 1)[0] for fact_id in fact.source_fact_ids)


def _metric_failure(
    diagnostic: FailureDiagnostic,
    *,
    reason: str | None = None,
    code: FailureCode | None = None,
    recovery_history: tuple[FailureCode, ...] | None = None,
    final_status: str | None = None,
) -> FailureDiagnostic:
    return replace(
        diagnostic,
        metric="gross_margin_trend",
        final_failure=code or diagnostic.final_failure,
        recovery_history=(
            diagnostic.recovery_history
            if recovery_history is None
            else recovery_history
        ),
        rejection_reason=reason or diagnostic.rejection_reason,
        final_status=final_status or diagnostic.final_status,
    )


def _is_financial_industry_sic(issuer_sic: str | None) -> bool:
    """Return whether SEC SIC places the issuer in Finance (major groups 60-67)."""

    if issuer_sic is None or len(issuer_sic) != 4 or not issuer_sic.isdigit():
        return False
    return 60 <= int(issuer_sic[:2]) <= 67


def compute_gross_margin_trend(
    *,
    ticker: str,
    facts: tuple[FinancialFact, ...],
    boundaries: tuple[FilingBoundary, ...],
    max_periods: int = 8,
    issuer_sic: str | None = None,
    ai_fallback: AiFallback | None = None,
    semantic_contexts: tuple[FilingSemanticContext, ...] = (),
) -> GrossMarginTrend:
    """Compute up to eight proven single-quarter gross-margin observations."""

    observations: list[GrossMarginObservation] = []
    failures: list[FailureDiagnostic] = []
    ai_attempts: list[AiConceptAttempt] = []
    contexts_by_accession = {
        context.boundary.accession: context for context in semantic_contexts
    }
    ordered = sorted(boundaries, key=lambda item: item.period_end, reverse=True)
    if _is_financial_industry_sic(issuer_sic):
        latest = ordered[0] if ordered else None
        major_group = issuer_sic[:2]
        return GrossMarginTrend(
            ticker=ticker,
            observations=(),
            failures=(
                FailureDiagnostic(
                    ticker=ticker,
                    concept="gross_margin",
                    metric="gross_margin_trend",
                    target_period=(latest.target_period if latest else "unavailable"),
                    final_failure=FailureCode.NOT_APPLICABLE,
                    recovery_history=(),
                    source="sec_company_metadata",
                    filing_accession=(latest.accession if latest else "unavailable"),
                    candidate_count=0,
                    candidate_summary=(
                        {
                            "issuer_sic": issuer_sic,
                            "sic_major_group": major_group,
                        },
                    ),
                    rejection_reason=(
                        "gross margin is not comparable for SEC SIC finance "
                        f"major group {major_group} because interest and "
                        "noninterest presentation is not revenue less cost of revenue"
                    ),
                    final_status="not_applicable",
                ),
            ),
        )
    for target in ordered[:max_periods]:
        revenue = resolve_quarterly_fact(
            facts=facts,
            boundaries=boundaries,
            target=target,
            canonical_concept="revenue",
        )
        cost = resolve_quarterly_fact(
            facts=facts,
            boundaries=boundaries,
            target=target,
            canonical_concept="cost_of_revenue",
        )
        formula = "(revenue - cost_of_revenue) / revenue"
        if isinstance(revenue, FailedFact) or isinstance(cost, FailedFact):
            gross_profit_revenue = resolve_quarterly_fact(
                facts=facts,
                boundaries=boundaries,
                target=target,
                canonical_concept="gross_profit_revenue",
            )
            gross_profit = resolve_quarterly_fact(
                facts=facts,
                boundaries=boundaries,
                target=target,
                canonical_concept="gross_profit",
            )
            if isinstance(gross_profit_revenue, FailedFact) or isinstance(
                gross_profit, FailedFact
            ):
                primary_failure = revenue if isinstance(revenue, FailedFact) else cost
                assert isinstance(primary_failure, FailedFact)
                recovery_failures = tuple(
                    item.diagnostic
                    for item in (gross_profit_revenue, gross_profit)
                    if isinstance(item, FailedFact)
                )
                recovery_history = tuple(
                    dict.fromkeys(
                        (
                            *primary_failure.diagnostic.recovery_history,
                            *(
                                item.final_failure
                                for item in recovery_failures
                                if item.final_failure
                                is not primary_failure.diagnostic.final_failure
                            ),
                        )
                    )
                )
                recovery_details = "; ".join(
                    f"{item.concept}: {item.final_failure.value} "
                    f"({item.rejection_reason})"
                    for item in recovery_failures
                )
                primary_diagnostic = primary_failure.diagnostic
                if (
                    ai_fallback is not None
                    and ai_fallback.config.enabled
                    and primary_diagnostic.concept == "cost_of_revenue"
                    and is_ai_eligible(primary_diagnostic.final_failure)
                    and target.accession in contexts_by_accession
                ):
                    context = contexts_by_accession[target.accession]
                    applicability = cost_of_revenue_sidecar_applicability(context)
                    if not applicability.applicable:
                        # Deterministic gate: neither equation operand is
                        # disclosed for this boundary, so no semantic proposal
                        # could ever be validated.  Do not ask the model.
                        failures.append(
                            _metric_failure(
                                primary_diagnostic,
                                code=FailureCode.NOT_APPLICABLE,
                                recovery_history=recovery_history,
                                final_status="not_applicable",
                                reason=applicability.reason,
                            )
                        )
                        continue
                    recovery = ai_fallback.recover(
                        diagnostic=primary_diagnostic,
                        context=context,
                    )
                    ai_attempts.append(recovery.attempt)
                    if recovery.mapping is not None:
                        retried_cost = resolve_quarterly_fact(
                            facts=(*facts, *recovery.mapped_facts),
                            boundaries=boundaries,
                            target=target,
                            canonical_concept="cost_of_revenue",
                            concept_overlay=recovery.mapping.concepts,
                        )
                        if isinstance(retried_cost, ResolvedFact):
                            cost = retried_cost
                            formula = "(revenue - cost_of_revenue) / revenue"
                            assert isinstance(revenue, ResolvedFact)
                        else:
                            cost = primary_failure
                    if not isinstance(cost, ResolvedFact):
                        failures.append(
                            _metric_failure(
                                primary_diagnostic,
                                recovery_history=recovery_history,
                                reason=(
                                    f"{primary_diagnostic.rejection_reason}; "
                                    "gross-profit recovery failed: "
                                    f"{recovery_details}"
                                ),
                            )
                        )
                        continue
                else:
                    failures.append(
                        _metric_failure(
                            primary_diagnostic,
                            recovery_history=recovery_history,
                            reason=(
                                f"{primary_diagnostic.rejection_reason}; "
                                f"gross-profit recovery failed: {recovery_details}"
                            ),
                        )
                    )
                    continue
            else:
                revenue = gross_profit_revenue
                cost = gross_profit
                formula = "gross_profit / revenue"
        assert isinstance(revenue, ResolvedFact)
        assert isinstance(cost, ResolvedFact)
        if revenue.fact.unit is not cost.fact.unit:
            failures.append(
                _metric_failure(
                    FailureDiagnostic(
                        ticker=ticker,
                        concept="revenue,cost_of_revenue",
                        metric="gross_margin_trend",
                        target_period=target.target_period,
                        final_failure=FailureCode.INVALID_CONTEXT,
                        recovery_history=(),
                        source="sec_filing_xbrl",
                        filing_accession=target.accession,
                        candidate_count=2,
                        candidate_summary=(
                            revenue.fact.to_dict(),
                            cost.fact.to_dict(),
                        ),
                        rejection_reason="metric inputs use incompatible units",
                    )
                )
            )
            continue
        if _leaf_accessions(revenue.fact) != _leaf_accessions(cost.fact):
            failures.append(
                _metric_failure(
                    FailureDiagnostic(
                        ticker=ticker,
                        concept="revenue,cost_of_revenue",
                        metric="gross_margin_trend",
                        target_period=target.target_period,
                        final_failure=FailureCode.INVALID_CONTEXT,
                        recovery_history=(),
                        source="sec_filing_xbrl",
                        filing_accession=target.accession,
                        candidate_count=2,
                        candidate_summary=(
                            revenue.fact.to_dict(),
                            cost.fact.to_dict(),
                        ),
                        rejection_reason=(
                            "gross-margin inputs do not share the same source accessions"
                        ),
                    )
                )
            )
            continue
        if revenue.fact.value == 0:
            failures.append(
                _metric_failure(
                    FailureDiagnostic(
                        ticker=ticker,
                        concept="revenue",
                        metric="gross_margin_trend",
                        target_period=target.target_period,
                        final_failure=FailureCode.INVALID_CONTEXT,
                        recovery_history=(),
                        source="sec_filing_xbrl",
                        filing_accession=target.accession,
                        candidate_count=1,
                        candidate_summary=(revenue.fact.to_dict(),),
                        rejection_reason="gross margin denominator is zero",
                    )
                )
            )
            continue
        if formula == "gross_profit / revenue":
            value = cost.fact.value / revenue.fact.value
            source_facts = (cost.fact, revenue.fact)
        else:
            value = (revenue.fact.value - cost.fact.value) / revenue.fact.value
            source_facts = (revenue.fact, cost.fact)
        observations.append(
            GrossMarginObservation(
                ticker=ticker,
                period_end=target.period_end,
                fiscal_year=target.fiscal_year,
                fiscal_period=target.fiscal_period,
                value=value,
                provenance=MetricProvenance(
                    formula=formula,
                    source_facts=source_facts,
                ),
            )
        )
    return GrossMarginTrend(
        ticker=ticker,
        observations=tuple(observations),
        failures=tuple(failures),
        ai_attempts=tuple(ai_attempts),
    )


# ----------------------------------------------------------------
# Stage 3 metric migration: 7 additional deterministic metrics
# ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MarketCapInput:
    """Explicitly supplied historical market capitalisation for one period.

    Never derived from the current price; an absent input yields
    ``missing_external_data`` rather than a guess.
    """

    value: Decimal
    period_end: date
    source: str


@dataclass(frozen=True, slots=True)
class Stage3Observation:
    metric: str
    ticker: str
    accession: str
    period_end: date
    fiscal_year: int
    fiscal_period: str
    value: Decimal
    formula: str
    provenance: MetricProvenance

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "ticker": self.ticker,
            "accession": self.accession,
            "period_end": self.period_end.isoformat(),
            "fiscal_year": self.fiscal_year,
            "fiscal_period": self.fiscal_period,
            "value": str(self.value),
            "formula": self.formula,
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class MetricResult:
    metric: str
    ticker: str
    observations: tuple[Stage3Observation, ...]
    failures: tuple[FailureDiagnostic, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "ticker": self.ticker,
            "observations": [item.to_dict() for item in self.observations],
            "failures": [item.to_dict() for item in self.failures],
        }


def _ordered_targets(
    boundaries: tuple[FilingBoundary, ...], max_periods: int
) -> tuple[FilingBoundary, ...]:
    ordered = sorted(boundaries, key=lambda item: item.period_end, reverse=True)
    return tuple(ordered[: max(0, max_periods)])


def _year_ago_boundary(
    boundaries: tuple[FilingBoundary, ...], target: FilingBoundary
) -> FilingBoundary | None:
    matches = [
        item
        for item in boundaries
        if item.ticker == target.ticker
        and item.fiscal_year == target.fiscal_year - 1
        and item.fiscal_period == target.fiscal_period
        and item.period_end < target.period_end
    ]
    return max(matches, key=lambda item: item.period_end) if matches else None


def _fact_summary(fact: FinancialFact) -> dict[str, object]:
    return {
        "fact_id": fact.fact_id,
        "accession": fact.accession,
        "concept": fact.concept,
        "value": str(fact.value),
        "period_end": fact.period_end.isoformat(),
        "context_id": fact.context_id,
    }


def _metric_diagnostic(
    *,
    metric: str,
    target: FilingBoundary,
    concept: str,
    code: FailureCode,
    reason: str,
    candidates: tuple[FinancialFact, ...] = (),
) -> FailureDiagnostic:
    return FailureDiagnostic(
        ticker=target.ticker,
        concept=concept,
        metric=metric,
        target_period=target.target_period,
        final_failure=code,
        recovery_history=(),
        source="sec_filing_xbrl",
        filing_accession=target.accession,
        candidate_count=len(candidates),
        candidate_summary=tuple(_fact_summary(item) for item in candidates),
        rejection_reason=reason,
    )


def _propagate_failure(metric: str, failed: FailedFact) -> FailureDiagnostic:
    return replace(failed.diagnostic, metric=metric)


def _observation(
    *,
    metric: str,
    target: FilingBoundary,
    value: Decimal,
    formula: str,
    source_facts: tuple[FinancialFact, ...],
) -> Stage3Observation:
    return Stage3Observation(
        metric=metric,
        ticker=target.ticker,
        accession=target.accession,
        period_end=target.period_end,
        fiscal_year=target.fiscal_year,
        fiscal_period=target.fiscal_period,
        value=value,
        formula=formula,
        provenance=MetricProvenance(formula=formula, source_facts=source_facts),
    )


def _resolve_flow(
    facts: tuple[FinancialFact, ...],
    boundaries: tuple[FilingBoundary, ...],
    target: FilingBoundary,
    concept: str,
) -> ResolvedFact | FailedFact:
    return resolve_quarterly_fact(
        facts=facts,
        boundaries=boundaries,
        target=target,
        canonical_concept=concept,
    )


def _resolve_instant(
    facts: tuple[FinancialFact, ...],
    target: FilingBoundary,
    concept: str,
) -> ResolvedFact | FailedFact:
    return resolve_instant_fact(
        facts=facts,
        target=target,
        canonical_concept=concept,
    )


def _first_failure(
    metric: str,
    results: tuple[ResolvedFact | FailedFact, ...],
    failures: list[FailureDiagnostic],
) -> bool:
    for item in results:
        if isinstance(item, FailedFact):
            failures.append(_propagate_failure(metric, item))
            return True
    return False


_FINANCIAL_INSTITUTION_INAPPLICABLE_METRICS = frozenset(
    {
        "ar_growth_vs_rev_growth",
        "gross_margin_trend",
        "interest_coverage",
        "net_debt_to_ebitda",
    }
)


def _issuer_inapplicable(
    *,
    metric: str,
    ticker: str,
    boundaries: tuple[FilingBoundary, ...],
    issuer_sic: str | None,
) -> MetricResult | None:
    """Return a not_applicable result for issuer types outside a metric's model."""

    if metric not in _FINANCIAL_INSTITUTION_INAPPLICABLE_METRICS:
        return None
    if not _is_financial_industry_sic(issuer_sic):
        return None
    latest = max(boundaries, key=lambda item: item.period_end, default=None)
    major_group = str(issuer_sic)[:2]
    return MetricResult(
        metric=metric,
        ticker=ticker,
        observations=(),
        failures=(
            FailureDiagnostic(
                ticker=ticker,
                concept=metric,
                metric=metric,
                target_period=(latest.target_period if latest else "unavailable"),
                final_failure=FailureCode.NOT_APPLICABLE,
                recovery_history=(),
                source="sec_company_metadata",
                filing_accession=(latest.accession if latest else "unavailable"),
                candidate_count=0,
                candidate_summary=(
                    {"issuer_sic": issuer_sic, "sic_major_group": major_group},
                ),
                rejection_reason=(
                    f"{metric} is not comparable for SEC SIC finance major group "
                    f"{major_group}"
                ),
                final_status="not_applicable",
            ),
        ),
    )


def compute_cash_conversion(
    *,
    ticker: str,
    facts: tuple[FinancialFact, ...],
    boundaries: tuple[FilingBoundary, ...],
    max_periods: int = 8,
    issuer_sic: str | None = None,
) -> MetricResult:
    """cash_conversion = operating_cash_flow / net_income (Decimal, signed)."""

    del issuer_sic  # cash conversion is meaningful for every issuer type

    metric = "cash_conversion"
    formula = "operating_cash_flow / net_income"
    observations: list[Stage3Observation] = []
    failures: list[FailureDiagnostic] = []
    for target in _ordered_targets(boundaries, max_periods):
        ocf = _resolve_flow(facts, boundaries, target, "operating_cash_flow")
        income = _resolve_flow(facts, boundaries, target, "net_income")
        if _first_failure(metric, (ocf, income), failures):
            continue
        assert isinstance(ocf, ResolvedFact) and isinstance(income, ResolvedFact)
        if income.fact.value == 0:
            failures.append(
                _metric_diagnostic(
                    metric=metric,
                    target=target,
                    concept="net_income",
                    code=FailureCode.ZERO_DENOMINATOR,
                    reason="net income is zero; cash conversion is undefined",
                    candidates=(income.fact,),
                )
            )
            continue
        observations.append(
            _observation(
                metric=metric,
                target=target,
                value=ocf.fact.value / income.fact.value,
                formula=formula,
                source_facts=(ocf.fact, income.fact),
            )
        )
    return MetricResult(metric, ticker, tuple(observations), tuple(failures))


def compute_accruals_ratio(
    *,
    ticker: str,
    facts: tuple[FinancialFact, ...],
    boundaries: tuple[FilingBoundary, ...],
    max_periods: int = 8,
    issuer_sic: str | None = None,
) -> MetricResult:
    """accruals_ratio = (net_income - operating_cash_flow) / average total assets."""

    del issuer_sic  # accruals are meaningful for every issuer type
    metric = "accruals_ratio"
    formula = (
        "(net_income - operating_cash_flow) / "
        "((beginning_total_assets + ending_total_assets) / 2)"
    )
    observations: list[Stage3Observation] = []
    failures: list[FailureDiagnostic] = []
    for target in _ordered_targets(boundaries, max_periods):
        prior = prior_fiscal_boundary(boundaries, target)
        if prior is None:
            failures.append(
                _metric_diagnostic(
                    metric=metric,
                    target=target,
                    concept="total_assets",
                    code=FailureCode.PERIOD_UNAVAILABLE,
                    reason=(
                        "the immediately preceding fiscal boundary is unavailable; "
                        "average total assets cannot be proven"
                    ),
                )
            )
            continue
        income = _resolve_flow(facts, boundaries, target, "net_income")
        ocf = _resolve_flow(facts, boundaries, target, "operating_cash_flow")
        beginning = _resolve_instant(facts, prior, "total_assets")
        ending = _resolve_instant(facts, target, "total_assets")
        if _first_failure(metric, (income, ocf, beginning, ending), failures):
            continue
        assert isinstance(income, ResolvedFact)
        assert isinstance(ocf, ResolvedFact)
        assert isinstance(beginning, ResolvedFact)
        assert isinstance(ending, ResolvedFact)
        average = (beginning.fact.value + ending.fact.value) / Decimal("2")
        if average == 0:
            failures.append(
                _metric_diagnostic(
                    metric=metric,
                    target=target,
                    concept="total_assets",
                    code=FailureCode.ZERO_DENOMINATOR,
                    reason="average total assets is zero; accruals ratio is undefined",
                    candidates=(beginning.fact, ending.fact),
                )
            )
            continue
        observations.append(
            _observation(
                metric=metric,
                target=target,
                value=(income.fact.value - ocf.fact.value) / average,
                formula=formula,
                source_facts=(income.fact, ocf.fact, beginning.fact, ending.fact),
            )
        )
    return MetricResult(metric, ticker, tuple(observations), tuple(failures))


def compute_ar_growth_vs_rev_growth(
    *,
    ticker: str,
    facts: tuple[FinancialFact, ...],
    boundaries: tuple[FilingBoundary, ...],
    max_periods: int = 8,
    issuer_sic: str | None = None,
) -> MetricResult:
    """AR YoY growth minus revenue YoY growth for comparable fiscal quarters."""

    metric = "ar_growth_vs_rev_growth"
    formula = "accounts_receivable_yoy - revenue_yoy"
    inapplicable = _issuer_inapplicable(
        metric=metric, ticker=ticker, boundaries=boundaries, issuer_sic=issuer_sic
    )
    if inapplicable is not None:
        return inapplicable
    observations: list[Stage3Observation] = []
    failures: list[FailureDiagnostic] = []
    for target in _ordered_targets(boundaries, max_periods):
        prior_year = _year_ago_boundary(boundaries, target)
        if prior_year is None:
            failures.append(
                _metric_diagnostic(
                    metric=metric,
                    target=target,
                    concept="accounts_receivable",
                    code=FailureCode.PERIOD_UNAVAILABLE,
                    reason=(
                        "no comparable year-ago fiscal boundary; YoY growth "
                        "cannot be proven"
                    ),
                )
            )
            continue
        ar_now = _resolve_instant(facts, target, "accounts_receivable")
        ar_then = _resolve_instant(facts, prior_year, "accounts_receivable")
        rev_now = _resolve_flow(facts, boundaries, target, "revenue")
        rev_then = _resolve_flow(facts, boundaries, prior_year, "revenue")
        if _first_failure(metric, (ar_now, ar_then, rev_now, rev_then), failures):
            continue
        assert isinstance(ar_now, ResolvedFact)
        assert isinstance(ar_then, ResolvedFact)
        assert isinstance(rev_now, ResolvedFact)
        assert isinstance(rev_then, ResolvedFact)
        if ar_then.fact.value == 0 or rev_then.fact.value == 0:
            failures.append(
                _metric_diagnostic(
                    metric=metric,
                    target=target,
                    concept="accounts_receivable,revenue",
                    code=FailureCode.ZERO_DENOMINATOR,
                    reason="a prior-year denominator is zero; YoY growth is undefined",
                    candidates=(ar_then.fact, rev_then.fact),
                )
            )
            continue
        ar_growth = (
            ar_now.fact.value - ar_then.fact.value
        ) / ar_then.fact.value
        rev_growth = (
            rev_now.fact.value - rev_then.fact.value
        ) / rev_then.fact.value
        observations.append(
            _observation(
                metric=metric,
                target=target,
                value=ar_growth - rev_growth,
                formula=formula,
                source_facts=(ar_now.fact, ar_then.fact, rev_now.fact, rev_then.fact),
            )
        )
    return MetricResult(metric, ticker, tuple(observations), tuple(failures))


def compute_net_buyback_yield(
    *,
    ticker: str,
    facts: tuple[FinancialFact, ...],
    boundaries: tuple[FilingBoundary, ...],
    max_periods: int = 8,
    market_cap: MarketCapInput | None = None,
    issuer_sic: str | None = None,
) -> MetricResult:
    """net_buyback_yield = (share_repurchases - SBC) / historical market cap."""

    del issuer_sic  # capital returns are meaningful for every issuer type
    metric = "net_buyback_yield"
    formula = "(share_repurchases - stock_based_compensation) / market_cap"
    observations: list[Stage3Observation] = []
    failures: list[FailureDiagnostic] = []
    for target in _ordered_targets(boundaries, max_periods):
        if market_cap is None:
            failures.append(
                _metric_diagnostic(
                    metric=metric,
                    target=target,
                    concept="market_cap",
                    code=FailureCode.MISSING_EXTERNAL_DATA,
                    reason=(
                        "historical market capitalisation is required and was not "
                        "supplied; current market cap is never substituted"
                    ),
                )
            )
            continue
        if market_cap.period_end != target.period_end:
            failures.append(
                _metric_diagnostic(
                    metric=metric,
                    target=target,
                    concept="market_cap",
                    code=FailureCode.INVALID_CONTEXT,
                    reason=(
                        "market cap period "
                        f"{market_cap.period_end} does not match the metric period "
                        f"{target.period_end}"
                    ),
                )
            )
            continue
        if market_cap.value == 0:
            failures.append(
                _metric_diagnostic(
                    metric=metric,
                    target=target,
                    concept="market_cap",
                    code=FailureCode.ZERO_DENOMINATOR,
                    reason="market capitalisation is zero; buyback yield is undefined",
                )
            )
            continue
        repurchases = _resolve_flow(facts, boundaries, target, "share_repurchases")
        sbc = _resolve_flow(facts, boundaries, target, "stock_based_compensation")
        if _first_failure(metric, (repurchases, sbc), failures):
            continue
        assert isinstance(repurchases, ResolvedFact) and isinstance(sbc, ResolvedFact)
        observations.append(
            _observation(
                metric=metric,
                target=target,
                value=(
                    repurchases.fact.value - sbc.fact.value
                ) / market_cap.value,
                formula=formula,
                source_facts=(repurchases.fact, sbc.fact),
            )
        )
    return MetricResult(metric, ticker, tuple(observations), tuple(failures))


def compute_diluted_share_count_yoy(
    *,
    ticker: str,
    facts: tuple[FinancialFact, ...],
    boundaries: tuple[FilingBoundary, ...],
    max_periods: int = 8,
    issuer_sic: str | None = None,
) -> MetricResult:
    """YoY change in diluted weighted-average shares (not period-end shares)."""

    del issuer_sic  # dilution is meaningful for every issuer type
    metric = "diluted_share_count_yoy"
    concept = "diluted_weighted_average_shares"
    formula = (
        "(diluted_weighted_average_shares - "
        "prior_year_diluted_weighted_average_shares) / "
        "prior_year_diluted_weighted_average_shares"
    )
    observations: list[Stage3Observation] = []
    failures: list[FailureDiagnostic] = []
    for target in _ordered_targets(boundaries, max_periods):
        prior_year = _year_ago_boundary(boundaries, target)
        if prior_year is None:
            failures.append(
                _metric_diagnostic(
                    metric=metric,
                    target=target,
                    concept=concept,
                    code=FailureCode.PERIOD_UNAVAILABLE,
                    reason="no comparable year-ago fiscal boundary for diluted shares",
                )
            )
            continue
        current = _resolve_flow(facts, boundaries, target, concept)
        prior = _resolve_flow(facts, boundaries, prior_year, concept)
        if _first_failure(metric, (current, prior), failures):
            continue
        assert isinstance(current, ResolvedFact) and isinstance(prior, ResolvedFact)
        if prior.fact.value == 0:
            failures.append(
                _metric_diagnostic(
                    metric=metric,
                    target=target,
                    concept=concept,
                    code=FailureCode.ZERO_DENOMINATOR,
                    reason="prior-year diluted share count is zero; YoY is undefined",
                    candidates=(prior.fact,),
                )
            )
            continue
        observations.append(
            _observation(
                metric=metric,
                target=target,
                value=(current.fact.value - prior.fact.value) / prior.fact.value,
                formula=formula,
                source_facts=(current.fact, prior.fact),
            )
        )
    return MetricResult(metric, ticker, tuple(observations), tuple(failures))


def compute_interest_coverage(
    *,
    ticker: str,
    facts: tuple[FinancialFact, ...],
    boundaries: tuple[FilingBoundary, ...],
    max_periods: int = 8,
    issuer_sic: str | None = None,
) -> MetricResult:
    """interest_coverage = operating_income / abs(interest_expense)."""

    metric = "interest_coverage"
    formula = "operating_income / abs(interest_expense)"
    inapplicable = _issuer_inapplicable(
        metric=metric, ticker=ticker, boundaries=boundaries, issuer_sic=issuer_sic
    )
    if inapplicable is not None:
        return inapplicable
    observations: list[Stage3Observation] = []
    failures: list[FailureDiagnostic] = []
    for target in _ordered_targets(boundaries, max_periods):
        income = _resolve_flow(facts, boundaries, target, "operating_income")
        interest = _resolve_flow(facts, boundaries, target, "interest_expense")
        if _first_failure(metric, (income, interest), failures):
            continue
        assert isinstance(income, ResolvedFact) and isinstance(interest, ResolvedFact)
        denominator = abs(interest.fact.value)
        if denominator == 0:
            failures.append(
                _metric_diagnostic(
                    metric=metric,
                    target=target,
                    concept="interest_expense",
                    code=FailureCode.ZERO_DENOMINATOR,
                    reason="interest expense is zero; coverage is undefined",
                    candidates=(interest.fact,),
                )
            )
            continue
        observations.append(
            _observation(
                metric=metric,
                target=target,
                value=income.fact.value / denominator,
                formula=formula,
                source_facts=(income.fact, interest.fact),
            )
        )
    return MetricResult(metric, ticker, tuple(observations), tuple(failures))


def compute_net_debt_to_ebitda(
    *,
    ticker: str,
    facts: tuple[FinancialFact, ...],
    boundaries: tuple[FilingBoundary, ...],
    max_periods: int = 8,
    issuer_sic: str | None = None,
) -> MetricResult:
    """net_debt_to_ebitda = (total_debt - cash) / GAAP EBITDA.

    EBITDA is operating income plus depreciation and amortisation.  Separate
    depreciation and amortisation facts are preferred; a combined D&A fact is
    the registered fallback.  Components are never summed into an unproven
    aggregate.
    """

    metric = "net_debt_to_ebitda"
    inapplicable = _issuer_inapplicable(
        metric=metric, ticker=ticker, boundaries=boundaries, issuer_sic=issuer_sic
    )
    if inapplicable is not None:
        return inapplicable
    observations: list[Stage3Observation] = []
    failures: list[FailureDiagnostic] = []
    for target in _ordered_targets(boundaries, max_periods):
        debt = _resolve_instant(facts, target, "total_debt")
        cash = _resolve_instant(facts, target, "cash_and_cash_equivalents")
        income = _resolve_flow(facts, boundaries, target, "operating_income")
        if _first_failure(metric, (debt, cash, income), failures):
            continue
        assert isinstance(debt, ResolvedFact)
        assert isinstance(cash, ResolvedFact)
        assert isinstance(income, ResolvedFact)
        depreciation = _resolve_flow(facts, boundaries, target, "depreciation")
        amortization = _resolve_flow(facts, boundaries, target, "amortization")
        separate = isinstance(depreciation, ResolvedFact) and isinstance(
            amortization, ResolvedFact
        )
        if separate:
            assert isinstance(depreciation, ResolvedFact)
            assert isinstance(amortization, ResolvedFact)
            ebitda = (
                income.fact.value + depreciation.fact.value + amortization.fact.value
            )
            da_facts = (depreciation.fact, amortization.fact)
            formula = (
                "(total_debt - cash_and_cash_equivalents) / "
                "(operating_income + depreciation + amortization)"
            )
        else:
            blocked = tuple(
                item
                for item in (depreciation, amortization)
                if isinstance(item, FailedFact)
                and item.diagnostic.final_failure
                in {FailureCode.AMBIGUOUS, FailureCode.INVALID_CONTEXT}
            )
            if blocked:
                # Ambiguity or an incompatible context must never be masked by
                # a combined-D&A fallback; only genuine absence falls back.
                failures.append(_propagate_failure(metric, blocked[0]))
                continue
            combined = _resolve_flow(
                facts, boundaries, target, "depreciation_and_amortization"
            )
            if isinstance(combined, FailedFact):
                failures.append(_propagate_failure(metric, combined))
                continue
            ebitda = income.fact.value + combined.fact.value
            da_facts = (combined.fact,)
            formula = (
                "(total_debt - cash_and_cash_equivalents) / "
                "(operating_income + depreciation_and_amortization)"
            )
        if ebitda == 0:
            failures.append(
                _metric_diagnostic(
                    metric=metric,
                    target=target,
                    concept="operating_income,depreciation_and_amortization",
                    code=FailureCode.ZERO_DENOMINATOR,
                    reason="GAAP EBITDA is zero; net debt to EBITDA is undefined",
                    candidates=(income.fact, *da_facts),
                )
            )
            continue
        observations.append(
            _observation(
                metric=metric,
                target=target,
                value=(debt.fact.value - cash.fact.value) / ebitda,
                formula=formula,
                source_facts=(debt.fact, cash.fact, income.fact, *da_facts),
            )
        )
    return MetricResult(metric, ticker, tuple(observations), tuple(failures))
