# SEC QA Chunking v2 — Five Difficult Cases

## Summary

| Metric | Before | After |
|---|---:|---:|
| Supported | 4 | 14 |
| Partial | 18 | 8 |
| Unsupported | 3 | 1 |
| Verified claims | 4 | 14 |
| Empty answers | 1 | 0 |

Expected v2 section retrieved in 5/5 cases.

## By Case

| Case | Before S/P/U | After S/P/U | Verified before/after | Empty before/after |
|---|---:|---:|---:|---:|
| retrieval-stage2-001 | 0/5/0 | 1/3/1 | 0/1 | True/False |
| retrieval-stage2-031 | 1/1/3 | 4/0/0 | 1/4 | False/False |
| retrieval-stage2-033 | 1/4/0 | 2/3/0 | 1/2 | False/False |
| retrieval-stage2-057 | 1/4/0 | 3/2/0 | 1/3 | False/False |
| retrieval-stage2-064 | 1/4/0 | 4/0/0 | 1/4 | False/False |

## Interpretation

This is a fixed five-case smoke comparison. No retrieval weights, QA prompts, Layer A, retry, Layer B, or filtering rules were changed.
