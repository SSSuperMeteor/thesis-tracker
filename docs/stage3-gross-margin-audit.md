# Stage 3 Gross Margin Foundation Audit

Date: 2026-09-19

## Repository state before this slice

- `src/thesis_tracker/metrics/financial.py` and
  `src/thesis_tracker/model/canonical.py` were placeholders.
- There was no financial-fact model, XBRL fact adapter, concept registry,
  deterministic period resolver, Stage 3 entry point, or Stage 3 test.
- Git history contained no deleted Stage 3 implementation to restore.
- Stage 1's `documents` table is the authoritative selected-filing boundary;
  Stage 2 remains unrelated to structured financial facts.
- The pre-change three-company Stage 3 baseline was therefore zero generated
  observations: no runnable Stage 3 pipeline existed.

## Scope decision

This slice implements the high-priority mechanical constraints from
`TODO-enforce-in-code.md`: required provenance, explicit reported/derived
origin, typed units, instant/duration validation, a `ResolvedFact | FailedFact`
result, and serializable canonical failure diagnostics. It also implements only
the registry entries and derivations needed by `gross_margin_trend`.

The 15-company duration-pattern calibration, Company Facts freshness recovery,
custom-concept recovery, and the other seven metrics remain backlog. No global
quarter-duration tolerance was introduced; quarter identity comes from adjacent
filing boundaries and exact context dates.

## Data path

The Stage 3 source starts with the unique successful Stage 1 filing family in
SQLite, applies that filing date/accession as an as-of ceiling, and reads
filing-level XBRL for periodic filings within the history window. Filing-level
XBRL was selected instead of aggregate Company Facts because it preserves
`context_id` and dimensions.

An amendment with no registered financial facts does not replace the original
financial statements. The adapter explicitly selects the newest member in the
same filing family that actually contains registered duration facts and records
the fallback in `resolver_path`. It never combines partial facts from different
family members.
