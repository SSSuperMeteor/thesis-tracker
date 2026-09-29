"""Real-data Stage 3 8-metric stress + gross_margin baseline invariance A/B.

Not part of the test suite: run with the real SEC corpus to validate the
migration.  Run from the repo root; writes eval/stage3_stress_8metric.json.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from thesis_tracker.financial import resolver as resolver_mod
from thesis_tracker.financial import sec_source
from thesis_tracker.financial.sec_source import load_stage3_inputs
from thesis_tracker.metrics.financial import (
    compute_accruals_ratio,
    compute_ar_growth_vs_rev_growth,
    compute_cash_conversion,
    compute_diluted_share_count_yoy,
    compute_gross_margin_trend,
    compute_interest_coverage,
    compute_net_buyback_yield,
    compute_net_debt_to_ebitda,
)

TICKERS = (
    "AAPL", "AMD", "AMZN", "AVGO", "GOOGL", "JPM", "LITE", "META",
    "MSFT", "NVDA", "ORCL", "SNDK", "TSLA", "VRT", "XOM",
)

# Registry as it existed before this migration (gross-margin vertical slice).
OLD_REGISTRY = {
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
}
NEW_REGISTRY = dict(sec_source.CONCEPT_REGISTRY)

METRICS = {
    "accruals_ratio": compute_accruals_ratio,
    "cash_conversion": compute_cash_conversion,
    "ar_growth_vs_rev_growth": compute_ar_growth_vs_rev_growth,
    "net_buyback_yield": compute_net_buyback_yield,
    "diluted_share_count_yoy": compute_diluted_share_count_yoy,
    "interest_coverage": compute_interest_coverage,
    "net_debt_to_ebitda": compute_net_debt_to_ebitda,
}


def gm_fingerprint(observation) -> dict:
    return {
        "period_end": observation.period_end.isoformat(),
        "fiscal_year": observation.fiscal_year,
        "fiscal_period": observation.fiscal_period,
        "value": str(observation.value),
        "formula": observation.provenance.formula,
        "facts": [
            {
                "concept": fact.concept,
                "value": str(fact.value),
                "origin": fact.origin.value,
                "accession": fact.accession,
                "resolver_path": list(fact.resolver_path),
                "source_fact_ids": list(fact.source_fact_ids),
            }
            for fact in observation.provenance.source_facts
        ],
    }


def load_with_registry(ticker: str, registry: dict):
    sec_source.CONCEPT_REGISTRY = registry
    resolver_mod.CONCEPT_REGISTRY = registry
    return load_stage3_inputs(ticker, include_semantic_contexts=False)


def main() -> None:
    gm_old_fingerprints: list = []
    gm_new_fingerprints: list = []
    gm_unchanged = True
    per_ticker = {}
    per_metric_requested = Counter()
    per_metric_success = Counter()
    metric_failures = {name: Counter() for name in METRICS}
    metric_failure_examples = {name: [] for name in METRICS}
    spot = {}

    for ticker in TICKERS:
        sic = "6021" if ticker == "JPM" else None

        old_loaded = load_with_registry(ticker, OLD_REGISTRY)
        old_gm = compute_gross_margin_trend(
            ticker=ticker,
            facts=old_loaded.facts,
            boundaries=old_loaded.boundaries,
            issuer_sic=sic,
        )
        loaded = load_with_registry(ticker, NEW_REGISTRY)
        new_gm = compute_gross_margin_trend(
            ticker=ticker,
            facts=loaded.facts,
            boundaries=loaded.boundaries,
            issuer_sic=sic,
        )
        old_fp = [gm_fingerprint(item) for item in old_gm.observations]
        new_fp = [gm_fingerprint(item) for item in new_gm.observations]
        ticker_gm_unchanged = old_fp == new_fp
        gm_unchanged = gm_unchanged and ticker_gm_unchanged
        gm_old_fingerprints.extend((ticker, item) for item in old_fp)
        gm_new_fingerprints.extend((ticker, item) for item in new_fp)

        requested = min(8, len(loaded.boundaries))
        metric_report = {}
        for name, function in METRICS.items():
            kwargs = {
                "ticker": ticker,
                "facts": loaded.facts,
                "boundaries": loaded.boundaries,
                "issuer_sic": sic,
            }
            if name == "net_buyback_yield":
                kwargs["market_cap"] = None
            result = function(**kwargs)
            per_metric_requested[name] += requested
            per_metric_success[name] += len(result.observations)
            codes = Counter(item.final_failure.value for item in result.failures)
            metric_failures[name].update(codes)
            if result.failures and len(metric_failure_examples[name]) < 40:
                for failure in result.failures:
                    metric_failure_examples[name].append(
                        {
                            "ticker": ticker,
                            "period": failure.target_period,
                            "code": failure.final_failure.value,
                            "concept": failure.concept,
                            "reason": failure.rejection_reason,
                        }
                    )
            metric_report[name] = {
                "observations": len(result.observations),
                "failures": dict(codes),
            }
            if ticker in {"NVDA", "AMD", "ORCL", "XOM", "LITE", "JPM"}:
                spot.setdefault(ticker, {})[name] = {
                    "observations": [
                        {
                            "period_end": obs.period_end.isoformat(),
                            "fiscal_year": obs.fiscal_year,
                            "fiscal_period": obs.fiscal_period,
                            "accession": obs.accession,
                            "value": str(obs.value),
                            "formula": obs.formula,
                            "facts": [
                                {
                                    "concept": fact.concept,
                                    "value": str(fact.value),
                                    "context_id": fact.context_id,
                                    "accession": fact.accession,
                                    "origin": fact.origin.value,
                                    "resolver_path": list(fact.resolver_path),
                                    "source_fact_ids": list(fact.source_fact_ids),
                                }
                                for fact in obs.provenance.source_facts
                            ],
                        }
                        for obs in result.observations
                    ],
                    "failures": [
                        {
                            "period": failure.target_period,
                            "code": failure.final_failure.value,
                            "concept": failure.concept,
                            "reason": failure.rejection_reason,
                        }
                        for failure in result.failures
                    ],
                }

        per_ticker[ticker] = {
            "requested": requested,
            "gross_margin_observations": len(new_gm.observations),
            "gross_margin_unchanged": ticker_gm_unchanged,
            "metrics": metric_report,
        }

    sec_source.CONCEPT_REGISTRY = NEW_REGISTRY
    resolver_mod.CONCEPT_REGISTRY = NEW_REGISTRY

    metric_summary = {}
    for name in METRICS:
        metric_summary[name] = {
            "requested": per_metric_requested[name],
            "success": per_metric_success[name],
            "unavailable": per_metric_requested[name] - per_metric_success[name],
            "coverage": (
                per_metric_success[name] / per_metric_requested[name]
                if per_metric_requested[name]
                else 0.0
            ),
            "failure_taxonomy": dict(metric_failures[name]),
        }

    total_requested = sum(per_metric_requested.values())
    total_success = sum(per_metric_success.values())
    payload = {
        "tickers": list(TICKERS),
        "gross_margin": {
            "old_observations": len(gm_old_fingerprints),
            "new_observations": len(gm_new_fingerprints),
            "exact_unchanged": gm_unchanged
            and gm_old_fingerprints == gm_new_fingerprints,
        },
        "per_metric": metric_summary,
        "overall": {
            "requested": total_requested,
            "success": total_success,
            "unavailable": total_requested - total_success,
            "coverage": total_success / total_requested if total_requested else 0.0,
        },
        "per_ticker": per_ticker,
        "failure_examples": metric_failure_examples,
        "spot_check": spot,
    }
    Path("eval/stage3_stress_8metric.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    print(json.dumps(
        {
            "gross_margin": payload["gross_margin"],
            "overall": payload["overall"],
            "per_metric": metric_summary,
        },
        indent=2,
        sort_keys=True,
    ))


if __name__ == "__main__":
    main()
