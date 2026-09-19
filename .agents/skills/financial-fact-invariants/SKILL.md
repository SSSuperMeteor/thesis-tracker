---
name: financial-fact-invariants
description: Hard correctness rules for any thesis-tracker task that reads, maps, validates, resolves, derives, stores or computes SEC/XBRL financial facts or metrics. Use this for every Stage 3 change touching concepts, fiscal periods, durations, units, dimensions, filing freshness, resolver logic, YTD/Q4 derivation, market-cap inputs or metric formulas — not only when something is failing. These rules outrank coverage, success rate and making tests pass.
---

# Financial Fact Invariants

These are the rules that cannot be expressed as an assertion in the
validator. Mechanical constraints (units, required provenance fields,
missing≠zero, derived-fact tagging) are enforced in code — if you find one
being enforced only by prose, that is a bug to file, not a rule to restate.

Failure codes are defined in `failure-taxonomy`. Do not redefine them here.

## The boundary

A fact is valid only when its concept, context, period and unit can be
*demonstrated* correct. When they cannot: fail closed with the appropriate
code. A visible failure beats an unproven number, always.

## 1. Period must be proven, not inferred

Never bind a fact to a requested quarter because FY matches, FP says "Q2",
the date is close, it is the newest candidate, or the value looks plausible.

FY/FP metadata is evidence. It is not proof. Proof comes from the filing's
reporting boundary and the context dates.

## 2. Flow and instant facts obey different period logic

Instant facts (balance-sheet items) are valid at a point; their period
identity is anchored by the as-of date plus the filing reporting boundary,
and no duration match is required. That anchors *period identity only* —
concept, dimensions, unit and context still have to pass.

Flow facts (revenue, net income, OCF) are valid over a duration. For them a
matching `period_end` alone is never sufficient.

## 3. Derivation requires proven preconditions, not arithmetic feasibility

YTD differencing and Q4 = FY − 9M are permitted only when every component
fact is independently valid, boundaries align, concepts are equivalent, units
agree, and entity/dimensions are compatible.

Subtraction producing a plausible number is not a precondition.

## 4. Concept semantics beat numerical similarity

Never accept a mapping because the value is near the expected number,
because the label looks similar, or because a data vendor uses that number.
`NetIncomeLoss`, `ProfitLoss` and
`NetIncomeLossAvailableToCommonStockholdersBasic` all look like net income
and are not interchangeable.

## 5. Consolidated ≠ dimensional

Do not silently accept a segment fact where a consolidated one is expected,
and do not drop dimensions to obtain a value. If dimensions prevent unique
resolution, that is `ambiguous`.

## 6. Freshness ranks valid facts; it does not rescue invalid ones

Among facts that already passed validation for the same target period, prefer
the more recently filed version. Recency is a tiebreaker applied last — never
a reason to accept a candidate that failed a constraint.

## 7. Ambiguity stays visible

If multiple candidates survive every hard constraint, return `ambiguous`.
Python owns the final boundary; an LLM may interpret candidates but never
overrides it.

## 8. Metric formulas are contracts

The eight metrics (`accruals_ratio`, `cash_conversion`,
`ar_growth_vs_rev_growth`, `gross_margin_trend`, `net_buyback_yield`,
`diluted_share_count_yoy`, `interest_coverage`, `net_debt_to_ebitda`) have
fixed definitions. Do not adjust a formula to improve data availability. A
formula change is a behaviour change: intentional, documented, tested.

## 9. Passing tests is not evidence of financial correctness

A green suite proves the code matches the test. Correctness-sensitive changes
additionally require representative real-filing validation.

## Prohibited moves

Weakening a validator to raise coverage. Ticker-specific correctness patches.
Editing test expectations to accommodate wrong output. Treating an LLM
selection as authoritative. Declaring completion from pytest alone.

If coverage is low, the answer is a better rule or a new registered recovery
path — never a looser gate.

## Before and after changing Stage 3

Before: name the invariant the change touches.
After: targeted tests → regression → representative real tickers → inspect
diagnostics → confirm no newly guessed facts entered the pipeline.
