"""SEC filing-level XBRL adapter bounded by the Stage 1 selected filing."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from edgar import Company

from thesis_tracker.financial.models import (
    FactKind,
    FactOrigin,
    FailureCode,
    FilingBoundary,
    FinancialFact,
    Unit,
)
from thesis_tracker.financial.registry import CONCEPT_REGISTRY
from thesis_tracker.financial.semantic_candidates import (
    FilingSemanticContext,
    extract_filing_semantic_context,
)
from thesis_tracker.ingest.filing_selection import (
    EdgarFilingSource,
    FilingSelector,
    SelectionRequest,
)


class SourceValidationError(RuntimeError):
    """A structured failure at the Stage 1 boundary or XBRL adapter."""

    def __init__(self, code: FailureCode, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Stage1Boundary:
    ticker: str
    cik: str
    accession: str
    form: str
    period_end: date
    filed_at: date


@dataclass(frozen=True, slots=True)
class Stage3Inputs:
    selected_boundary: Stage1Boundary
    boundaries: tuple[FilingBoundary, ...]
    facts: tuple[FinancialFact, ...]
    semantic_contexts: tuple[FilingSemanticContext, ...] = ()


class _DateBoundCompany:
    """Apply the as-of window at the SEC query, before metadata validation."""

    def __init__(self, company: Any, *, since: date, until: date) -> None:
        self._company = company
        self._since = since
        self._until = until

    def __getattr__(self, name: str) -> Any:
        return getattr(self._company, name)

    def get_filings(self, **kwargs: Any) -> Any:
        return self._company.get_filings(
            **kwargs,
            filing_date=(self._since, self._until),
        )


def _as_date(value: object, field: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as error:
        raise SourceValidationError(
            FailureCode.INVALID_CONTEXT,
            f"invalid {field}: {value!r}",
        ) from error


def load_stage1_boundary(db_path: Path, ticker: str) -> Stage1Boundary:
    """Load one explicitly selected successful filing (or one amendment family)."""

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT ticker, cik, accession, form_type, period_end, filing_date,
                   is_amendment, amends_accession
            FROM documents
            WHERE upper(ticker) = upper(?) AND ingestion_status = 'success'
            ORDER BY filing_date, accession
            """,
            (ticker,),
        ).fetchall()
    if not rows:
        raise SourceValidationError(
            FailureCode.PERIOD_UNAVAILABLE,
            f"no successful Stage 1 filing boundary for {ticker.upper()}",
        )
    period_ends = {str(row[4]) for row in rows}
    if len(period_ends) != 1:
        raise SourceValidationError(
            FailureCode.AMBIGUOUS,
            f"multiple Stage 1 reporting boundaries for {ticker.upper()}",
        )
    if len(rows) > 1:
        originals = {str(row[2]) for row in rows if not bool(row[6])}
        amendments = [row for row in rows if bool(row[6])]
        if len(originals) != 1 or any(str(row[7]) not in originals for row in amendments):
            raise SourceValidationError(
                FailureCode.AMBIGUOUS,
                f"Stage 1 rows do not form one filing family for {ticker.upper()}",
            )
    row = rows[-1]
    return Stage1Boundary(
        ticker=str(row[0]).upper(),
        cik=str(row[1]),
        accession=str(row[2]),
        form=str(row[3]),
        period_end=_as_date(row[4], "period_end"),
        filed_at=_as_date(row[5], "filing_date"),
    )


def _unit(row: dict[str, Any]) -> Unit | None:
    currency = str(row.get("currency") or "").upper()
    unit_ref = str(row.get("unit_ref") or "")
    if currency == "USD" or unit_ref.lower() == "usd":
        return Unit.USD
    if currency == "SHARES" or unit_ref.lower() in {"shares", "share"}:
        return Unit.SHARES
    return None


def _decimal(value: object) -> Decimal:
    normalized = str(value).replace(",", "").strip()
    try:
        return Decimal(normalized)
    except InvalidOperation as error:
        raise SourceValidationError(
            FailureCode.INVALID_CONTEXT,
            f"non-decimal XBRL value {value!r}",
        ) from error


def extract_filing_facts(
    boundary: FilingBoundary,
    xbrl: Any,
    *,
    amendment_fallback: bool = False,
) -> tuple[FinancialFact, ...]:
    """Extract registered duration and instant candidates with XBRL contexts."""

    registered = {concept for values in CONCEPT_REGISTRY.values() for concept in values}
    unique: dict[str, FinancialFact] = {}
    for row in xbrl.facts.get_facts():
        concept = str(row.get("concept") or "")
        raw_period_type = str(row.get("period_type") or "")
        if concept not in registered or raw_period_type not in {"duration", "instant"}:
            continue
        is_instant = raw_period_type == "instant"
        context_id = str(row.get("context_ref") or "")
        context = xbrl.contexts.get(context_id)
        if not context_id or context is None:
            raise SourceValidationError(
                FailureCode.INVALID_CONTEXT,
                f"{boundary.accession} fact lacks a resolvable context",
            )
        unit = _unit(row)
        if unit is None:
            continue
        raw_dimensions = getattr(context, "dimensions", None) or {}
        dimensions = tuple(
            sorted((str(axis), str(member)) for axis, member in raw_dimensions.items())
        )
        if is_instant:
            # Instant facts carry the as-of date in period_instant, not period_end.
            period_start = None
            period_end = _as_date(row.get("period_instant"), "period_instant")
        else:
            period_start = _as_date(row.get("period_start"), "period_start")
            period_end = _as_date(row.get("period_end"), "period_end")
            if period_start is None:
                continue
        candidate = FinancialFact(
            ticker=boundary.ticker,
            accession=boundary.accession,
            concept=concept,
            value=_decimal(row.get("value")),
            unit=unit,
            period_start=period_start,
            period_end=period_end,
            filed_at=boundary.filed_at,
            form=boundary.form,
            fiscal_year=int(row.get("fiscal_year") or boundary.fiscal_year),
            fiscal_period=str(row.get("fiscal_period") or boundary.fiscal_period),
            source="sec_filing_xbrl",
            extraction_path="filing.xbrl().facts.get_facts()",
            context_id=context_id,
            dimensions=dimensions,
            origin=FactOrigin.REPORTED,
            resolver_path=(
                "stage1_boundary",
                *(
                    ("amendment_without_registered_facts",)
                    if amendment_fallback
                    else ()
                ),
                "filing_xbrl",
            ),
            fact_kind=FactKind.INSTANT if is_instant else FactKind.DURATION,
        )
        # The same concept/context can be reported more than once at different
        # XBRL precision (e.g. decimals=-8 and decimals=-6).  Keep every distinct
        # value so the resolver, not the loader, owns the ambiguity decision for
        # that concept instead of aborting the whole filing.
        unique[(candidate.fact_id, candidate.unit.value, str(candidate.value))] = (
            candidate
        )
    return tuple(unique.values())


def _select_financial_member(members: tuple[Any, ...]) -> tuple[Any, Any, bool]:
    """Prefer the most complete registered fact set within one filing family.

    An amendment can carry a sparse XBRL document, so selecting merely the
    newest member that reports *some* registered concept can silently drop the
    facts other metrics need.  Pick the member with the most registered facts
    instead; ties keep the newest member so recency still wins when the fact
    coverage is equal.
    """

    registered = {concept for values in CONCEPT_REGISTRY.values() for concept in values}
    best: tuple[int, Any, Any, bool] | None = None
    newest_with_xbrl: tuple[Any, Any, bool] | None = None
    for index in range(len(members) - 1, -1, -1):
        member = members[index]
        xbrl = member.filing.xbrl()
        if xbrl is None:
            continue
        amendment_fallback = index != len(members) - 1
        if newest_with_xbrl is None:
            newest_with_xbrl = (member, xbrl, amendment_fallback)
        count = sum(
            1
            for row in xbrl.facts.get_facts()
            if row.get("concept") in registered
            and row.get("period_type") in {"duration", "instant"}
        )
        if count and (best is None or count > best[0]):
            best = (count, member, xbrl, amendment_fallback)
    if best is not None:
        return best[1], best[2], best[3]
    if newest_with_xbrl is not None:
        return newest_with_xbrl
    latest_accession = members[-1].metadata.accession
    raise SourceValidationError(
        FailureCode.REGISTRY_GAP,
        f"filing family ending at {latest_accession} has no registered facts",
    )


def _history_start(boundary_date: date, years: int) -> date:
    try:
        return boundary_date.replace(year=boundary_date.year - years)
    except ValueError:
        return boundary_date.replace(year=boundary_date.year - years, day=28)


def _eligible_members(family: Any, selected: Stage1Boundary) -> tuple[Any, ...]:
    """Apply the exact Stage 1 accession ceiling within an ordered family."""

    members = tuple(family.members)
    for index, member in enumerate(members):
        if member.metadata.accession == selected.accession:
            return members[: index + 1]
    return tuple(
        member
        for member in members
        if member.metadata.filing_date < selected.filed_at
    )


def load_stage3_inputs(
    ticker: str,
    *,
    db_path: Path = Path("data/corpus.db"),
    history_years: int = 4,
    max_filings: int | None = None,
    include_semantic_contexts: bool = False,
) -> Stage3Inputs:
    """Load date-bounded periodic XBRL facts no later than the Stage 1 filing."""

    selected = load_stage1_boundary(db_path, ticker)
    since = _history_start(selected.filed_at, history_years)
    selector = FilingSelector(
        EdgarFilingSource(
            company_factory=lambda requested_ticker: _DateBoundCompany(
                Company(requested_ticker),
                since=since,
                until=selected.filed_at,
            )
        )
    )
    families = selector.select(
        SelectionRequest(
            ticker=selected.ticker,
            since=since,
        ),
        today=selected.filed_at,
    )
    as_of_families: list[tuple[Any, ...]] = []
    for family in families:
        eligible = _eligible_members(family, selected)
        if eligible:
            as_of_families.append(eligible)
    if not any(
        member.metadata.accession == selected.accession
        for members in as_of_families
        for member in members
    ):
        raise SourceValidationError(
            FailureCode.INVALID_CONTEXT,
            "Stage 1 accession is absent from SEC periodic filing metadata",
        )
    ordered = sorted(
        as_of_families,
        key=lambda members: (
            members[-1].metadata.report_date,
            members[-1].metadata.filing_date,
            members[-1].metadata.accession,
        ),
    )
    if max_filings is not None:
        ordered = ordered[-max_filings:]

    boundaries: list[FilingBoundary] = []
    facts: list[FinancialFact] = []
    semantic_contexts: list[FilingSemanticContext] = []
    for members in ordered:
        member, xbrl, amendment_fallback = _select_financial_member(members)
        info = xbrl.entity_info
        raw_period = str(info.get("fiscal_period") or "")
        normalized_period = "Q4" if member.metadata.base_form == "10-K" else raw_period
        if normalized_period not in {"Q1", "Q2", "Q3", "Q4"}:
            raise SourceValidationError(
                FailureCode.PERIOD_UNAVAILABLE,
                f"filing {member.metadata.accession} lacks a valid fiscal period",
            )
        fiscal_year = int(info.get("fiscal_year") or 0)
        if fiscal_year <= 0:
            raise SourceValidationError(
                FailureCode.PERIOD_UNAVAILABLE,
                f"filing {member.metadata.accession} lacks a valid fiscal year",
            )
        filing_boundary = FilingBoundary(
            ticker=selected.ticker,
            accession=member.metadata.accession,
            filed_at=member.metadata.filing_date,
            form=member.metadata.form,
            fiscal_year=fiscal_year,
            fiscal_period=normalized_period,
            period_end=member.metadata.report_date,
            source="sec_filing_metadata",
        )
        boundaries.append(filing_boundary)
        facts.extend(
            extract_filing_facts(
                filing_boundary,
                xbrl,
                amendment_fallback=amendment_fallback,
            )
        )
        if include_semantic_contexts:
            semantic_contexts.append(
                extract_filing_semantic_context(filing_boundary, xbrl)
            )
    return Stage3Inputs(
        selected_boundary=selected,
        boundaries=tuple(boundaries),
        facts=tuple(facts),
        semantic_contexts=tuple(semantic_contexts),
    )
