# Stage 2 Retrieval Regression — Chunking v2 (overlap)

Generated: 2026-09-12T06:42:14.163422+00:00

## 1. Configuration

- Chunking: `chunker-v2-overlap` (target_chunk_chars=3000, overlap_chars=500)
- Embedding: `qwen3-vl-embedding` / 1024 dims
- Vector collection: `sec_chunks_qwen3_vl_embedding_1024_chunkv2`
- Retrieval depth: Top 20; RRF k=60
- Ground truth: `eval/retrieval_stage2_questions.json` (unchanged, 70 cases)
- Matching rule: a retrieved `parent::chunk_NNN` counts as a hit when its logical parent is the expected chunk id.

## 2. Corpus and index

- Valid v2 subchunks: 868
- Logical parents: 181
- Subchunks per ticker: `{"AMD": 114, "LITE": 204, "NVDA": 89, "ORCL": 174, "SNDK": 114, "TSLA": 87, "VRT": 86}`
- Logical parents per ticker: `{"AMD": 22, "LITE": 19, "NVDA": 21, "ORCL": 43, "SNDK": 25, "TSLA": 26, "VRT": 25}`
- Indexed vectors per ticker: `{"AMD": 114, "LITE": 204, "NVDA": 89, "ORCL": 174, "SNDK": 114, "TSLA": 87, "VRT": 86}`
- Index stats: `{"batches": 0, "embedded_chunks": 0, "removed_stale": 0, "skipped_unchanged": 868, "total_chunks": 868}`

## 3. Overall metrics

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 70 | 0.557 | 0.814 | 0.914 | 0.943 | 0.943 | 0.692 |
| Vector | 70 | 0.443 | 0.643 | 0.786 | 0.871 | 0.971 | 0.574 |
| Hybrid | 70 | 0.529 | 0.814 | 0.900 | 0.957 | 0.971 | 0.687 |

## 4. Before / after

Baseline: v1 logical sections (`eval/retrieval_stage2_results.json`). After: chunking v2 with the same queries, filters, retrievers, and depth.

| Method | Metric | Before | After | Δ |
|---|---|---:|---:|---:|
| Bm25 | recall_at_1 | 0.614 | 0.557 | -0.057 |
| Bm25 | recall_at_3 | 0.857 | 0.814 | -0.043 |
| Bm25 | recall_at_5 | 0.957 | 0.914 | -0.043 |
| Bm25 | recall_at_10 | 0.957 | 0.943 | -0.014 |
| Bm25 | recall_at_20 | 1.000 | 0.943 | -0.057 |
| Bm25 | mrr | 0.738 | 0.692 | -0.046 |
| Vector | recall_at_1 | 0.471 | 0.443 | -0.029 |
| Vector | recall_at_3 | 0.771 | 0.643 | -0.129 |
| Vector | recall_at_5 | 0.914 | 0.786 | -0.129 |
| Vector | recall_at_10 | 0.971 | 0.871 | -0.100 |
| Vector | recall_at_20 | 1.000 | 0.971 | -0.029 |
| Vector | mrr | 0.639 | 0.574 | -0.065 |
| Hybrid | recall_at_1 | 0.600 | 0.529 | -0.071 |
| Hybrid | recall_at_3 | 0.843 | 0.814 | -0.029 |
| Hybrid | recall_at_5 | 0.929 | 0.900 | -0.029 |
| Hybrid | recall_at_10 | 0.986 | 0.957 | -0.029 |
| Hybrid | recall_at_20 | 1.000 | 0.971 | -0.029 |
| Hybrid | mrr | 0.735 | 0.687 | -0.048 |

### Rank movement per method

- Bm25: improved 11, regressed 22, unchanged 37
- Vector: improved 9, regressed 32, unchanged 29
- Hybrid: improved 14, regressed 24, unchanged 32

### Recall@20 cases lost versus the v1 baseline

| Method | Case | Ticker | Type | Expected section | v1 rank | v2 rank | Siblings in v2 Top-20 | Duplicate parents in v2 Top-20 |
|---|---|---|---|---|---:|---:|---:|---:|
| Bm25 | `stage2-043` | SNDK | accounting | Supplemental Financial Statement Data | 5 | miss | 16 | 3 |
| Bm25 | `stage2-049` | SNDK | capital structure | Debt | 4 | miss | 13 | 3 |
| Bm25 | `stage2-063` | VRT | accounting | OTHER FINANCIAL INFORMATION | 14 | miss | 11 | 4 |
| Bm25 | `stage2-066` | VRT | risk | Part II, Item 1A | 15 | miss | 11 | 5 |
| Vector | `stage2-046` | SNDK | risk | Part II, Item 1A | 18 | miss | 14 | 3 |
| Vector | `stage2-050` | SNDK | legal | Legal Proceedings | 13 | miss | 14 | 3 |
| Hybrid | `stage2-063` | VRT | accounting | OTHER FINANCIAL INFORMATION | 8 | miss | 11 | 2 |
| Hybrid | `stage2-066` | VRT | risk | Part II, Item 1A | 11 | miss | 12 | 4 |

### Candidate-pool diversity inside Top-20

In v1 every logical parent was one chunk, so Top-20 always carried 20 distinct sections. Overlapping subchunks let siblings repeat, shrinking the pool of distinct sections that a downstream reranker or reader could use.

| Method | Mean distinct parents in Top-20 | Min | Max | Mean duplicate slots | Mean slots lost vs v1 depth |
|---|---:|---:|---:|---:|---:|
| Bm25 | 6.96 | 2 | 13 | 13.04 | 13.04 |
| Vector | 6.47 | 3 | 14 | 13.53 | 13.53 |
| Hybrid | 6.79 | 3 | 14 | 13.21 | 13.21 |

### Highest-crowding Hybrid cases

| Case | Ticker | Type | Expected section | Distinct parents in Top-20 | Duplicate slots | Largest single-parent share | Expected rank |
|---|---|---|---|---:|---:|---:|---:|
| `stage2-006` | AMD | risk | Part II, Item 1A | 3 | 17 | 17 | 2 |
| `stage2-026` | NVDA | risk | Part II, Item 1A | 4 | 16 | 13 | 1 |
| `stage2-060` | TSLA | accounting | Equity Incentive Plans | 6 | 14 | 13 | 5 |
| `stage2-016` | LITE | risk | Part II, Item 1A | 3 | 17 | 12 | 1 |
| `stage2-013` | LITE | accounting | Balance Sheet Details | 4 | 16 | 12 | 8 |
| `stage2-011` | LITE | numeric / financial | Part I, Item 2 | 3 | 17 | 11 | 1 |
| `stage2-020` | LITE | accounting | Restructuring and Related Charges (Reversals) | 6 | 14 | 11 | 1 |
| `stage2-043` | SNDK | accounting | Supplemental Financial Statement Data | 5 | 15 | 10 | 10 |

## 5. Focus checks on overlap side effects

1. Hybrid Recall@5: 0.929 → 0.900 (-0.029) — decreased
2. Recall@20 complete: BM25 1.000 → 0.943; Vector 1.000 → 0.971; Hybrid 1.000 → 0.971
3. Risk cases (n=7) — see section 8 for detail
4. Duplicate Top-K: Top-5 slots consumed by sibling subchunks 144 (Hybrid), rate 41.1%
5. Duplicate crowding: cases with any duplicate parent in Top-5 — BM25 64, Vector 68, Hybrid 67

## 6. By company

### AMD

- Cases: 10

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 10 | 0.500 | 0.900 | 1.000 | 1.000 | 1.000 | 0.708 |
| Vector | 10 | 0.300 | 0.600 | 0.700 | 0.800 | 1.000 | 0.483 |
| Hybrid | 10 | 0.300 | 0.800 | 1.000 | 1.000 | 1.000 | 0.595 |

| Method | Recall@5 before | Recall@5 after | Δ Recall@5 | MRR before | MRR after | Δ MRR |
|---|---:|---:|---:|---:|---:|---:|
| Bm25 | 1.000 | 1.000 | +0.000 | 0.803 | 0.708 | -0.095 |
| Vector | 0.800 | 0.700 | -0.100 | 0.703 | 0.483 | -0.220 |
| Hybrid | 1.000 | 1.000 | +0.000 | 0.808 | 0.595 | -0.213 |

### LITE

- Cases: 10

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 10 | 0.500 | 0.800 | 0.900 | 1.000 | 1.000 | 0.656 |
| Vector | 10 | 0.400 | 0.700 | 0.900 | 1.000 | 1.000 | 0.562 |
| Hybrid | 10 | 0.700 | 0.900 | 0.900 | 1.000 | 1.000 | 0.796 |

| Method | Recall@5 before | Recall@5 after | Δ Recall@5 | MRR before | MRR after | Δ MRR |
|---|---:|---:|---:|---:|---:|---:|
| Bm25 | 1.000 | 0.900 | -0.100 | 0.667 | 0.656 | -0.011 |
| Vector | 1.000 | 0.900 | -0.100 | 0.733 | 0.562 | -0.171 |
| Hybrid | 1.000 | 0.900 | -0.100 | 0.742 | 0.796 | +0.054 |

### NVDA

- Cases: 10

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 10 | 0.800 | 1.000 | 1.000 | 1.000 | 1.000 | 0.883 |
| Vector | 10 | 0.800 | 0.900 | 1.000 | 1.000 | 1.000 | 0.858 |
| Hybrid | 10 | 0.800 | 1.000 | 1.000 | 1.000 | 1.000 | 0.900 |

| Method | Recall@5 before | Recall@5 after | Δ Recall@5 | MRR before | MRR after | Δ MRR |
|---|---:|---:|---:|---:|---:|---:|
| Bm25 | 1.000 | 1.000 | +0.000 | 0.817 | 0.883 | +0.067 |
| Vector | 1.000 | 1.000 | +0.000 | 0.870 | 0.858 | -0.012 |
| Hybrid | 1.000 | 1.000 | +0.000 | 0.833 | 0.900 | +0.067 |

### ORCL

- Cases: 10

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 10 | 0.300 | 0.700 | 1.000 | 1.000 | 1.000 | 0.515 |
| Vector | 10 | 0.300 | 0.600 | 0.800 | 0.900 | 1.000 | 0.492 |
| Hybrid | 10 | 0.300 | 0.700 | 0.800 | 1.000 | 1.000 | 0.504 |

| Method | Recall@5 before | Recall@5 after | Δ Recall@5 | MRR before | MRR after | Δ MRR |
|---|---:|---:|---:|---:|---:|---:|
| Bm25 | 0.900 | 1.000 | +0.100 | 0.632 | 0.515 | -0.117 |
| Vector | 1.000 | 0.800 | -0.200 | 0.637 | 0.492 | -0.145 |
| Hybrid | 0.900 | 0.800 | -0.100 | 0.600 | 0.504 | -0.096 |

### SNDK

- Cases: 10

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 10 | 0.600 | 0.800 | 0.800 | 0.800 | 0.800 | 0.700 |
| Vector | 10 | 0.500 | 0.500 | 0.700 | 0.800 | 0.800 | 0.556 |
| Hybrid | 10 | 0.400 | 0.700 | 0.800 | 0.900 | 1.000 | 0.586 |

| Method | Recall@5 before | Recall@5 after | Δ Recall@5 | MRR before | MRR after | Δ MRR |
|---|---:|---:|---:|---:|---:|---:|
| Bm25 | 1.000 | 0.800 | -0.200 | 0.745 | 0.700 | -0.045 |
| Vector | 0.800 | 0.700 | -0.100 | 0.533 | 0.556 | +0.023 |
| Hybrid | 0.800 | 0.800 | +0.000 | 0.674 | 0.586 | -0.087 |

### TSLA

- Cases: 10

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 10 | 0.700 | 0.800 | 0.900 | 1.000 | 1.000 | 0.792 |
| Vector | 10 | 0.400 | 0.700 | 0.900 | 1.000 | 1.000 | 0.579 |
| Hybrid | 10 | 0.600 | 0.800 | 1.000 | 1.000 | 1.000 | 0.745 |

| Method | Recall@5 before | Recall@5 after | Δ Recall@5 | MRR before | MRR after | Δ MRR |
|---|---:|---:|---:|---:|---:|---:|
| Bm25 | 1.000 | 0.900 | -0.100 | 0.833 | 0.792 | -0.042 |
| Vector | 1.000 | 0.900 | -0.100 | 0.470 | 0.579 | +0.109 |
| Hybrid | 1.000 | 1.000 | +0.000 | 0.833 | 0.745 | -0.088 |

### VRT

- Cases: 10

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 10 | 0.500 | 0.700 | 0.800 | 0.800 | 0.800 | 0.592 |
| Vector | 10 | 0.400 | 0.500 | 0.500 | 0.600 | 1.000 | 0.487 |
| Hybrid | 10 | 0.600 | 0.800 | 0.800 | 0.800 | 0.800 | 0.683 |

| Method | Recall@5 before | Recall@5 after | Δ Recall@5 | MRR before | MRR after | Δ MRR |
|---|---:|---:|---:|---:|---:|---:|
| Bm25 | 0.800 | 0.800 | +0.000 | 0.672 | 0.592 | -0.080 |
| Vector | 0.800 | 0.500 | -0.300 | 0.525 | 0.487 | -0.038 |
| Hybrid | 0.800 | 0.800 | +0.000 | 0.655 | 0.683 | +0.028 |

## 7. By question type

### accounting

- Cases: 11

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 11 | 0.455 | 0.636 | 0.818 | 0.818 | 0.818 | 0.576 |
| Vector | 11 | 0.182 | 0.273 | 0.455 | 0.727 | 1.000 | 0.323 |
| Hybrid | 11 | 0.455 | 0.545 | 0.727 | 0.909 | 0.909 | 0.557 |

| Method | Recall@5 before | Recall@5 after | Δ Recall@5 | MRR before | MRR after | Δ MRR |
|---|---:|---:|---:|---:|---:|---:|
| Bm25 | 0.909 | 0.818 | -0.091 | 0.520 | 0.576 | +0.056 |
| Vector | 0.818 | 0.455 | -0.364 | 0.439 | 0.323 | -0.116 |
| Hybrid | 0.909 | 0.727 | -0.182 | 0.458 | 0.557 | +0.098 |

### capital structure

- Cases: 14

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 14 | 0.214 | 0.714 | 0.929 | 0.929 | 0.929 | 0.475 |
| Vector | 14 | 0.286 | 0.571 | 0.857 | 0.929 | 1.000 | 0.475 |
| Hybrid | 14 | 0.429 | 0.857 | 0.857 | 0.929 | 1.000 | 0.647 |

| Method | Recall@5 before | Recall@5 after | Δ Recall@5 | MRR before | MRR after | Δ MRR |
|---|---:|---:|---:|---:|---:|---:|
| Bm25 | 1.000 | 0.929 | -0.071 | 0.613 | 0.475 | -0.138 |
| Vector | 1.000 | 0.857 | -0.143 | 0.558 | 0.475 | -0.083 |
| Hybrid | 1.000 | 0.857 | -0.143 | 0.639 | 0.647 | +0.008 |

### legal

- Cases: 3

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 3 | 0.333 | 1.000 | 1.000 | 1.000 | 1.000 | 0.611 |
| Vector | 3 | 0.000 | 0.000 | 0.000 | 0.333 | 0.667 | 0.059 |
| Hybrid | 3 | 0.000 | 0.333 | 0.667 | 1.000 | 1.000 | 0.275 |

| Method | Recall@5 before | Recall@5 after | Δ Recall@5 | MRR before | MRR after | Δ MRR |
|---|---:|---:|---:|---:|---:|---:|
| Bm25 | 1.000 | 1.000 | +0.000 | 0.611 | 0.611 | +0.000 |
| Vector | 0.333 | 0.000 | -0.333 | 0.148 | 0.059 | -0.089 |
| Hybrid | 0.667 | 0.667 | +0.000 | 0.298 | 0.275 | -0.023 |

### numeric / financial

- Cases: 13

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 13 | 0.769 | 0.923 | 1.000 | 1.000 | 1.000 | 0.853 |
| Vector | 13 | 0.692 | 0.769 | 0.923 | 0.923 | 1.000 | 0.758 |
| Hybrid | 13 | 0.692 | 0.923 | 1.000 | 1.000 | 1.000 | 0.827 |

| Method | Recall@5 before | Recall@5 after | Δ Recall@5 | MRR before | MRR after | Δ MRR |
|---|---:|---:|---:|---:|---:|---:|
| Bm25 | 1.000 | 1.000 | +0.000 | 0.814 | 0.853 | +0.038 |
| Vector | 1.000 | 0.923 | -0.077 | 0.797 | 0.758 | -0.039 |
| Hybrid | 1.000 | 1.000 | +0.000 | 0.897 | 0.827 | -0.071 |

### risk

- Cases: 7

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 7 | 0.857 | 0.857 | 0.857 | 0.857 | 0.857 | 0.857 |
| Vector | 7 | 0.286 | 0.714 | 0.714 | 0.714 | 0.857 | 0.460 |
| Hybrid | 7 | 0.429 | 0.857 | 0.857 | 0.857 | 0.857 | 0.643 |

| Method | Recall@5 before | Recall@5 after | Δ Recall@5 | MRR before | MRR after | Δ MRR |
|---|---:|---:|---:|---:|---:|---:|
| Bm25 | 0.714 | 0.857 | +0.143 | 0.663 | 0.857 | +0.195 |
| Vector | 0.714 | 0.714 | +0.000 | 0.478 | 0.460 | -0.018 |
| Hybrid | 0.571 | 0.857 | +0.286 | 0.486 | 0.643 | +0.157 |

### segment

- Cases: 8

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 8 | 0.375 | 0.750 | 0.750 | 1.000 | 1.000 | 0.560 |
| Vector | 8 | 0.125 | 0.625 | 0.875 | 1.000 | 1.000 | 0.403 |
| Hybrid | 8 | 0.250 | 0.750 | 1.000 | 1.000 | 1.000 | 0.521 |

| Method | Recall@5 before | Recall@5 after | Δ Recall@5 | MRR before | MRR after | Δ MRR |
|---|---:|---:|---:|---:|---:|---:|
| Bm25 | 1.000 | 0.750 | -0.250 | 0.792 | 0.560 | -0.232 |
| Vector | 1.000 | 0.875 | -0.125 | 0.698 | 0.403 | -0.294 |
| Hybrid | 1.000 | 1.000 | +0.000 | 0.938 | 0.521 | -0.417 |

### semantic outlook

- Cases: 14

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 14 | 0.786 | 0.929 | 1.000 | 1.000 | 1.000 | 0.863 |
| Vector | 14 | 0.929 | 1.000 | 1.000 | 1.000 | 1.000 | 0.964 |
| Hybrid | 14 | 0.857 | 1.000 | 1.000 | 1.000 | 1.000 | 0.905 |

| Method | Recall@5 before | Recall@5 after | Δ Recall@5 | MRR before | MRR after | Δ MRR |
|---|---:|---:|---:|---:|---:|---:|
| Bm25 | 1.000 | 1.000 | +0.000 | 1.000 | 0.863 | -0.137 |
| Vector | 1.000 | 1.000 | +0.000 | 0.881 | 0.964 | +0.083 |
| Hybrid | 1.000 | 1.000 | +0.000 | 1.000 | 0.905 | -0.095 |

## 8. Risk cases

Risk cases: 7

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 7 | 0.857 | 0.857 | 0.857 | 0.857 | 0.857 | 0.857 |
| Vector | 7 | 0.286 | 0.714 | 0.714 | 0.714 | 0.857 | 0.460 |
| Hybrid | 7 | 0.429 | 0.857 | 0.857 | 0.857 | 0.857 | 0.643 |

| Case | Ticker | Expected section | BM25 before→after | Vector before→after | Hybrid before→after | Hybrid crowding (unique parents / GT-parent) |
|---|---|---|---|---|---|---|
| `stage2-006` | AMD | Part II, Item 1A | 1→1 | 1→3 | 1→2 | 2 / yes |
| `stage2-016` | LITE | Part II, Item 1A | 1→1 | 3→3 | 2→1 | 2 / yes |
| `stage2-026` | NVDA | Part II, Item 1A | 1→1 | 1→1 | 1→1 | 1 / yes |
| `stage2-036` | ORCL | Part II, Item 1A | 14→1 | 3→2 | 6→2 | 5 / no |
| `stage2-046` | SNDK | Part II, Item 1A | 1→1 | 18→miss | 7→2 | 4 / no |
| `stage2-056` | TSLA | Part II, Item 1A | 2→1 | 2→1 | 2→1 | 5 / no |
| `stage2-066` | VRT | Part II, Item 1A | 15→miss | 8→19 | 11→miss | 2 / no |

## 9. Duplicate crowding analysis

Top-5 duplicate crowding occurs when two or more retrieved subchunks share one logical parent, consuming slots that another section could use.

| Method | Cases with duplicate parent in Top-5 | Rate | Top-5 slots lost to siblings | Slot rate | Mean unique parents in Top-5 | Max subchunks of one parent | GT-parent crowding | GT missed@5 while crowded |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 64 | 91.4% | 134 | 38.3% | 3.09 | 5 | 32 | 2 |
| Vector | 68 | 97.1% | 141 | 40.3% | 2.99 | 5 | 27 | 13 |
| Hybrid | 67 | 95.7% | 144 | 41.1% | 2.94 | 5 | 30 | 5 |

A case counts as *GT-parent crowding* when the ground-truth parent itself contributes the duplicate slots, meaning sibling overlap did not displace the correct parent. *GT missed@5 while crowded* counts cases where some parent consumed duplicate Top-5 slots and the expected parent still ranked 6 or worse.

### Cases where the expected parent was outside Top-5 while Top-5 was crowded

| Method | Case | Ticker | Type | Expected rank | Unique parents | Crowded parent | Crowded parent is GT |
|---|---|---|---|---:|---:|---|---|
| Bm25 | `stage2-018` | LITE | segment | 7 | 2 | `0001628280-26-030777::part_ii_item_1a` | no |
| Bm25 | `stage2-058` | TSLA | segment | 6 | 2 | `0001628280-26-049270::part_i_item_2` | no |
| Vector | `stage2-003` | AMD | accounting | 11 | 2 | `0000002488-26-000123::part_i_item_1` | no |
| Vector | `stage2-007` | AMD | legal | 10 | 3 | `0000002488-26-000123::part_i_item_2` | no |
| Vector | `stage2-010` | AMD | accounting | 17 | 3 | `0000002488-26-000123::part_i_item_1` | no |
| Vector | `stage2-013` | LITE | accounting | 8 | 1 | `0001628280-26-030777::part_i_item_1` | no |
| Vector | `stage2-038` | ORCL | segment | 9 | 3 | `0001193125-26-101045::note::10::segment_information` | no |
| Vector | `stage2-040` | ORCL | legal | 13 | 4 | `0001193125-26-101045::note::12::legal_proceedings` | no |
| Vector | `stage2-049` | SNDK | capital structure | 9 | 4 | `0001628280-26-029401::part_i_item_1` | no |
| Vector | `stage2-060` | TSLA | accounting | 8 | 3 | `0001628280-26-049270::part_i_item_1` | no |
| Vector | `stage2-061` | VRT | numeric / financial | 13 | 3 | `0001628280-26-050609::part_i_item_2` | no |
| Vector | `stage2-063` | VRT | accounting | 20 | 3 | `0001628280-26-050609::part_i_item_1` | no |
| Vector | `stage2-066` | VRT | risk | 19 | 2 | `0001628280-26-050609::part_i_item_2` | no |
| Vector | `stage2-067` | VRT | capital structure | 11 | 3 | `0001628280-26-050609::part_i_item_2` | no |
| Vector | `stage2-070` | VRT | accounting | 10 | 3 | `0001628280-26-050609::part_i_item_2` | no |
| Hybrid | `stage2-013` | LITE | accounting | 8 | 1 | `0001628280-26-030777::part_i_item_1` | no |
| Hybrid | `stage2-037` | ORCL | capital structure | 6 | 3 | `0001193125-26-101045::note::07::leases_and_other_commitments` | no |
| Hybrid | `stage2-040` | ORCL | legal | 8 | 4 | `0001193125-26-101045::note::12::legal_proceedings` | no |
| Hybrid | `stage2-043` | SNDK | accounting | 10 | 3 | `0001628280-26-029401::part_i_item_1` | no |
| Hybrid | `stage2-049` | SNDK | capital structure | 16 | 2 | `0001628280-26-029401::part_i_item_1` | no |

## 10. Notable regressions

### Bm25 — improved (11)

| Case | Ticker | Type | Baseline rank | v2 rank | Δ (positive = closer) | Expected section | Top-20 parents gained | Top-20 parents lost |
|---|---|---|---:|---:|---:|---|---|---|
| `stage2-036` | ORCL | risk | 14 | 1 | 13 | Part II, Item 1A | - | `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::09::income_taxes`, `0001193125-26-101045::note::10::segment_information` (+7) |
| `stage2-020` | LITE | accounting | 4 | 1 | 3 | Restructuring and Related Charges (Reversals) | - | `0001628280-26-030777::note::01::recently_issued_accounting_pronouncements`, `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::05::fair_value_measurements` (+10) |
| `stage2-031` | ORCL | numeric / financial | 3 | 1 | 2 | Part I, Item 2 | - | `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::03::fair_value_measurements`, `0001193125-26-101045::note::05::restructuring_activities` (+10) |
| `stage2-003` | AMD | accounting | 5 | 4 | 1 | Supplemental Financial Statement Information | - | `0000002488-26-000123::note::00::the_company`, `0000002488-26-000123::note::03::segment_reporting`, `0000002488-26-000123::note::05::goodwill_and_acquisition_related_intangibles_net` (+8) |
| `stage2-010` | AMD | accounting | 3 | 2 | 1 | Common Stock and Stock-based Compensation | - | `0000002488-26-000123::note::00::the_company`, `0000002488-26-000123::note::01::basis_of_presentation_and_significant_accounting_p`, `0000002488-26-000123::note::02::supplemental_financial_statement_information` (+10) |
| `stage2-012` | LITE | numeric / financial | 2 | 1 | 1 | Part I, Item 2 | - | `0001628280-26-030777::note::01::recently_issued_accounting_pronouncements`, `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::05::fair_value_measurements` (+11) |
| `stage2-019` | LITE | capital structure | 4 | 3 | 1 | Debt | - | `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::05::fair_value_measurements`, `0001628280-26-030777::note::09::accumulated_other_comprehensive_income_loss` (+8) |
| `stage2-027` | NVDA | capital structure | 2 | 1 | 1 | Commitments and Contingencies | - | `0001045810-26-000075::note::00::summary_of_significant_accounting_policies`, `0001045810-26-000075::note::01::stock_based_compensation`, `0001045810-26-000075::note::02::net_income_per_share` (+11) |
| `stage2-030` | NVDA | capital structure | 3 | 2 | 1 | Part II, Item 2 | - | `0001045810-26-000075::note::00::summary_of_significant_accounting_policies`, `0001045810-26-000075::note::01::stock_based_compensation`, `0001045810-26-000075::note::03::intangible_assets_and_goodwill` (+8) |
| `stage2-042` | SNDK | numeric / financial | 2 | 1 | 1 | Part I, Item 2 | - | `0001628280-26-029401::note::00::organization_and_basis_of_presentation`, `0001628280-26-029401::note::01::recent_accounting_pronouncements`, `0001628280-26-029401::note::02::geographic_information_and_concentrations_of_risk` (+11) |
| `stage2-056` | TSLA | risk | 2 | 1 | 1 | Part II, Item 1A | - | `0001628280-26-049270::note::01::fair_value_of_financial_instruments`, `0001628280-26-049270::note::03::inventory`, `0001628280-26-049270::note::04::property_plant_and_equipment_net` (+8) |

### Bm25 — regressed (22)

| Case | Ticker | Type | Baseline rank | v2 rank | Δ (positive = closer) | Expected section | Top-20 parents gained | Top-20 parents lost |
|---|---|---|---:|---:|---:|---|---|---|
| `stage2-018` | LITE | segment | 3 | 7 | -4 | Part I, Item 2 | - | `0001628280-26-030777::note::01::recently_issued_accounting_pronouncements`, `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::04::cash_cash_equivalents_and_short_term_investments` (+13) |
| `stage2-058` | TSLA | segment | 2 | 6 | -4 | Segment Reporting and Information about Geographic Areas | - | `0001628280-26-049270::note::01::fair_value_of_financial_instruments`, `0001628280-26-049270::note::02::acquisition`, `0001628280-26-049270::note::03::inventory` (+11) |
| `stage2-034` | ORCL | semantic outlook | 1 | 4 | -3 | Part I, Item 2 | - | `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::04::notes_payable_and_other_borrowings`, `0001193125-26-101045::note::05::restructuring_activities` (+9) |
| `stage2-014` | LITE | semantic outlook | 1 | 3 | -2 | Part I, Item 2 | - | `0001628280-26-030777::note::01::recently_issued_accounting_pronouncements`, `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::04::cash_cash_equivalents_and_short_term_investments` (+13) |
| `stage2-032` | ORCL | numeric / financial | 1 | 3 | -2 | Part I, Item 2 | - | `0001193125-26-101045::note::01::insider_trading_arrangements`, `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::07::leases_and_other_commitments` (+11) |
| `stage2-038` | ORCL | segment | 1 | 3 | -2 | SEGMENT INFORMATION | - | `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::08::stockholders_equity`, `0001193125-26-101045::note::09::income_taxes` (+11) |
| `stage2-039` | ORCL | capital structure | 3 | 5 | -2 | Part I, Item 2 | - | `0001193125-26-101045::note::03::fair_value_measurements`, `0001193125-26-101045::note::04::notes_payable_and_other_borrowings`, `0001193125-26-101045::note::05::restructuring_activities` (+6) |
| `stage2-067` | VRT | capital structure | 1 | 3 | -2 | COMMITMENTS AND CONTINGENCIES | - | `0001628280-26-050609::note::00::description_of_business`, `0001628280-26-050609::note::03::revenue`, `0001628280-26-050609::note::04::restructuring_costs` (+9) |
| `stage2-001` | AMD | segment | 2 | 3 | -1 | Segment Reporting | - | `0000002488-26-000123::note::00::the_company`, `0000002488-26-000123::note::01::basis_of_presentation_and_significant_accounting_p`, `0000002488-26-000123::note::02::supplemental_financial_statement_information` (+11) |
| `stage2-008` | AMD | segment | 1 | 2 | -1 | Segment Reporting | - | `0000002488-26-000123::note::00::the_company`, `0000002488-26-000123::note::01::basis_of_presentation_and_significant_accounting_p`, `0000002488-26-000123::note::02::supplemental_financial_statement_information` (+10) |
| `stage2-009` | AMD | capital structure | 1 | 2 | -1 | Debt, Revolving Credit Facility and Commercial Paper Program | - | `0000002488-26-000123::note::00::the_company`, `0000002488-26-000123::note::01::basis_of_presentation_and_significant_accounting_p`, `0000002488-26-000123::note::03::segment_reporting` (+11) |
| `stage2-013` | LITE | accounting | 3 | 4 | -1 | Balance Sheet Details | - | `0001628280-26-030777::note::01::recently_issued_accounting_pronouncements`, `0001628280-26-030777::note::05::fair_value_measurements`, `0001628280-26-030777::note::08::debt` (+10) |
| … | | | | | | | 10 more regressed cases | |

### Vector — improved (9)

| Case | Ticker | Type | Baseline rank | v2 rank | Δ (positive = closer) | Expected section | Top-20 parents gained | Top-20 parents lost |
|---|---|---|---:|---:|---:|---|---|---|
| `stage2-055` | TSLA | semantic outlook | 3 | 1 | 2 | Part I, Item 2 | - | `0001628280-26-049270::note::03::inventory`, `0001628280-26-049270::note::04::property_plant_and_equipment_net`, `0001628280-26-049270::note::05::accrued_liabilities_and_other` (+11) |
| `stage2-004` | AMD | semantic outlook | 2 | 1 | 1 | Part I, Item 2 | - | `0000002488-26-000123::note::02::supplemental_financial_statement_information`, `0000002488-26-000123::note::03::segment_reporting`, `0000002488-26-000123::note::05::goodwill_and_acquisition_related_intangibles_net` (+7) |
| `stage2-029` | NVDA | capital structure | 5 | 4 | 1 | Debt | - | `0001045810-26-000075::note::00::summary_of_significant_accounting_policies`, `0001045810-26-000075::note::01::stock_based_compensation`, `0001045810-26-000075::note::02::net_income_per_share` (+8) |
| `stage2-036` | ORCL | risk | 3 | 2 | 1 | Part II, Item 1A | `0001193125-26-101045::note::09::income_taxes` | `0001193125-26-101045::note::01::insider_trading_arrangements`, `0001193125-26-101045::note::10::segment_information`, `0001193125-26-389274::note::01::insider_trading_arrangements` (+4) |
| `stage2-047` | SNDK | capital structure | 2 | 1 | 1 | Related Parties and Related Commitments and Contingencies | - | `0001628280-26-029401::note::02::geographic_information_and_concentrations_of_risk`, `0001628280-26-029401::note::03::revenue`, `0001628280-26-029401::note::04::supplemental_financial_statement_data` (+11) |
| `stage2-052` | TSLA | numeric / financial | 2 | 1 | 1 | Part I, Item 2 | - | `0001628280-26-049270::note::03::inventory`, `0001628280-26-049270::note::04::property_plant_and_equipment_net`, `0001628280-26-049270::note::05::accrued_liabilities_and_other` (+10) |
| `stage2-054` | TSLA | semantic outlook | 2 | 1 | 1 | Part I, Item 2 | - | `0001628280-26-049270::note::00::summary_of_significant_accounting_policies`, `0001628280-26-049270::note::01::fair_value_of_financial_instruments`, `0001628280-26-049270::note::03::inventory` (+11) |
| `stage2-056` | TSLA | risk | 2 | 1 | 1 | Part II, Item 1A | - | `0001628280-26-049270::note::01::fair_value_of_financial_instruments`, `0001628280-26-049270::note::07::debt`, `0001628280-26-049270::note::08::equity_incentive_plans` (+9) |
| `stage2-068` | VRT | segment | 3 | 2 | 1 | SEGMENT INFORMATION | - | `0001628280-26-050609::note::01::basis_of_presentation_and_summary_of_significant_a`, `0001628280-26-050609::note::02::acquisitions`, `0001628280-26-050609::note::04::restructuring_costs` (+12) |

### Vector — regressed (32)

| Case | Ticker | Type | Baseline rank | v2 rank | Δ (positive = closer) | Expected section | Top-20 parents gained | Top-20 parents lost |
|---|---|---|---:|---:|---:|---|---|---|
| `stage2-063` | VRT | accounting | 7 | 20 | -13 | OTHER FINANCIAL INFORMATION | - | `0001628280-26-050609::note::01::basis_of_presentation_and_summary_of_significant_a`, `0001628280-26-050609::note::02::acquisitions`, `0001628280-26-050609::note::03::revenue` (+11) |
| `stage2-010` | AMD | accounting | 6 | 17 | -11 | Common Stock and Stock-based Compensation | - | `0000002488-26-000123::note::02::supplemental_financial_statement_information`, `0000002488-26-000123::note::03::segment_reporting`, `0000002488-26-000123::note::05::goodwill_and_acquisition_related_intangibles_net` (+9) |
| `stage2-066` | VRT | risk | 8 | 19 | -11 | Part II, Item 1A | - | `0001628280-26-050609::note::02::acquisitions`, `0001628280-26-050609::note::03::revenue`, `0001628280-26-050609::note::04::restructuring_costs` (+9) |
| `stage2-040` | ORCL | legal | 5 | 13 | -8 | LEGAL PROCEEDINGS | `0001193125-26-101045::note::05::restructuring_activities`, `0001193125-26-101045::note::09::income_taxes`, `0001193125-26-389274::note::09::income_taxes` | `0001193125-26-101045::note::07::leases_and_other_commitments`, `0001193125-26-101045::note::10::segment_information`, `0001193125-26-389274::note::01::insider_trading_arrangements` (+6) |
| `stage2-061` | VRT | numeric / financial | 5 | 13 | -8 | REVENUE | - | `0001628280-26-050609::note::01::basis_of_presentation_and_summary_of_significant_a`, `0001628280-26-050609::note::02::acquisitions`, `0001628280-26-050609::note::04::restructuring_costs` (+12) |
| `stage2-038` | ORCL | segment | 2 | 9 | -7 | SEGMENT INFORMATION | - | `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::05::restructuring_activities`, `0001193125-26-101045::note::06::deferred_revenues` (+11) |
| `stage2-067` | VRT | capital structure | 4 | 11 | -7 | COMMITMENTS AND CONTINGENCIES | - | `0001628280-26-050609::note::01::basis_of_presentation_and_summary_of_significant_a`, `0001628280-26-050609::note::02::acquisitions`, `0001628280-26-050609::note::03::revenue` (+11) |
| `stage2-003` | AMD | accounting | 5 | 11 | -6 | Supplemental Financial Statement Information | - | `0000002488-26-000123::note::05::goodwill_and_acquisition_related_intangibles_net`, `0000002488-26-000123::note::06::related_party_equity_joint_ventures`, `0000002488-26-000123::note::07::financial_instruments` (+8) |
| `stage2-013` | LITE | accounting | 3 | 8 | -5 | Balance Sheet Details | - | `0001628280-26-030777::note::01::recently_issued_accounting_pronouncements`, `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::04::cash_cash_equivalents_and_short_term_investments` (+10) |
| `stage2-070` | VRT | accounting | 5 | 10 | -5 | ACQUISITIONS | - | `0001628280-26-050609::note::01::basis_of_presentation_and_summary_of_significant_a`, `0001628280-26-050609::note::03::revenue`, `0001628280-26-050609::note::04::restructuring_costs` (+11) |
| `stage2-007` | AMD | legal | 6 | 10 | -4 | Commitments and Contingencies | - | `0000002488-26-000123::note::02::supplemental_financial_statement_information`, `0000002488-26-000123::note::03::segment_reporting`, `0000002488-26-000123::note::05::goodwill_and_acquisition_related_intangibles_net` (+8) |
| `stage2-049` | SNDK | capital structure | 5 | 9 | -4 | Debt | - | `0001628280-26-029401::note::01::recent_accounting_pronouncements`, `0001628280-26-029401::note::02::geographic_information_and_concentrations_of_risk`, `0001628280-26-029401::note::03::revenue` (+11) |
| … | | | | | | | 20 more regressed cases | |

### Hybrid — improved (14)

| Case | Ticker | Type | Baseline rank | v2 rank | Δ (positive = closer) | Expected section | Top-20 parents gained | Top-20 parents lost |
|---|---|---|---:|---:|---:|---|---|---|
| `stage2-046` | SNDK | risk | 7 | 2 | 5 | Part II, Item 1A | - | `0001628280-26-029401::note::01::recent_accounting_pronouncements`, `0001628280-26-029401::note::02::geographic_information_and_concentrations_of_risk`, `0001628280-26-029401::note::03::revenue` (+11) |
| `stage2-036` | ORCL | risk | 6 | 2 | 4 | Part II, Item 1A | `0001193125-26-101045::note::05::restructuring_activities`, `0001193125-26-101045::part_ii_item_1a`, `0001193125-26-389274::note::03::fair_value_measurements` | `0001193125-26-101045::note::10::segment_information`, `0001193125-26-101045::note::12::legal_proceedings`, `0001193125-26-101045::part_i_item_4` (+6) |
| `stage2-020` | LITE | accounting | 4 | 1 | 3 | Restructuring and Related Charges (Reversals) | - | `0001628280-26-030777::note::01::recently_issued_accounting_pronouncements`, `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::05::fair_value_measurements` (+10) |
| `stage2-019` | LITE | capital structure | 3 | 1 | 2 | Debt | - | `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::04::cash_cash_equivalents_and_short_term_investments`, `0001628280-26-030777::note::09::accumulated_other_comprehensive_income_loss` (+8) |
| `stage2-050` | SNDK | legal | 7 | 5 | 2 | Legal Proceedings | - | `0001628280-26-029401::note::04::supplemental_financial_statement_data`, `0001628280-26-029401::note::05::fair_value_measurements_and_investments`, `0001628280-26-029401::note::07::debt` (+7) |
| `stage2-010` | AMD | accounting | 3 | 2 | 1 | Common Stock and Stock-based Compensation | - | `0000002488-26-000123::note::02::supplemental_financial_statement_information`, `0000002488-26-000123::note::05::goodwill_and_acquisition_related_intangibles_net`, `0000002488-26-000123::note::06::related_party_equity_joint_ventures` (+9) |
| `stage2-016` | LITE | risk | 2 | 1 | 1 | Part II, Item 1A | - | `0001628280-26-030777::note::01::recently_issued_accounting_pronouncements`, `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::04::cash_cash_equivalents_and_short_term_investments` (+13) |
| `stage2-023` | NVDA | accounting | 2 | 1 | 1 | Commitments and Contingencies | - | `0001045810-26-000075::note::00::summary_of_significant_accounting_policies`, `0001045810-26-000075::note::01::stock_based_compensation`, `0001045810-26-000075::note::02::net_income_per_share` (+12) |
| `stage2-029` | NVDA | capital structure | 3 | 2 | 1 | Debt | - | `0001045810-26-000075::note::01::stock_based_compensation`, `0001045810-26-000075::note::02::net_income_per_share`, `0001045810-26-000075::note::07::derivative_financial_instruments` (+9) |
| `stage2-039` | ORCL | capital structure | 2 | 1 | 1 | Part I, Item 2 | - | `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::03::fair_value_measurements`, `0001193125-26-101045::note::05::restructuring_activities` (+7) |
| `stage2-056` | TSLA | risk | 2 | 1 | 1 | Part II, Item 1A | - | `0001628280-26-049270::note::01::fair_value_of_financial_instruments`, `0001628280-26-049270::note::03::inventory`, `0001628280-26-049270::note::07::debt` (+8) |
| `stage2-059` | TSLA | capital structure | 3 | 2 | 1 | Debt | - | `0001628280-26-049270::note::01::fair_value_of_financial_instruments`, `0001628280-26-049270::note::03::inventory`, `0001628280-26-049270::note::04::property_plant_and_equipment_net` (+8) |
| … | | | | | | | 2 more improved cases | |

### Hybrid — regressed (24)

| Case | Ticker | Type | Baseline rank | v2 rank | Δ (positive = closer) | Expected section | Top-20 parents gained | Top-20 parents lost |
|---|---|---|---:|---:|---:|---|---|---|
| `stage2-049` | SNDK | capital structure | 5 | 16 | -11 | Debt | `0001628280-26-029401::part_i_item_4` | `0001628280-26-029401::note::01::recent_accounting_pronouncements`, `0001628280-26-029401::note::02::geographic_information_and_concentrations_of_risk`, `0001628280-26-029401::note::03::revenue` (+9) |
| `stage2-043` | SNDK | accounting | 4 | 10 | -6 | Supplemental Financial Statement Data | - | `0001628280-26-029401::note::01::recent_accounting_pronouncements`, `0001628280-26-029401::note::02::geographic_information_and_concentrations_of_risk`, `0001628280-26-029401::note::03::revenue` (+12) |
| `stage2-013` | LITE | accounting | 3 | 8 | -5 | Balance Sheet Details | - | `0001628280-26-030777::note::01::recently_issued_accounting_pronouncements`, `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::04::cash_cash_equivalents_and_short_term_investments` (+12) |
| `stage2-040` | ORCL | legal | 4 | 8 | -4 | LEGAL PROCEEDINGS | `0001193125-26-101045::note::05::restructuring_activities` | `0001193125-26-101045::note::01::insider_trading_arrangements`, `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::07::leases_and_other_commitments` (+6) |
| `stage2-001` | AMD | segment | 1 | 4 | -3 | Segment Reporting | - | `0000002488-26-000123::note::01::basis_of_presentation_and_significant_accounting_p`, `0000002488-26-000123::note::02::supplemental_financial_statement_information`, `0000002488-26-000123::note::06::related_party_equity_joint_ventures` (+10) |
| `stage2-058` | TSLA | segment | 1 | 4 | -3 | Segment Reporting and Information about Geographic Areas | - | `0001628280-26-049270::note::01::fair_value_of_financial_instruments`, `0001628280-26-049270::note::03::inventory`, `0001628280-26-049270::note::04::property_plant_and_equipment_net` (+11) |
| `stage2-060` | TSLA | accounting | 2 | 5 | -3 | Equity Incentive Plans | - | `0001628280-26-049270::note::00::summary_of_significant_accounting_policies`, `0001628280-26-049270::note::01::fair_value_of_financial_instruments`, `0001628280-26-049270::note::04::property_plant_and_equipment_net` (+11) |
| `stage2-018` | LITE | segment | 1 | 3 | -2 | Part I, Item 2 | - | `0001628280-26-030777::note::01::recently_issued_accounting_pronouncements`, `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::04::cash_cash_equivalents_and_short_term_investments` (+13) |
| `stage2-034` | ORCL | semantic outlook | 1 | 3 | -2 | Part I, Item 2 | - | `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::04::notes_payable_and_other_borrowings`, `0001193125-26-101045::note::05::restructuring_activities` (+8) |
| `stage2-035` | ORCL | semantic outlook | 1 | 3 | -2 | Part I, Item 2 | - | `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::05::restructuring_activities`, `0001193125-26-101045::note::06::deferred_revenues` (+11) |
| `stage2-037` | ORCL | capital structure | 4 | 6 | -2 | LEASES AND OTHER COMMITMENTS | - | `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::03::fair_value_measurements`, `0001193125-26-101045::note::05::restructuring_activities` (+8) |
| `stage2-003` | AMD | accounting | 4 | 5 | -1 | Supplemental Financial Statement Information | - | `0000002488-26-000123::note::03::segment_reporting`, `0000002488-26-000123::note::05::goodwill_and_acquisition_related_intangibles_net`, `0000002488-26-000123::note::06::related_party_equity_joint_ventures` (+8) |
| … | | | | | | | 12 more regressed cases | |

## 11. Notable improvements

### Bm25 — improved (11)

| Case | Ticker | Type | Baseline rank | v2 rank | Δ (positive = closer) | Expected section | Top-20 parents gained | Top-20 parents lost |
|---|---|---|---:|---:|---:|---|---|---|
| `stage2-036` | ORCL | risk | 14 | 1 | 13 | Part II, Item 1A | - | `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::09::income_taxes`, `0001193125-26-101045::note::10::segment_information` (+7) |
| `stage2-020` | LITE | accounting | 4 | 1 | 3 | Restructuring and Related Charges (Reversals) | - | `0001628280-26-030777::note::01::recently_issued_accounting_pronouncements`, `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::05::fair_value_measurements` (+10) |
| `stage2-031` | ORCL | numeric / financial | 3 | 1 | 2 | Part I, Item 2 | - | `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::03::fair_value_measurements`, `0001193125-26-101045::note::05::restructuring_activities` (+10) |
| `stage2-003` | AMD | accounting | 5 | 4 | 1 | Supplemental Financial Statement Information | - | `0000002488-26-000123::note::00::the_company`, `0000002488-26-000123::note::03::segment_reporting`, `0000002488-26-000123::note::05::goodwill_and_acquisition_related_intangibles_net` (+8) |
| `stage2-010` | AMD | accounting | 3 | 2 | 1 | Common Stock and Stock-based Compensation | - | `0000002488-26-000123::note::00::the_company`, `0000002488-26-000123::note::01::basis_of_presentation_and_significant_accounting_p`, `0000002488-26-000123::note::02::supplemental_financial_statement_information` (+10) |
| `stage2-012` | LITE | numeric / financial | 2 | 1 | 1 | Part I, Item 2 | - | `0001628280-26-030777::note::01::recently_issued_accounting_pronouncements`, `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::05::fair_value_measurements` (+11) |
| `stage2-019` | LITE | capital structure | 4 | 3 | 1 | Debt | - | `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::05::fair_value_measurements`, `0001628280-26-030777::note::09::accumulated_other_comprehensive_income_loss` (+8) |
| `stage2-027` | NVDA | capital structure | 2 | 1 | 1 | Commitments and Contingencies | - | `0001045810-26-000075::note::00::summary_of_significant_accounting_policies`, `0001045810-26-000075::note::01::stock_based_compensation`, `0001045810-26-000075::note::02::net_income_per_share` (+11) |
| `stage2-030` | NVDA | capital structure | 3 | 2 | 1 | Part II, Item 2 | - | `0001045810-26-000075::note::00::summary_of_significant_accounting_policies`, `0001045810-26-000075::note::01::stock_based_compensation`, `0001045810-26-000075::note::03::intangible_assets_and_goodwill` (+8) |
| `stage2-042` | SNDK | numeric / financial | 2 | 1 | 1 | Part I, Item 2 | - | `0001628280-26-029401::note::00::organization_and_basis_of_presentation`, `0001628280-26-029401::note::01::recent_accounting_pronouncements`, `0001628280-26-029401::note::02::geographic_information_and_concentrations_of_risk` (+11) |
| `stage2-056` | TSLA | risk | 2 | 1 | 1 | Part II, Item 1A | - | `0001628280-26-049270::note::01::fair_value_of_financial_instruments`, `0001628280-26-049270::note::03::inventory`, `0001628280-26-049270::note::04::property_plant_and_equipment_net` (+8) |

### Bm25 — regressed (22)

| Case | Ticker | Type | Baseline rank | v2 rank | Δ (positive = closer) | Expected section | Top-20 parents gained | Top-20 parents lost |
|---|---|---|---:|---:|---:|---|---|---|
| `stage2-018` | LITE | segment | 3 | 7 | -4 | Part I, Item 2 | - | `0001628280-26-030777::note::01::recently_issued_accounting_pronouncements`, `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::04::cash_cash_equivalents_and_short_term_investments` (+13) |
| `stage2-058` | TSLA | segment | 2 | 6 | -4 | Segment Reporting and Information about Geographic Areas | - | `0001628280-26-049270::note::01::fair_value_of_financial_instruments`, `0001628280-26-049270::note::02::acquisition`, `0001628280-26-049270::note::03::inventory` (+11) |
| `stage2-034` | ORCL | semantic outlook | 1 | 4 | -3 | Part I, Item 2 | - | `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::04::notes_payable_and_other_borrowings`, `0001193125-26-101045::note::05::restructuring_activities` (+9) |
| `stage2-014` | LITE | semantic outlook | 1 | 3 | -2 | Part I, Item 2 | - | `0001628280-26-030777::note::01::recently_issued_accounting_pronouncements`, `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::04::cash_cash_equivalents_and_short_term_investments` (+13) |
| `stage2-032` | ORCL | numeric / financial | 1 | 3 | -2 | Part I, Item 2 | - | `0001193125-26-101045::note::01::insider_trading_arrangements`, `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::07::leases_and_other_commitments` (+11) |
| `stage2-038` | ORCL | segment | 1 | 3 | -2 | SEGMENT INFORMATION | - | `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::08::stockholders_equity`, `0001193125-26-101045::note::09::income_taxes` (+11) |
| `stage2-039` | ORCL | capital structure | 3 | 5 | -2 | Part I, Item 2 | - | `0001193125-26-101045::note::03::fair_value_measurements`, `0001193125-26-101045::note::04::notes_payable_and_other_borrowings`, `0001193125-26-101045::note::05::restructuring_activities` (+6) |
| `stage2-067` | VRT | capital structure | 1 | 3 | -2 | COMMITMENTS AND CONTINGENCIES | - | `0001628280-26-050609::note::00::description_of_business`, `0001628280-26-050609::note::03::revenue`, `0001628280-26-050609::note::04::restructuring_costs` (+9) |
| `stage2-001` | AMD | segment | 2 | 3 | -1 | Segment Reporting | - | `0000002488-26-000123::note::00::the_company`, `0000002488-26-000123::note::01::basis_of_presentation_and_significant_accounting_p`, `0000002488-26-000123::note::02::supplemental_financial_statement_information` (+11) |
| `stage2-008` | AMD | segment | 1 | 2 | -1 | Segment Reporting | - | `0000002488-26-000123::note::00::the_company`, `0000002488-26-000123::note::01::basis_of_presentation_and_significant_accounting_p`, `0000002488-26-000123::note::02::supplemental_financial_statement_information` (+10) |
| `stage2-009` | AMD | capital structure | 1 | 2 | -1 | Debt, Revolving Credit Facility and Commercial Paper Program | - | `0000002488-26-000123::note::00::the_company`, `0000002488-26-000123::note::01::basis_of_presentation_and_significant_accounting_p`, `0000002488-26-000123::note::03::segment_reporting` (+11) |
| `stage2-013` | LITE | accounting | 3 | 4 | -1 | Balance Sheet Details | - | `0001628280-26-030777::note::01::recently_issued_accounting_pronouncements`, `0001628280-26-030777::note::05::fair_value_measurements`, `0001628280-26-030777::note::08::debt` (+10) |
| … | | | | | | | 10 more regressed cases | |

### Vector — improved (9)

| Case | Ticker | Type | Baseline rank | v2 rank | Δ (positive = closer) | Expected section | Top-20 parents gained | Top-20 parents lost |
|---|---|---|---:|---:|---:|---|---|---|
| `stage2-055` | TSLA | semantic outlook | 3 | 1 | 2 | Part I, Item 2 | - | `0001628280-26-049270::note::03::inventory`, `0001628280-26-049270::note::04::property_plant_and_equipment_net`, `0001628280-26-049270::note::05::accrued_liabilities_and_other` (+11) |
| `stage2-004` | AMD | semantic outlook | 2 | 1 | 1 | Part I, Item 2 | - | `0000002488-26-000123::note::02::supplemental_financial_statement_information`, `0000002488-26-000123::note::03::segment_reporting`, `0000002488-26-000123::note::05::goodwill_and_acquisition_related_intangibles_net` (+7) |
| `stage2-029` | NVDA | capital structure | 5 | 4 | 1 | Debt | - | `0001045810-26-000075::note::00::summary_of_significant_accounting_policies`, `0001045810-26-000075::note::01::stock_based_compensation`, `0001045810-26-000075::note::02::net_income_per_share` (+8) |
| `stage2-036` | ORCL | risk | 3 | 2 | 1 | Part II, Item 1A | `0001193125-26-101045::note::09::income_taxes` | `0001193125-26-101045::note::01::insider_trading_arrangements`, `0001193125-26-101045::note::10::segment_information`, `0001193125-26-389274::note::01::insider_trading_arrangements` (+4) |
| `stage2-047` | SNDK | capital structure | 2 | 1 | 1 | Related Parties and Related Commitments and Contingencies | - | `0001628280-26-029401::note::02::geographic_information_and_concentrations_of_risk`, `0001628280-26-029401::note::03::revenue`, `0001628280-26-029401::note::04::supplemental_financial_statement_data` (+11) |
| `stage2-052` | TSLA | numeric / financial | 2 | 1 | 1 | Part I, Item 2 | - | `0001628280-26-049270::note::03::inventory`, `0001628280-26-049270::note::04::property_plant_and_equipment_net`, `0001628280-26-049270::note::05::accrued_liabilities_and_other` (+10) |
| `stage2-054` | TSLA | semantic outlook | 2 | 1 | 1 | Part I, Item 2 | - | `0001628280-26-049270::note::00::summary_of_significant_accounting_policies`, `0001628280-26-049270::note::01::fair_value_of_financial_instruments`, `0001628280-26-049270::note::03::inventory` (+11) |
| `stage2-056` | TSLA | risk | 2 | 1 | 1 | Part II, Item 1A | - | `0001628280-26-049270::note::01::fair_value_of_financial_instruments`, `0001628280-26-049270::note::07::debt`, `0001628280-26-049270::note::08::equity_incentive_plans` (+9) |
| `stage2-068` | VRT | segment | 3 | 2 | 1 | SEGMENT INFORMATION | - | `0001628280-26-050609::note::01::basis_of_presentation_and_summary_of_significant_a`, `0001628280-26-050609::note::02::acquisitions`, `0001628280-26-050609::note::04::restructuring_costs` (+12) |

### Vector — regressed (32)

| Case | Ticker | Type | Baseline rank | v2 rank | Δ (positive = closer) | Expected section | Top-20 parents gained | Top-20 parents lost |
|---|---|---|---:|---:|---:|---|---|---|
| `stage2-063` | VRT | accounting | 7 | 20 | -13 | OTHER FINANCIAL INFORMATION | - | `0001628280-26-050609::note::01::basis_of_presentation_and_summary_of_significant_a`, `0001628280-26-050609::note::02::acquisitions`, `0001628280-26-050609::note::03::revenue` (+11) |
| `stage2-010` | AMD | accounting | 6 | 17 | -11 | Common Stock and Stock-based Compensation | - | `0000002488-26-000123::note::02::supplemental_financial_statement_information`, `0000002488-26-000123::note::03::segment_reporting`, `0000002488-26-000123::note::05::goodwill_and_acquisition_related_intangibles_net` (+9) |
| `stage2-066` | VRT | risk | 8 | 19 | -11 | Part II, Item 1A | - | `0001628280-26-050609::note::02::acquisitions`, `0001628280-26-050609::note::03::revenue`, `0001628280-26-050609::note::04::restructuring_costs` (+9) |
| `stage2-040` | ORCL | legal | 5 | 13 | -8 | LEGAL PROCEEDINGS | `0001193125-26-101045::note::05::restructuring_activities`, `0001193125-26-101045::note::09::income_taxes`, `0001193125-26-389274::note::09::income_taxes` | `0001193125-26-101045::note::07::leases_and_other_commitments`, `0001193125-26-101045::note::10::segment_information`, `0001193125-26-389274::note::01::insider_trading_arrangements` (+6) |
| `stage2-061` | VRT | numeric / financial | 5 | 13 | -8 | REVENUE | - | `0001628280-26-050609::note::01::basis_of_presentation_and_summary_of_significant_a`, `0001628280-26-050609::note::02::acquisitions`, `0001628280-26-050609::note::04::restructuring_costs` (+12) |
| `stage2-038` | ORCL | segment | 2 | 9 | -7 | SEGMENT INFORMATION | - | `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::05::restructuring_activities`, `0001193125-26-101045::note::06::deferred_revenues` (+11) |
| `stage2-067` | VRT | capital structure | 4 | 11 | -7 | COMMITMENTS AND CONTINGENCIES | - | `0001628280-26-050609::note::01::basis_of_presentation_and_summary_of_significant_a`, `0001628280-26-050609::note::02::acquisitions`, `0001628280-26-050609::note::03::revenue` (+11) |
| `stage2-003` | AMD | accounting | 5 | 11 | -6 | Supplemental Financial Statement Information | - | `0000002488-26-000123::note::05::goodwill_and_acquisition_related_intangibles_net`, `0000002488-26-000123::note::06::related_party_equity_joint_ventures`, `0000002488-26-000123::note::07::financial_instruments` (+8) |
| `stage2-013` | LITE | accounting | 3 | 8 | -5 | Balance Sheet Details | - | `0001628280-26-030777::note::01::recently_issued_accounting_pronouncements`, `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::04::cash_cash_equivalents_and_short_term_investments` (+10) |
| `stage2-070` | VRT | accounting | 5 | 10 | -5 | ACQUISITIONS | - | `0001628280-26-050609::note::01::basis_of_presentation_and_summary_of_significant_a`, `0001628280-26-050609::note::03::revenue`, `0001628280-26-050609::note::04::restructuring_costs` (+11) |
| `stage2-007` | AMD | legal | 6 | 10 | -4 | Commitments and Contingencies | - | `0000002488-26-000123::note::02::supplemental_financial_statement_information`, `0000002488-26-000123::note::03::segment_reporting`, `0000002488-26-000123::note::05::goodwill_and_acquisition_related_intangibles_net` (+8) |
| `stage2-049` | SNDK | capital structure | 5 | 9 | -4 | Debt | - | `0001628280-26-029401::note::01::recent_accounting_pronouncements`, `0001628280-26-029401::note::02::geographic_information_and_concentrations_of_risk`, `0001628280-26-029401::note::03::revenue` (+11) |
| … | | | | | | | 20 more regressed cases | |

### Hybrid — improved (14)

| Case | Ticker | Type | Baseline rank | v2 rank | Δ (positive = closer) | Expected section | Top-20 parents gained | Top-20 parents lost |
|---|---|---|---:|---:|---:|---|---|---|
| `stage2-046` | SNDK | risk | 7 | 2 | 5 | Part II, Item 1A | - | `0001628280-26-029401::note::01::recent_accounting_pronouncements`, `0001628280-26-029401::note::02::geographic_information_and_concentrations_of_risk`, `0001628280-26-029401::note::03::revenue` (+11) |
| `stage2-036` | ORCL | risk | 6 | 2 | 4 | Part II, Item 1A | `0001193125-26-101045::note::05::restructuring_activities`, `0001193125-26-101045::part_ii_item_1a`, `0001193125-26-389274::note::03::fair_value_measurements` | `0001193125-26-101045::note::10::segment_information`, `0001193125-26-101045::note::12::legal_proceedings`, `0001193125-26-101045::part_i_item_4` (+6) |
| `stage2-020` | LITE | accounting | 4 | 1 | 3 | Restructuring and Related Charges (Reversals) | - | `0001628280-26-030777::note::01::recently_issued_accounting_pronouncements`, `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::05::fair_value_measurements` (+10) |
| `stage2-019` | LITE | capital structure | 3 | 1 | 2 | Debt | - | `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::04::cash_cash_equivalents_and_short_term_investments`, `0001628280-26-030777::note::09::accumulated_other_comprehensive_income_loss` (+8) |
| `stage2-050` | SNDK | legal | 7 | 5 | 2 | Legal Proceedings | - | `0001628280-26-029401::note::04::supplemental_financial_statement_data`, `0001628280-26-029401::note::05::fair_value_measurements_and_investments`, `0001628280-26-029401::note::07::debt` (+7) |
| `stage2-010` | AMD | accounting | 3 | 2 | 1 | Common Stock and Stock-based Compensation | - | `0000002488-26-000123::note::02::supplemental_financial_statement_information`, `0000002488-26-000123::note::05::goodwill_and_acquisition_related_intangibles_net`, `0000002488-26-000123::note::06::related_party_equity_joint_ventures` (+9) |
| `stage2-016` | LITE | risk | 2 | 1 | 1 | Part II, Item 1A | - | `0001628280-26-030777::note::01::recently_issued_accounting_pronouncements`, `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::04::cash_cash_equivalents_and_short_term_investments` (+13) |
| `stage2-023` | NVDA | accounting | 2 | 1 | 1 | Commitments and Contingencies | - | `0001045810-26-000075::note::00::summary_of_significant_accounting_policies`, `0001045810-26-000075::note::01::stock_based_compensation`, `0001045810-26-000075::note::02::net_income_per_share` (+12) |
| `stage2-029` | NVDA | capital structure | 3 | 2 | 1 | Debt | - | `0001045810-26-000075::note::01::stock_based_compensation`, `0001045810-26-000075::note::02::net_income_per_share`, `0001045810-26-000075::note::07::derivative_financial_instruments` (+9) |
| `stage2-039` | ORCL | capital structure | 2 | 1 | 1 | Part I, Item 2 | - | `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::03::fair_value_measurements`, `0001193125-26-101045::note::05::restructuring_activities` (+7) |
| `stage2-056` | TSLA | risk | 2 | 1 | 1 | Part II, Item 1A | - | `0001628280-26-049270::note::01::fair_value_of_financial_instruments`, `0001628280-26-049270::note::03::inventory`, `0001628280-26-049270::note::07::debt` (+8) |
| `stage2-059` | TSLA | capital structure | 3 | 2 | 1 | Debt | - | `0001628280-26-049270::note::01::fair_value_of_financial_instruments`, `0001628280-26-049270::note::03::inventory`, `0001628280-26-049270::note::04::property_plant_and_equipment_net` (+8) |
| … | | | | | | | 2 more improved cases | |

### Hybrid — regressed (24)

| Case | Ticker | Type | Baseline rank | v2 rank | Δ (positive = closer) | Expected section | Top-20 parents gained | Top-20 parents lost |
|---|---|---|---:|---:|---:|---|---|---|
| `stage2-049` | SNDK | capital structure | 5 | 16 | -11 | Debt | `0001628280-26-029401::part_i_item_4` | `0001628280-26-029401::note::01::recent_accounting_pronouncements`, `0001628280-26-029401::note::02::geographic_information_and_concentrations_of_risk`, `0001628280-26-029401::note::03::revenue` (+9) |
| `stage2-043` | SNDK | accounting | 4 | 10 | -6 | Supplemental Financial Statement Data | - | `0001628280-26-029401::note::01::recent_accounting_pronouncements`, `0001628280-26-029401::note::02::geographic_information_and_concentrations_of_risk`, `0001628280-26-029401::note::03::revenue` (+12) |
| `stage2-013` | LITE | accounting | 3 | 8 | -5 | Balance Sheet Details | - | `0001628280-26-030777::note::01::recently_issued_accounting_pronouncements`, `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::04::cash_cash_equivalents_and_short_term_investments` (+12) |
| `stage2-040` | ORCL | legal | 4 | 8 | -4 | LEGAL PROCEEDINGS | `0001193125-26-101045::note::05::restructuring_activities` | `0001193125-26-101045::note::01::insider_trading_arrangements`, `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::07::leases_and_other_commitments` (+6) |
| `stage2-001` | AMD | segment | 1 | 4 | -3 | Segment Reporting | - | `0000002488-26-000123::note::01::basis_of_presentation_and_significant_accounting_p`, `0000002488-26-000123::note::02::supplemental_financial_statement_information`, `0000002488-26-000123::note::06::related_party_equity_joint_ventures` (+10) |
| `stage2-058` | TSLA | segment | 1 | 4 | -3 | Segment Reporting and Information about Geographic Areas | - | `0001628280-26-049270::note::01::fair_value_of_financial_instruments`, `0001628280-26-049270::note::03::inventory`, `0001628280-26-049270::note::04::property_plant_and_equipment_net` (+11) |
| `stage2-060` | TSLA | accounting | 2 | 5 | -3 | Equity Incentive Plans | - | `0001628280-26-049270::note::00::summary_of_significant_accounting_policies`, `0001628280-26-049270::note::01::fair_value_of_financial_instruments`, `0001628280-26-049270::note::04::property_plant_and_equipment_net` (+11) |
| `stage2-018` | LITE | segment | 1 | 3 | -2 | Part I, Item 2 | - | `0001628280-26-030777::note::01::recently_issued_accounting_pronouncements`, `0001628280-26-030777::note::03::business_combinations`, `0001628280-26-030777::note::04::cash_cash_equivalents_and_short_term_investments` (+13) |
| `stage2-034` | ORCL | semantic outlook | 1 | 3 | -2 | Part I, Item 2 | - | `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::04::notes_payable_and_other_borrowings`, `0001193125-26-101045::note::05::restructuring_activities` (+8) |
| `stage2-035` | ORCL | semantic outlook | 1 | 3 | -2 | Part I, Item 2 | - | `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::05::restructuring_activities`, `0001193125-26-101045::note::06::deferred_revenues` (+11) |
| `stage2-037` | ORCL | capital structure | 4 | 6 | -2 | LEASES AND OTHER COMMITMENTS | - | `0001193125-26-101045::note::02::basis_of_presentation_recent_accounting_pronouncem`, `0001193125-26-101045::note::03::fair_value_measurements`, `0001193125-26-101045::note::05::restructuring_activities` (+8) |
| `stage2-003` | AMD | accounting | 4 | 5 | -1 | Supplemental Financial Statement Information | - | `0000002488-26-000123::note::03::segment_reporting`, `0000002488-26-000123::note::05::goodwill_and_acquisition_related_intangibles_net`, `0000002488-26-000123::note::06::related_party_equity_joint_ventures` (+8) |
| … | | | | | | | 12 more regressed cases | |

## 12. Unchanged-baseline noise check

Occurrence counts of typically irrelevant sections inside non-ground-truth Top-5 results (inspection only).

```json
{
  "bm25": {
    "leases": 4,
    "legal proceedings": 2
  },
  "hybrid": {
    "leases": 5,
    "legal proceedings": 3
  },
  "vector": {
    "leases": 3,
    "legal proceedings": 2
  }
}
```

## 13. Conclusion

- Verdict: **regressed**
- Rationale: Hybrid Recall@5 Δ=-0.029, Hybrid MRR Δ=-0.048; Recall@20 did not hold at 0.971 for Hybrid.
- Duplicate crowding detected: yes (analysis only — no retrieval or chunking parameters were changed).
