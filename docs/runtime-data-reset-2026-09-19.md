# Runtime Filing Data Reset — 2026-09-19

## Boundary

This reset retains source code, tests, Git history, configuration, skills,
input definitions, and human labels. It removes runtime SEC filings and every
result, report, cache, or index computed from the old filing corpus.

## Before reset

### SQLite

| Path | Bytes | Documents | Chunks |
|---|---:|---:|---:|
| `data/corpus.db` | 8,990,720 | 16 | 2,096 |
| `data/corpus_pre_chunkv2.db` | 2,457,600 | 8 | 181 |

### Raw filings

There were 15 files totaling 5,234,014 bytes:

| Path | Bytes |
|---|---:|
| `data/raw/AAPL_10-Q_2026-06-27_0000320193-26-000020.txt` | 137,491 |
| `data/raw/AMD_10-Q_2026-06-27_0000002488-26-000123.txt` | 290,335 |
| `data/raw/AMZN_10-Q_2026-06-30_0001018724-26-000026.txt` | 271,737 |
| `data/raw/AVGO_10-Q_2026-08-02_0001730168-26-000080.txt` | 283,690 |
| `data/raw/GOOGL_10-Q_2026-06-30_0001652044-26-000071.txt` | 340,300 |
| `data/raw/JPM_10-Q_2026-06-30_0001628280-26-054343.txt` | 1,301,269 |
| `data/raw/LITE_10-Q_2026-03-28_0001628280-26-030777.txt` | 498,200 |
| `data/raw/META_10-Q_2026-06-30_0001628280-26-050705.txt` | 480,554 |
| `data/raw/MSFT_10-Q_2026-03-31_0001193125-26-191507.txt` | 259,125 |
| `data/raw/NVDA_10-Q_2026-07-26_0001045810-26-000075.txt` | 223,099 |
| `data/raw/ORCL_10-Q_2026-08-31_0001193125-26-389274.txt` | 172,203 |
| `data/raw/SNDK_10-Q_2026-04-03_0001628280-26-029401.txt` | 243,965 |
| `data/raw/TSLA_10-Q_2026-06-30_0001628280-26-049270.txt` | 205,772 |
| `data/raw/VRT_10-Q_2026-06-30_0001628280-26-050609.txt` | 206,712 |
| `data/raw/XOM_10-Q_2026-06-30_0000034088-26-000093.txt` | 319,562 |

### Chroma and EDGAR caches

- `store/vectors/`: 14 files, 48,046,676 bytes.
- Chroma SQLite: 3 collections and 1,092 embeddings.
- `data/cache/edgar/`: 2,483 files, 228,625,045 bytes.

The Chroma collections were:

- `sec_chunks_qwen3_7_text_embedding_flash_1024`
- `sec_chunks_qwen3_vl_embedding_1024`
- `sec_chunks_qwen3_vl_embedding_1024_chunkv2`

### Generated Stage 2 outputs

The following 15 tracked files were generated from the old corpus and were
approved for deletion:

- `eval/retrieval_stage2_chunkv2_parentcollapse_report.md` — 3,260 bytes
- `eval/retrieval_stage2_chunkv2_parentcollapse_results.json` — 3,137,285 bytes
- `eval/retrieval_stage2_chunkv2_report.md` — 61,873 bytes
- `eval/retrieval_stage2_chunkv2_results.json` — 3,550,058 bytes
- `eval/retrieval_stage2_report.md` — 15,205 bytes
- `eval/retrieval_stage2_results.json` — 4,465,169 bytes
- `eval/sec_qa_034_nondeterminism_results.json` — 242,876 bytes
- `eval/sec_qa_chunkv2_difficult5_report.md` — 853 bytes
- `eval/sec_qa_chunkv2_difficult5_results.json` — 82,699 bytes
- `eval/sec_qa_evidence_only_report.md` — 4,160 bytes
- `eval/sec_qa_evidence_only_results.json` — 109,903 bytes
- `eval/sec_qa_grounding_retry_smoke.json` — 553,657 bytes
- `eval/sec_qa_smoke_result.json` — 187,106 bytes
- `eval/sec_qa_stage2_acceptance_30_report.md` — 7,627 bytes
- `eval/sec_qa_stage2_acceptance_30_results.json` — 518,357 bytes

### Generated Stage 3 outputs

The following four untracked files were approved for deletion:

- `eval/stage3_missing_fact_coverage.json` — 226,579 bytes
- `eval/stage3_missing_fact_coverage_report.md` — 8,283 bytes
- `eval/stage3_stress_test_report.md` — 31,223 bytes
- `eval/stage3_stress_test_results.json` — 2,306,198 bytes

## Preserved fixtures

These input definitions and human annotations are retained. Their pre-reset
SHA-256 hashes are recorded to detect accidental changes:

| Path | SHA-256 |
|---|---|
| `eval/retrieval_stage2_questions.json` | `6e638f3d3313f9362e52498d9f63f13ce6434625a6359a3ff0b5e456419cd2eb` |
| `eval/sec_claim_support_manual_results.json` | `e939e1fb783a5d241d8fcdeb7662381c4b5a7c401693a25c0ea0835939ea16e6` |
| `eval/agent_cases.yaml` | `d6db79803d2149d9c466eb49e58bc20d9c97d49981a0c2c812cb1441006a0c09` |
| `eval/gold_cases.yaml` | `45fc79f5e9a1497cddc7f38073d98b283616c854a458db274c0b47674f357b1d` |
| `eval/historical_set.yaml` | `91fb24df27e55c957be9f8a09893dda1373f8e4480a4995ae7cd35695a6b1e1d` |

## Recoverability

- Tracked raw filings, SQLite databases, and Stage 2 reports can be recovered
  from Git history if historical comparison is ever required.
- Untracked raw filings and Stage 3 outputs are removed locally; their source
  SEC filings or evaluation runs can reproduce them.
- Ignored EDGAR and Chroma caches are not recoverable as local cache state.
  They can be deterministically repopulated from SEC metadata, filing bodies,
  canonical chunks, and the configured embedding model.

## After reset

The destructive reset completed with this checkpoint:

| Store | Count |
|---|---:|
| `data/corpus.db` documents | 0 |
| `data/corpus.db` chunks | 0 |
| `data/raw/` filing files | 0 |
| `store/vectors/` files | 0 |
| `data/cache/edgar/` files | 0 |
| backup databases | 0 |
| approved generated result/report files remaining | 0 |

All five preserved fixture hashes still match the pre-reset values. The empty
SQLite database retains the valid Stage 1 schema. Final-schema zero-state
counts and isolated smoke-test evidence are appended after implementation.
