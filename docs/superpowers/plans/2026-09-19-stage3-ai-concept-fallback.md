# Stage 3 AI-Assisted Concept Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Do not use
> subagents for this session. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional, cached DeepSeek concept-proposal sidecar whose
filing-local proposals can affect `gross_margin_trend` only after deterministic
Python validation and normal financial-fact resolution.

**Architecture:** Preserve the deterministic-first gross-margin path. Extract a
small income-statement semantic context alongside each already bounded filing,
ask an injected provider only for eligible semantic failures, validate proposals
with exact Decimal equations and filing identity, then pass an immutable
per-call concept overlay into the existing resolver. Cache proposals, never
approvals, and serialize every attempt separately from financial facts.

**Tech Stack:** Python 3.12, dataclasses, Pydantic 2, sqlite3, hashlib/json,
OpenAI-compatible DeepSeek client, edgartools filing-level XBRL, pytest, ruff.

**Spec:**
`docs/superpowers/specs/2026-09-19-stage3-ai-concept-fallback-design.md`

## Global Constraints

- Do not modify Stage 1 ingestion or Stage 2 retrieval/chunking/citation code.
- Do not start metric #2.
- Do not commit or push; replace commit checkpoints with `git diff --check` and
  `git status --short` evidence.
- The model proposes identifiers and semantic relations only; it never returns
  or approves a numeric fact.
- Only `registry_gap` and `custom_concept_only` trigger AI.
- Use Decimal strict equality; add no tolerance.
- Do not write AI proposals into `CONCEPT_REGISTRY`.
- Do not use ticker-specific production branches.
- Default/disabled/API-failure behavior must preserve the existing serialized
  deterministic result.
- Unit tests use deterministic fakes and must not call a real API.
- Read secrets only through existing environment-backed settings and never log
  them.

## File Structure

- Create `src/thesis_tracker/financial/semantic_candidates.py` for immutable
  filing-local income-statement records and XBRL extraction.
- Create `src/thesis_tracker/financial/ai_concepts.py` for strict proposal
  schemas, prompt construction, provider protocol, DeepSeek provider, hashes,
  and response parsing.
- Create `src/thesis_tracker/financial/ai_cache.py` for the standalone SQLite
  proposal cache.
- Create `src/thesis_tracker/financial/ai_validation.py` for exact accounting,
  filing-local, context, period, unit, dimension, relation, and accession
  validation.
- Create `src/thesis_tracker/financial/ai_fallback.py` for eligibility,
  orchestration, ephemeral mapping, audit records, and safe degradation.
- Modify `src/thesis_tracker/financial/models.py` only for AI validation
  provenance and origin carried by accepted facts.
- Modify `src/thesis_tracker/financial/sec_source.py` only to attach semantic
  contexts from the same already-selected XBRL objects.
- Modify `src/thesis_tracker/financial/resolver.py` only to accept an explicit
  immutable per-call concept overlay and propagate AI provenance; its default
  candidate set and behavior stay unchanged.
- Modify `src/thesis_tracker/metrics/financial.py` to run the optional sidecar
  after deterministic semantic failure and serialize attempt audit records.
- Create focused tests in `tests/test_financial_semantic_candidates.py`,
  `tests/test_financial_ai_concepts.py`, `tests/test_financial_ai_cache.py`,
  `tests/test_financial_ai_validation.py`, and
  `tests/test_gross_margin_ai_fallback.py`.
- Create `docs/stage3-ai-concept-experiment.md` for exact real-run evidence.

## Review Focus

- A proposal names a real concept but a different context: validator rejects it
  and no mapped fact reaches the resolver. Covered in Task 4.
- A cache entry parses but its candidate input has changed: key mismatch forces
  a new provider call. Covered in Task 3.
- A proposal returns duplicate or extra candidate fields: strict schema rejects
  it and the original failure survives. Covered in Task 2.
- A component set happens to sum numerically but lacks a calculation relation:
  it is rejected as components, not accepted by coincidence. Covered in Task 4.
- An AI-validated source participates in Q4/YTD derivation: the derived fact
  remains `derived` while retaining AI validation provenance and source IDs.
  Covered in Task 5.

---

### Task 1: Add the disabled sidecar seam and semantic trigger policy

**Files:**
- Create: `src/thesis_tracker/financial/ai_fallback.py`
- Create: `tests/test_gross_margin_ai_fallback.py`
- Modify: `src/thesis_tracker/metrics/financial.py`

**Interfaces:**
- Consumes: existing `compute_gross_margin_trend(...) -> GrossMarginTrend`.
- Produces:
  - `AiFallbackConfig(enabled: bool)`
  - `AiFallback(Protocol).recover(...)`
  - `is_ai_eligible(code: FailureCode) -> bool`
  - optional `ai_fallback` metric argument
  - test fixtures `FailIfCalledFallback`, `make_boundary`, and `make_fact`

- [ ] **Step 1: Capture the serialized baseline**

  Build a one-period revenue/cost fixture and store the literal expected
  `GrossMarginTrend.to_dict()` result. Do not derive the expected dictionary
  from the production serializer.

- [ ] **Step 2: Write the deterministic-success no-call test**

  Add
  `test_deterministic_success_never_calls_ai_fallback`. Pass a fake fallback
  whose `recover` method raises `AssertionError`; expect the normal observation.

- [ ] **Step 3: Write the disabled byte-equivalence test**

  Add `test_disabled_ai_fallback_matches_deterministic_serialization`. Compare
  canonical `json.dumps(..., sort_keys=True, separators=(",", ":"))` for the
  no-sidecar call and the explicitly disabled-sidecar call.

- [ ] **Step 4: Write the non-semantic no-call tests**

  Parameterize `not_applicable`, `period_unavailable`,
  `duration_unavailable`, `invalid_context`, and `ambiguous` diagnostics.
  Confirm the fake fallback is never invoked and the original
  `final_failure` survives. Separately assert only `registry_gap` and
  `custom_concept_only` return true from `is_ai_eligible`.

- [ ] **Step 5: Run the tests and verify RED**

  Run:
  `uv run pytest -p no:cacheprovider tests/test_gross_margin_ai_fallback.py -q`

  Expected: failures because optional sidecar interfaces do not exist. Confirm
  every failure is an absent interface, not malformed fixture data.

- [ ] **Step 6: Implement the minimal disabled seam**

  Add the immutable config, eligibility predicate, protocol, and optional metric
  argument. The metric invokes no sidecar on a success or ineligible
  diagnostic. When disabled or absent, do not alter the result model,
  observation builders, failure builders, or serialized output.

- [ ] **Step 7: Run Task 1 tests GREEN**

  Run the Task 1 command and `tests/test_gross_margin_trend.py`. Confirm the
  literal/canonical baseline and all existing metric tests pass.

- [ ] **Step 8: Record the baseline checkpoint**

  Run `git diff --check` and `git status --short`; do not commit.

### Task 2: Define strict proposals and reuse the DeepSeek client pattern

**Files:**
- Create: `src/thesis_tracker/financial/ai_concepts.py`
- Create: `tests/test_financial_ai_concepts.py`
- Read: `src/thesis_tracker/config.py`
- Read: `src/thesis_tracker/qa/sec_qa.py`

**Interfaces:**
- Produces:
  - `PROMPT_VERSION: str`
  - `SemanticRelation(StrEnum)`
  - `ProposalStatus(StrEnum)`
  - `CandidateConceptProposal(BaseModel)`
  - `ConceptProposal(BaseModel)`
  - `ConceptProviderResponse(dataclass)` containing raw response and optional
    prompt/completion/total usage
  - `ConceptProposalProvider(Protocol).propose(*, system_prompt: str,
    user_prompt: str) -> ConceptProviderResponse`
  - `DeepSeekConceptProposalProvider`
  - `parse_concept_proposal(raw: str, expected_target: str)`
  - `canonical_json(value)`, `sha256_text(value)`
  - `build_proposal_prompts(input_payload)`

- [ ] **Step 1: Write strict schema tests**

  Test valid `candidate`, `no_match`, and `insufficient_evidence` responses.
  Test rejection of `approved`, numeric value fields, extra top-level fields,
  empty evidence lists, duplicate concepts, invalid relations, and a response
  target different from `expected_target`.

- [ ] **Step 2: Write prompt-boundary tests**

  Assert the prompt contains the literal prompt version, forbids numeric facts
  and approval, represents filing metadata as canonical JSON, and includes only
  the supplied payload. Do not assert incidental prose beyond these contracts.

- [ ] **Step 3: Write provider failure/usage tests**

  Inject a minimal fake OpenAI-compatible client below the provider. Verify
  empty content raises a typed response error and token usage is returned
  without exposing the API key.

- [ ] **Step 4: Run the tests and verify RED**

  Run:
  `uv run pytest -p no:cacheprovider tests/test_financial_ai_concepts.py -q`

  Expected: import failure because `ai_concepts.py` is absent.

- [ ] **Step 5: Implement the minimal schema/provider module**

  Use Pydantic `ConfigDict(extra="forbid", frozen=True)`, field validators for
  uniqueness/non-empty strings, `load_settings()`, `OpenAI`, JSON response
  format, temperature zero, 60-second timeout, and two retries. Permit client
  injection so tests never touch the network.

- [ ] **Step 6: Run targeted tests GREEN**

  Run the Task 2 command and confirm exact pass count with no network traffic.

- [ ] **Step 7: Run existing DeepSeek-facing regressions**

  Run:
  `uv run pytest -p no:cacheprovider tests/test_sec_qa.py tests/test_claim_support.py -q`

  Existing Stage 2 provider behavior must remain unchanged.

- [ ] **Step 8: Record the checkpoint**

  Run `git diff --check` and `git status --short`; do not commit.

### Task 3: Add a standalone proposal cache

**Files:**
- Create: `src/thesis_tracker/financial/ai_cache.py`
- Create: `tests/test_financial_ai_cache.py`

**Interfaces:**
- Consumes: `ConceptProviderResponse`, `ConceptProposal`, canonical JSON/hash
  helpers, prompt version, model name.
- Produces:
  - `ProposalCacheKey.build(accession, target, input_payload, prompt_version,
    model_name)`
  - `CachedConceptProposal`
  - `SqliteConceptProposalCache(path).get(key)`
  - `SqliteConceptProposalCache(path).put(key, provider_response, proposal,
    created_at)`

- [ ] **Step 1: Write round-trip and negative-response cache tests**

  Use `tmp_path`. Verify candidate, no-match, and insufficient-evidence
  proposals round-trip with raw response, hashes, model, prompt version,
  timestamp, and usage.

- [ ] **Step 2: Write cache-key isolation tests**

  Independently change accession, target, canonical candidate metadata, prompt
  version, and model. Each change must miss the original record.

- [ ] **Step 3: Write reparse and duplicate-call behavior tests**

  Corrupt a cached parsed JSON record and require a typed cache error rather
  than silent use. In an orchestration-shaped fixture, confirm a valid cache
  hit returns without invoking the provider.

- [ ] **Step 4: Run the tests and verify RED**

  Run:
  `uv run pytest -p no:cacheprovider tests/test_financial_ai_cache.py -q`

  Expected: import failure because the cache module is absent.

- [ ] **Step 5: Implement SQLite cache**

  Create the parent directory, initialize one table with a primary-key hash,
  use parameterized SQL, commit each write atomically, and close connections
  through context managers. Do not cache provider/network errors.

- [ ] **Step 6: Run targeted tests GREEN and record checkpoint**

  Run the Task 3 tests, `git diff --check`, and `git status --short`; do not
  commit.

### Task 4: Extract filing-local candidates and validate proposals

**Files:**
- Create: `src/thesis_tracker/financial/semantic_candidates.py`
- Create: `src/thesis_tracker/financial/ai_validation.py`
- Create: `tests/test_financial_semantic_candidates.py`
- Create: `tests/test_financial_ai_validation.py`
- Modify: `src/thesis_tracker/financial/sec_source.py`
- Modify: `src/thesis_tracker/financial/models.py`

**Interfaces:**
- Produces from `semantic_candidates.py`:
  - `SemanticFactCandidate` with exact Decimal/provenance/context fields
  - `PresentationMetadata`
  - `FilingSemanticContext`
  - `extract_filing_semantic_context(boundary, xbrl)`
  - `build_ai_candidate_payload(context, target_concept)`
- Extends `Stage3Inputs` with
  `semantic_contexts: tuple[FilingSemanticContext, ...]` populated from the
  same XBRL object and boundary used for registered fact extraction.
- Produces from `ai_validation.py`:
  - `ValidationStatus(StrEnum)` (`accepted`, `rejected`)
  - `ValidationEvidence`
  - `ValidatedConceptMapping`
  - `ConceptValidationResult`
  - `validate_concept_proposal(proposal, context, target_boundary)`
- Extends `FactOrigin` with `AI_ASSISTED_VALIDATED` and `FinancialFact` with an
  immutable optional `ai_validation` provenance tuple.

- [ ] **Step 1: Write semantic extraction tests**

  Build a complete fake XBRL income-statement dataframe plus raw facts,
  contexts, element catalog, and boundary. Verify:

  - only income-statement concepts are included;
  - values remain Decimal rather than dataframe floats;
  - QName normalization is reversible;
  - label/parent/level/weight/documentation are retained;
  - dimension presence, unit, period, context, and accession are retained;
  - the prompt payload excludes unrelated statements and future accessions.

- [ ] **Step 2: Run semantic tests RED**

  Run:
  `uv run pytest -p no:cacheprovider tests/test_financial_semantic_candidates.py -q`

  Expected: missing module failure.

- [ ] **Step 3: Implement minimal extraction**

  Join statement presentation rows to raw XBRL facts by normalized QName. Use
  the raw fact strings for Decimal values. Include consolidated numeric rows
  and dimensioned/abstract rows only as non-numeric presentation evidence.
  Never choose a candidate during extraction.

- [ ] **Step 4: Run semantic tests GREEN**

  Run Task 4 semantic tests and existing
  `tests/test_financial_sec_source.py`; confirm no source regression.

- [ ] **Step 5: Write validator tests**

  Use literal facts for these independent cases:

  - hallucinated QName absent from the target accession is rejected;
  - real same-accession candidate with `100 - 61 != 40` is rejected;
  - `100 - 60 == 40` with identical USD duration/context and no dimensions is
    accepted;
  - a numerically matching component relation is rejected without an explicit
    complete calculation relationship;
  - a candidate from another accession is rejected;
  - mismatched unit, dates, context family, dimensions, period type, or filing
    boundary is rejected;
  - `not_equivalent` and `component` cannot become a single aggregate mapping;
  - two surviving equivalent candidates remain rejected as ambiguous;
  - an accepted result lists equation operands, contexts, accession, candidate
    QName, source fact IDs, and validator path.

- [ ] **Step 6: Run validator tests RED**

  Run:
  `uv run pytest -p no:cacheprovider tests/test_financial_ai_validation.py -q`

  Expected: missing validator module failure.

- [ ] **Step 7: Implement exact validator**

  Match revenue, candidate cost, and gross profit only within the same
  accession, unit, start/end dates, consolidated context family, and boundary.
  Evaluate the equation with Decimal `==`. Accept exactly one proven concept;
  all other paths return structured rejection evidence.

- [ ] **Step 8: Run Task 4 tests GREEN and record checkpoint**

  Run both new test files plus `tests/test_financial_sec_source.py`, then
  `git diff --check` and `git status --short`; do not commit.

### Task 5: Orchestrate eligible fallback and ephemeral resolver overlay

**Files:**
- Create: `src/thesis_tracker/financial/ai_fallback.py`
- Modify: `src/thesis_tracker/financial/resolver.py`
- Modify: `src/thesis_tracker/metrics/financial.py`
- Modify: `tests/test_financial_fact_resolver.py`
- Modify: `tests/test_gross_margin_ai_fallback.py`

**Interfaces:**
- Adds to `resolve_quarterly_fact`:
  `concept_overlay: tuple[str, ...] = ()`. The allowed concepts are the
  permanent canonical registry tuple plus this immutable per-call tuple.
- Produces:
  - the existing `AiFallbackConfig(enabled: bool)` from Task 1; prompt version
    is read from the proposal module's `PROMPT_VERSION`
  - `AiAttemptStatus(StrEnum)` (`accepted`, `rejected`, `no_match`,
    `insufficient_evidence`, `provider_error`, `invalid_response`)
  - `AiConceptAttempt` with full audit serialization
  - `AiConceptFallback(provider, cache, config).recover(...)`
  - `AiRecoveryResult(mapping, mapped_facts, attempt)`
- Adds optional `ai_fallback` and `semantic_contexts` arguments to
  `compute_gross_margin_trend` and an `ai_attempts` tuple to
  `GrossMarginTrend`.

- [ ] **Step 1: Write resolver overlay tests**

  Prove a custom fact is unresolved without an overlay and resolves with an
  explicit overlay only when all existing period/context/unit/dimension rules
  pass. Prove the overlay cannot rescue a different accession or ambiguous
  context. Prove the permanent registry object is unchanged after the call.

- [ ] **Step 2: Run overlay tests RED**

  Run the named resolver tests. Expected: unexpected keyword argument
  `concept_overlay`.

- [ ] **Step 3: Implement minimal immutable overlay**

  Merge the registered tuple and per-call tuple with deterministic
  de-duplication inside the call only. Do not mutate module state. Propagate AI
  validation records into derived facts while leaving derived origin intact.

- [ ] **Step 4: Run resolver tests GREEN and full resolver regression**

  Run `tests/test_financial_fact_resolver.py` and confirm all pre-existing
  cases have identical outcomes.

- [ ] **Step 5: Complete the twelve fallback behavior tests**

  Add or complete tests for:

  1. deterministic success no provider call;
  2. registry gap provider invocation;
  3. hallucination rejection;
  4. failed equation rejection;
  5. fully valid acceptance;
  6. component rejection;
  7. cross-accession rejection;
  8. period failure no provider call;
  9. provider exception preserving original diagnostic;
  10. cache hit avoiding a second call while revalidating;
  11. accepted observation with origin, candidate, equation, model, prompt,
      hashes, validator path, and source IDs;
  12. disabled serialization identical to baseline.

  Also test no-match/insufficient responses, invalid JSON, and an
  AI-validated source participating in a derived quarter.

- [ ] **Step 6: Run fallback tests RED**

  Run:
  `uv run pytest -p no:cacheprovider tests/test_gross_margin_ai_fallback.py -q`

  Expected: failures at the missing orchestration/audit interfaces.

- [ ] **Step 7: Implement eligibility and orchestration**

  Run the current deterministic flow first. Invoke `recover` only for an
  eligible failure and matching semantic context. Cache lookup precedes the
  provider. Parse and validate every cached/live proposal. On acceptance,
  construct AI-provenance facts from the exact source candidates, pass the
  per-accession overlay to normal resolution, and compute the observation only
  after resolution. On any error/rejection, retain the original diagnostic and
  append an audit attempt.

- [ ] **Step 8: Run fallback tests GREEN**

  Run Task 5 tests, then all financial tests:

  ```bash
  uv run pytest -p no:cacheprovider \
    tests/test_financial_fact_resolver.py \
    tests/test_financial_sec_source.py \
    tests/test_gross_margin_trend.py \
    tests/test_financial_semantic_candidates.py \
    tests/test_financial_ai_concepts.py \
    tests/test_financial_ai_cache.py \
    tests/test_financial_ai_validation.py \
    tests/test_gross_margin_ai_fallback.py
  ```

- [ ] **Step 9: Record the checkpoint**

  Run `git diff --check` and `git status --short`; do not commit.

### Task 6: Run the controlled DeepSeek experiment

**Files:**
- Create: `docs/stage3-ai-concept-experiment.md`
- Do not modify deterministic code based only on model output.

**Interfaces:**
- Consumes the completed provider, cache, source contexts, validator, metric,
  and current runtime corpus.
- Produces a human-readable audit report with exact before/after and API/cache
  statistics.

- [ ] **Step 1: Re-record the five-company deterministic baseline**

  Run ORCL, XOM, LITE, SNDK, and JPM with fallback disabled. Record requested
  periods, observations, final failures, recovery history, periods,
  accessions, values, formulas, concepts, and source fact IDs.

- [ ] **Step 2: Check API availability without exposing the key**

  Check only whether `DEEPSEEK_API_KEY` is present. Do not print its value. If
  absent, exercise the provider-error path and report that the live portion
  could not run; continue all deterministic verification.

- [ ] **Step 3: Run the eligible unresolved subset with a fresh cache**

  Enable AI for ORCL, XOM, and LITE. Include SNDK and JPM in the run to prove
  zero calls for non-eligible cases. Record each call, proposal, validator
  evidence, status, and any accepted observation. Do not edit rules to improve
  acceptance.

- [ ] **Step 4: Repeat using the same cache**

  Confirm candidate/no-match/insufficient responses cause zero duplicate model
  calls. Record cache-hit counts and token usage from the first run.

- [ ] **Step 5: Inspect every changed observation**

  For each change, document original failure, raw proposal, parsed proposal,
  exact validator evidence, accepted/rejected status, final value, formula,
  origin, resolver path, and source fact IDs. If no observation changes, state
  zero exactly.

- [ ] **Step 6: Write the experiment report**

  Include the five-company comparison, proposal list, rejection reasons,
  provider calls, cache hits, model, prompt version, token usage/cost when
  available, API failures, and a keep/remove recommendation.

### Task 7: Final 15-company regression and verification

**Files:**
- Update: `docs/stage3-ai-concept-experiment.md`
- Verify all changed files; do not commit.

**Interfaces:**
- Produces final evidence for deterministic invariance, AI-only additions,
  failure distribution, tests, lint, and diff integrity.

- [ ] **Step 1: Run all 15 companies with fallback disabled**

  Compare the canonical serialized result to the recorded 93-observation
  baseline. Require exact equality for period, value, formula, concepts,
  origin, accessions, resolver paths, and source fact IDs.

- [ ] **Step 2: Run all 15 companies with fallback enabled**

  Confirm deterministic successes do not call the provider and remain exact.
  Count new observations only when an `accepted` AI attempt and complete
  validation provenance are present. Report final failure distribution and
  recovery histories separately.

- [ ] **Step 3: Run targeted regression tickers**

  Print the 24 NVDA/AMD/AAPL observation fingerprints and compare them to the
  current report. Recheck LITE, ORCL, XOM, SNDK, and JPM classifications.

- [ ] **Step 4: Run lint**

  Run `uv run ruff check .`. Expected: exit 0 and `All checks passed!`.

- [ ] **Step 5: Run the full suite**

  Run `uv run pytest -p no:cacheprovider`. Expected: all collected tests pass;
  report the exact count and every warning.

- [ ] **Step 6: Run diff integrity and scope checks**

  Run:

  ```bash
  git diff --check
  git status --short
  git diff --name-only
  ```

  Confirm no Stage 1/Stage 2 implementation file changed and no secret/cache
  artifact is tracked.

- [ ] **Step 7: Final report**

  Report in Chinese: architecture, trigger policy, files, five-company
  before/after, proposals, acceptances/rejections, observation delta, exact
  preservation of the original 93, API/cache/token/cost figures, verification
  commands, keep/remove recommendation, risks, and unresolved issues. State
  explicitly that no commit or push was created.
