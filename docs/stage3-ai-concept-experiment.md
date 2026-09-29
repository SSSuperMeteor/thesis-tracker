# Stage 3 AI-Assisted Concept Resolver Experiment

Date: 2026-09-19

## Scope and safety boundary

This experiment adds an optional sidecar to the existing deterministic
`gross_margin_trend` path. The permanent concept registry and deterministic
resolver remain authoritative and unchanged in behavior. The execution order
is:

1. run registered-concept resolution and the existing deterministic
   gross-profit recovery;
2. only after a final semantic `registry_gap` or `custom_concept_only`, build a
   filing-local proposal payload;
3. ask DeepSeek for a structured candidate interpretation;
4. reparse the response with a strict schema;
5. validate the candidate in Python against the same filing/accession, unit,
   duration, context, dimensions, and exact Decimal equation;
6. expose a validated concept through an immutable per-call overlay only.

The sidecar cannot choose a filing, period, context, unit, numeric value, or
final mapping. It cannot update `CONCEPT_REGISTRY`. Cached proposals are
revalidated on every use. Provider and schema failures preserve the original
metric diagnostic.

The gross-margin integration only asks for `cost_of_revenue`. It does not run
for `period_unavailable`, `duration_unavailable`, `invalid_context`,
`ambiguous`, `not_applicable`, or any existing successful observation.

## Deterministic baseline

The five-company controlled baseline was reproduced before enabling the
sidecar:

| Ticker | Requested | Observations | Baseline failure |
|---|---:|---:|---|
| ORCL | 8 | 0 | seven `registry_gap`; one `invalid_context` |
| XOM | 8 | 0 | eight `registry_gap` |
| LITE | 8 | 7 | one `registry_gap` with `duration_unavailable` recovery history |
| SNDK | 7 | 6 | one `period_unavailable` |
| JPM | 8 | 0 | one metric-level `not_applicable` |

## Live DeepSeek experiment

The experiment used `deepseek-chat`, prompt version
`stage3-concept-v1`, and a fresh standalone SQLite cache. It made 16 first-run
API calls: seven ORCL filings, eight XOM filings, and one LITE filing. SNDK and
JPM made zero calls. A second identical run and the final 15-company run used
the same 16 cache rows and made no additional calls.

| Ticker | Attempts | Proposal pattern | Python result | Before → after |
|---|---:|---|---|---|
| ORCL | 7 | Cloud/software, hardware, and services components; five filings also proposed `CostsAndExpenses` | five `equation_inputs_unavailable`; two `non_aggregate_relation` | 0/8 → 0/8 |
| XOM | 8 | Crude/product purchases and production/manufacturing components; `CostsAndExpenses` as a possible aggregate | rejected: `equation_inputs_unavailable` | 0/8 → 0/8 |
| LITE | 1 | cost excluding DDA and cost amortization, both classified as components | rejected: `non_aggregate_relation` | 7/8 → 7/8 |
| SNDK | 0 | none; fiscal-boundary failure is ineligible | no attempt | 6/7 → 6/7 |
| JPM | 0 | none; deterministic applicability gate runs first | no attempt | not applicable → not applicable |

No proposal was accepted and no observation was added.

### ORCL

Across the seven eligible filings, the model consistently identified the
issuer-specific expense concepts, including combinations of:

- `orcl:CloudAndSoftwareExpenses` or
  `orcl:CloudServicesAndLicenseSupportExpenses`;
- `orcl:HardwareExpenses`;
- `orcl:ServicesExpense`;
- `us-gaap:CostsAndExpenses` as a possible aggregate.

The component proposals cannot become an aggregate without a complete
filing-local calculation relationship. Where proposed, `CostsAndExpenses`
cannot be validated as cost of revenue because the filing does not provide the
required consolidated `GrossProfit` operand for
`Revenue - CandidateCost == GrossProfit`. Five attempts therefore stopped at
`equation_inputs_unavailable`; the two component-only attempts stopped at
`non_aggregate_relation`. The validator did not sum the custom components and
did not relabel total operating costs.

The newest ORCL filing remained `invalid_context` and never called the model
because its filing and fact fiscal-year metadata conflict.

### XOM

The model consistently proposed:

- `xom:CrudeOilAndProductPurchases` as a component;
- `xom:ProductionAndManufacturingExpenses` as a component;
- `us-gaap:CostsAndExpenses` as a possible aggregate;
- in one filing, additional tax and depreciation components.

No component was summed. `CostsAndExpenses` includes costs outside traditional
cost of revenue, and the filing supplies no consolidated `GrossProfit` fact
with which to prove the required equation. All eight proposals were rejected
as `equation_inputs_unavailable`. XOM remained eight `registry_gap` failures.

### LITE

For FY2025-Q4 the model proposed
`CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization` and
`CostOfGoodsAndServicesSoldAmortization`, explicitly as two components. No
filing calculation relationship in this experiment proved a canonical
aggregate, so the validator returned `non_aggregate_relation`. AI was not
allowed to bridge the existing RCI/RCE cross-concept FY-minus-9M issue; the
original `registry_gap` and `duration_unavailable` recovery history remained.

## Cache and usage

- Fresh first run: 16 API calls and 16 cache inserts.
- Immediate repeat: 16 cache hits, zero duplicate API calls.
- Full 15-company regression: the cache remained exactly 16 rows.
- Final controlled-run prompt tokens: 236,792.
- Final controlled-run completion tokens: 6,383.
- Final controlled-run total tokens: 243,175.
- Billed cost was not present in provider responses, so no cost estimate is
  asserted.

An earlier 16-call run exposed that dataframe NaN metadata had been serialized
as a dimension/weight value. After fixing that input bug, the experiment was
deliberately repeated with a fresh cache rather than reusing stale hashes.
Total actual API usage across both runs was therefore 32 calls and 486,612
tokens (473,660 prompt; 12,952 completion). Only the final 16-row cache and its
results are used above.

The cache key includes accession, canonical target, canonicalized filing-local
metadata/input hash, prompt version, and model name. Candidate, `no_match`, and
`insufficient_evidence` responses are cacheable; provider/network failures are
not.

## Validator behavior found during the experiment

The live proposal shape exposed one diagnostic bug: when a proposal listed a
possible aggregate together with component concepts, the validator initially
returned on the first component and did not inspect the aggregate. The fix is
general and fail-closed: components are excluded from mapping, while any
separately proposed aggregate is still independently tested. A regression test
proves that a component cannot hide a valid aggregate or become one itself.

A second regression distinguishes absent equation operands from incompatible
contexts. ORCL/XOM now report `equation_inputs_unavailable` rather than the less
accurate `context_mismatch` when `GrossProfit` is absent. Neither fix changes a
numeric result or loosens validation.

## Full 15-company regression

The final run requested 119 periods. Before and after enabling the sidecar:

- observations: 93 → 93;
- final failures: 16 `registry_gap`, one `invalid_context`, one
  `period_unavailable`, and one metric-level `not_applicable`;
- AI attempts: 16, all rejected;
- new observations: zero;
- all 93 observation fingerprints were exactly unchanged.

The fingerprint comparison covered period end, fiscal year/quarter, Decimal
value, formula, concept, fact value, reported/derived origin, accession,
resolver path, and source fact IDs. NVDA, AMD, and AAPL retained all 24 prior
observations exactly and made zero AI calls.

## Deterministic applicability gate

After the experiment, Python gained a deterministic applicability gate in front
of the cost-of-revenue sidecar. The sidecar is only entered when the requested
filing boundary discloses at least one registered operand of the equation
`revenue - cost_of_revenue == gross_profit`:

- `us-gaap:GrossProfit`, or
- `us-gaap:CostOfRevenue` / `us-gaap:CostOfGoodsAndServicesSold`.

When neither operand exists, the observation is classified `not_applicable`
(concept `cost_of_revenue`), no semantic proposal is built, and the provider is
never called. The gate is duration-agnostic, so a fact ending at the boundary
proves concept disclosure even when its duration is not the single quarter, and
it is generic data-driven logic with no ticker-specific branch.

The sidecar therefore handles only semantic failures where a legitimate concept
may still exist — for example LITE, whose filing discloses
`us-gaap:GrossProfit` while the cost concept is an issuer extension.

Re-verified on the same 15-company set after adding the gate (same real SEC
inputs; a deterministic counting provider measured the AI calls):

| Metric | Before gate | After gate |
|---|---:|---:|
| Requested periods | 119 | 119 |
| Observations (baseline → sidecar enabled) | 93 → 93 | 93 → 93 |
| AI attempts | 16 | 1 (LITE only) |
| ORCL / XOM attempts | 7 / 8 | 0 / 0 |
| New observations | 0 | 0 |
| Failure codes | 16 `registry_gap`, 1 `invalid_context`, 1 `period_unavailable`, 1 SIC `not_applicable` | 1 `registry_gap` (LITE), 15 `not_applicable` (ORCL/XOM), 1 `invalid_context`, 1 `period_unavailable`, 1 SIC `not_applicable` |

All 93 baseline observation fingerprints remained identical, and NVDA (8
observations) and JPM (SIC `not_applicable`) were unaffected with zero AI
attempts.

## Recommendation

Keep the sidecar as an optional experimental/audit capability, not as a
coverage mechanism enabled by default; the deterministic applicability gate is
what keeps it scoped to failures where a semantic concept can still exist. The
model produced useful and accountingly sensible candidate classifications for
ORCL, XOM, and LITE, while Python rejected every unprovable mapping and
preserved all deterministic results. This demonstrates safe degradation and
useful semantic diagnostics, but it did not improve gross-margin coverage in
this corpus.

Before broader production use, reduce prompt size (the filing-local income
statement payload is safe but token-heavy), add provider observability for
billed cost, and collect cases where a genuine issuer aggregate plus
`GrossProfit` allows exact validation. Strict equality should remain unchanged
until real XBRL decimals/precision evidence demonstrates a legitimate rounding
rejection.
