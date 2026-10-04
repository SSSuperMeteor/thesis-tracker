"""Explicit SEC collection step for append-only point-in-time fact snapshots."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from edgar import Company

from thesis_tracker.financial.models import FailureCode, FilingBoundary
from thesis_tracker.financial.pit_store import (
    DEFAULT_FACT_DB,
    SnapshotFiling,
    append_filings,
    stored_accessions,
)
from thesis_tracker.financial.registry import CONCEPT_REGISTRY
from thesis_tracker.financial.sec_source import (
    SourceValidationError,
    _DateBoundCompany,
    extract_filing_facts,
)
from thesis_tracker.ingest.filing_selection import (
    EdgarFilingSource,
    FilingSelector,
    SelectionRequest,
)


def collect_filing_snapshots(
    ticker: str, *, since: date, until: date,
    db_path: Path | str = DEFAULT_FACT_DB, selector: FilingSelector | None = None,
) -> dict:
    """Fetch SEC metadata and XBRL outside the tool and append complete accessions."""
    ticker = ticker.strip().upper()
    if not ticker or since > until:
        raise ValueError("ticker and a valid date window are required")
    if selector is None:
        selector = FilingSelector(EdgarFilingSource(
            company_factory=lambda symbol: _DateBoundCompany(
                Company(symbol), since=since, until=until,
            ),
        ))
    families = selector.select(SelectionRequest(ticker=ticker, since=since), today=until)
    registered = {concept for group in CONCEPT_REGISTRY.values() for concept in group}
    cached = stored_accessions(db_path, ticker)
    snapshots = []
    xbrl_loads = 0
    for family in families:
        members = tuple(family.members)
        original_accession = members[0].metadata.accession
        for member in members:
            metadata = member.metadata
            if metadata.filing_date > until or metadata.accession in cached:
                continue
            xbrl_loads += 1
            xbrl = member.filing.xbrl()
            if xbrl is None:
                continue
            info = xbrl.entity_info
            period = "Q4" if metadata.base_form == "10-K" else str(info.get("fiscal_period") or "")
            year = int(info.get("fiscal_year") or 0)
            if period not in {"Q1", "Q2", "Q3", "Q4"} or year <= 0:
                raise SourceValidationError(
                    FailureCode.PERIOD_UNAVAILABLE,
                    f"filing {metadata.accession} lacks a valid fiscal period",
                )
            boundary = FilingBoundary(
                ticker=ticker, accession=metadata.accession,
                filed_at=metadata.filing_date, form=metadata.form,
                fiscal_year=year, fiscal_period=period,
                period_end=metadata.report_date, source="sec_filing_metadata",
            )
            rows = xbrl.facts.get_facts()
            count = sum(row.get("concept") in registered and row.get("period_type") in
                        {"duration", "instant"} for row in rows)
            accepted = metadata.acceptance_datetime
            snapshots.append(SnapshotFiling(
                boundary=boundary, original_accession=original_accession,
                cik=str(metadata.cik),
                acceptance_at=accepted.isoformat() if accepted is not None else None,
                issuer_sic=str(member.company.sic) if member.company.sic else None,
                registered_count=count,
                facts=extract_filing_facts(boundary, xbrl),
            ))
    added = append_filings(db_path, snapshots)
    return {"ticker": ticker, "filing_families": len(families),
            "xbrl_loads": xbrl_loads, "added_filings": added,
            "added_facts": sum(len(item.facts) for item in snapshots)}
