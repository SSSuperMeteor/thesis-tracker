# Stage 2 SEC QA Acceptance — 30 Questions

## Summary

- Questions: 30
- Completed: 30
- Accepted cases: 26
- Failed cases: 4
- Cases with Layer B partial claims: 9
- Case acceptance rate: 86.67%
- Infrastructure failures: 0
- Candidate claims: 125
- Initial grounding rate: 100.00%
- Retry attempts: 0
- Retry successes: 0
- Retry success rate: N/A
- Final grounding rate: 100.00%
- Layer B supported: 100
- Layer B partial: 22
- Layer B unsupported: 3
- Final verified claims: 100
- Answer coverage: 86.67%
- Unsupported leakage: 0

## Acceptance

Citation grounding >= 95%: PASS

Unsupported leakage == 0: PASS

Overall Stage 2 QA acceptance: PASS

## By Company

### AMD

- Questions/completed: 5/5
- Candidate claims: 22
- Initial grounding: 100.00%
- Retry attempts/successes: 0/0
- Final grounding: 100.00%
- Supported/partial/unsupported: 19/3/0
- Verified claims: 19
- Answer coverage: 100.00%
- Unsupported leakage: 0

### LITE

- Questions/completed: 4/4
- Candidate claims: 15
- Initial grounding: 100.00%
- Retry attempts/successes: 0/0
- Final grounding: 100.00%
- Supported/partial/unsupported: 15/0/0
- Verified claims: 15
- Answer coverage: 75.00%
- Unsupported leakage: 0

### NVDA

- Questions/completed: 4/4
- Candidate claims: 20
- Initial grounding: 100.00%
- Retry attempts/successes: 0/0
- Final grounding: 100.00%
- Supported/partial/unsupported: 19/1/0
- Verified claims: 19
- Answer coverage: 100.00%
- Unsupported leakage: 0

### ORCL

- Questions/completed: 4/4
- Candidate claims: 16
- Initial grounding: 100.00%
- Retry attempts/successes: 0/0
- Final grounding: 100.00%
- Supported/partial/unsupported: 10/4/2
- Verified claims: 10
- Answer coverage: 75.00%
- Unsupported leakage: 0

### SNDK

- Questions/completed: 4/4
- Candidate claims: 12
- Initial grounding: 100.00%
- Retry attempts/successes: 0/0
- Final grounding: 100.00%
- Supported/partial/unsupported: 11/1/0
- Verified claims: 11
- Answer coverage: 75.00%
- Unsupported leakage: 0

### TSLA

- Questions/completed: 4/4
- Candidate claims: 20
- Initial grounding: 100.00%
- Retry attempts/successes: 0/0
- Final grounding: 100.00%
- Supported/partial/unsupported: 14/6/0
- Verified claims: 14
- Answer coverage: 100.00%
- Unsupported leakage: 0

### VRT

- Questions/completed: 5/5
- Candidate claims: 20
- Initial grounding: 100.00%
- Retry attempts/successes: 0/0
- Final grounding: 100.00%
- Supported/partial/unsupported: 12/7/1
- Verified claims: 12
- Answer coverage: 80.00%
- Unsupported leakage: 0

## Case Outcomes

| Case | Ticker | Status | Expected parent rank | Candidates | Grounded | Supported | Partial | Unsupported | Empty | Primary issue |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---|
| retrieval-stage2-001 | AMD | accepted | 3 | 5 | 5 | 5 | 0 | 0 | false | - |
| retrieval-stage2-002 | AMD | accepted | 1 | 2 | 2 | 2 | 0 | 0 | false | - |
| retrieval-stage2-005 | AMD | accepted | 1 | 5 | 5 | 2 | 3 | 0 | false | claim_support_failure |
| retrieval-stage2-006 | AMD | accepted | 2 | 5 | 5 | 5 | 0 | 0 | false | - |
| retrieval-stage2-009 | AMD | accepted | 2 | 5 | 5 | 5 | 0 | 0 | false | - |
| retrieval-stage2-011 | LITE | failed | 1 | 0 | 0 | 0 | 0 | 0 | true | answer_generation_failure |
| retrieval-stage2-014 | LITE | accepted | 1 | 5 | 5 | 5 | 0 | 0 | false | - |
| retrieval-stage2-016 | LITE | accepted | 2 | 5 | 5 | 5 | 0 | 0 | false | - |
| retrieval-stage2-018 | LITE | accepted | 3 | 5 | 5 | 5 | 0 | 0 | false | - |
| retrieval-stage2-021 | NVDA | accepted | 1 | 5 | 5 | 4 | 1 | 0 | false | evidence_selection_failure |
| retrieval-stage2-025 | NVDA | accepted | 1 | 5 | 5 | 5 | 0 | 0 | false | - |
| retrieval-stage2-026 | NVDA | accepted | 1 | 5 | 5 | 5 | 0 | 0 | false | - |
| retrieval-stage2-027 | NVDA | accepted | 1 | 5 | 5 | 5 | 0 | 0 | false | - |
| retrieval-stage2-031 | ORCL | accepted | 2 | 5 | 5 | 5 | 0 | 0 | false | - |
| retrieval-stage2-033 | ORCL | accepted | 1 | 5 | 5 | 1 | 3 | 1 | false | claim_support_failure |
| retrieval-stage2-034 | ORCL | accepted | 2 | 5 | 5 | 4 | 1 | 0 | false | evidence_selection_failure |
| retrieval-stage2-036 | ORCL | failed | 2 | 1 | 1 | 0 | 0 | 1 | true | claim_support_failure |
| retrieval-stage2-042 | SNDK | accepted | 1 | 3 | 3 | 3 | 0 | 0 | false | - |
| retrieval-stage2-044 | SNDK | accepted | 1 | 4 | 4 | 4 | 0 | 0 | false | - |
| retrieval-stage2-046 | SNDK | failed | miss | 0 | 0 | 0 | 0 | 0 | true | retrieval_failure |
| retrieval-stage2-048 | SNDK | accepted | 1 | 5 | 5 | 4 | 1 | 0 | false | evidence_selection_failure |
| retrieval-stage2-051 | TSLA | accepted | 3 | 5 | 5 | 2 | 3 | 0 | false | evidence_selection_failure |
| retrieval-stage2-054 | TSLA | accepted | 1 | 5 | 5 | 5 | 0 | 0 | false | - |
| retrieval-stage2-056 | TSLA | accepted | 1 | 5 | 5 | 5 | 0 | 0 | false | - |
| retrieval-stage2-057 | TSLA | accepted | 1 | 5 | 5 | 2 | 3 | 0 | false | claim_support_failure |
| retrieval-stage2-062 | VRT | accepted | 1 | 5 | 5 | 1 | 4 | 0 | false | claim_support_failure |
| retrieval-stage2-064 | VRT | failed | 1 | 0 | 0 | 0 | 0 | 0 | true | answer_generation_failure |
| retrieval-stage2-066 | VRT | accepted | miss | 5 | 5 | 5 | 0 | 0 | false | - |
| retrieval-stage2-067 | VRT | accepted | 3 | 5 | 5 | 5 | 0 | 0 | false | - |
| retrieval-stage2-068 | VRT | accepted | 1 | 5 | 5 | 1 | 3 | 1 | false | evidence_selection_failure |
## Failures / Difficult Cases

- Final Layer A failures: none
- Retry failures: none
- Layer B partial: retrieval-stage2-005 (3), retrieval-stage2-021 (1), retrieval-stage2-033 (3), retrieval-stage2-034 (1), retrieval-stage2-048 (1), retrieval-stage2-051 (3), retrieval-stage2-057 (3), retrieval-stage2-062 (4), retrieval-stage2-068 (3)
- Layer B unsupported: retrieval-stage2-033 (1), retrieval-stage2-036 (1), retrieval-stage2-068 (1)
- Empty final answers: retrieval-stage2-011, retrieval-stage2-036, retrieval-stage2-046, retrieval-stage2-064
- Infrastructure errors: none

Layer A items are citation-grounding failures; retry items are evidence repair failures; partial and unsupported items are Layer B entailment rejections. Empty answers contain no supported claim after filtering.

### Five Most Difficult Cases

#### retrieval-stage2-036 — ORCL

What changes to Oracle's risk factors were reported in this quarter?

- Issues: Layer B unsupported, empty final answer
- Final grounding failures: 0
- Retry attempts/failures: 0/0
- Partial/unsupported: 0/1
- Empty final answer: true
- Infrastructure errors: 0

#### retrieval-stage2-011 — LITE

Which product and end-market trends drove Lumentum's quarterly revenue change?

- Issues: empty final answer
- Final grounding failures: 0
- Retry attempts/failures: 0/0
- Partial/unsupported: 0/0
- Empty final answer: true
- Infrastructure errors: 0

#### retrieval-stage2-046 — SNDK

What material changes to Sandisk's risk factors were disclosed?

- Issues: empty final answer
- Final grounding failures: 0
- Retry attempts/failures: 0/0
- Partial/unsupported: 0/0
- Empty final answer: true
- Infrastructure errors: 0

#### retrieval-stage2-064 — VRT

What does Vertiv's backlog indicate about management's outlook for future sales?

- Issues: empty final answer
- Final grounding failures: 0
- Retry attempts/failures: 0/0
- Partial/unsupported: 0/0
- Empty final answer: true
- Infrastructure errors: 0

#### retrieval-stage2-033 — ORCL

What property, plant and equipment balances and additions did Oracle report?

- Issues: Layer B partial, Layer B unsupported
- Final grounding failures: 0
- Retry attempts/failures: 0/0
- Partial/unsupported: 3/1
- Empty final answer: false
- Infrastructure errors: 0
