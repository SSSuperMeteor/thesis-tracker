# Stage 3 predecessor/lookback window fix audit

Generated from `eval/stage3_failure_root_cause_audit.json` cases whose
`root_cause` is `resolver_boundary_window_truncation`. The source audit contains
exactly 11 such cases, all in `ar_growth_vs_rev_growth`.

## Rule change

`load_stage3_inputs()` still applies the existing four-year SEC filing-date
window and the exact Stage 1 accession ceiling. It no longer applies a second,
default count cap of 12 filing families. `max_filings` remains available as an
explicit caller override.

This is a date-bounded history rule rather than a count selected for these
issuers. Eight reported metric periods plus the comparable prior-year period
and that period's predecessor can require at least 13 quarterly boundaries;
amendment/fiscal calendars can produce more filing families in the same date
window. All resolver period, duration, context, and accession checks remain
unchanged.

## Eleven audited cases after the fix

Every row below was loaded again from SEC data and resolved under the new
default. `target ctx` is the context evidence recorded by the source audit;
`predecessor ctx` is the matching fact observed again in the formerly omitted
filing. All 11 corresponding YoY metric periods now produce an observation.

| ticker | audited period | target accession / ctx | predecessor accession / ctx | new revenue resolution | metric period | new classification / Decimal value |
|---|---|---|---|---|---|---|
| AAPL | 2023-Q4 | `0000320193-23-000106` / `c-1` | `0000320193-23-000077` / `c-21`, `c-1` | derived `derived:c-1-c-1`; source IDs include target `c-1` and predecessor `c-1`; 89498000000 | 2024-Q4 | success / 0.07154123229051977848834376062 |
| AMD | 2023-Q3 | `0000002488-23-000195` / `c-3`, `c-1` | `0000002488-23-000139` / `c-3`, `c-1` | reported `c-3`; 5800000000 | 2024-Q3 | success / 0.2570368980527543905134888036 |
| AMZN | 2023-Q3 | `0001018724-23-000018` / `c-10`, `c-1` | `0001018724-23-000012` / `c-10`, `c-1` | reported `c-10`; 143083000000 | 2024-Q3 | success / 0.0788841348966850429277852480 |
| AVGO | 2023-Q4 | `0001730168-23-000096` / `c-1` | `0001730168-23-000077` / `c-13`, `c-1` | derived `derived:c-1-c-1`; source IDs include target `c-1` and predecessor `c-1`; 9295000000 | 2024-Q4 | success / -0.1118688735292803387042692443 |
| GOOGL | 2023-Q3 | `0001652044-23-000094` / `c-16`, `c-1` | `0001652044-23-000070` / `c-16`, `c-1` | reported `c-16`; 76693000000 | 2024-Q3 | success / 0.0461481768322049788215430950 |
| META | 2023-Q3 | `0001326801-23-000103` / `c-10`, `c-1` | `0001326801-23-000093` / `c-10`, `c-1` | reported `c-10`; 34146000000 | 2024-Q3 | success / -0.0530284337807178177732686739 |
| MSFT | 2024-Q1 | `0000950170-23-054855` / `C_705576da-7be5-4d51-9ee4-7b08149476e9` | `0000950170-23-035122` / `C_da7a1266-ce2b-41b7-83ff-bb6e03dc1fa5` | reported `C_705576da-7be5-4d51-9ee4-7b08149476e9`; 56517000000 | 2025-Q1 | success / 0.0342594906630766633133022695 |
| NVDA | 2024-Q3 | `0001045810-23-000227` / `c-3`, `c-1` | `0001045810-23-000175` / `c-3`, `c-1` | `us-gaap:Revenues`, reported `c-3`; 18120000000 | 2025-Q3 | success / 0.1932850678949419722809145755 |
| ORCL | 2024-Q2 | `0000950170-23-069682` / `C_9c2197f2-29d2-4e15-971f-13f393f48bf1`, `C_3d0b361e-b744-4ebf-ac25-96e101b92261` | `0000950170-23-047713` / `C_ebd51344-8d4c-4ad3-a42f-ad896f666fe3` | reported `C_9c2197f2-29d2-4e15-971f-13f393f48bf1`; 12941000000 | 2025-Q2 | success / 0.1154009757393490403991052232 |
| TSLA | 2023-Q3 | `0001628280-23-034847` / `c-33`, `c-1` | `0000950170-23-033872` / `C_dc1ab3aa-7aec-4209-b34d-9d071d427036`, `C_858a5813-8d51-4a12-988b-666fe5f58043` | reported `c-33`; 23350000000 | 2024-Q3 | success / 0.2362242955711906461371129466 |
| VRT | 2023-Q3 | `0001628280-23-035351` / `c-11`, `c-1` | `0001628280-23-026777` / `c-11`, `c-1` | reported `c-11`; 1742600000 | 2024-Q3 | success / -0.0375652198301464838349230307 |

## Fifteen-company, eight-metric stress A/B

The before side is `eval/stage3_stress_8metric.json`. The after side is a fresh
run over the same 15 tickers and up to eight periods per ticker (119 requested
periods per metric).

| metric | before success / 119 | after success / 119 | before coverage | after coverage | change |
|---|---:|---:|---:|---:|---:|
| accruals_ratio | 98 | 98 | 82.3529% | 82.3529% | 0 |
| ar_growth_vs_rev_growth | 79 | 90 | 66.3866% | 75.6303% | +11 |
| cash_conversion | 98 | 98 | 82.3529% | 82.3529% | 0 |
| diluted_share_count_yoy | 57 | 65 | 47.8992% | 54.6218% | +8 |
| interest_coverage | 87 | 87 | 73.1092% | 73.1092% | 0 |
| net_buyback_yield | 0 | 0 | 0.0000% | 0.0000% | 0 |
| net_debt_to_ebitda | 49 | 49 | 41.1765% | 41.1765% | 0 |
| gross_margin_trend | 93 | 93 | 78.1513% | 78.1513% | 0 |
| seven new metrics | 468 / 833 | 487 / 833 | 56.1825% | 58.4634% | +19 |
| all eight metrics | 561 / 952 | 580 / 952 | 58.9286% | 60.9244% | +19 |

The additional eight successes in `diluted_share_count_yoy` are the same
failure class: year-ago duration resolution can also require a predecessor
beyond the former 12-family cap. No metric rule or registry mapping changed.

Failure taxonomy changes:

- `ar_growth_vs_rev_growth`: `period_unavailable` 22 -> 11; all other codes
  unchanged (`ambiguous` 1, `invalid_context` 1, `not_applicable` 1,
  `registry_gap` 8).
- `diluted_share_count_yoy`: `period_unavailable` 12 -> 4; all other codes
  unchanged (`duration_unavailable` 23, `invalid_context` 3, `registry_gap` 24).
- `interest_coverage` and `net_debt_to_ebitda`: observation counts and every
  failure-code count are exactly unchanged.

## Gross-margin baseline fingerprint

The complete `to_dict()` representation of every gross-margin observation was
sorted by ticker/period and serialized with stable JSON keys.

- old explicit `max_filings=12`: 93 observations,
  SHA-256 `b7fed6ec6019fb739a6c04546ab0539cc3d2aad131ff932d180a0bd358a5a385`
- new default date window: 93 observations,
  SHA-256 `b7fed6ec6019fb739a6c04546ab0539cc3d2aad131ff932d180a0bd358a5a385`
- full object equality: `true`; differing fields: 0

## Risk assessment

No ambiguity-selection, concept-registry, period-validation, duration-validation,
context-validation, or accession-ceiling code changed. The resolver sees more
filing families but must still pass the same hard checks, so the change does not
introduce a fallback-to-latest path or suppress ambiguity. The stress A/B adds
no `ambiguous` failures and does not convert any existing success to failure.
The remaining failures retain their previous fail-closed classifications.
