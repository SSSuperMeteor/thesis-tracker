# Stage 1 Filing Ingest Reset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reset all old runtime filing data and make Stage 1 select amendment-aware latest or historical SEC filing families before complete-filing ingest and Stage 2 indexing.

**Architecture:** A new pure filing-selection module obtains official SEC metadata through an injected edgartools boundary, validates and groups originals plus amendments into filing families, and returns deterministic latest/history results. A coordinator then reuses the existing canonical parser, atomically persists each family by accession, and reconciles Chroma; Stage 2 keeps its ranking and citation algorithms while form filtering becomes base-form aware.

**Tech Stack:** Python 3.12, edgartools 5.58, SQLite, ChromaDB, DashScope embeddings, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-19-stage1-filing-ingest-reset-design.md`

## Global Constraints

- SEC submissions metadata is the only filing-recency authority.
- Supported exact SEC forms are `10-Q`, `10-K`, `10-Q/A`, and `10-K/A`; base forms are `10-Q` and `10-K`.
- AI, Company Facts, IR pages, and local corpus contents never select the latest filing.
- Accession is the filing identity; report date groups an original with amendments.
- Invalid or incomplete amendment families fail closed and cannot expose the unamended original as current.
- Existing CanonicalDoc/Chunk exact-span, lineage, citation, parser version, and normalizer version behavior stays unchanged unless explicitly extended below.
- Stage 2 BM25, vector ranking, RRF, Layer A, and Layer B algorithms are not redesigned.
- Preserve all existing uncommitted path-safety changes in `sec_adapter.py`.
- Preserve all input-definition and human-annotation fixtures; delete only old-corpus outputs, reports, caches, indexes, and runtime filing data.
- Do not print `DEEPSEEK_API_KEY`, `DASHSCOPE_API_KEY`, or `EDGAR_IDENTITY`.

## Review Focus

- An amendment filed after a newer-period original: latest is determined by family effective filing date, and both members of the selected family are ingested.
- An amendment whose original is outside a history lower bound: the selected result includes the whole family.
- An orphan or ambiguous amendment: selection fails before any raw filing is downloaded.
- A partial amendment that fails canonical sanity: all family members become non-success and no family chunks remain retrievable.
- Repeated family ingest with unchanged text: SQLite/Chroma counts stay fixed and the second vector refresh embeds zero chunks.

---

### Task 1: Record and Remove Old Runtime Filing Data

**Files:**
- Create: `docs/runtime-data-reset-2026-09-19.md`
- Delete: `data/corpus_pre_chunkv2.db`
- Replace with empty schema: `data/corpus.db`
- Delete: all 15 currently enumerated files under `data/raw/`
- Delete: `data/cache/edgar/`
- Delete: `store/vectors/`
- Delete: the 15 approved Stage 2 generated files listed below
- Delete: the four approved Stage 3 generated files listed below

**Interfaces:**
- Consumes: the approved deletion boundary and the current read-only inventory.
- Produces: an auditable before/after record and project runtime stores containing zero company filing records.

The Stage 2 generated files are:

```text
eval/retrieval_stage2_chunkv2_parentcollapse_report.md
eval/retrieval_stage2_chunkv2_parentcollapse_results.json
eval/retrieval_stage2_chunkv2_report.md
eval/retrieval_stage2_chunkv2_results.json
eval/retrieval_stage2_report.md
eval/retrieval_stage2_results.json
eval/sec_qa_034_nondeterminism_results.json
eval/sec_qa_chunkv2_difficult5_report.md
eval/sec_qa_chunkv2_difficult5_results.json
eval/sec_qa_evidence_only_report.md
eval/sec_qa_evidence_only_results.json
eval/sec_qa_grounding_retry_smoke.json
eval/sec_qa_smoke_result.json
eval/sec_qa_stage2_acceptance_30_report.md
eval/sec_qa_stage2_acceptance_30_results.json
```

The Stage 3 generated files are:

```text
eval/stage3_missing_fact_coverage.json
eval/stage3_missing_fact_coverage_report.md
eval/stage3_stress_test_report.md
eval/stage3_stress_test_results.json
```

- [ ] **Step 1: Write the reset audit with the exact pre-delete inventory**

Record the 15 raw paths, both SQLite files and row counts, Chroma file/count
inventory, EDGAR cache file/byte counts, generated output paths, and preserved
fixture paths. State which tracked files are recoverable from Git and which
ignored caches are only reproducible by fetching/indexing again.

- [ ] **Step 2: Re-check Git tracking and code references immediately before deletion**

Run:

```bash
git ls-files data data/raw eval store
rg -n "retrieval_stage2_questions|sec_claim_support_manual_results|corpus_pre_chunkv2|store/vectors|data/cache" src tests eval .gitignore
```

Expected: the two retained JSON fixtures and three YAML fixtures are identified
as inputs; none of the 19 generated files is required as a test input.

- [ ] **Step 3: Delete only the enumerated runtime targets**

Use explicit paths from the audit. Remove `data/cache/edgar` and
`store/vectors` as exact directories, remove each raw/generated file from the
recorded list, and remove both SQLite files. Do not remove `data/`, `eval/`,
`store/.gitkeep`, source, tests, skills, or Git metadata.

- [ ] **Step 4: Recreate an empty valid corpus database**

Run the current schema initializer against `data/corpus.db`:

```bash
uv run python -c "import sqlite3; from thesis_tracker.ingest.sec_adapter import _ensure_schema; con=sqlite3.connect('data/corpus.db'); _ensure_schema(con); con.commit(); con.close()"
```

- [ ] **Step 5: Verify zero runtime data and preserved fixtures**

Run read-only checks proving:

```text
documents = 0
chunks = 0
raw filing files = 0
Chroma files/collections/embeddings = 0
EDGAR project cache files = 0
backup DB absent
19 generated result/report files absent
retrieval_stage2_questions.json present
sec_claim_support_manual_results.json present
all three YAML fixtures present
```

- [ ] **Step 6: Commit the audited reset**

Stage only the audit, tracked runtime deletions, tracked generated-output
deletions, and empty `data/corpus.db`:

```bash
git commit -m "chore: reset historical filing runtime data"
```

### Task 2: Add Pure Amendment-Aware SEC Filing Selection

**Files:**
- Create: `src/thesis_tracker/ingest/filing_selection.py`
- Create: `tests/test_stage1_filing_selection.py`

**Interfaces:**
- Produces: `SelectionRequest`, `FilingMetadata`, `SourceFiling`,
  `FilingFamily`, `FilingSelector`, `EdgarFilingSource`,
  `SelectionFailureCode`, and `FilingSelectionError`.
- Consumes later: `IngestCoordinator` receives `tuple[FilingFamily, ...]` from
  `FilingSelector.select(request, today=current_date)`.

The public shapes are:

```python
class SelectionFailureCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    COMPANY_UNAVAILABLE = "company_unavailable"
    SEC_METADATA_UNAVAILABLE = "sec_metadata_unavailable"
    INVALID_METADATA = "invalid_metadata"
    NO_MATCHING_FILINGS = "no_matching_filings"


@dataclass(frozen=True, slots=True)
class SelectionRequest:
    ticker: str
    latest: bool = False
    years: int | None = None
    since: date | None = None


@dataclass(frozen=True, slots=True)
class FilingMetadata:
    ticker: str
    cik: str
    accession: str
    form: str
    base_form: str
    is_amendment: bool
    amends_accession: str | None
    filing_date: date
    report_date: date
    primary_document: str


@dataclass(frozen=True, slots=True)
class SourceFiling:
    metadata: FilingMetadata
    company: object = field(compare=False, repr=False)
    filing: object = field(compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class FilingFamily:
    original: SourceFiling
    amendments: tuple[SourceFiling, ...]

    @property
    def members(self) -> tuple[SourceFiling, ...]:
        return (self.original, *self.amendments)

    @property
    def effective(self) -> SourceFiling:
        return self.members[-1]
```

- [ ] **Step 1: Write request and date-boundary tests**

Add tests proving no flag defaults to latest; explicit latest behaves the same;
latest/years/since conflicts fail with `INVALID_REQUEST`; years must be
positive; since is inclusive; and a 2028-02-29 clock minus one year becomes
2027-02-28.

- [ ] **Step 2: Run request tests and verify RED**

Run:

```bash
uv run pytest -p no:cacheprovider tests/test_stage1_filing_selection.py -q
```

Expected: collection/import failure because `filing_selection` does not exist.

- [ ] **Step 3: Implement request validation and lower-bound calculation**

Implement immutable dataclasses, `SelectionFailureCode`, and
`FilingSelectionError(code, message)`. Normalize ticker to uppercase and reject
blank tickers. Compute years with `date.replace`, using February 28 only for the
leap-day failure case.

- [ ] **Step 4: Run request tests and verify GREEN**

Run the same test file and confirm the request/date cases pass.

- [ ] **Step 5: Write latest/history/family tests**

Use literal `SourceFiling` records to prove:

- exact supported forms only;
- `10-Q/A` links to the one `10-Q` with the same CIK/report date;
- multiple amendments order ascending inside the family;
- latest uses the effective member `(filing_date, accession)` descending;
- a later amendment can make its family latest;
- `since` and `years` include a whole family when its effective member is in
  range;
- history families order descending and accessions de-duplicate;
- orphan amendment, multiple originals, amendment-before-original, duplicate
  accession with conflicting metadata, and missing required fields fail with
  `INVALID_METADATA`;
- no valid family fails with `NO_MATCHING_FILINGS`.

- [ ] **Step 6: Run family tests and verify RED**

Expected: failures because grouping, validation, and selection are absent.

- [ ] **Step 7: Implement minimal family grouping and selection**

Group on `(cik, base_form, report_date)`, validate all accessions before range
filtering, use `dataclasses.replace` to attach `amends_accession`, and use the
literal sort keys specified in the spec. Do not consult local storage or
document contents.

- [ ] **Step 8: Write and run the edgartools boundary test RED**

Inject a fake `Company` factory and assert `EdgarFilingSource` calls:

```python
company.get_filings(
    form=["10-Q", "10-K"],
    amendments=True,
    trigger_full_load=include_history,
)
```

The fake rows must contain the complete real metadata shape used by production:
form, accession number, filing date, period of report, and primary document.

- [ ] **Step 9: Implement `EdgarFilingSource` and verify GREEN**

Set SEC identity from configuration/environment, construct `Company(ticker)`,
map metadata without downloading filing bodies, and wrap external failures as
`FilingSelectionError` with the appropriate code. Never print identity values.

- [ ] **Step 10: Run selection tests and commit**

```bash
uv run pytest -p no:cacheprovider tests/test_stage1_filing_selection.py -q
git commit -m "feat: select amendment-aware SEC filing families"
```

### Task 3: Persist Filing-Family Metadata Atomically

**Files:**
- Modify: `src/thesis_tracker/ingest/sec_adapter.py`
- Extend: `tests/test_raw_filing_path.py`
- Create: `tests/test_stage1_family_persistence.py`

**Interfaces:**
- Consumes: every selected family member's `FilingMetadata` plus any successfully
  built `CanonicalDoc` values.
- Produces: `_build(company, filing, *, metadata, raw_dir)`, extended
  `CanonicalDoc`, and
  `save_family(members, docs_by_accession, db_path,
  failures_by_accession=None)`.

- [ ] **Step 1: Write metadata/path injection tests**

Add failing tests showing `_build`/raw path creation can target a temporary raw
directory and that a CanonicalDoc can retain:

```text
primary_document
base_form
is_amendment
amends_accession
```

Keep every existing path-traversal and missing-value test unchanged so the
current uncommitted path-safety behavior remains protected.

- [ ] **Step 2: Run targeted tests and verify RED**

```bash
uv run pytest -p no:cacheprovider tests/test_raw_filing_path.py tests/test_stage1_family_persistence.py -q
```

Expected: new constructor/path/metadata assertions fail because the parameters
and fields do not exist.

- [ ] **Step 3: Extend CanonicalDoc and raw path injection minimally**

Add optional metadata fields after existing required CanonicalDoc fields so
older keyword-based tests remain valid. Add `raw_dir: Path = RAW_DIR` to
`_raw_filing_path` and `_build`, resolve containment against that injected
directory, and pass selected metadata into the document. Do not change parser
or normalizer versions.

- [ ] **Step 4: Verify metadata/path tests GREEN**

Run the targeted tests and confirm all old path-safety cases plus new temporary
path cases pass.

- [ ] **Step 5: Write schema migration and family atomicity tests**

Using temporary SQLite databases, test both a fresh schema and the current
legacy schema. Assert columns are added without losing rows. For a successful
original plus amendment, assert two document rows, independent accessions,
exact forms, relationship fields, and distinct chunks. Rerun and assert counts
do not change.

Add a failure case where the amendment has sanity failures. Assert both family
documents are non-success and all chunks for both accessions are removed,
including chunks left by an earlier successful run.

Add a build-exception case where metadata exists but no CanonicalDoc was
produced for the amendment. Assert the metadata-only failed amendment row is
stored, the original is also non-success, and previously stored family chunks
are removed.

- [ ] **Step 6: Run persistence tests and verify RED**

Expected: missing columns and missing `save_family` failures.

- [ ] **Step 7: Implement schema migration and transactional `save_family`**

Extend DDL/migration with:

```sql
primary_document TEXT,
base_form TEXT,
is_amendment INTEGER NOT NULL DEFAULT 0,
amends_accession TEXT
```

Extract the existing UPSERT/chunk replacement statements into a connection-
scoped helper. `save` delegates for one document; `save_family` opens one
transaction for every selected metadata member. If any member fails or lacks a
built document, write metadata-only failure rows where necessary, pass a
family-level failure to all members, and store no chunks. This lets a parser
exception invalidate a previously successful unamended original instead of
leaving it silently queryable.

- [ ] **Step 8: Run persistence and existing chunking tests GREEN**

```bash
uv run pytest -p no:cacheprovider tests/test_raw_filing_path.py tests/test_stage1_family_persistence.py tests/test_sec_chunking_v2.py -q
```

- [ ] **Step 9: Commit the persistence change**

Stage the full `sec_adapter.py` including the pre-existing path-safety changes,
and explicitly describe that preservation in the commit message/body:

```bash
git commit -m "feat: persist SEC filing families atomically"
```

### Task 4: Add the Complete Ingest Coordinator and CLI

**Files:**
- Create: `src/thesis_tracker/ingest/pipeline.py`
- Create: `src/thesis_tracker/ingest/cli.py`
- Modify: `src/thesis_tracker/ingest/sec_adapter.py` (legacy main delegates)
- Modify: `pyproject.toml`
- Create: `tests/test_stage1_ingest_pipeline.py`
- Create: `tests/test_stage1_ingest_cli.py`

**Interfaces:**
- Consumes: `FilingSelector`, `_build`, `assert_sane`, `save_family`, and
  `VectorRetriever.index()`.
- Produces: `IngestPaths`, `FamilyIngestResult`, `IngestResult`,
  `IngestCoordinator.ingest()`, and the `ingest` console command.

The coordinator result is:

```python
@dataclass(frozen=True, slots=True)
class IngestPaths:
    raw_dir: Path = Path("data/raw")
    db_path: Path = Path("data/corpus.db")
    vector_path: Path = Path("store/vectors")


@dataclass(frozen=True, slots=True)
class FamilyIngestResult:
    effective_accession: str
    member_accessions: tuple[str, ...]
    document_count: int
    chunk_count: int
    status: str


@dataclass(frozen=True, slots=True)
class IngestResult:
    families: tuple[FamilyIngestResult, ...]
    index_stats: IndexStats | None
    stage2_ready: bool
```

- [ ] **Step 1: Write coordinator order and idempotency tests**

With fake selected families, fake complete CanonicalDocs, temporary SQLite, and
the existing fake embedding provider, prove:

- selector finishes before the first builder call;
- original builds before amendments;
- family persistence happens only after all members are built and checked;
- vector reconciliation happens once after all successful families;
- `no_vector_index=True` returns `stage2_ready=False` and creates no Chroma;
- repeating a family leaves document/chunk/vector counts unchanged and the
  second index reports zero embedded chunks;
- adding a new latest accession preserves the prior family;
- a failed or unbuildable amendment stores no family chunks, invalidates a
  previously successful original, and prevents vector refresh.

- [ ] **Step 2: Run pipeline tests and verify RED**

```bash
uv run pytest -p no:cacheprovider tests/test_stage1_ingest_pipeline.py -q
```

- [ ] **Step 3: Implement `IngestCoordinator` minimally**

Use constructor injection for selector, document builder, family saver, and
indexer factory. The production defaults bind existing Stage 1 and Stage 2
components. Collect all selected metadata before invoking the builder. Propagate
structured selection, sanity, persistence, and embedding failures without
claiming readiness.

- [ ] **Step 4: Verify pipeline tests GREEN**

Run the pipeline test file and inspect exact document/chunk/vector counts.

- [ ] **Step 5: Write CLI parsing tests RED**

Test these forms:

```text
ingest NVDA
ingest NVDA --latest
ingest NVDA --years 3
ingest NVDA --since 2023-01-01
ingest NVDA --no-vector-index
ingest NVDA --raw-dir /tmp/raw --db /tmp/corpus.db --vector-path /tmp/vectors
```

Assert latest/years/since are mutually exclusive, invalid dates and nonpositive
years exit nonzero before coordinator invocation, and output labels a
`--no-vector-index` run as not Stage 2 ready.

- [ ] **Step 6: Implement CLI and project entry point**

Add:

```toml
[project.scripts]
ingest = "thesis_tracker.ingest.cli:main"
```

Use argparse with one-or-more tickers and the exact flags from the spec. Create
one production selector/coordinator, process each ticker, and print member
metadata plus exact counts. Replace the large legacy `__main__` block in
`sec_adapter.py` with delegation to `thesis_tracker.ingest.cli.main` while
keeping import behavior safe.

- [ ] **Step 7: Run CLI and pipeline tests GREEN**

```bash
uv run pytest -p no:cacheprovider tests/test_stage1_ingest_pipeline.py tests/test_stage1_ingest_cli.py -q
```

- [ ] **Step 8: Commit coordinator and CLI**

```bash
git commit -m "feat: ingest selected SEC filing families"
```

### Task 5: Preserve Stage 2 Contracts With Amendment Metadata

**Files:**
- Modify: `src/thesis_tracker/retrieve/bm25.py`
- Modify: `src/thesis_tracker/retrieve/vector.py`
- Modify: `tests/test_bm25.py`
- Modify: `tests/test_vector.py`
- Modify: `tests/test_retrieval_stage2_evaluation.py`
- Modify: `tests/test_retrieval_stage2_chunkv2.py`
- Extend: `tests/test_stage1_ingest_pipeline.py`

**Interfaces:**
- Consumes: documents with exact `form_type`, `base_form`, `is_amendment`, and
  accession-scoped chunks.
- Produces: base-form-aware BM25/Chroma filtering while retaining exact form in
  each result and unchanged RRF/citation behavior.

- [ ] **Step 1: Write BM25 amendment-filter test RED**

Add a temporary `10-Q` original, its `10-Q/A` amendment, and a `10-K` row.
Search with `form_type="10-Q"`; assert both Q accessions are returned, the K is
not returned, and result `form_type` values remain `10-Q` and `10-Q/A`.

- [ ] **Step 2: Implement BM25 base-form filtering GREEN**

Load `d.base_form` explicitly, filter on base form, and continue returning
`d.form_type` as exact form. Update all test fixture schemas with literal
`base_form` values rather than dynamically introspecting SQL.

- [ ] **Step 3: Write vector amendment metadata/filter test RED**

Index the same three documents with the fake embedding provider. Assert Chroma
metadata includes `base_form` and integer `is_amendment`; a `10-Q` query sees
both Q members; returned exact forms remain distinct; repeated index embeds
zero new chunks.

- [ ] **Step 4: Implement vector metadata/filter support GREEN**

Extend `_ChunkRecord`, its metadata mapping, SQLite SELECT, and Chroma where
filter to use base form. Do not change embedding text, distance, ranking, or
collection identity.

- [ ] **Step 5: Make historical benchmark tests explicit about empty runtime data**

Before validating the retained historical manifest, skip only when
`data/corpus.db` is absent or contains zero verified chunks:

```python
if not catalog:
    pytest.skip("historical Stage 2 benchmark requires a populated runtime corpus")
```

Do not change the 70 question expectations, ticker set, chunk IDs, or metric
values. A non-empty mismatched corpus must still fail, preserving the original
sanity check.

- [ ] **Step 6: Run Stage 2 contract tests**

```bash
uv run pytest -p no:cacheprovider tests/test_bm25.py tests/test_vector.py tests/test_hybrid.py tests/test_citation.py tests/test_claim_support.py tests/test_retrieval_stage2_evaluation.py tests/test_retrieval_stage2_chunkv2.py -q
```

Expected: all fixture-backed tests pass; exactly the two historical runtime-
corpus tests skip while the project corpus is empty. Layer A exact grounding,
Layer B states, fallback behavior, and unsupported-leakage expectations remain
unchanged.

- [ ] **Step 7: Commit Stage 2 contract adaptation**

```bash
git commit -m "fix: retain amendments in Stage 2 form filters"
```

### Task 6: Verify the Complete Reset and Regression Suite

**Files:**
- Update: `docs/runtime-data-reset-2026-09-19.md` with final zero-state counts

**Interfaces:**
- Consumes: all implementation tasks and the cleaned project runtime paths.
- Produces: fresh lint/test evidence and a documented zero-data checkpoint.

- [ ] **Step 1: Initialize the cleaned corpus with the final schema**

Run `_ensure_schema` once against `data/corpus.db`, then query `PRAGMA
table_info(documents)` and both row counts. Confirm family metadata columns are
present and both tables contain zero rows.

- [ ] **Step 2: Run targeted Stage 1 tests**

```bash
uv run pytest -p no:cacheprovider tests/test_stage1_filing_selection.py tests/test_raw_filing_path.py tests/test_stage1_family_persistence.py tests/test_stage1_ingest_pipeline.py tests/test_stage1_ingest_cli.py tests/test_sec_chunking_v2.py -q
```

- [ ] **Step 3: Run lint**

```bash
uv run ruff check .
```

- [ ] **Step 4: Run the full suite**

```bash
uv run pytest -p no:cacheprovider
```

Expected: no failures; the two historical runtime-corpus benchmark tests are
reported as skips, not as altered expected values. Compare all other Stage 2
test names with the baseline of 298 passes and confirm no new regression.

- [ ] **Step 5: Re-run the zero-state audit**

Record exact final counts for raw files, documents, chunks, Chroma files,
EDGAR cache files, backup DB, and generated reports. Verify every retained
fixture by exact path.

- [ ] **Step 6: Commit final audit updates**

```bash
git commit -m "test: verify empty filing runtime state"
```

### Task 7: Run an Isolated Real SEC and Stage 2 Smoke Test

**Files:**
- No persistent project data files
- Update: `docs/runtime-data-reset-2026-09-19.md` with smoke metadata and counts

**Interfaces:**
- Consumes: live SEC metadata/body access, configured EDGAR identity, configured
  DashScope embedding access, and the production ingest command.
- Produces: one real latest filing family in a validated `/tmp` directory,
  successful BM25/vector/hybrid retrieval, repeat-ingest idempotency evidence,
  and a clean project runtime after temporary cleanup.

- [ ] **Step 1: Confirm credentials without printing values**

Print only present/missing status for `EDGAR_IDENTITY`, `DASHSCOPE_API_KEY`, and
`DEEPSEEK_API_KEY`. DeepSeek is required only if deterministic boundary parsing
needs the existing repair path.

- [ ] **Step 2: Create and validate an isolated smoke root**

Use `mktemp -d /tmp/thesis-tracker-stage1-smoke.XXXXXX`, resolve it, and refuse
to continue unless it starts with `/tmp/thesis-tracker-stage1-smoke.`. Point
raw, SQLite, vectors, `EDGAR_CACHE_DIR`, and `EDGAR_LOCAL_DATA_DIR` beneath that
root.

- [ ] **Step 3: Ingest one ticker in latest mode**

Run production `ingest` for `NVDA --latest` with the temporary paths. Capture
the SEC-selected family and record exact CIK, effective accession, all member
accessions/forms, filing dates, report dates, primary documents, full-text
character counts, and chunk counts.

- [ ] **Step 4: Prove Stage 2 can retrieve new chunks**

Use production BM25, vector, and hybrid retrievers against the temporary DB and
Chroma path with a query drawn from the ingested filing. Record returned chunk
IDs/accessions and verify cited evidence remains an exact normalized substring
of its returned chunk. This changes neither Layer A nor Layer B behavior.

- [ ] **Step 5: Repeat the same ingest and prove idempotency**

Record before/after document, chunk, raw-file, Chroma embedding, and collection
counts. Assert every count is identical and the second `IndexStats` reports:

```text
embedded_chunks = 0
skipped_unchanged = total_chunks
removed_stale = 0
```

- [ ] **Step 6: Remove the validated temporary smoke root**

After revalidating the resolved prefix, delete only that temporary directory.
Report that live SEC/cache/vector smoke data is not recoverable locally but can
be reproduced from SEC metadata plus the same ingestion code.

- [ ] **Step 7: Verify project runtime remains empty**

Repeat the Task 6 zero-state audit against project paths. The smoke must not add
raw files, documents, chunks, vectors, or EDGAR cache entries to the repository.

- [ ] **Step 8: Run final lint and full tests after smoke**

```bash
uv run ruff check .
uv run pytest -p no:cacheprovider
```

- [ ] **Step 9: Commit the smoke audit record**

```bash
git commit -m "test: record Stage 1 latest ingest smoke"
```
