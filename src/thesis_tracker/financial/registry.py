"""Auditable canonical concept mappings for Stage 3 metrics.

Only standard US-GAAP (and the DEI share-count concept) tags are registered.
A canonical concept maps to its accepted aliases in precedence order.
"""

from __future__ import annotations

CONCEPT_REGISTRY: dict[str, tuple[str, ...]] = {
    # --- Income statement flows -------------------------------------
    "revenue": (
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap:Revenues",
        "us-gaap:SalesRevenueNet",
    ),
    "cost_of_revenue": (
        "us-gaap:CostOfRevenue",
        "us-gaap:CostOfGoodsAndServicesSold",
    ),
    "gross_profit": ("us-gaap:GrossProfit",),
    "gross_profit_revenue": (
        "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap:Revenues",
        "us-gaap:SalesRevenueNet",
    ),
    "net_income": (
        "us-gaap:NetIncomeLoss",
        "us-gaap:ProfitLoss",
    ),
    "operating_income": ("us-gaap:OperatingIncomeLoss",),
    "interest_expense": (
        "us-gaap:InterestExpenseNonoperating",
        "us-gaap:InterestExpense",
        "us-gaap:InterestExpenseDebt",
    ),
    "stock_based_compensation": ("us-gaap:ShareBasedCompensation",),
    "share_repurchases": (
        "us-gaap:PaymentsForRepurchaseOfCommonStock",
    ),
    "depreciation": ("us-gaap:Depreciation",),
    "amortization": (
        "us-gaap:AmortizationOfIntangibleAssets",
        "us-gaap:AmortizationOfAcquiredIntangibleAssets",
    ),
    "depreciation_and_amortization": (
        "us-gaap:DepreciationDepletionAndAmortization",
        "us-gaap:DepreciationAndAmortization",
    ),
    # --- Cash flow --------------------------------------------------
    "operating_cash_flow": (
        "us-gaap:NetCashProvidedByUsedInOperatingActivities",
        "us-gaap:NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    # --- Balance sheet instants -------------------------------------
    "total_assets": ("us-gaap:Assets",),
    "accounts_receivable": (
        "us-gaap:AccountsReceivableNetCurrent",
        "us-gaap:AccountsReceivableNet",
    ),
    "total_debt": (
        "us-gaap:DebtLongtermAndShorttermCombinedAmount",
        "us-gaap:LongTermDebtAndFinanceLeaseObligations",
        "us-gaap:LongTermDebtAndCapitalLeaseObligations",
        "us-gaap:LongTermDebt",
    ),
    "cash_and_cash_equivalents": (
        "us-gaap:CashAndCashEquivalentsAtCarryingValue",
    ),
    # --- Share counts -----------------------------------------------
    "diluted_weighted_average_shares": (
        "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
    ),
    "shares_outstanding": ("dei:EntityCommonStockSharesOutstanding",),
}

# Concepts whose single-quarter value must never be derived from YTD/annual
# differences.  Diluted weighted-average share counts are period averages, not
# additive flows, so Q4/YTD differencing is not a proven precondition.
NON_DERIVABLE_CONCEPTS: frozenset[str] = frozenset(
    {"diluted_weighted_average_shares"}
)
