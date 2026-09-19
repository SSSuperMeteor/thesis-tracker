---
name: failure-taxonomy
description: Canonical definitions and decision boundaries for thesis-tracker financial-fact failure codes (period_unavailable, source_stale, duration_unavailable, invalid_context, ambiguous, registry_gap, custom_concept_only, not_applicable, unresolved, true_missing). Use this whenever a Stage 3 observation fails, whenever you must label or report a failure, whenever you must decide which recovery path applies, and during any multi-company stress test. Use it even if the failure looks obvious — multiple codes may appear in one observation's recovery_history, but a failed observation has exactly one canonical final_failure, and which one it is is not guessable. Do not invent new failure labels without reading this first.
---

# Failure Taxonomy

Authoritative definition of the failure codes. Everything else in the repo
references these; do not redefine them elsewhere.

## Evaluation order

Evaluate in this order. The order decides which recovery path is attempted
next — several codes describe the same symptom at a different stage.

```
1. not_applicable      metric is meaningless for this issuer
2. source_stale        newer official filing exists, aggregation source lags
3. registry_gap        facts exist, our concept registry lacks the mapping
4. custom_concept_only issuer discloses only via custom taxonomy concepts
5. invalid_context     candidate exists, its context cannot be proven compatible
6. period_unavailable  target reporting period cannot be established
7. duration_unavailable period exists, required duration missing/underivable
8. ambiguous           >1 candidate survives all hard constraints
9. unresolved          known-not-missing, but no safe rule produces a result
10. true_missing       genuinely not disclosed
```

Period comes before duration because `duration_unavailable` presupposes that
the period was established. Ask in this order:

```
does this fiscal period exist?   → no  → period_unavailable
              ↓ yes
does it carry the target duration? → no → duration_unavailable
```

**Implementation constraint**: the period-existence check must not apply a
duration filter. If it does, "the quarter exists but only as YTD" collapses
into `period_unavailable` and the real cause is lost.

## First match is not the verdict

A code that triggers a recovery attempt is a **step in the chain**, not
necessarily the final classification. Recovery can succeed at that step and
the observation still fail later, for a different reason.

```
Company Facts lags          → source_stale        (recovery triggered)
→ filing-level XBRL recovery → succeeds
→ only custom concepts found → custom_concept_only (what actually blocked it)
```

Record both:

```
recovery_history: ["source_stale"]
final_failure:    "custom_concept_only"
```

`final_failure` is the code that actually prevented a valid result, and it is
what aggregate counts report. `recovery_history` is every code that fired on
the way there — without it, diagnostics lose the fact that a recovery path
ran at all, and the next round has to rediscover it.

Stop evaluating only when a code both matches and has no registered recovery
left to attempt.

`true_missing` is almost always wrong as a first classification. Reaching it
requires having ruled out 1–9 *and* exhausted their recovery paths.

## Code definitions

### not_applicable
Metric is not economically meaningful for this issuer or industry.
Example: conventional operating-company margin metrics against a bank (JPM).
→ Not a data-quality failure. Do not count it against coverage. Do not
substitute an analogous concept.

### source_stale
An official filing exists that the aggregation source has not yet reflected.

Possible real case under investigation: XOM. Do **not** treat it as a
confirmed `source_stale` regression case until Company Concept or
filing-level evidence shows the aggregation source actually lacks the
latest-period fact. The same symptom is produced by a resolver querying the
wrong concept or duration, and the two require opposite fixes.

**Requires independent evidence** that the newer filing exists — SEC
submissions metadata, not an assumption.

Recovery: confirm latest filing → locate accession + reporting period →
filing-level XBRL → **re-enter the normal validation pipeline**. A fact from
the newest filing is not thereby valid.

### registry_gap
Structured facts exist and are usable, but our concept registry has no
validated mapping for them. This is our coverage problem, not the issuer's.

Recovery: add the mapping only with demonstrated semantic equivalence, plus a
regression test. Numeric resemblance is not evidence of equivalence.

### custom_concept_only
The disclosure itself depends on an issuer-specific custom concept; no
acceptable standard concept exists.

Distinction from `registry_gap`: registry_gap = we failed to register an
otherwise standard concept. custom_concept_only = there is no standard
concept to register.

Recovery: preserve the custom concept and its provenance. Do not promote it
into the canonical registry without explicit semantic evidence.

### invalid_context
A candidate exists but its reporting context cannot be proven compatible:
context belongs to a different period, fiscal metadata conflicts with filing
evidence, dimensional context is incompatible, or the fact cannot be tied to a
unique filing reporting boundary.

FY/FP labels alone are never sufficient proof of compatibility.

Recovery: reject that candidate, try others. Never relax context validation to
raise the success rate.

### period_unavailable
The target reporting period cannot be established from currently valid facts,
*after* freshness has been evaluated.

Do not use when:
- a newer filing exists but the source lags → `source_stale`
- the period exists and only the duration is wrong → `duration_unavailable`

The existence check must be duration-agnostic: a quarter that exists only as
part of a YTD context still exists.

Never map a nearby period onto the requested quarter.

### duration_unavailable
The period is established, but the required duration context does not exist
— typically only YTD or annual flow values where a single quarter is needed.

Derivation is permitted **only** through registered deterministic rules
(YTD differencing, Q4 = FY − 9M), and only when every precondition holds. If
preconditions cannot be proven, keep this code rather than subtracting
anyway.

### ambiguous
Two or more candidates survive all hard constraints and no deterministic rule
selects one. Observed for net income on AVGO / LITE / SNDK / VRT.

Preserve every material candidate plus provenance. Do not resolve by
recency, magnitude, plausibility, or by which one makes a downstream metric
look better. An LLM may interpret candidates semantically; it may not
override a hard constraint.

### unresolved
Residual state: enough evidence to know this is not ordinary missing data,
but no safe rule exists. Prefer `ambiguous` when the specific problem is
multiple surviving candidates.

### true_missing
After exhausting 1–9, the disclosure genuinely appears absent.
Do not substitute a different concept, an earlier period, or zero.

## Recovery is not a validation bypass

Every recovered or derived fact re-enters the normal pipeline: concept →
context → period → duration → unit → ambiguity. Recovery changes where a
fact came from, never whether it must be validated.

## Generality rule

`if ticker == "XOM"` is prohibited unless the accounting behaviour is
genuinely issuer-specific and the reason is written down.

Ask instead: which failure class did this issuer expose? Fix the class, then
re-verify the original issuer **and** the existing regression set.

## Relationship to other skills

- Root cause not yet known → `systematic-debugging` first. This skill labels
  demonstrated failures; it does not perform the investigation.
- Hard constraints that decide validity → `financial-fact-invariants`.
- Running this across many companies → `stress-test-runbook`.

## Diagnostic record

A failed observation should serialize at least:

```
ticker, concept, metric, target_period,
final_failure, recovery_history, source, filing_accession,
candidate_count, candidate_summary, rejection_reason, final_status
```

Aggregate counts are keyed on `final_failure`. `recovery_history` is never
dropped, even when recovery succeeded.

Never flatten distinct codes into a generic `missing`.

## When a failure counts as fixed

The code disappearing is not sufficient. Required: the resulting fact is
provably valid, invariants hold, regression tests pass, representative real
filings pass, and no previously valid case regressed.
