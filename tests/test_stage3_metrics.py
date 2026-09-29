"""Stage 3 metric migration tests: 7 metrics on the current architecture.

Every metric is exercised through the current resolver, registry, provenance
model, and failure taxonomy.  No Company Facts primary source is involved.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from thesis_tracker.financial.models import (
    FactKind,
    FactOrigin,
    FailureCode,
    FilingBoundary,
    FinancialFact,
    Unit,
)
from thesis_tracker.financial.resolver import (
    resolve_instant_fact,
    resolve_quarterly_fact,
)
from thesis_tracker.financial.sec_source import extract_filing_facts
from thesis_tracker.metrics.financial import (
    MarketCapInput,
    compute_accruals_ratio,
    compute_ar_growth_vs_rev_growth,
    compute_cash_conversion,
    compute_diluted_share_count_yoy,
    compute_interest_coverage,
    compute_net_buyback_yield,
    compute_net_debt_to_ebitda,
)


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
        filed_at=period_end,
        form=form,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        period_end=period_end,
        source="sec_filing_metadata",
    )


def _fact(
    boundary: FilingBoundary,
    concept: str,
    value: str,
    *,
    kind: FactKind,
    period_start: date | None,
    unit: Unit = Unit.USD,
) -> FinancialFact:
    return FinancialFact(
        ticker=boundary.ticker,
        accession=boundary.accession,
        concept=concept,
        value=Decimal(value),
        unit=unit,
        period_start=period_start,
        period_end=boundary.period_end,
        filed_at=boundary.filed_at,
        form=boundary.form,
        fiscal_year=boundary.fiscal_year,
        fiscal_period=boundary.fiscal_period,
        source="sec_filing_xbrl",
        extraction_path="filing.xbrl().facts.get_facts()",
        context_id=f"{boundary.accession}|{concept}",
        dimensions=(),
        origin=FactOrigin.REPORTED,
        resolver_path=("stage1_boundary", "filing_xbrl"),
        fact_kind=kind,
    )


def flow(
    boundary: FilingBoundary,
    concept: str,
    value: str,
    *,
    start: date,
    unit: Unit = Unit.USD,
) -> FinancialFact:
    return _fact(
        boundary,
        concept,
        value,
        kind=FactKind.DURATION,
        period_start=start,
        unit=unit,
    )


def instant(
    boundary: FilingBoundary,
    concept: str,
    value: str,
    *,
    unit: Unit = Unit.USD,
) -> FinancialFact:
    return _fact(
        boundary,
        concept,
        value,
        kind=FactKind.INSTANT,
        period_start=None,
        unit=unit,
    )


# 2026-Q2 target with its prior quarter and the year-ago pair.
Q1_2025 = make_boundary("acc-2025q1", 2025, "Q1", date(2025, 3, 31))
Q2_2025 = make_boundary("acc-2025q2", 2025, "Q2", date(2025, 6, 30))
Q1_2026 = make_boundary("acc-2026q1", 2026, "Q1", date(2026, 3, 31))
Q2_2026 = make_boundary("acc-2026q2", 2026, "Q2", date(2026, 6, 30))
BOUNDARIES = (Q1_2025, Q2_2025, Q1_2026, Q2_2026)


def quarter_facts() -> tuple[FinancialFact, ...]:
    """One comparable quarter pair plus balance-sheet instants."""
    return (
        flow(Q1_2026, "us-gaap:NetIncomeLoss", "90", start=date(2026, 1, 1)),
        flow(Q2_2026, "us-gaap:NetIncomeLoss", "100", start=date(2026, 4, 1)),
        flow(
            Q2_2026,
            "us-gaap:NetCashProvidedByUsedInOperatingActivities",
            "150",
            start=date(2026, 4, 1),
        ),
        instant(Q1_2026, "us-gaap:Assets", "1000"),
        instant(Q2_2026, "us-gaap:Assets", "1200"),
        instant(Q2_2025, "us-gaap:AccountsReceivableNetCurrent", "250"),
        instant(Q2_2026, "us-gaap:AccountsReceivableNetCurrent", "300"),
        flow(Q2_2025, "us-gaap:Revenues", "800", start=date(2025, 4, 1)),
        flow(Q2_2026, "us-gaap:Revenues", "1000", start=date(2026, 4, 1)),
        flow(
            Q2_2026,
            "us-gaap:OperatingIncomeLoss",
            "200",
            start=date(2026, 4, 1),
        ),
        flow(
            Q2_2026,
            "us-gaap:InterestExpenseNonoperating",
            "-40",
            start=date(2026, 4, 1),
        ),
        instant(Q2_2026, "us-gaap:DebtLongtermAndShorttermCombinedAmount", "800"),
        instant(Q2_2026, "us-gaap:CashAndCashEquivalentsAtCarryingValue", "300"),
        flow(
            Q2_2026,
            "us-gaap:DepreciationDepletionAndAmortization",
            "50",
            start=date(2026, 4, 1),
        ),
        flow(
            Q2_2026,
            "us-gaap:PaymentsForRepurchaseOfCommonStock",
            "120",
            start=date(2026, 4, 1),
        ),
        flow(
            Q2_2026,
            "us-gaap:ShareBasedCompensation",
            "20",
            start=date(2026, 4, 1),
        ),
        flow(
            Q1_2026,
            "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
            "500",
            start=date(2026, 1, 1),
            unit=Unit.SHARES,
        ),
        flow(
            Q2_2026,
            "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
            "550",
            start=date(2026, 4, 1),
            unit=Unit.SHARES,
        ),
        flow(
            Q1_2025,
            "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
            "450",
            start=date(2025, 1, 1),
            unit=Unit.SHARES,
        ),
        flow(
            Q2_2025,
            "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
            "500",
            start=date(2025, 4, 1),
            unit=Unit.SHARES,
        ),
        flow(
            Q1_2025,
            "us-gaap:Revenues",
            "700",
            start=date(2025, 1, 1),
        ),
        flow(
            Q1_2026,
            "us-gaap:Revenues",
            "900",
            start=date(2026, 1, 1),
        ),
    )


def result_for(metric_result):
    assert len(metric_result.observations) == 1
    return metric_result.observations[0]


# ----------------------------------------------------------------
# resolve_instant_fact
# ----------------------------------------------------------------


def test_instant_resolver_requires_the_target_reporting_boundary() -> None:
    resolved = resolve_instant_fact(
        facts=quarter_facts(),
        target=Q2_2026,
        canonical_concept="total_assets",
    )
    assert resolved.fact.value == Decimal("1200")
    assert resolved.fact.accession == Q2_2026.accession

    missing = resolve_instant_fact(
        facts=quarter_facts(),
        target=make_boundary("acc-empty", 2026, "Q3", date(2026, 9, 30)),
        canonical_concept="total_assets",
    )
    assert missing.diagnostic.final_failure is FailureCode.PERIOD_UNAVAILABLE


# ----------------------------------------------------------------
# 1. cash_conversion
# ----------------------------------------------------------------


def test_cash_conversion_uses_quarter_ocf_over_net_income() -> None:
    result = compute_cash_conversion(
        ticker="TEST", facts=quarter_facts(), boundaries=BOUNDARIES, max_periods=1
    )
    observation = result_for(result)
    assert observation.value == Decimal("1.5")
    assert observation.formula == "operating_cash_flow / net_income"
    assert observation.fiscal_period == "Q2"
    assert observation.accession == Q2_2026.accession
    assert {fact.concept for fact in observation.provenance.source_facts} == {
        "us-gaap:NetCashProvidedByUsedInOperatingActivities",
        "us-gaap:NetIncomeLoss",
    }


def test_cash_conversion_keeps_negative_net_income_sign() -> None:
    facts = list(quarter_facts())
    facts = [
        fact
        if fact.concept != "us-gaap:NetIncomeLoss" or fact.accession != "acc-2026q2"
        else _fact(
            Q2_2026,
            "us-gaap:NetIncomeLoss",
            "-50",
            kind=FactKind.DURATION,
            period_start=date(2026, 4, 1),
        )
        for fact in facts
    ]
    result = compute_cash_conversion(
        ticker="TEST", facts=tuple(facts), boundaries=BOUNDARIES, max_periods=1
    )
    assert result_for(result).value == Decimal("-3")


def test_cash_conversion_fails_closed_on_zero_net_income() -> None:
    facts = [
        fact
        if fact.concept != "us-gaap:NetIncomeLoss" or fact.accession != "acc-2026q2"
        else _fact(
            Q2_2026,
            "us-gaap:NetIncomeLoss",
            "0",
            kind=FactKind.DURATION,
            period_start=date(2026, 4, 1),
        )
        for fact in quarter_facts()
    ]
    result = compute_cash_conversion(
        ticker="TEST", facts=tuple(facts), boundaries=BOUNDARIES, max_periods=1
    )
    assert result.observations == ()
    assert result.failures[0].final_failure is FailureCode.ZERO_DENOMINATOR


def test_cash_conversion_reports_missing_ocf() -> None:
    facts = tuple(
        fact
        for fact in quarter_facts()
        if fact.concept != "us-gaap:NetCashProvidedByUsedInOperatingActivities"
    )
    result = compute_cash_conversion(
        ticker="TEST", facts=facts, boundaries=BOUNDARIES, max_periods=1
    )
    assert result.observations == ()
    assert result.failures[0].final_failure is FailureCode.REGISTRY_GAP


# ----------------------------------------------------------------
# 2. accruals_ratio
# ----------------------------------------------------------------


def test_accruals_ratio_and_average_assets() -> None:
    result = compute_accruals_ratio(
        ticker="TEST", facts=quarter_facts(), boundaries=BOUNDARIES, max_periods=1
    )
    observation = result_for(result)
    # (100 - 150) / ((1000 + 1200) / 2) = -50 / 1100
    assert observation.value == Decimal("-50") / Decimal("1100")
    assert observation.formula == (
        "(net_income - operating_cash_flow) / "
        "((beginning_total_assets + ending_total_assets) / 2)"
    )
    assert {fact.concept for fact in observation.provenance.source_facts} == {
        "us-gaap:NetIncomeLoss",
        "us-gaap:NetCashProvidedByUsedInOperatingActivities",
        "us-gaap:Assets",
    }
    assert len(observation.provenance.source_facts) == 4


def test_accruals_ratio_never_substitutes_ending_assets_for_average() -> None:
    facts = tuple(
        fact
        for fact in quarter_facts()
        if not (fact.concept == "us-gaap:Assets" and fact.accession == "acc-2026q1")
    )
    result = compute_accruals_ratio(
        ticker="TEST", facts=facts, boundaries=BOUNDARIES, max_periods=1
    )
    assert result.observations == ()
    assert result.failures[0].final_failure in {
        FailureCode.PERIOD_UNAVAILABLE,
        FailureCode.INVALID_CONTEXT,
    }


def test_accruals_ratio_fails_closed_on_zero_average_assets() -> None:
    facts = [
        _fact(
            Q2_2026,
            "us-gaap:Assets",
            "0",
            kind=FactKind.INSTANT,
            period_start=None,
        )
        if fact.concept == "us-gaap:Assets" and fact.accession == "acc-2026q2"
        else (
            _fact(
                Q1_2026,
                "us-gaap:Assets",
                "0",
                kind=FactKind.INSTANT,
                period_start=None,
            )
            if fact.concept == "us-gaap:Assets" and fact.accession == "acc-2026q1"
            else fact
        )
        for fact in quarter_facts()
    ]
    result = compute_accruals_ratio(
        ticker="TEST", facts=tuple(facts), boundaries=BOUNDARIES, max_periods=1
    )
    assert result.observations == ()
    assert result.failures[0].final_failure is FailureCode.ZERO_DENOMINATOR


# ----------------------------------------------------------------
# 3. ar_growth_vs_rev_growth
# ----------------------------------------------------------------


def test_ar_growth_minus_revenue_growth() -> None:
    result = compute_ar_growth_vs_rev_growth(
        ticker="TEST", facts=quarter_facts(), boundaries=BOUNDARIES, max_periods=1
    )
    observation = result_for(result)
    # AR 250 -> 300 = +20%; revenue 800 -> 1000 = +25%
    assert observation.value == Decimal("0.20") - Decimal("0.25")
    assert observation.formula == "accounts_receivable_yoy - revenue_yoy"
    assert {fact.concept for fact in observation.provenance.source_facts} == {
        "us-gaap:AccountsReceivableNetCurrent",
        "us-gaap:Revenues",
    }


def test_ar_growth_fails_closed_when_prior_ar_is_zero() -> None:
    facts = [
        _fact(
            Q2_2025,
            "us-gaap:AccountsReceivableNetCurrent",
            "0",
            kind=FactKind.INSTANT,
            period_start=None,
        )
        if fact.concept == "us-gaap:AccountsReceivableNetCurrent"
        and fact.accession == "acc-2025q2"
        else fact
        for fact in quarter_facts()
    ]
    result = compute_ar_growth_vs_rev_growth(
        ticker="TEST", facts=tuple(facts), boundaries=BOUNDARIES, max_periods=1
    )
    assert result.observations == ()
    assert result.failures[0].final_failure is FailureCode.ZERO_DENOMINATOR


def test_ar_growth_requires_a_comparable_year_ago_boundary() -> None:
    boundaries = tuple(
        item for item in BOUNDARIES if item.accession != "acc-2025q2"
    )
    result = compute_ar_growth_vs_rev_growth(
        ticker="TEST", facts=quarter_facts(), boundaries=boundaries, max_periods=1
    )
    assert result.observations == ()
    assert result.failures[0].final_failure is FailureCode.PERIOD_UNAVAILABLE


# ----------------------------------------------------------------
# 4. net_buyback_yield
# ----------------------------------------------------------------


def market_cap(value: str = "10000") -> MarketCapInput:
    return MarketCapInput(
        value=Decimal(value),
        period_end=Q2_2026.period_end,
        source="fixture",
    )


def test_net_buyback_yield_requires_explicit_market_cap() -> None:
    result = compute_net_buyback_yield(
        ticker="TEST",
        facts=quarter_facts(),
        boundaries=BOUNDARIES,
        max_periods=1,
        market_cap=None,
    )
    assert result.observations == ()
    assert result.failures[0].final_failure is FailureCode.MISSING_EXTERNAL_DATA


def test_net_buyback_yield_is_repurchases_minus_sbc_over_market_cap() -> None:
    result = compute_net_buyback_yield(
        ticker="TEST",
        facts=quarter_facts(),
        boundaries=BOUNDARIES,
        max_periods=1,
        market_cap=market_cap(),
    )
    observation = result_for(result)
    assert observation.value == (Decimal("120") - Decimal("20")) / Decimal("10000")
    assert observation.formula == "(share_repurchases - stock_based_compensation) / market_cap"
    assert observation.provenance.source_facts


def test_net_buyback_yield_rejects_a_mismatched_market_cap_period() -> None:
    result = compute_net_buyback_yield(
        ticker="TEST",
        facts=quarter_facts(),
        boundaries=BOUNDARIES,
        max_periods=1,
        market_cap=MarketCapInput(
            value=Decimal("10000"),
            period_end=date(2025, 12, 31),
            source="fixture",
        ),
    )
    assert result.observations == ()
    assert result.failures[0].final_failure is FailureCode.INVALID_CONTEXT


# ----------------------------------------------------------------
# 5. diluted_share_count_yoy
# ----------------------------------------------------------------


def test_diluted_share_count_yoy() -> None:
    result = compute_diluted_share_count_yoy(
        ticker="TEST", facts=quarter_facts(), boundaries=BOUNDARIES, max_periods=1
    )
    observation = result_for(result)
    # diluted weighted-average shares 500 -> 550
    assert observation.value == (Decimal("550") - Decimal("500")) / Decimal("500")
    assert observation.formula == (
        "(diluted_weighted_average_shares - "
        "prior_year_diluted_weighted_average_shares) / "
        "prior_year_diluted_weighted_average_shares"
    )
    assert {fact.concept for fact in observation.provenance.source_facts} == {
        "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding"
    }


def test_diluted_share_count_yoy_fails_closed_on_zero_prior_shares() -> None:
    facts = [
        flow(
            Q2_2025,
            "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
            "0",
            start=date(2025, 4, 1),
            unit=Unit.SHARES,
        )
        if fact.concept == "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding"
        and fact.accession == "acc-2025q2"
        else fact
        for fact in quarter_facts()
    ]
    result = compute_diluted_share_count_yoy(
        ticker="TEST", facts=tuple(facts), boundaries=BOUNDARIES, max_periods=1
    )
    assert result.observations == ()
    assert result.failures[0].final_failure is FailureCode.ZERO_DENOMINATOR


# ----------------------------------------------------------------
# 6. interest_coverage
# ----------------------------------------------------------------


def test_interest_coverage_normalizes_interest_sign() -> None:
    result = compute_interest_coverage(
        ticker="TEST", facts=quarter_facts(), boundaries=BOUNDARIES, max_periods=1
    )
    observation = result_for(result)
    # operating income 200 / abs(-40) = 5
    assert observation.value == Decimal("5")
    assert observation.formula == "operating_income / abs(interest_expense)"

    positive = [
        _fact(
            Q2_2026,
            "us-gaap:InterestExpenseNonoperating",
            "40",
            kind=FactKind.DURATION,
            period_start=date(2026, 4, 1),
        )
        if fact.concept == "us-gaap:InterestExpenseNonoperating"
        else fact
        for fact in quarter_facts()
    ]
    again = compute_interest_coverage(
        ticker="TEST", facts=tuple(positive), boundaries=BOUNDARIES, max_periods=1
    )
    assert result_for(again).value == Decimal("5")


def test_interest_coverage_fails_closed_on_zero_interest() -> None:
    facts = [
        _fact(
            Q2_2026,
            "us-gaap:InterestExpenseNonoperating",
            "0",
            kind=FactKind.DURATION,
            period_start=date(2026, 4, 1),
        )
        if fact.concept == "us-gaap:InterestExpenseNonoperating"
        else fact
        for fact in quarter_facts()
    ]
    result = compute_interest_coverage(
        ticker="TEST", facts=tuple(facts), boundaries=BOUNDARIES, max_periods=1
    )
    assert result.observations == ()
    assert result.failures[0].final_failure is FailureCode.ZERO_DENOMINATOR


# ----------------------------------------------------------------
# 7. net_debt_to_ebitda
# ----------------------------------------------------------------


def test_net_debt_to_ebitda_uses_gaap_ebitda_with_combined_da() -> None:
    result = compute_net_debt_to_ebitda(
        ticker="TEST", facts=quarter_facts(), boundaries=BOUNDARIES, max_periods=1
    )
    observation = result_for(result)
    # net debt = 800 - 300 = 500; EBITDA = 200 + 50 = 250
    assert observation.value == Decimal("500") / Decimal("250")
    assert observation.formula == (
        "(total_debt - cash_and_cash_equivalents) / "
        "(operating_income + depreciation_and_amortization)"
    )
    assert {fact.concept for fact in observation.provenance.source_facts} == {
        "us-gaap:DebtLongtermAndShorttermCombinedAmount",
        "us-gaap:CashAndCashEquivalentsAtCarryingValue",
        "us-gaap:OperatingIncomeLoss",
        "us-gaap:DepreciationDepletionAndAmortization",
    }


def test_net_debt_to_ebitda_falls_back_to_separate_depreciation_and_amortization() -> None:
    facts = tuple(
        fact
        for fact in quarter_facts()
        if fact.concept != "us-gaap:DepreciationDepletionAndAmortization"
    ) + (
        flow(Q2_2026, "us-gaap:Depreciation", "30", start=date(2026, 4, 1)),
        flow(
            Q2_2026,
            "us-gaap:AmortizationOfIntangibleAssets",
            "20",
            start=date(2026, 4, 1),
        ),
    )
    result = compute_net_debt_to_ebitda(
        ticker="TEST", facts=facts, boundaries=BOUNDARIES, max_periods=1
    )
    observation = result_for(result)
    assert observation.value == Decimal("500") / Decimal("250")
    assert "depreciation" in observation.formula


def test_net_debt_to_ebitda_fails_closed_on_zero_ebitda() -> None:
    facts = [
        _fact(
            Q2_2026,
            "us-gaap:OperatingIncomeLoss",
            "-50",
            kind=FactKind.DURATION,
            period_start=date(2026, 4, 1),
        )
        if fact.concept == "us-gaap:OperatingIncomeLoss"
        else fact
        for fact in quarter_facts()
    ]
    result = compute_net_debt_to_ebitda(
        ticker="TEST", facts=tuple(facts), boundaries=BOUNDARIES, max_periods=1
    )
    assert result.observations == ()
    assert result.failures[0].final_failure is FailureCode.ZERO_DENOMINATOR


def test_net_debt_to_ebitda_does_not_invent_a_missing_da_aggregate() -> None:
    facts = tuple(
        fact
        for fact in quarter_facts()
        if fact.concept
        not in {
            "us-gaap:DepreciationDepletionAndAmortization",
            "us-gaap:Depreciation",
            "us-gaap:AmortizationOfIntangibleAssets",
        }
    )
    result = compute_net_debt_to_ebitda(
        ticker="TEST", facts=facts, boundaries=BOUNDARIES, max_periods=1
    )
    assert result.observations == ()
    assert result.failures[0].final_failure in {
        FailureCode.PERIOD_UNAVAILABLE,
        FailureCode.REGISTRY_GAP,
    }


@pytest.mark.parametrize(
    "metric",
    [
        compute_cash_conversion,
        compute_accruals_ratio,
        compute_ar_growth_vs_rev_growth,
        compute_diluted_share_count_yoy,
        compute_interest_coverage,
        compute_net_debt_to_ebitda,
    ],
)
def test_every_metric_serializes_provenance(metric) -> None:
    result = metric(
        ticker="TEST", facts=quarter_facts(), boundaries=BOUNDARIES, max_periods=1
    )
    payload = result.to_dict()
    assert payload["metric"]
    observation = payload["observations"][0]
    assert observation["period_end"] == "2026-06-30"
    assert observation["fiscal_year"] == 2026
    assert observation["fiscal_period"] == "Q2"
    assert observation["value"]
    assert observation["formula"]
    for fact in observation["provenance"]["source_facts"]:
        assert fact["concept"]
        assert fact["accession"]
        assert fact["origin"] == "reported"
        assert fact["resolver_path"]
    assert observation["provenance"]["source_fact_ids"]


@pytest.mark.parametrize(
    ("metric", "function"),
    [
        ("ar_growth_vs_rev_growth", compute_ar_growth_vs_rev_growth),
        ("interest_coverage", compute_interest_coverage),
        ("net_debt_to_ebitda", compute_net_debt_to_ebitda),
    ],
)
def test_financial_issuer_metrics_are_not_applicable(metric, function) -> None:
    result = function(
        ticker="TEST",
        facts=quarter_facts(),
        boundaries=BOUNDARIES,
        max_periods=1,
        issuer_sic="6021",
    )
    assert result.observations == ()
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.final_failure is FailureCode.NOT_APPLICABLE
    assert failure.source == "sec_company_metadata"
    assert failure.concept == metric


# ----------------------------------------------------------------
# Recovered historical semantics
# ----------------------------------------------------------------


def test_diluted_shares_q4_is_never_derived() -> None:
    q3 = make_boundary("acc-2026q3", 2026, "Q3", date(2026, 9, 30))
    q4 = make_boundary("acc-2026q4", 2026, "Q4", date(2026, 12, 31), form="10-K")
    facts = (
        flow(
            q4,
            "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
            "600",
            start=date(2026, 1, 1),
            unit=Unit.SHARES,
        ),
    )
    result = resolve_quarterly_fact(
        facts=facts,
        boundaries=(q3, q4),
        target=q4,
        canonical_concept="diluted_weighted_average_shares",
    )
    assert hasattr(result, "diagnostic")
    assert result.diagnostic.final_failure is FailureCode.DURATION_UNAVAILABLE
    assert "not additive" in result.diagnostic.rejection_reason


def test_net_debt_does_not_mask_ambiguous_da_with_a_combined_fallback() -> None:
    facts = list(quarter_facts())
    facts.append(
        flow(Q2_2026, "us-gaap:Depreciation", "10", start=date(2026, 4, 1))
    )
    facts.append(
        flow(Q2_2026, "us-gaap:Depreciation", "12", start=date(2026, 4, 1))
    )
    result = compute_net_debt_to_ebitda(
        ticker="TEST", facts=tuple(facts), boundaries=BOUNDARIES, max_periods=1
    )
    assert result.observations == ()
    assert result.failures[0].final_failure is FailureCode.AMBIGUOUS


class _FakeContext:
    dimensions: dict = {}


class _FakeContexts:
    def get(self, _context_id: str) -> _FakeContext:
        return _FakeContext()


class _FakeFacts:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def get_facts(self) -> list[dict]:
        return self._rows


class _FakeXbrl:
    def __init__(self, rows: list[dict]) -> None:
        self.facts = _FakeFacts(rows)
        self.contexts = _FakeContexts()


def test_conflicting_xbrl_precision_rows_survive_to_the_resolver() -> None:
    rows = [
        {
            "concept": "us-gaap:Depreciation",
            "period_type": "duration",
            "period_start": "2026-04-01",
            "period_end": "2026-06-30",
            "value": value,
            "unit_ref": "usd",
            "currency": "USD",
            "context_ref": "c-1",
            "fiscal_year": 2026,
            "fiscal_period": "Q2",
        }
        for value in ("100", "101")
    ]
    facts = extract_filing_facts(Q2_2026, _FakeXbrl(rows))
    assert len(facts) == 2
    assert {str(fact.value) for fact in facts} == {"100", "101"}

    result = resolve_quarterly_fact(
        facts=facts,
        boundaries=BOUNDARIES,
        target=Q2_2026,
        canonical_concept="depreciation",
    )
    assert hasattr(result, "diagnostic")
    assert result.diagnostic.final_failure is FailureCode.AMBIGUOUS
