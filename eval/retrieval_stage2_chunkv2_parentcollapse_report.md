# Stage 2 Retrieval — Chunking v2 Parent Collapse

## Configuration

- Dynamic child pool: `min(N, max(50, ceil(N * 0.50)))`
- Parent candidate depth: 20
- RRF k: 60; equal route weights
- Parent signal: best child only; no multi-hit bonus

## Metrics

### v1

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 70 | 0.614 | 0.857 | 0.957 | 0.957 | 1.000 | 0.738 |
| Vector | 70 | 0.471 | 0.771 | 0.914 | 0.971 | 1.000 | 0.639 |
| Hybrid | 70 | 0.600 | 0.843 | 0.929 | 0.986 | 1.000 | 0.735 |

### raw_v2

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 70 | 0.557 | 0.814 | 0.914 | 0.943 | 0.943 | 0.692 |
| Vector | 70 | 0.443 | 0.643 | 0.786 | 0.871 | 0.971 | 0.574 |
| Hybrid | 70 | 0.529 | 0.814 | 0.900 | 0.957 | 0.971 | 0.687 |

### parent_collapse

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 70 | 0.557 | 0.886 | 0.943 | 0.971 | 1.000 | 0.716 |
| Vector | 70 | 0.443 | 0.757 | 0.886 | 0.971 | 0.986 | 0.622 |
| Hybrid | 70 | 0.514 | 0.843 | 0.929 | 0.986 | 1.000 | 0.692 |

## Crowding

| Version | Method | Mean distinct Top-5 | Mean distinct Top-20 | Duplicate slots Top-5 | Duplicate slots Top-20 |
|---|---|---:|---:|---:|---:|
| raw_v2 | bm25 | 3.09 | 6.96 | 134 | 913 |
| raw_v2 | vector | 2.99 | 6.47 | 141 | 947 |
| raw_v2 | hybrid | 2.94 | 6.79 | 144 | 925 |
| parent_collapse | bm25 | 5.00 | 14.01 | 0 | 0 |
| parent_collapse | vector | 5.00 | 13.86 | 0 | 0 |
| parent_collapse | hybrid | 5.00 | 16.63 | 0 | 0 |

## Displacement Cases

| Case | Method | v1 rank / @5 / @20 | raw-v2 rank / @5 / @20 | collapse rank / @5 / @20 |
|---|---|---|---|---|
| stage2-013 | bm25 | 3 / Y / Y | 4 / Y / Y | 2 / Y / Y |
| stage2-013 | vector | 3 / Y / Y | 8 / N / Y | 2 / Y / Y |
| stage2-013 | hybrid | 3 / Y / Y | 8 / N / Y | 2 / Y / Y |
| stage2-037 | bm25 | 4 / Y / Y | 5 / Y / Y | 4 / Y / Y |
| stage2-037 | vector | 2 / Y / Y | 5 / Y / Y | 4 / Y / Y |
| stage2-037 | hybrid | 4 / Y / Y | 6 / N / Y | 4 / Y / Y |
| stage2-040 | bm25 | 3 / Y / Y | 3 / Y / Y | 3 / Y / Y |
| stage2-040 | vector | 5 / Y / Y | 13 / N / Y | 10 / N / Y |
| stage2-040 | hybrid | 4 / Y / Y | 8 / N / Y | 6 / N / Y |
| stage2-043 | bm25 | 5 / Y / Y | miss / N / N | 6 / N / Y |
| stage2-043 | vector | 4 / Y / Y | 4 / Y / Y | 3 / Y / Y |
| stage2-043 | hybrid | 4 / Y / Y | 10 / N / Y | 4 / Y / Y |
| stage2-049 | bm25 | 4 / Y / Y | miss / N / N | 8 / N / Y |
| stage2-049 | vector | 5 / Y / Y | 9 / N / Y | 5 / Y / Y |
| stage2-049 | hybrid | 5 / Y / Y | 16 / N / Y | 6 / N / Y |
| stage2-063 | bm25 | 14 / N / Y | miss / N / N | 12 / N / Y |
| stage2-063 | vector | 7 / N / Y | 20 / N / Y | 6 / N / Y |
| stage2-063 | hybrid | 8 / N / Y | miss / N / N | 5 / Y / Y |
| stage2-066 | bm25 | 15 / N / Y | miss / N / N | 15 / N / Y |
| stage2-066 | vector | 8 / N / Y | 19 / N / Y | 8 / N / Y |
| stage2-066 | hybrid | 11 / N / Y | miss / N / N | 9 / N / Y |

## Notes

The 70 frozen questions and logical-parent ground truth were unchanged. Chunk size, overlap, embedding, BM25, RRF k, and route weights were unchanged.
