# Stage 3 AI-Assisted Financial Concept Fallback Design

Date: 2026-09-19

## Purpose

Experimentally add an AI-assisted concept resolver to the existing Stage 3
financial-fact foundation without changing the authority of the deterministic
resolver. The model may propose filing-local semantic mappings only. Python
must independently prove every accepted mapping before the mapped facts can
re-enter the existing period, duration, unit, context, dimension, accession,
and ambiguity checks.

The experiment is successful if it safely produces useful proposals, rejects
unsupported proposals, preserves the current deterministic baseline, degrades
to that baseline when disabled or unavailable, and makes every attempt
auditable. It does not need to add an observation.

## Scope

This slice applies only to `gross_margin_trend` and only to the existing Stage
3 unresolved cases. It does not implement another metric, modify Stage 1 or
Stage 2, change parser or normalizer versions, or persist model proposals into
the global concept registry.

The real-data experiment covers ORCL, XOM, LITE, SNDK, and JPM first, followed
by the existing 15-company gross-margin stress set. The baseline is:

- 119 potential requested quarters;
- JPM metric-level `not_applicable`;
- 111 applicable quarters;
- 93 deterministic observations;
- 18 unavailable observations.

## Non-negotiable authority boundary

The control flow is:

```text
deterministic resolution
  -> success or non-semantic failure: return without AI
  -> eligible semantic failure: build filing-local input
  -> cached or live model candidate proposal
  -> strict response schema
  -> deterministic Python validation
  -> validated ephemeral mapping
  -> existing resolver constraints
  -> metric computation or original structured failure
```

The model cannot return a numeric fact, select an accession, establish a
fiscal period, approve a mapping, write the permanent registry, waive a hard
constraint, or choose between ambiguous surviving contexts. Model confidence
has no authority.

## Trigger policy

AI fallback is eligible only when the current canonical failure is:

- `registry_gap`; or
- `custom_concept_only`.

It is not invoked for:

- deterministic success;
- `not_applicable`;
- `period_unavailable`;
- `duration_unavailable`;
- `invalid_context`;
- `ambiguous`;
- `source_stale`, `unresolved`, or `true_missing` in this first version;
- accession, unit, context, dimension, or filing-boundary mismatch.

JPM remains governed by the existing deterministic SEC SIC applicability
gate. SNDK's missing fiscal boundary and ORCL's newest invalid fiscal metadata
do not trigger AI.

## Components

### Filing-local semantic source

A new financial semantic-source component derives candidate metadata from the
same bounded filing families already selected by Stage 1 and loaded by the
Stage 3 filing adapter. It does not run an independent latest-filing query.

For each `FilingBoundary`, it records only the consolidated, monetary,
duration facts presented on the filing's income statement. Abstract rows and
dimensioned rows may be included as presentation metadata but cannot become
numeric candidates. The record includes:

- ticker, form, accession, filed date, and reporting boundary;
- concept QName;
- filing label and available taxonomy documentation;
- standard/custom namespace classification;
- income-statement position, parent concept, parent abstract, level, and
  calculation/presentation weight where available;
- unit and period type;
- period start and end;
- context ID and whether dimensions are present;
- Decimal value retained only for Python validation and the narrowly scoped
  equation neighborhood sent to the model;
- related registered revenue and gross-profit facts from the same filing and
  context family.

The input builder selects the target concept, issuer-defined income-statement
concepts, relevant standard concepts, and the smallest equation neighborhood
needed to interpret cost of revenue. It never serializes the complete filing,
filing text, notes, unrelated statements, future filings, or facts from an
unrelated accession.

### Proposal schema

The model response is parsed with a strict Pydantic schema that rejects extra
fields. Its semantic content is equivalent to:

```json
{
  "target": "cost_of_revenue",
  "status": "candidate",
  "candidate_concepts": [
    {
      "concept": "issuer:ConceptQName",
      "semantic_relation": "equivalent",
      "reason": "short filing-local explanation",
      "evidence_fields": ["label", "parent_concept"]
    }
  ]
}
```

Allowed statuses are `candidate`, `no_match`, and
`insufficient_evidence`. Allowed relations are `equivalent`, `component`,
`possible_aggregate`, and `not_equivalent`. The target must equal the request
target. Candidate concepts and evidence field names must be non-empty and
unique. No approval, confidence override, accession selection, numeric fact,
or calculation result is accepted in the schema.

### DeepSeek provider

The provider follows the existing repository pattern: `load_settings()` for
`DEEPSEEK_API_KEY` and `DEEPSEEK_BASE_URL`, the installed OpenAI-compatible
client, model `deepseek-chat` unless explicitly configured, JSON response
format, temperature zero, bounded timeout, and bounded retries.

The provider implements a small protocol so unit tests use a deterministic
fake. API/provider/parse failures are caught at the fallback boundary and
never break deterministic metric computation. Secrets are neither serialized
nor logged.

The prompt identifies all supplied fields as untrusted filing data, forbids
external knowledge and numeric conclusions, and requests only candidate
semantic relationships. The prompt/template has an explicit version string.

### Cache

AI responses use a separate runtime SQLite database, not the Stage 1 corpus.
The default path is under ignored local runtime data. Tests supply a temporary
path.

The cache key is the SHA-256 hash of:

- accession;
- canonical target;
- canonical JSON candidate input;
- prompt version;
- requested model.

The cache stores candidate, no-match, insufficient-evidence, and structured
parse results. Provider/network failures are not cached because they do not
represent a semantic response. Each cache record stores:

- key, accession, target, prompt version, and model;
- canonical input JSON and input hash;
- raw structured response and response hash;
- parsed proposal JSON;
- creation timestamp;
- available prompt/completion/total token usage.

Cache reads re-run strict schema parsing and Python validation. A cached
proposal is never a cached approval.

### Deterministic validator

The validator consumes the proposal and the exact filing-local semantic
context used to build the prompt. It does not call the model.

All accepted candidates must pass:

1. The concept exists in the target accession's parsed XBRL input.
2. The candidate is a monetary duration fact with a supported unit.
3. The candidate has no dimensions when a consolidated fact is required.
4. Its context, dates, filing metadata, and accession match the target
   boundary.
5. The proposal relation is compatible with the attempted recovery.
6. Accounting structure proves the mapping.

For a single candidate proposed as equivalent to `cost_of_revenue`, the first
version accepts only when same-accession, same-unit, same-duration,
same-context-family revenue and gross-profit facts prove, using `Decimal`:

```text
Revenue - CandidateCost == GrossProfit
```

Equality is exact. No tolerance is introduced. If later real filing evidence
shows a legitimate XBRL rounding rejection, tolerance requires a separate
design based on XBRL decimals or precision.

A component cannot be treated as an aggregate. Multiple component concepts
may be accepted only if a filing calculation relationship explicitly proves
that the complete candidate set is the cost side of the revenue/gross-profit
equation and exact Decimal validation succeeds. In the absence of such a
relationship, the proposal is rejected even if the arithmetic happens to
match.

RCI/RCE cross-concept derivation is not justified by the model's semantic
opinion. Each duration needed by FY-minus-9M must independently contain
filing-local evidence that the two concepts are equal for that exact context.
Equality in only the FY context does not prove equality in the 9M context.

Validation produces structured evidence including the candidate QName,
relation, equation operands, exact comparison result, context IDs, units,
periods, dimensions, accession, source fact IDs, and validator path. A failed
validation contains a precise rejection reason and leaves the original fact
failure canonical.

### Ephemeral mapping and resolver re-entry

An accepted validation creates an immutable per-accession mapping overlay. It
is passed explicitly to a sidecar resolution call and is never added to
`CONCEPT_REGISTRY` or shared with another filing.

The existing deterministic resolver algorithm and default call behavior remain
unchanged. Its call surface may gain one explicit immutable per-call concept
overlay; when omitted, the candidate set and serialized output are identical
to the current resolver. The sidecar prepares validated candidate facts and
passes only the accepted per-accession overlay into the same hard checks for
unit, duration, boundary, context, dimensions, accession lineage, derivation,
and ambiguity. If this second pass fails, no fact is returned.

AI-validated reported facts use an explicit `ai_assisted_validated` origin and
carry validation metadata. A later YTD or Q4 derivation remains marked as
derived while preserving the AI validation chain in resolver path,
validation evidence, and source fact IDs.

### Metric orchestration and audit

`gross_margin_trend` receives an optional AI fallback dependency and filing
semantic contexts. When the dependency is absent or disabled, the output is
identical to the current deterministic result.

Every AI attempt serializes:

- ticker, target period, accession, target concept, and original failure;
- whether the response came from cache;
- model and prompt version;
- input and response hashes;
- timestamp and available token usage;
- raw structured response;
- parsed proposal;
- accepted/rejected/error status;
- deterministic validation evidence or rejection reason;
- final observation fact IDs when accepted.

An API or parse error records an audit attempt and returns the original
diagnostic. It does not invent a new failure taxonomy code or suppress the
original failure.

## Expected real-data behavior

### ORCL

The model may identify `orcl:CloudAndSoftwareExpenses`,
`orcl:HardwareExpenses`, and `orcl:ServicesExpense` as cost components. They
must remain rejected unless filing calculation relationships prove a complete
aggregate and the revenue/gross-profit equation is available and exact. The
newest invalid fiscal-year context bypasses AI entirely.

### XOM

The model may identify purchases and production/manufacturing expenses or may
report insufficient evidence. `CostsAndExpenses` cannot be relabeled as COGS,
and expense components cannot be summed without explicit calculation proof.
Failure is expected to remain visible.

### LITE

The model may describe RCI and RCE as related. FY equality alone cannot prove
9M equality, so FY2025-Q4 remains unavailable unless both required durations
carry deterministic filing-local equivalence evidence.

### SNDK and JPM

SNDK remains `period_unavailable` and does not invoke AI. JPM remains one
deterministic `not_applicable` result and does not invoke AI.

## Test requirements

Unit tests use deterministic providers and temporary cache databases. No test
calls a real API. They cover:

1. deterministic success never calls AI;
2. `registry_gap` can trigger AI;
3. a hallucinated concept is rejected;
4. a filing-local candidate whose equation fails is rejected;
5. equation, unit, period, context, and accession all passing produces an
   accepted fact;
6. a component cannot masquerade as an aggregate;
7. a candidate cannot cross accessions;
8. `period_unavailable` does not trigger AI;
9. API/provider failure preserves the original diagnostic;
10. a cached proposal avoids another provider call and is revalidated;
11. an accepted observation contains complete AI provenance;
12. disabled fallback is byte-for-byte equivalent after serialization to the
    deterministic baseline.

Additional boundary tests cover strict schema rejection, no-match caching,
cache key changes for prompt/input/model changes, malformed SIC-independent
paths, dimensioned candidates, unit mismatch, duplicate candidates, and
derived-fact propagation of AI provenance.

## Real-data experiment and reporting

The experiment runs in this order:

1. record the current ORCL/XOM/LITE/SNDK/JPM baseline;
2. enable the real DeepSeek provider only for eligible unresolved
   observations;
3. record calls, cache hits, proposals, validator evidence, rejection reasons,
   token usage, and any accepted values;
4. repeat from cache to prove no duplicate calls;
5. run all 15 companies with AI enabled;
6. compare every original deterministic observation by ticker, period, value,
   formula, concept, origin, and source fact IDs;
7. report exact failure-code and observation deltas.

No accepted observation may replace or alter an existing deterministic
success. New observations may arise only from validator-approved proposals.
If `DEEPSEEK_API_KEY` is unavailable or an API request fails, the run reports
the condition and preserves the baseline.

## Files and ownership

The implementation is expected to keep responsibilities separated:

- `src/thesis_tracker/financial/semantic_candidates.py`: filing-local input
  and candidate records;
- `src/thesis_tracker/financial/ai_concepts.py`: proposal schema, provider,
  prompt, hashing, and response parsing;
- `src/thesis_tracker/financial/ai_cache.py`: SQLite cache;
- `src/thesis_tracker/financial/ai_validation.py`: deterministic accounting
  and filing-boundary validation;
- `src/thesis_tracker/financial/ai_fallback.py`: trigger policy,
  orchestration, ephemeral mapping, and audit result;
- narrowly scoped changes to financial models, source adapter, resolver
  call surface, and gross-margin orchestration;
- focused unit tests for each boundary and one real-data experiment report.

Existing Stage 1 and Stage 2 modules remain untouched.

## Risks and controls

- **Prompt injection in filing labels/documentation:** all filing strings are
  untrusted JSON data; the system prompt forbids following embedded
  instructions; Python treats output only as candidate identifiers.
- **Hallucinated concepts:** membership in the target accession is mandatory.
- **Numerical coincidence:** arithmetic alone is insufficient for component
  aggregation; presentation/calculation structure is also required.
- **Cache staleness:** prompt version, model, accession, and full canonical
  input participate in the key; cached responses are revalidated.
- **Hidden baseline changes:** fallback is optional, success-first, and covered
  by serialized disabled-mode and 15-company comparisons.
- **Cost expansion:** only unresolved eligible observations call the model;
  identical inputs use cached successes and no-match responses.
- **Non-reproducible AI output:** raw response, hashes, model, prompt version,
  timestamp, token usage, and deterministic validation evidence are retained.

## Completion criteria

- All twelve required behavioral tests pass without real API calls.
- Existing deterministic tests and Stage 1/Stage 2 regressions pass.
- ORCL/XOM/LITE/SNDK/JPM before/after results are recorded.
- The final 15-company run proves all 93 original observations unchanged.
- Every new observation, if any, has complete AI-assisted validation
  provenance.
- API failure and disabled mode reproduce the deterministic baseline.
- `uv run ruff check .`, `uv run pytest -p no:cacheprovider`, and
  `git diff --check` pass.
- No commit or push is created.
