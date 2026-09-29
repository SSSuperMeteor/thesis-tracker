# Stage 3 — 7-metric migration final report

generated_utc: 2026-09-20T08:15:25Z

## Verification (fresh, this session)
- uv run pytest -p no:cacheprovider  -> 345 passed, 1 third-party warning
- uv run ruff check .                 -> All checks passed!
- git diff --check                    -> exit 0
- gross_margin A/B (old vs new registry, 15 tickers): 93 -> 93, exact fingerprints equal

## Files changed
- src/thesis_tracker/financial/registry.py   : 18 canonical concepts + NON_DERIVABLE_CONCEPTS
- src/thesis_tracker/financial/models.py     : FailureCode += ZERO_DENOMINATOR, MISSING_EXTERNAL_DATA
- src/thesis_tracker/financial/resolver.py   : + resolve_instant_fact, prior_fiscal_boundary, non-derivable guard
- src/thesis_tracker/financial/sec_source.py  : instant extraction (period_instant), shares units,
                                               duplicate-precision kept for resolver, most-complete member selection
- src/thesis_tracker/metrics/financial.py     : MarketCapInput, Stage3Observation, MetricResult,
                                               7 compute_* metrics, SIC applicability for 3 metrics
- tests/test_stage3_metrics.py                : new (34 tests)
- tests/test_financial_sec_source.py          : 1 test updated to resolver-owned ambiguity
- eval/stage3_stress_8metric.json             : machine-readable stress artifact (not committed)
- eval/stage3_final_report.md                 : this report

## 8-metric stress (15 companies, 119 requested periods per metric)
| metric | requested | success | unavailable | coverage | top failures |
|---|---:|---:|---:|---:|---|
| accruals_ratio | 119 | 98 | 21 | 82.4% | ambiguous 18, duration_unavailable 1, invalid_context 1, period_unavailable 1 |
| cash_conversion | 119 | 98 | 21 | 82.4% | ambiguous 18, duration_unavailable 1, invalid_context 1, period_unavailable 1 |
| ar_growth_vs_rev_growth | 119 | 79 | 40 | 66.4% | period_unavailable 22, registry_gap 8, invalid_context 1, ambiguous 1, not_applicable 1 |
| net_buyback_yield | 119 | 0 | 119 | 0.0% | missing_external_data 119 |
| diluted_share_count_yoy | 119 | 57 | 62 | 47.9% | registry_gap 24, duration_unavailable 23, period_unavailable 12, invalid_context 3 |
| interest_coverage | 119 | 87 | 32 | 73.1% | period_unavailable 12, registry_gap 8, duration_unavailable 2, invalid_context 2, not_applicable 1 |
| net_debt_to_ebitda | 119 | 49 | 70 | 41.2% | period_unavailable 22, invalid_context 19, registry_gap 11, ambiguous 10, not_applicable 1 |
| gross_margin_trend | 119 | 93 | 26 | 78.2% | unchanged baseline (26 failures) |
| OVERALL (7 new) | 833 | 468 | 365 | 56.2% | |
| OVERALL (8 metrics) | 952 | 561 | 391 | 58.9% | |

## NVDA / AMD (8 metrics)
NVDA: gross_margin 8/8 (unchanged) | accruals 8/8 | cash 8/8 | ar_growth 7/8 | diluted 5/8 | interest 8/8 | net_debt 8/8 | buyback 0/8 (missing_external_data)
AMD : gross_margin 8/8 (unchanged) | accruals 6/8 (ambiguous 2) | cash 6/8 (ambiguous 2) | ar_growth 7/8 | diluted 5/8 | interest 8/8 | net_debt 0/8 (fail-closed) | buyback 0/8

## Spot-check evidence (period / accession / concept / context / formula / Decimal value / provenance)
Recorded per metric in eval/stage3_stress_8metric.json -> spot_check[NVDA|AMD|ORCL|XOM|LITE|JPM].
Example NVDA cash_conversion 2027-Q2, accession 0001045810-26-000075, concept us-gaap:NetCashProvidedByUsedInOperatingActivities,
context derived:c-1-c-1, origin derived, resolver_path [stage1_boundary, filing_xbrl, ytd_difference], value 0.40338...
