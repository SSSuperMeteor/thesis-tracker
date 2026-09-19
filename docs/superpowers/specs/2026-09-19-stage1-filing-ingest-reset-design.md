# Stage 1 Filing Ingest Reset Design

## Purpose

Rebuild Stage 1 so SEC filing metadata, not local data or an AI model, decides
which periodic filing to ingest. The default operation ingests the latest
supported filing, while explicit history modes ingest a deterministic filing
date range. Selected filings continue through the existing complete-filing
canonicalization, chunking, SQLite, and Stage 2 indexing path.

This work also removes every runtime artifact derived from the old filing
corpus while retaining source code, tests, Git history, development
configuration, and hand-authored evaluation inputs.

## Scope

### In scope

- Select SEC `10-Q` and `10-K` filings from official metadata before download.
- Make latest selection the default.
- Support inclusive `--since YYYY-MM-DD` and rolling `--years N` history
  requests.
- Persist accession, form, filing date, report date, and primary document.
- Preserve complete filing parsing, canonical text, verified chunks, lineage,
  and Stage 2 retrieval/citation metadata.
- Make repeated ingestion of an accession idempotent in SQLite and Chroma.
- Refresh Stage 2 vector data after a successful ingest batch.
- Delete old runtime filing data and generated evaluation outputs under the
  approved boundary.
- Run a real one-ticker smoke test in isolated temporary storage and remove
  that storage afterward.

### Out of scope

- Stage 2 ranking, RRF, generation, grounding, or claim-support redesign.
- Stage 3 latest-filing discovery, IR fallback, DeepSeek selection, financial
  fact resolution, or metric work.
- Company Facts as a filing-freshness authority.
- Ticker-specific selection or parsing rules.
- Changes to `PARSER_VERSION` or `NORMALIZER_VERSION`.

## Approved Runtime Data Boundary

The reset deletes inputs or outputs produced from the old runtime corpus:

- all files under `data/raw/`;
- all document and chunk rows in `data/corpus.db`, leaving an empty valid
  schema;
- `data/corpus_pre_chunkv2.db`;
- all Chroma files under `store/vectors/`;
- all EDGAR project cache files under `data/cache/edgar/` and any other
  project-local SEC discovery cache;
- the 15 tracked Stage 2 benchmark/QA result and report files generated from
  the old corpus;
- the four Stage 3 generated metric/stress-test files.

The reset preserves input definitions and human annotations:

- `eval/retrieval_stage2_questions.json`;
- `eval/sec_claim_support_manual_results.json`;
- `eval/agent_cases.yaml`, `eval/gold_cases.yaml`, and
  `eval/historical_set.yaml`;
- all other hand-authored fixtures and Gold Cases;
- all source, tests, configuration, documentation, skills, and Git history.

The governing distinction is: retain input definitions and human labels;
delete outputs, reports, caches, and indexes computed from the old corpus.
Before deletion, the exact path list, file counts, byte counts, database row
counts, and Chroma counts are recorded. Ambiguous files are not deleted.

## Architecture

### 1. Filing selection module

A focused Stage 1 module owns filing selection. It does not parse filing text,
write storage, or call an LLM.

It exposes:

- an immutable selection request containing ticker and exactly one mode;
- immutable filing metadata containing ticker, CIK, accession, form,
  `filing_date`, `report_date`, and `primary_document`;
- a selected filing containing validated metadata and the corresponding
  edgartools filing handle;
- a provider boundary so tests can supply complete SEC-shaped metadata without
  network calls;
- a production edgartools provider backed by SEC submissions metadata.

Selection failures use explicit Stage 1 error codes such as invalid request,
company unavailable, SEC metadata unavailable, invalid metadata, and no
matching periodic filing. The selector never falls back to a local filing,
Company Facts, IR pages, or an AI model.

### 2. Supported forms and amendment exclusion

The supported forms are exactly `10-Q` and `10-K`.

Amendments are excluded twice:

1. edgartools is requested with `amendments=False`;
2. Python accepts only exact normalized form values in `{10-Q, 10-K}`.

Thus `10-Q/A`, `10-K/A`, and any other form cannot become latest or enter a
history result even if an upstream provider returns it unexpectedly. Tests pin
both the provider argument and the Python-side rejection.

### 3. Latest semantics

Latest is the default when no history option is supplied. `--latest` is an
explicit spelling of the same mode.

The selector:

1. resolves ticker to company and CIK through edgartools;
2. obtains official filing metadata for exact supported periodic forms;
3. validates required metadata before any download;
4. sorts by `(filing_date, accession)` descending;
5. returns exactly the first filing.

The filing date is the primary recency key because it records when the SEC
accepted the filing. Accession is the stable deterministic tie-breaker and the
unique filing identity. Report date describes the covered period but does not
decide which filing was filed most recently.

If required metadata cannot be proven, selection fails closed rather than
choosing a plausible candidate.

### 4. History semantics

`--since DATE` includes filings whose SEC filing date is greater than or equal
to `DATE`.

`--years N` requires a positive integer and computes an inclusive lower bound
by moving the injected current date back `N` calendar years. February 29 maps
to February 28 when the target year is not a leap year.

History results:

- contain only exact `10-Q` and `10-K` forms;
- exclude amendments;
- use filing date, not report date, for the range boundary;
- are sorted by `(filing_date, accession)` descending;
- are de-duplicated by accession;
- fail if conflicting metadata is returned for one accession.

`--latest`, `--since`, and `--years` are mutually exclusive. Omitting all
three selects latest.

### 5. Ingest orchestration

An ingest coordinator performs one ordered flow:

1. validate the request;
2. select all filing metadata;
3. for each selected accession, build the existing full `CanonicalDoc`;
4. run the existing strict `assert_sane` checks;
5. save the document and chunks transactionally;
6. after the batch succeeds, refresh the vector index from successful,
   span-verified SQLite chunks.

Selection always completes before the first filing download or parse. A
selection error therefore cannot leave a partially selected history range.

The existing canonical parser and chunker remain authoritative for filing
text. Their strict exact-span checks and optional boundary-repair validation
are unchanged. AI may repair a text boundary under the existing Python
validator, but it never participates in filing selection.

Paths for raw files, SQLite, and Chroma are injectable while retaining current
project defaults. This permits isolated integration and smoke tests without
repopulating the cleaned project runtime stores.

### 6. Persistence and idempotency

`documents.accession` remains the SQLite primary key. The documents schema
adds a nullable `primary_document` field; all existing fields retain their
meaning. `period_end` continues to store SEC report date for compatibility.

Saving one accession uses the existing UPSERT plus delete-and-reinsert chunk
transaction. Different accessions therefore coexist, while rerunning the same
accession cannot add duplicate document or chunk rows.

Chunk IDs, `parent_id`, `prev_id`, `next_id`, order, exact text spans,
`text_hash`, and extraction metadata remain unchanged. No historical filing
can overwrite another because every derived row is scoped by accession.

Chroma continues to use chunk ID as its item identity and `text_hash` plus
embedding model metadata to skip unchanged embeddings. A full reconciliation
after each successful ingest batch removes vector records whose SQLite chunks
no longer exist. Repeating an unchanged accession performs zero new document
embeddings and does not increase collection size.

BM25 has no persistent index in the current architecture; it reads verified
SQLite chunks at query time, so no separate BM25 cleanup or rebuild is needed.

### 7. CLI and API

The CLI supports one or more tickers and these mutually exclusive modes:

- no mode flag or `--latest`;
- `--years N`;
- `--since YYYY-MM-DD`.

The complete ingest path includes vector refresh by default. The explicit
`--no-vector-index` option supports controlled diagnostics or environments
without an embedding credential; its use is visible in output and the command
does not report that run as a complete Stage 2-ready ingest.

The program prints selected accession, form, filing date, report date, primary
document, document/chunk counts, and vector index statistics without printing
secret values.

The prior direct module invocation remains usable where practical, but its
default changes from latest `10-Q` to latest supported periodic filing across
both `10-Q` and `10-K`.

## Stage 2 Contract Protection

The Stage 2 contract remains:

- BM25 joins `chunks.accession` to `documents.accession` and filters successful,
  span-verified chunks;
- Chroma metadata includes ticker, form, accession, section, title, text hash,
  embedding model, and dimension;
- hybrid retrieval performs existing RRF and parent collapse;
- citations point to the exact child chunk containing normalized exact-match
  evidence;
- Layer A grounding, Layer B support, unsupported leakage rules, and fallback
  states are unchanged.

Adding `primary_document` is backward-compatible because Stage 2 uses explicit
column lists. No retrieval algorithm or citation validator is modified.

The two pre-existing corpus-bound Stage 2 benchmark tests currently fail
because runtime corpus tickers and the fixed question manifest disagree. Once
runtime data is intentionally empty, those tests explicitly skip with a reason
that the historical benchmark corpus is absent. Their expected values are not
changed. Unit and temporary-store integration tests continue to exercise BM25,
vector retrieval, RRF, parent/child metadata, Layer A, and Layer B.

## Error Handling

- Invalid or conflicting mode flags fail before SEC access.
- Invalid dates and non-positive year counts fail before SEC access.
- Missing or malformed accession, form, filing date, report date, or required
  company identity fails selection.
- Unsupported forms and amendments are rejected, never silently normalized to
  a supported form.
- No matching filing is a structured failure, not a fallback to local data.
- Parser sanity failures retain their current failed-document behavior and do
  not emit chunks or vectors.
- Embedding failures propagate as an incomplete ingest; canonical SQLite data
  remains auditable, and the command does not claim Stage 2 readiness.
- No broad exception is converted to `None`.

## Testing

Tests use complete fake SEC metadata at the provider boundary and real
temporary SQLite/Chroma stores with the existing fake embedding provider.
They cover:

1. default and explicit latest select the current `10-Q`/`10-K` accession;
2. accession and saved metadata match the SEC-shaped response;
3. amendments are excluded at both provider and selector boundaries;
4. repeating one accession leaves document, chunk, and vector counts stable;
5. a newly filed accession becomes latest and is added without removing the
   earlier accession;
6. `--years N` uses an inclusive calendar-year filing-date boundary;
7. `--since DATE` is inclusive;
8. history ordering and same-accession de-duplication are deterministic;
9. multiple filings coexist;
10. CanonicalDoc/chunk/parent metadata remains consumable by BM25, vector,
    hybrid, and citation code;
11. temporary cleanup inspection reports zero raw files, database rows,
    vectors, caches, and generated output artifacts;
12. the approved hand-authored fixtures remain present.

Every behavior change follows red-green-refactor. Tests do not call the live
SEC service. Live SEC and embedding access are reserved for the final smoke
test.

## Verification and Smoke Test

Verification runs:

- targeted Stage 1 selection, persistence, vector, retrieval, and citation
  tests;
- `uv run ruff check .`;
- `uv run pytest`;
- read-only counts proving the project runtime stores are empty after reset.

The final smoke test uses one ticker and isolated temporary paths. It:

1. asks SEC metadata for latest supported periodic filing;
2. records accession, form, filing date, report date, and primary document;
3. downloads and chunks the complete filing;
4. builds vectors and demonstrates Stage 2 BM25/vector/hybrid retrieval over
   the new chunks;
5. repeats the ingest and proves document, chunk, and vector counts do not
   increase and unchanged chunks are not re-embedded;
6. deletes the temporary smoke data.

After smoke cleanup, project runtime raw, SQLite company rows, Chroma, and
EDGAR cache remain empty. Any unavailable external credential or SEC/network
failure is reported precisely rather than replaced with a synthetic success.
