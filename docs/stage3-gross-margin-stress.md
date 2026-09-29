# Stage 3 Gross Margin: 15-Company Stress Report

Date: 2026-09-19

## Scope and repository audit

The working tree already contained the uncommitted Stage 3 correctness
foundation described in `stage3-gross-margin-audit.md`: filing-level XBRL,
full fact provenance, an auditable concept registry, deterministic
single-quarter/YTD/Q4 resolution, amendment-family fallback, structured
failures, and the `gross_margin_trend` vertical slice. Stage 1 ingestion and
Stage 2 retrieval/chunking/citation code were not changed in this stress pass.

The diagnostic-first baseline produced 78 observations, eight false
`ambiguous` results for TSLA, two source-level aborts (JPM and LITE), and 25
other observation failures. The final fresh run used the same Stage 1 filing
boundaries for all 15 requested tickers.

## Final result

There were 119 potential requested quarters: eight for every issuer except
SNDK, whose bounded filing history contains seven. JPM is classified once at
metric level as `not_applicable`; among the remaining 111 applicable quarters,
93 observations are proven and 18 remain unavailable. Of the 93 observations,
70 are reported single-quarter facts and 23 are deterministic derivations.

| Ticker | Requested | Observations | Final result |
|---|---:|---:|---|
| AAPL | 8 | 8 | complete |
| AMD | 8 | 8 | complete |
| AMZN | 8 | 8 | complete |
| AVGO | 8 | 8 | complete |
| GOOGL | 8 | 8 | complete |
| JPM | 8 | 0 | one metric-level `not_applicable` |
| LITE | 8 | 7 | one `registry_gap`; recovery records `duration_unavailable` |
| META | 8 | 8 | complete |
| MSFT | 8 | 8 | complete |
| NVDA | 8 | 8 | complete |
| ORCL | 8 | 0 | seven `registry_gap`, one `invalid_context` |
| SNDK | 7 | 6 | one `period_unavailable` |
| TSLA | 8 | 8 | complete after exact-alias resolver fix |
| VRT | 8 | 8 | complete |
| XOM | 8 | 0 | eight `registry_gap` |

Final canonical diagnostic distribution is:

| Failure | Count | Notes |
|---|---:|---|
| `not_applicable` | 1 | JPM, metric level rather than eight artificial period failures |
| `registry_gap` | 16 | LITE 1, ORCL 7, XOM 8 |
| `invalid_context` | 1 | ORCL filing/fact fiscal-year conflict |
| `period_unavailable` | 1 | SNDK lacks the immediately prior fiscal boundary |
| `ambiguous` | 0 | TSLA false ambiguity was fixed without relaxing conflicting candidates |
| `duration_unavailable` | 0 final | one LITE recovery attempt records this in `recovery_history` |
| `true_missing` | 0 | not asserted where registry/custom/source explanations remain |

## Observation detail

Each entry is
`period_end | fiscal period | gross margin | origin | accession provenance | revenue concept | cost/gross-profit concept`.
For derived observations, accession provenance is `FY/current accession / prior
9M accession`. Concept abbreviations are:

- `RCE`: `us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax`
- `RCI`: `us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax`
- `REV`: `us-gaap:Revenues`
- `COR`: `us-gaap:CostOfRevenue`
- `COGS`: `us-gaap:CostOfGoodsAndServicesSold`
- `GP`: `us-gaap:GrossProfit`

### AAPL (8/8)

```text
2026-06-27 | FY2026-Q3 | 50.0562% | reported | 0000320193-26-000020 | RCE | COGS
2026-03-28 | FY2026-Q2 | 49.2706% | reported | 0000320193-26-000013 | RCE | COGS
2025-12-27 | FY2026-Q1 | 48.1587% | reported | 0000320193-26-000006 | RCE | COGS
2025-09-27 | FY2025-Q4 | 47.1776% | derived  | 0000320193-25-000079/0000320193-25-000073 | RCE | COGS
2025-06-28 | FY2025-Q3 | 46.4907% | reported | 0000320193-25-000073 | RCE | COGS
2025-03-29 | FY2025-Q2 | 47.0506% | reported | 0000320193-25-000057 | RCE | COGS
2024-12-28 | FY2025-Q1 | 46.8825% | reported | 0000320193-25-000008 | RCE | COGS
2024-09-28 | FY2024-Q4 | 46.2225% | derived  | 0000320193-24-000123/0000320193-24-000081 | RCE | COGS
```

### AMD (8/8)

```text
2026-06-27 | FY2026-Q2 | 53.7708% | reported | 0000002488-26-000123 | RCE | COGS
2026-03-28 | FY2026-Q1 | 52.8236% | reported | 0000002488-26-000076 | RCE | COGS
2025-12-27 | FY2025-Q4 | 54.3038% | derived  | 0000002488-26-000018/0000002488-25-000166 | RCE | COGS
2025-09-27 | FY2025-Q3 | 51.6980% | reported | 0000002488-25-000166 | RCE | COGS
2025-06-28 | FY2025-Q2 | 39.8048% | reported | 0000002488-25-000108 | RCE | COGS
2025-03-29 | FY2025-Q1 | 50.2286% | reported | 0000002488-25-000047 | RCE | COGS
2024-12-28 | FY2024-Q4 | 50.6921% | derived  | 0000002488-25-000012/0000002488-24-000163 | RCE | COGS
2024-09-28 | FY2024-Q3 | 50.1393% | reported | 0000002488-24-000163 | RCE | COGS
```

### AMZN (8/8)

```text
2026-06-30 | FY2026-Q2 | 52.2557% | reported | 0001018724-26-000026 | RCE | COGS
2026-03-31 | FY2026-Q1 | 51.8161% | reported | 0001018724-26-000014 | RCE | COGS
2025-12-31 | FY2025-Q4 | 48.4694% | derived  | 0001018724-26-000004/0001018724-25-000123 | RCE | COGS
2025-09-30 | FY2025-Q3 | 50.7851% | reported | 0001018724-25-000123 | RCE | COGS
2025-06-30 | FY2025-Q2 | 51.8139% | reported | 0001018724-25-000086 | RCE | COGS
2025-03-31 | FY2025-Q1 | 50.5509% | reported | 0001018724-25-000036 | RCE | COGS
2024-12-31 | FY2024-Q4 | 47.3391% | derived  | 0001018724-25-000004/0001018724-24-000161 | RCE | COGS
2024-09-30 | FY2024-Q3 | 49.0316% | reported | 0001018724-24-000161 | RCE | COGS
```

### AVGO (8/8)

```text
2026-08-02 | FY2026-Q3 | 69.1291% | reported | 0001730168-26-000080 | RCE | COR
2026-05-03 | FY2026-Q2 | 69.4776% | reported | 0001730168-26-000054 | RCE | COR
2026-02-01 | FY2026-Q1 | 68.1322% | reported | 0001730168-26-000016 | RCE | COR
2025-11-02 | FY2025-Q4 | 67.9933% | derived  | 0001730168-25-000121/0001730168-25-000098 | RCE | COR
2025-08-03 | FY2025-Q3 | 67.0950% | reported | 0001730168-25-000098 | RCE | COR
2025-05-04 | FY2025-Q2 | 67.9619% | reported | 0001730168-25-000064 | RCE | COR
2025-02-02 | FY2025-Q1 | 68.0142% | reported | 0001730168-25-000021 | RCE | COR
2024-11-03 | FY2024-Q4 | 64.0529% | derived  | 0001730168-24-000139/0001730168-24-000099 | RCE | COR
```

### GOOGL (8/8)

```text
2026-06-30 | FY2026-Q2 | 61.6490% | reported | 0001652044-26-000071 | REV | COR
2026-03-31 | FY2026-Q1 | 62.4454% | reported | 0001652044-26-000048 | REV | COR
2025-12-31 | FY2025-Q4 | 59.7932% | derived  | 0001652044-26-000018/0001652044-25-000091 | REV | COR
2025-09-30 | FY2025-Q3 | 59.5793% | reported | 0001652044-25-000091 | REV | COR
2025-06-30 | FY2025-Q2 | 59.5149% | reported | 0001652044-25-000062 | REV | COR
2025-03-31 | FY2025-Q1 | 59.7037% | reported | 0001652044-25-000043 | RCE | COR
2024-12-31 | FY2024-Q4 | 57.9005% | derived  | 0001652044-25-000014/0001652044-24-000118 | RCE | COR
2024-09-30 | FY2024-Q3 | 58.6781% | reported | 0001652044-24-000118 | RCE | COR
```

### LITE (7/8)

These observations use the filing-reported `GP / revenue` path; `GP` is shown
in the final column rather than a cost concept.

```text
2026-06-27 | FY2026-Q4 | 47.4312% | derived  | 0001628280-26-057358/0001628280-26-030777 | RCI | GP
2026-03-28 | FY2026-Q3 | 44.1613% | reported | 0001628280-26-030777 | RCI | GP
2025-12-27 | FY2026-Q2 | 36.0781% | reported | 0001628280-26-005129 | RCI | GP
2025-09-27 | FY2026-Q1 | 34.0015% | reported | 0001628280-25-049073 | RCI | GP
2025-03-29 | FY2025-Q3 | 28.8100% | reported | 0001628280-25-022788 | RCE | GP
2024-12-28 | FY2025-Q2 | 24.7638% | reported | 0001628280-25-004288 | RCE | GP
2024-09-28 | FY2025-Q1 | 23.1226% | reported | 0001628280-24-046443 | RCE | GP
```

### META (8/8)

```text
2026-06-30 | FY2026-Q2 | 81.3654% | reported | 0001628280-26-050705 | RCE | COR
2026-03-31 | FY2026-Q1 | 81.8543% | reported | 0001628280-26-028526 | RCE | COR
2025-12-31 | FY2025-Q4 | 81.7909% | derived  | 0001628280-26-003942/0001628280-25-047240 | RCE | COR
2025-09-30 | FY2025-Q3 | 82.0343% | reported | 0001628280-25-047240 | RCE | COR
2025-06-30 | FY2025-Q2 | 82.1302% | reported | 0001628280-25-036791 | RCE | COR
2025-03-31 | FY2025-Q1 | 82.1052% | reported | 0001326801-25-000054 | RCE | COR
2024-12-31 | FY2024-Q4 | 81.7319% | derived  | 0001326801-25-000017/0001326801-24-000081 | RCE | COR
2024-09-30 | FY2024-Q3 | 81.8301% | reported | 0001326801-24-000081 | RCE | COR
```

### MSFT (8/8)

```text
2026-06-30 | FY2026-Q4 | 67.1970% | derived  | 0001193125-26-323660/0001193125-26-191507 | RCE | COGS
2026-03-31 | FY2026-Q3 | 67.6327% | reported | 0001193125-26-191507 | RCE | COGS
2025-12-31 | FY2026-Q2 | 68.0361% | reported | 0001193125-26-027207 | RCE | COGS
2025-09-30 | FY2026-Q1 | 69.0459% | reported | 0001193125-25-256321 | RCE | COGS
2025-06-30 | FY2025-Q4 | 68.5849% | derived  | 0000950170-25-100235/0000950170-25-061046 | RCE | COGS
2025-03-31 | FY2025-Q3 | 68.7166% | reported | 0000950170-25-061046 | RCE | COGS
2024-12-31 | FY2025-Q2 | 68.6940% | reported | 0000950170-25-010491 | RCE | COGS
2024-09-30 | FY2025-Q1 | 69.3543% | reported | 0000950170-24-118967 | RCE | COGS
```

### NVDA (8/8)

```text
2026-07-26 | FY2027-Q2 | 74.9753% | reported | 0001045810-26-000075 | REV | COR
2026-04-26 | FY2027-Q1 | 74.9335% | reported | 0001045810-26-000052 | REV | COR
2026-01-25 | FY2026-Q4 | 74.9967% | derived  | 0001045810-26-000021/0001045810-25-000230 | REV | COR
2025-10-26 | FY2026-Q3 | 73.4116% | reported | 0001045810-25-000230 | REV | COR
2025-07-27 | FY2026-Q2 | 72.4237% | reported | 0001045810-25-000209 | REV | COR
2025-04-27 | FY2026-Q1 | 60.5238% | reported | 0001045810-25-000116 | REV | COR
2025-01-26 | FY2025-Q4 | 73.0289% | derived  | 0001045810-25-000023/0001045810-24-000316 | REV | COR
2024-10-27 | FY2025-Q3 | 74.5568% | reported | 0001045810-24-000316 | REV | COR
```

### SNDK (6/7)

```text
2026-07-03 | FY2026-Q4 | 84.5733% | derived  | 0001628280-26-057406/0001628280-26-029401 | RCE | COGS
2026-04-03 | FY2026-Q3 | 78.3529% | reported | 0001628280-26-029401 | RCE | COGS
2026-01-02 | FY2026-Q2 | 50.9421% | reported | 0001628280-26-004407 | RCE | COGS
2025-10-03 | FY2026-Q1 | 29.7660% | reported | 0001628280-25-050698 | RCE | COGS
2025-06-27 | FY2025-Q4 | 26.1967% | derived  | 0002023554-25-000034/0002023554-25-000027 | RCE | COGS
2025-03-28 | FY2025-Q3 | 22.5369% | reported | 0002023554-25-000027 | RCE | COGS
```

### TSLA (8/8)

```text
2026-06-30 | FY2026-Q2 | 16.8260% | reported | 0001628280-26-049270 | RCE | COR
2026-03-31 | FY2026-Q1 | 21.0837% | reported | 0001628280-26-026673 | RCE | COR
2025-12-31 | FY2025-Q4 | 20.1157% | derived  | 0001628280-26-003952/0001628280-25-045968 | RCE | COR
2025-09-30 | FY2025-Q3 | 17.9890% | reported | 0001628280-25-045968 | RCE | COR
2025-06-30 | FY2025-Q2 | 17.2386% | reported | 0001628280-25-035806 | RCE | COR
2025-03-31 | FY2025-Q1 | 16.3072% | reported | 0001628280-25-018911 | RCE | COR
2024-12-31 | FY2024-Q4 | 16.2563% | derived  | 0001628280-25-003063/0001628280-24-043486 | RCE | COR
2024-09-30 | FY2024-Q3 | 19.8435% | reported | 0001628280-24-043486 | RCE | COR
```

### VRT (8/8)

```text
2026-06-30 | FY2026-Q2 | 37.7149% | reported | 0001628280-26-050609 | RCE | COGS
2026-03-31 | FY2026-Q1 | 37.7316% | reported | 0001628280-26-026556 | RCE | COGS
2025-12-31 | FY2025-Q4 | 38.9375% | derived  | 0001674101-26-000008/0001674101-25-000024 | RCE | COGS
2025-09-30 | FY2025-Q3 | 37.7719% | reported | 0001674101-25-000024 | RCE | COGS
2025-06-30 | FY2025-Q2 | 33.9866% | reported | 0001674101-25-000008 | RCE | COGS
2025-03-31 | FY2025-Q1 | 33.7181% | reported | 0001628280-25-019372 | RCE | COGS
2024-12-31 | FY2024-Q4 | 37.0866% | derived  | 0001628280-25-005905/0001628280-24-043698 | RCE | COGS
2024-09-30 | FY2024-Q3 | 36.4794% | reported | 0001628280-24-043698 | RCE | COGS
```

JPM, ORCL, and XOM have no returned observations; their explicit results are
listed below.

## Unavailable and not-applicable detail

| Ticker | Period end / target | Final failure | Accession | Candidate count | Root class and diagnostic |
|---|---|---|---|---:|---|
| JPM | metric / latest FY2026-Q2 | `not_applicable` | 0001628280-26-054343 | 0 | D. SEC SIC 6021, National Commercial Banks. The filing presents net interest income, noninterest revenue, and noninterest expense; `revenue - cost_of_revenue` is not a comparable banking gross margin. |
| LITE | 2025-06-28 / FY2025-Q4 | `registry_gap` | 0001628280-25-040830 | 0 | B/E. No registered aggregate cost. The GP recovery is also rejected: FY revenue co-reports RCI/RCE but 9M reports RCE, so cross-alias subtraction is not proven. `recovery_history=[duration_unavailable]`. |
| ORCL | 2026-08-31 / FY2026-Q1 | `invalid_context` | 0001193125-26-389274 | 14 | C/E. Filing metadata says FY2026 while candidate fact metadata says FY2027; resolver correctly refuses to guess. |
| ORCL | 2026-05-31 / FY2026-Q4 | `registry_gap` | 0001193125-26-277521 | 0 | B. Cost is split into custom cloud/software, hardware, and services concepts; no proven standard aggregate mapping. |
| ORCL | 2026-02-28 / FY2026-Q3 | `registry_gap` | 0001193125-26-101045 | 0 | B. Same custom-concept-only cost structure. |
| ORCL | 2025-11-30 / FY2026-Q2 | `registry_gap` | 0001193125-25-315925 | 0 | B. Same custom-concept-only cost structure. |
| ORCL | 2025-08-31 / FY2026-Q1 | `registry_gap` | 0001193125-25-200095 | 0 | B. Same custom-concept-only cost structure. |
| ORCL | 2025-05-31 / FY2025-Q4 | `registry_gap` | 0000950170-25-087926 | 0 | B. Same custom-concept-only cost structure. |
| ORCL | 2025-02-28 / FY2025-Q3 | `registry_gap` | 0000950170-25-037143 | 0 | B. Same custom-concept-only cost structure. |
| ORCL | 2024-11-30 / FY2025-Q2 | `registry_gap` | 0000950170-24-134973 | 0 | B. Same custom-concept-only cost structure. |
| SNDK | 2024-12-27 / FY2025-Q2 | `period_unavailable` | 0002023554-25-000016 | 2 | C. Direct-quarter/YTD candidates exist, but the immediately previous fiscal boundary is outside the bounded SNDK history; no duration may be inferred. |
| XOM | 2026-06-30 / FY2026-Q2 | `registry_gap` | 0000034088-26-000093 | 0 | C. No direct aggregate cost-of-revenue or gross-profit fact. |
| XOM | 2026-03-31 / FY2026-Q1 | `registry_gap` | 0000034088-26-000067 | 0 | C. Same source limitation. |
| XOM | 2025-12-31 / FY2025-Q4 | `registry_gap` | 0000034088-26-000045 | 0 | C. Same source limitation. |
| XOM | 2025-09-30 / FY2025-Q3 | `registry_gap` | 0000034088-25-000061 | 0 | C. Same source limitation. |
| XOM | 2025-06-30 / FY2025-Q2 | `registry_gap` | 0000034088-25-000042 | 0 | C. Same source limitation. |
| XOM | 2025-03-31 / FY2025-Q1 | `registry_gap` | 0000034088-25-000024 | 0 | C. Same source limitation. |
| XOM | 2024-12-31 / FY2024-Q4 | `registry_gap` | 0000034088-25-000010 | 0 | C. Same source limitation. |
| XOM | 2024-09-30 / FY2024-Q3 | `registry_gap` | 0000034088-24-000068 | 0 | C. XOM reports purchases, production/manufacturing, depreciation, exploration, SG&A, and taxes separately. Mapping total `CostsAndExpenses` to cost of revenue would change metric semantics and was rejected. |

`true_missing` is deliberately not used for ORCL/XOM: the stress pass proved
that the registered aggregate is absent, but custom concepts and alternative
statement presentations remain, so the stricter registry/source diagnoses take
precedence.

## Root-cause decisions and fixes

### A. General implementation bugs fixed

1. TSLA reports both `RCE` and `REV` with identical value, unit, duration,
   accession, context ID, and dimensions. The resolver previously counted two
   registered aliases as two economic facts. It now coalesces aliases only when
   every non-concept identity field is identical, uses explicit registry order,
   and records `equivalent_registered_aliases_coalesced` in provenance. Any
   context/value/duration difference remains ambiguous.
2. A filing family with no registered fact previously raised before boundaries
   could be constructed. The adapter now keeps the newest parseable XBRL member
   so the resolver emits per-period serializable failures. Amendment fallback
   still prefers a family member with registered duration facts and never
   combines members.
3. Gross margin applicability was missing. A general SEC SIC finance
   major-group (60-67) gate now returns one serialized `not_applicable` result
   before fact resolution. It requires an explicitly supplied SEC SIC; absence
   or malformed SIC does not trigger a guess.

### B. Registry coverage fixed or retained

LITE proved a general, standard-GAAP path: `GrossProfit / revenue`. The registry
now includes `GrossProfit` and a separate gross-profit revenue family including
`RevenueFromContractWithCustomerIncludingAssessedTax`. This is a fallback only;
the existing `(revenue - cost_of_revenue) / revenue` path remains preferred.
The 2026 filing identity was manually checked:

```text
revenue                 3,014.0m
cost excluding DDA      1,680.5m
cost amortization          77.6m
gross profit            1,255.9m
3,014.0 - 1,680.5 - 77.6 = 1,255.9
```

ORCL remains a registry/custom-concept gap. Adding its three issuer concepts or
summing them without a general accounting mapping would be a ticker-specific
recovery, so no code was added.

### C. Source limitations retained

SNDK has only seven bounded periodic filings and cannot prove the predecessor
for FY2025-Q2. XOM has no direct cost-of-revenue/gross-profit aggregate in the
inspected filing presentation. Neither condition was converted to zero or
reconstructed from an unproven expense sum.

### D. Metric not applicable

JPM is a bank (SIC 6021). It is represented by one `not_applicable` diagnostic,
not eight misleading missing-period rows and not a fabricated gross margin.

### E. Unresolved ambiguity/inconsistency retained

LITE FY2025-Q4 cannot cross-subtract RCI and RCE durations. ORCL's newest filing
has conflicting fiscal-year metadata. Both remain fail-closed.

## Raw-fact spot checks and silent-wrong-data risks

- TSLA 2026-Q2 accession `0001628280-26-049270` reports RCE and REV both as
  28,236m in context `c-33`, COR 23,485m, and GP 4,751m. The returned 16.8260%
  reconciles to GP; differing aliases would still fail as ambiguous.
- SNDK's high FY2026-Q4 value was not discarded as an outlier. FY revenue/cost
  are 20,248m/5,776m and 9M revenue/cost are 11,283m/4,393m. The proven Q4
  difference is therefore 8,965m/1,383m and 84.5733%; reported GP also
  reconciles. This is unusual but not a resolver error.
- LITE's concept transition is blocked for FY2025-Q4 instead of silently mixing
  assessed-tax definitions.
- XOM's `CostsAndExpenses` is not relabeled as cost of revenue.
- No returned observation mixes unrelated accessions or filing families. No
  silent wrong-data result was found in the final set.

## Tests added in this stress pass

- exact registered aliases in the same XBRL context coalesce with auditable
  provenance;
- a family without registered facts preserves its latest XBRL boundary for
  diagnostics;
- SEC SIC finance issuers return metric-level `not_applicable`;
- the standard LITE GP/revenue filing pattern computes deterministically;
- a failed GP recovery is serialized in `recovery_history`.

The pre-existing duration, annual-vs-quarter, YTD/Q4, ambiguity, accession,
dimension, fiscal-metadata, and amendment tests remain in place.

## Remaining foundation issues before metric #2

- Decide whether ORCL custom cost components can be mapped by a general,
  audited accounting registry. Until then they remain unavailable.
- Decide whether a filing-local equality proof is ever sufficient to bridge
  RCI/RCE for a derived quarter. Current behavior correctly refuses it.
- A reusable stress CLI/artifact writer does not yet exist; this report was
  generated from the Python API. That is operational tooling, not required for
  correctness of the next metric.
- Applicability metadata is supplied explicitly to the metric API; a later
  source-bound issuer-metadata object could remove that caller responsibility
  without changing the rule.

Subject to those explicit limitations, the fact/provenance/period foundation
is stable enough to begin metric #2 without weakening fail-closed behavior.
