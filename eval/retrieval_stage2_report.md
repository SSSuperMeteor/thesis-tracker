# Stage 2 Retrieval Benchmark

Generated: 2026-09-11T23:02:43.731171+00:00

## 1. Corpus overview

Tickers: 7
; valid chunks: 181
; valid benchmark cases: 70.

| Ticker | Form | Accession | Status | Valid | Invalid |
|---|---|---|---|---:|---:|
| AMD | 10-Q | 0000002488-26-000123 | success | 22 | 0 |
| LITE | 10-Q | 0001628280-26-030777 | success | 19 | 0 |
| NVDA | 10-Q | 0001045810-26-000075 | success | 21 | 0 |
| ORCL | 10-Q | 0001193125-26-389274 | success | 21 | 0 |
| ORCL | 10-Q | 0001193125-26-101045 | success | 22 | 0 |
| SNDK | 10-Q | 0001628280-26-029401 | success | 25 | 0 |
| TSLA | 10-Q | 0001628280-26-049270 | success | 26 | 0 |
| VRT | 10-Q | 0001628280-26-050609 | success | 25 | 0 |

## 2. Embedding configuration

- Model: `qwen3-vl-embedding`
- Dimension: 1024
- Collection: `sec_chunks_qwen3_vl_embedding_1024`
- Retrieval depth: Top 20
- RRF k: 60

## 3. Ground-truth overview

- Source cases: 70
- Valid cases: 70
- Invalid cases excluded from denominator: 0
- Cases per ticker: `{"AMD": 10, "LITE": 10, "NVDA": 10, "ORCL": 10, "SNDK": 10, "TSLA": 10, "VRT": 10}`
- Cases per question type: `{"accounting": 11, "capital structure": 14, "legal": 3, "numeric / financial": 13, "risk": 7, "segment": 8, "semantic outlook": 14}`

### Invalid ground truth

None.

## 4. Overall metrics

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 70 | 0.614 | 0.857 | 0.957 | 0.957 | 1.000 | 0.738 |
| Vector | 70 | 0.471 | 0.771 | 0.914 | 0.971 | 1.000 | 0.639 |
| Hybrid | 70 | 0.600 | 0.843 | 0.929 | 0.986 | 1.000 | 0.735 |

## 5. Ticker-level metrics

### AMD

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 10 | 0.700 | 0.900 | 1.000 | 1.000 | 1.000 | 0.803 |
| Vector | 10 | 0.600 | 0.700 | 0.800 | 1.000 | 1.000 | 0.703 |
| Hybrid | 10 | 0.700 | 0.900 | 1.000 | 1.000 | 1.000 | 0.808 |

Best: hybrid

### LITE

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 10 | 0.500 | 0.800 | 1.000 | 1.000 | 1.000 | 0.667 |
| Vector | 10 | 0.600 | 1.000 | 1.000 | 1.000 | 1.000 | 0.733 |
| Hybrid | 10 | 0.600 | 0.900 | 1.000 | 1.000 | 1.000 | 0.742 |

Best: hybrid

### NVDA

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 10 | 0.700 | 1.000 | 1.000 | 1.000 | 1.000 | 0.817 |
| Vector | 10 | 0.800 | 0.900 | 1.000 | 1.000 | 1.000 | 0.870 |
| Hybrid | 10 | 0.700 | 1.000 | 1.000 | 1.000 | 1.000 | 0.833 |

Best: vector

### ORCL

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 10 | 0.500 | 0.800 | 0.900 | 0.900 | 1.000 | 0.632 |
| Vector | 10 | 0.400 | 0.900 | 1.000 | 1.000 | 1.000 | 0.637 |
| Hybrid | 10 | 0.400 | 0.700 | 0.900 | 1.000 | 1.000 | 0.600 |

Best: vector

### SNDK

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 10 | 0.600 | 0.800 | 1.000 | 1.000 | 1.000 | 0.745 |
| Vector | 10 | 0.400 | 0.500 | 0.800 | 0.800 | 1.000 | 0.533 |
| Hybrid | 10 | 0.600 | 0.600 | 0.800 | 1.000 | 1.000 | 0.674 |

Best: bm25

### TSLA

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 10 | 0.700 | 1.000 | 1.000 | 1.000 | 1.000 | 0.833 |
| Vector | 10 | 0.100 | 0.900 | 1.000 | 1.000 | 1.000 | 0.470 |
| Hybrid | 10 | 0.700 | 1.000 | 1.000 | 1.000 | 1.000 | 0.833 |

Best: bm25, hybrid

### VRT

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 10 | 0.600 | 0.700 | 0.800 | 0.800 | 1.000 | 0.672 |
| Vector | 10 | 0.400 | 0.500 | 0.800 | 1.000 | 1.000 | 0.525 |
| Hybrid | 10 | 0.500 | 0.800 | 0.800 | 0.900 | 1.000 | 0.655 |

Best: vector

## 6. Question-type metrics

### accounting

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 11 | 0.364 | 0.636 | 0.909 | 0.909 | 1.000 | 0.520 |
| Vector | 11 | 0.273 | 0.455 | 0.818 | 1.000 | 1.000 | 0.439 |
| Hybrid | 11 | 0.182 | 0.636 | 0.909 | 1.000 | 1.000 | 0.458 |

### capital structure

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 14 | 0.429 | 0.786 | 1.000 | 1.000 | 1.000 | 0.613 |
| Vector | 14 | 0.286 | 0.786 | 1.000 | 1.000 | 1.000 | 0.558 |
| Hybrid | 14 | 0.429 | 0.857 | 1.000 | 1.000 | 1.000 | 0.639 |

### legal

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 3 | 0.333 | 1.000 | 1.000 | 1.000 | 1.000 | 0.611 |
| Vector | 3 | 0.000 | 0.000 | 0.333 | 0.667 | 1.000 | 0.148 |
| Hybrid | 3 | 0.000 | 0.333 | 0.667 | 1.000 | 1.000 | 0.298 |

### numeric / financial

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 13 | 0.692 | 0.923 | 1.000 | 1.000 | 1.000 | 0.814 |
| Vector | 13 | 0.692 | 0.923 | 1.000 | 1.000 | 1.000 | 0.797 |
| Hybrid | 13 | 0.846 | 1.000 | 1.000 | 1.000 | 1.000 | 0.897 |

### risk

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 7 | 0.571 | 0.714 | 0.714 | 0.714 | 1.000 | 0.663 |
| Vector | 7 | 0.286 | 0.714 | 0.714 | 0.857 | 1.000 | 0.478 |
| Hybrid | 7 | 0.286 | 0.571 | 0.571 | 0.857 | 1.000 | 0.486 |

### segment

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 8 | 0.625 | 1.000 | 1.000 | 1.000 | 1.000 | 0.792 |
| Vector | 8 | 0.500 | 0.875 | 1.000 | 1.000 | 1.000 | 0.698 |
| Hybrid | 8 | 0.875 | 1.000 | 1.000 | 1.000 | 1.000 | 0.938 |

### semantic outlook

| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bm25 | 14 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| Vector | 14 | 0.786 | 1.000 | 1.000 | 1.000 | 1.000 | 0.881 |
| Hybrid | 14 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

### Type comparison

- Best Hybrid type(s): ['semantic outlook']
- Worst Hybrid type(s): ['risk']
- Strongest types by method (Recall@5, then MRR): `{"bm25": ["semantic outlook"], "hybrid": ["semantic outlook"], "vector": ["semantic outlook"]}`
- Types won by each method: `{"bm25": ["legal", "semantic outlook"], "hybrid": ["accounting", "capital structure", "numeric / financial", "segment", "semantic outlook"], "vector": ["risk"]}`
- Hybrid MRR improved over the best single route: ['capital structure', 'numeric / financial', 'segment']
- Hybrid worsened on Recall@5 or MRR: ['accounting', 'legal', 'risk']

## 7. Ranking vs recall diagnosis

- Hybrid Top 20 hit but Top 5 miss: 5 (7.1%)
- Hybrid Top 20 miss: 0 (0.0%)
- Hybrid Top 10 hit but Top 5 miss: 4
- Hybrid Top 5 hit but Top 1 miss: 23

## 8. Ten representative failure cases

| Ticker | Type | Question | Expected section/chunk | BM25 | Vector | Hybrid | Category | Diagnosis | Hybrid Top 5 |
|---|---|---|---|---:|---:|---:|---|---|---|
| VRT | risk | What material risk factor changes does Vertiv report for supply chain, customers, and demand? | Part II, Item 1A / `0001628280-26-050609::part_ii_item_1a` | 15 | 8 | 11 | keyword noise | Topical operating and commitment sections match supply, customer, and demand terms more strongly than the risk section. | 1. `0001628280-26-050609::part_i_item_2` (Part I, Item 2); 2. `0001628280-26-050609::note::12::commitments_and_contingencies` (COMMITMENTS AND CONTINGENCIES); 3. `0001628280-26-050609::part_i_item_1` (Part I, Item 1); 4. `0001628280-26-050609::part_ii_item_1` (Part II, Item 1); 5. `0001628280-26-050609::note::00::description_of_business` (DESCRIPTION OF BUSINESS) |
| VRT | accounting | What inventory balances did Vertiv report in other financial information? | OTHER FINANCIAL INFORMATION / `0001628280-26-050609::note::07::other_financial_information` | 14 | 7 | 8 | metadata limitation | The inventory table is embedded in a generically titled note, weakening both lexical and semantic ranking signals. | 1. `0001628280-26-050609::part_i_item_1` (Part I, Item 1); 2. `0001628280-26-050609::part_i_item_2` (Part I, Item 2); 3. `0001628280-26-050609::note::12::commitments_and_contingencies` (COMMITMENTS AND CONTINGENCIES); 4. `0001628280-26-050609::note::02::acquisitions` (ACQUISITIONS); 5. `0001628280-26-050609::part_ii_item_1` (Part II, Item 1) |
| SNDK | risk | What material changes to Sandisk's risk factors were disclosed? | Part II, Item 1A / `0001628280-26-029401::part_ii_item_1a` | 1 | 18 | 7 | semantic false positive | Vector neighbors lowered a strong lexical result during RRF fusion. | 1. `0001628280-26-029401::note::00::organization_and_basis_of_presentation` (Organization and Basis of Presentation); 2. `0001628280-26-029401::part_i_item_2` (Part I, Item 2); 3. `0001628280-26-029401::part_i_item_1` (Part I, Item 1); 4. `0001628280-26-029401::note::09::related_parties_and_related_commitments_and_contin` (Related Parties and Related Commitments and Contingencies); 5. `0001628280-26-029401::note::05::fair_value_measurements_and_investments` (Fair Value Measurements and Investments) |
| SNDK | legal | What legal proceedings does Sandisk disclose? | Legal Proceedings / `0001628280-26-029401::note::14::legal_proceedings` | 2 | 13 | 7 | filing has weak evidence | The expected section is a short cross-reference or no-change disclosure, so richer topical sections rank above it. | 1. `0001628280-26-029401::part_i_item_1` (Part I, Item 1); 2. `0001628280-26-029401::part_ii_item_1` (Part II, Item 1); 3. `0001628280-26-029401::note::09::related_parties_and_related_commitments_and_contin` (Related Parties and Related Commitments and Contingencies); 4. `0001628280-26-029401::part_i_item_2` (Part I, Item 2); 5. `0001628280-26-029401::part_ii_item_6` (Part II, Item 6) |
| ORCL | risk | What changes to Oracle's risk factors were reported in this quarter? | Part II, Item 1A / `0001193125-26-389274::part_ii_item_1a` | 14 | 3 | 6 | filing has weak evidence | The expected section is a short cross-reference or no-change disclosure, so richer topical sections rank above it. | 1. `0001193125-26-389274::part_i_item_2` (Part I, Item 2); 2. `0001193125-26-101045::part_i_item_1` (Part I, Item 1); 3. `0001193125-26-101045::part_i_item_2` (Part I, Item 2); 4. `0001193125-26-389274::part_i_item_1` (Part I, Item 1); 5. `0001193125-26-389274::note::02::basis_of_presentation_recent_accounting_pronouncem` (BASIS OF PRESENTATION, RECENT ACCOUNTING PRONOUNCEMENTS AND OTHER) |
| SNDK | capital structure | What debt balances, interest rates, and maturities does Sandisk report? | Debt / `0001628280-26-029401::note::07::debt` | 4 | 5 | 5 | wrong section promoted | Broad filing sections accumulated stronger cross-route support than the more specific expected note. | 1. `0001628280-26-029401::part_i_item_1` (Part I, Item 1); 2. `0001628280-26-029401::part_i_item_2` (Part I, Item 2); 3. `0001628280-26-029401::note::00::organization_and_basis_of_presentation` (Organization and Basis of Presentation); 4. `0001628280-26-029401::note::09::related_parties_and_related_commitments_and_contin` (Related Parties and Related Commitments and Contingencies); 5. `0001628280-26-029401::note::07::debt` (Debt) |
| ORCL | capital structure | What lease liabilities and other contractual commitments does Oracle disclose? | LEASES AND OTHER COMMITMENTS / `0001193125-26-389274::note::07::leases_and_other_commitments` | 4 | 2 | 4 | ground-truth ambiguity | A matching section from another filing ranks first; the query does not identify the target filing period. | 1. `0001193125-26-101045::note::07::leases_and_other_commitments` (LEASES AND OTHER COMMITMENTS); 2. `0001193125-26-389274::part_i_item_1` (Part I, Item 1); 3. `0001193125-26-101045::part_i_item_1` (Part I, Item 1); 4. `0001193125-26-389274::note::07::leases_and_other_commitments` (LEASES AND OTHER COMMITMENTS); 5. `0001193125-26-389274::part_i_item_2` (Part I, Item 2) |
| ORCL | legal | What material legal proceedings and contingencies does Oracle disclose? | LEGAL PROCEEDINGS / `0001193125-26-389274::note::12::legal_proceedings` | 3 | 5 | 4 | wrong section promoted | Broad filing sections accumulated stronger cross-route support than the more specific expected note. | 1. `0001193125-26-389274::part_i_item_1` (Part I, Item 1); 2. `0001193125-26-101045::part_i_item_1` (Part I, Item 1); 3. `0001193125-26-389274::part_ii_item_1` (Part II, Item 1); 4. `0001193125-26-389274::note::12::legal_proceedings` (LEGAL PROCEEDINGS); 5. `0001193125-26-101045::note::12::legal_proceedings` (LEGAL PROCEEDINGS) |
| LITE | accounting | What restructuring charges and reversals did Lumentum recognize? | Restructuring and Related Charges (Reversals) / `0001628280-26-030777::note::10::restructuring_and_related_charges_reversals` | 4 | 3 | 4 | wrong section promoted | Broad filing sections accumulated stronger cross-route support than the more specific expected note. | 1. `0001628280-26-030777::part_i_item_2` (Part I, Item 2); 2. `0001628280-26-030777::note::06::balance_sheet_details` (Balance Sheet Details); 3. `0001628280-26-030777::part_i_item_1` (Part I, Item 1); 4. `0001628280-26-030777::note::10::restructuring_and_related_charges_reversals` (Restructuring and Related Charges (Reversals)); 5. `0001628280-26-030777::part_ii_item_1a` (Part II, Item 1A) |
| SNDK | accounting | What inventory and working capital balances did Sandisk report? | Supplemental Financial Statement Data / `0001628280-26-029401::note::04::supplemental_financial_statement_data` | 5 | 4 | 4 | wrong section promoted | Broad filing sections accumulated stronger cross-route support than the more specific expected note. | 1. `0001628280-26-029401::part_i_item_2` (Part I, Item 2); 2. `0001628280-26-029401::part_i_item_1` (Part I, Item 1); 3. `0001628280-26-029401::note::09::related_parties_and_related_commitments_and_contin` (Related Parties and Related Commitments and Contingencies); 4. `0001628280-26-029401::note::04::supplemental_financial_statement_data` (Supplemental Financial Statement Data); 5. `0001628280-26-029401::note::00::organization_and_basis_of_presentation` (Organization and Basis of Presentation) |

## 9. Reranker recommendation

- Recommendation: NO
- Primary issue: already good enough
- Evidence: Hybrid Recall@5=0.929, MRR=0.735, and only 5/70 cases sit at ranks 6-20.

### Potentially noisy sections in non-ground-truth Top 5 results

These are occurrence counts for inspection, not automatic error labels.

```json
{
  "bm25": {
    "leases": 3,
    "legal proceedings": 1
  },
  "hybrid": {
    "leases": 3,
    "legal proceedings": 1
  },
  "vector": {
    "leases": 5,
    "legal proceedings": 1
  }
}
```

## 10. Next recommended action

Keep the current retrieval stack and investigate only the remaining outlier cases.
