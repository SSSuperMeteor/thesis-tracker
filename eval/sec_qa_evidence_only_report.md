# SEC QA — Evidence-Only Fallback Verification (8 Targeted Cases)

Generated: 2026-09-12T08:11:03.658533+00:00

## Summary

- Cases: 8 (completed 8)
- answered_count: 3
- evidence_only_count: 5
- insufficient_evidence_count: 0
- accepted_cases: 3 / failed_cases: 5 (evidence_only is an output mode, not an acceptance)
- empty_answer_count: 0
- grounding_rate: 100.00% (16/16)
- unsupported_leakage_count: 0
- infrastructure_error_count: 0

## Per-case results

| Case | Old status | New status | Acceptance | Failure category | Retrieval state | Claim result | Final output mode |
|---|---|---|---|---|---|---|---|
| retrieval-stage2-011 | failed / answer_generation_failure | evidence_only | failed | answer_generation_failure | expected parent rank 1 of 5 retrieved | 0 candidate / 0 grounded / 0 verified / 0 rejected | raw_evidence |
| retrieval-stage2-036 | failed / claim_support_failure | evidence_only | failed | claim_support_failure | expected parent rank 2 of 5 retrieved | 1 candidate / 1 grounded / 0 verified / 1 rejected | raw_evidence |
| retrieval-stage2-046 | failed / retrieval_failure | evidence_only | failed | retrieval_failure | expected parent miss of 5 retrieved | 0 candidate / 0 grounded / 0 verified / 0 rejected | raw_evidence |
| retrieval-stage2-064 | failed / answer_generation_failure | evidence_only | failed | answer_generation_failure | expected parent rank 1 of 5 retrieved | 0 candidate / 0 grounded / 0 verified / 0 rejected | raw_evidence |
| retrieval-stage2-009 | accepted | answered | accepted | - | expected parent rank 2 of 5 retrieved | 5 candidate / 5 grounded / 5 verified / 0 rejected | verified_claims |
| retrieval-stage2-051 | accepted / evidence_selection_failure | answered | accepted | evidence_selection_failure | expected parent rank 3 of 5 retrieved | 5 candidate / 5 grounded / 1 verified / 4 rejected | verified_claims |
| retrieval-stage2-034 | accepted / evidence_selection_failure | evidence_only | failed | answer_generation_failure | expected parent rank 2 of 5 retrieved | 0 candidate / 0 grounded / 0 verified / 0 rejected | raw_evidence |
| retrieval-stage2-031 | accepted | answered | accepted | - | expected parent rank 2 of 5 retrieved | 5 candidate / 5 grounded / 5 verified / 0 rejected | verified_claims |

## Evidence-only detail

| Case | Triggered | Reason | Retrieved evidence | Exported evidence |
|---|---|---|---:|---:|
| retrieval-stage2-011 | yes | no_supported_claims | 5 | 5 |
| retrieval-stage2-036 | yes | no_supported_claims | 5 | 5 |
| retrieval-stage2-046 | yes | no_supported_claims | 5 | 5 |
| retrieval-stage2-064 | yes | no_supported_claims | 5 | 5 |
| retrieval-stage2-009 | no | - | 5 | 0 |
| retrieval-stage2-051 | no | - | 5 | 0 |
| retrieval-stage2-034 | yes | no_supported_claims | 5 | 5 |
| retrieval-stage2-031 | no | - | 5 | 0 |

## Claim verification detail

| Case | Candidates | Verified | Rejected reasons | Grounded | Unsupported leakage |
|---|---:|---:|---|---:|---:|
| retrieval-stage2-011 | 0 | 0 | - | 0 | 0 |
| retrieval-stage2-036 | 1 | 0 | unsupported | 1 | 0 |
| retrieval-stage2-046 | 0 | 0 | - | 0 | 0 |
| retrieval-stage2-064 | 0 | 0 | - | 0 | 0 |
| retrieval-stage2-009 | 5 | 5 | - | 5 | 0 |
| retrieval-stage2-051 | 5 | 1 | partial_support | 5 | 0 |
| retrieval-stage2-034 | 0 | 0 | - | 0 | 0 |
| retrieval-stage2-031 | 5 | 5 | - | 5 | 0 |

## Acceptance gates

Grounding rate >= 95%: PASS

Unsupported leakage == 0: PASS

## Fallback rule

- verified claims > 0: `answered`, output verified claims.
- no verified claims and retrieved evidence is non-empty: `evidence_only`, output the raw retrieved evidence.
- no verified claims and no retrieved evidence: `insufficient_evidence`.
- No route-consensus, rank, or score gate is applied; `evidence_only` is an output mode and never counts as an accepted case.

## Configuration unchanged

- Retrieval depth Top-5, chunking v2, parent collapse, vector collection, Layer A, Layer B, thresholds, and gold data are unchanged.
- Retrieval signals (distances, route ranks) are deterministic; claim generation is not, so a previously accepted case can still produce zero candidates on a rerun.
