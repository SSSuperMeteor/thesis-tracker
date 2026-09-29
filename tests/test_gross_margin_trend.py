from __future__ import annotations

from datetime import date
from decimal import Decimal

from thesis_tracker.financial.models import (
    FactOrigin,
    FailureCode,
    FilingBoundary,
    FinancialFact,
    Unit,
)
from thesis_tracker.metrics.financial import compute_gross_margin_trend


def test_gross_margin_uses_python_decimal_and_carries_provenance() -> None:
    prior_q4 = FilingBoundary(
        ticker="TEST",
        accession="prior-fy",
        filed_at=date(2026, 2, 1),
        form="10-K",
        fiscal_year=2025,
        fiscal_period="Q4",
        period_end=date(2025, 12, 31),
        source="sec_filing_metadata",
    )
    q1 = FilingBoundary(
        ticker="TEST",
        accession="q1",
        filed_at=date(2026, 5, 1),
        form="10-Q",
        fiscal_year=2026,
        fiscal_period="Q1",
        period_end=date(2026, 3, 31),
        source="sec_filing_metadata",
    )
    revenue = FinancialFact(
        ticker="TEST",
        accession="q1",
        concept="us-gaap:Revenues",
        value=Decimal("200"),
        unit=Unit.USD,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 3, 31),
        filed_at=date(2026, 5, 1),
        form="10-Q",
        fiscal_year=2026,
        fiscal_period="Q1",
        source="sec_filing_xbrl",
        extraction_path="filing.xbrl().facts",
        context_id="revenue-context",
        dimensions=(),
        origin=FactOrigin.REPORTED,
        resolver_path=("filing_xbrl",),
    )
    cost = FinancialFact(
        ticker="TEST",
        accession="q1",
        concept="us-gaap:CostOfRevenue",
        value=Decimal("80"),
        unit=Unit.USD,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 3, 31),
        filed_at=date(2026, 5, 1),
        form="10-Q",
        fiscal_year=2026,
        fiscal_period="Q1",
        source="sec_filing_xbrl",
        extraction_path="filing.xbrl().facts",
        context_id="cost-context",
        dimensions=(),
        origin=FactOrigin.REPORTED,
        resolver_path=("filing_xbrl",),
    )

    result = compute_gross_margin_trend(
        ticker="TEST",
        facts=(revenue, cost),
        boundaries=(prior_q4, q1),
        max_periods=1,
    )

    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.value == Decimal("0.6")
    assert observation.period_end == date(2026, 3, 31)
    assert observation.provenance.formula == "(revenue - cost_of_revenue) / revenue"
    assert observation.provenance.source_fact_ids == (revenue.fact_id, cost.fact_id)
    assert observation.to_dict()["provenance"]["source_facts"][0]["accession"] == "q1"
    assert result.failures == ()


def test_metric_rejects_revenue_and_cost_with_different_source_accessions() -> None:
    prior_q4 = FilingBoundary(
        ticker="TEST",
        accession="prior-fy",
        filed_at=date(2026, 2, 1),
        form="10-K",
        fiscal_year=2025,
        fiscal_period="Q4",
        period_end=date(2025, 12, 31),
        source="sec_filing_metadata",
    )
    q1 = FilingBoundary(
        ticker="TEST",
        accession="q1",
        filed_at=date(2026, 5, 1),
        form="10-Q",
        fiscal_year=2026,
        fiscal_period="Q1",
        period_end=date(2026, 3, 31),
        source="sec_filing_metadata",
    )

    def make(concept: str, accession: str, value: str, context: str) -> FinancialFact:
        return FinancialFact(
            ticker="TEST",
            accession=accession,
            concept=concept,
            value=Decimal(value),
            unit=Unit.USD,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 3, 31),
            filed_at=date(2026, 5, 1),
            form="10-Q",
            fiscal_year=2026,
            fiscal_period="Q1",
            source="sec_filing_xbrl",
            extraction_path="filing.xbrl().facts",
            context_id=context,
            dimensions=(),
            origin=FactOrigin.REPORTED,
            resolver_path=("filing_xbrl",),
        )

    result = compute_gross_margin_trend(
        ticker="TEST",
        facts=(
            make("us-gaap:Revenues", "q1", "200", "revenue"),
            make("us-gaap:CostOfRevenue", "other", "80", "cost"),
        ),
        boundaries=(prior_q4, q1),
        max_periods=1,
    )

    assert result.observations == ()
    assert len(result.failures) == 1
    assert result.failures[0].final_failure.value == "invalid_context"


def test_financial_industry_is_not_applicable_before_fact_resolution() -> None:
    latest = FilingBoundary(
        ticker="BANK",
        accession="bank-q1",
        filed_at=date(2026, 5, 1),
        form="10-Q",
        fiscal_year=2026,
        fiscal_period="Q1",
        period_end=date(2026, 3, 31),
        source="sec_filing_metadata",
    )

    result = compute_gross_margin_trend(
        ticker="BANK",
        facts=(),
        boundaries=(latest,),
        max_periods=8,
        issuer_sic="6021",
    )

    assert result.observations == ()
    assert len(result.failures) == 1
    diagnostic = result.failures[0]
    assert diagnostic.final_failure is FailureCode.NOT_APPLICABLE
    assert diagnostic.target_period == "2026-Q1"
    assert diagnostic.candidate_summary == (
        {"issuer_sic": "6021", "sic_major_group": "60"},
    )


def test_lite_standard_gross_profit_path_uses_same_context_revenue() -> None:
    prior = FilingBoundary(
        ticker="LITE",
        accession="0001628280-25-040830",
        filed_at=date(2025, 8, 22),
        form="10-K",
        fiscal_year=2025,
        fiscal_period="Q4",
        period_end=date(2025, 6, 28),
        source="sec_filing_metadata",
    )
    target = FilingBoundary(
        ticker="LITE",
        accession="0001628280-25-049073",
        filed_at=date(2025, 11, 6),
        form="10-Q",
        fiscal_year=2026,
        fiscal_period="Q1",
        period_end=date(2025, 9, 27),
        source="sec_filing_metadata",
    )

    def lite_fact(concept: str, value: str) -> FinancialFact:
        return FinancialFact(
            ticker="LITE",
            accession=target.accession,
            concept=concept,
            value=Decimal(value),
            unit=Unit.USD,
            period_start=date(2025, 6, 29),
            period_end=target.period_end,
            filed_at=target.filed_at,
            form=target.form,
            fiscal_year=target.fiscal_year,
            fiscal_period=target.fiscal_period,
            source="sec_filing_xbrl",
            extraction_path="filing.xbrl().facts",
            context_id="c-1",
            dimensions=(),
            origin=FactOrigin.REPORTED,
            resolver_path=("filing_xbrl",),
        )

    revenue = lite_fact(
        "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
        "533800000",
    )
    gross_profit = lite_fact("us-gaap:GrossProfit", "181500000")

    result = compute_gross_margin_trend(
        ticker="LITE",
        facts=(revenue, gross_profit),
        boundaries=(prior, target),
        max_periods=1,
        issuer_sic="3669",
    )

    assert result.failures == ()
    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.value == Decimal("0.3400149868864743349569127014")
    assert observation.provenance.formula == "gross_profit / revenue"
    assert observation.provenance.source_fact_ids == (
        gross_profit.fact_id,
        revenue.fact_id,
    )


def test_failed_gross_profit_recovery_is_serialized_in_diagnostic_history() -> None:
    q3 = FilingBoundary(
        ticker="TEST",
        accession="q3",
        filed_at=date(2025, 11, 1),
        form="10-Q",
        fiscal_year=2025,
        fiscal_period="Q3",
        period_end=date(2025, 9, 30),
        source="sec_filing_metadata",
    )
    q4 = FilingBoundary(
        ticker="TEST",
        accession="fy",
        filed_at=date(2026, 2, 1),
        form="10-K",
        fiscal_year=2025,
        fiscal_period="Q4",
        period_end=date(2025, 12, 31),
        source="sec_filing_metadata",
    )

    def annual_fact(
        boundary: FilingBoundary,
        concept: str,
        value: str,
        context_id: str,
    ) -> FinancialFact:
        return FinancialFact(
            ticker="TEST",
            accession=boundary.accession,
            concept=concept,
            value=Decimal(value),
            unit=Unit.USD,
            period_start=date(2025, 1, 1),
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

    excluding = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
    including = "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax"
    facts = (
        annual_fact(q3, excluding, "80", "q3-revenue"),
        annual_fact(q4, excluding, "100", "fy-revenue"),
        annual_fact(q4, including, "100", "fy-revenue"),
        annual_fact(q3, "us-gaap:GrossProfit", "30", "q3-gross-profit"),
        annual_fact(q4, "us-gaap:GrossProfit", "40", "fy-gross-profit"),
    )

    result = compute_gross_margin_trend(
        ticker="TEST",
        facts=facts,
        boundaries=(q3, q4),
        max_periods=1,
    )

    assert result.observations == ()
    assert len(result.failures) == 1
    assert result.failures[0].final_failure is FailureCode.REGISTRY_GAP
    assert result.failures[0].recovery_history == (
        FailureCode.DURATION_UNAVAILABLE,
    )
