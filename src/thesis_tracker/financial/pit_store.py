"""Append-only local SEC fact snapshots for point-in-time Stage 3 reads."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from thesis_tracker.financial.models import (
    FactKind,
    FactOrigin,
    FailureCode,
    FilingBoundary,
    FinancialFact,
    Unit,
)
from thesis_tracker.financial.sec_source import (
    SourceValidationError,
    Stage1Boundary,
    Stage3Inputs,
    _history_start,
)

DEFAULT_FACT_DB = Path("data/cache/financial_facts.db")


@dataclass(frozen=True, slots=True)
class SnapshotFiling:
    boundary: FilingBoundary
    original_accession: str
    cik: str
    acceptance_at: str | None
    issuer_sic: str | None
    registered_count: int
    facts: tuple[FinancialFact, ...]


@dataclass(frozen=True, slots=True)
class LoadedSnapshot:
    inputs: Stage3Inputs
    issuer_sic: str | None
    retrieved_at: str


def _connect(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("""CREATE TABLE IF NOT EXISTS filing_snapshots (
        accession TEXT PRIMARY KEY, original_accession TEXT NOT NULL,
        ticker TEXT NOT NULL, cik TEXT NOT NULL, form TEXT NOT NULL,
        period_end TEXT NOT NULL, filed_at TEXT NOT NULL,
        fiscal_year INTEGER NOT NULL, fiscal_period TEXT NOT NULL,
        acceptance_at TEXT, issuer_sic TEXT, registered_count INTEGER NOT NULL,
        retrieved_at TEXT NOT NULL
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS financial_fact_snapshots (
        accession TEXT NOT NULL, ordinal INTEGER NOT NULL,
        filed_at TEXT NOT NULL, retrieved_at TEXT NOT NULL,
        payload_json TEXT NOT NULL, PRIMARY KEY(accession, ordinal),
        FOREIGN KEY(accession) REFERENCES filing_snapshots(accession)
    )""")
    connection.commit()
    return connection


def stored_accessions(path: Path | str, ticker: str) -> set[str]:
    """Return immutable accessions already captured, without creating the database."""
    file = Path(path)
    if not file.exists():
        return set()
    with sqlite3.connect(f"file:{quote(str(file.resolve()))}?mode=ro", uri=True) as connection:
        return {row[0] for row in connection.execute(
            "SELECT accession FROM filing_snapshots WHERE upper(ticker)=upper(?)", (ticker,),
        )}


def append_filings(path: Path | str, filings: list[SnapshotFiling]) -> int:
    """Validate and append complete accession snapshots, never rewriting prior facts."""
    added = 0
    with _connect(path) as connection:
        for filing in filings:
            boundary = filing.boundary
            if not filing.original_accession or not filing.cik:
                raise ValueError("filing family and CIK are required")
            if any(fact.accession != boundary.accession or fact.filed_at != boundary.filed_at
                   or fact.ticker != boundary.ticker for fact in filing.facts):
                raise ValueError("fact provenance differs from filing boundary")
            existing = connection.execute(
                "SELECT ticker, filed_at, period_end, form, fiscal_year, fiscal_period, "
                "original_accession, cik, acceptance_at, issuer_sic, registered_count "
                "FROM filing_snapshots WHERE accession=?",
                (boundary.accession,),
            ).fetchone()
            if existing is not None:
                expected = (
                    boundary.ticker, boundary.filed_at.isoformat(), boundary.period_end.isoformat(),
                    boundary.form, boundary.fiscal_year, boundary.fiscal_period,
                    filing.original_accession, filing.cik, filing.acceptance_at,
                    filing.issuer_sic, filing.registered_count,
                )
                payloads = [row[0] for row in connection.execute(
                    "SELECT payload_json FROM financial_fact_snapshots "
                    "WHERE accession=? ORDER BY ordinal", (boundary.accession,),
                )]
                incoming = [json.dumps(fact.to_dict(), sort_keys=True) for fact in filing.facts]
                if existing != expected or payloads != incoming:
                    raise ValueError("conflicting accession snapshot")
                continue
            retrieved = datetime.now(timezone.utc).isoformat()
            with connection:
                connection.execute("""INSERT INTO filing_snapshots VALUES
                    (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    boundary.accession, filing.original_accession, boundary.ticker,
                    filing.cik, boundary.form, boundary.period_end.isoformat(),
                    boundary.filed_at.isoformat(), boundary.fiscal_year,
                    boundary.fiscal_period, filing.acceptance_at, filing.issuer_sic,
                    filing.registered_count, retrieved,
                ))
                for ordinal, fact in enumerate(filing.facts):
                    connection.execute(
                        "INSERT INTO financial_fact_snapshots VALUES (?,?,?,?,?)",
                        (boundary.accession, ordinal, fact.filed_at.isoformat(),
                         retrieved, json.dumps(fact.to_dict(), sort_keys=True)),
                    )
            added += 1
    return added


def _fact_from_dict(item: dict) -> FinancialFact:
    if item["origin"] != FactOrigin.REPORTED.value:
        raise SourceValidationError(FailureCode.INVALID_CONTEXT, "snapshot contains non-reported fact")
    return FinancialFact(
        ticker=item["ticker"], accession=item["accession"], concept=item["concept"],
        value=Decimal(item["value"]), unit=Unit(item["unit"]),
        period_start=date.fromisoformat(item["period_start"]) if item["period_start"] else None,
        period_end=date.fromisoformat(item["period_end"]),
        filed_at=date.fromisoformat(item["filed_at"]), form=item["form"],
        fiscal_year=item["fiscal_year"], fiscal_period=item["fiscal_period"],
        source=item["source"], extraction_path=item["extraction_path"],
        context_id=item["context_id"], dimensions=tuple(sorted(item["dimensions"].items())),
        origin=FactOrigin(item["origin"]), resolver_path=tuple(item["resolver_path"]),
        fact_kind=FactKind(item["fact_kind"]),
    )


def load_snapshot(path: Path | str, ticker: str, *, as_of: date,
                  history_years: int = 4) -> LoadedSnapshot:
    """Read only filings public by as_of, then choose the existing financial member per family."""
    file = Path(path)
    if not file.exists():
        raise SourceValidationError(FailureCode.PERIOD_UNAVAILABLE, "截至该日没有已披露的财报；本地事实库为空。")
    connection = sqlite3.connect(f"file:{quote(str(file.resolve()))}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT * FROM filing_snapshots WHERE upper(ticker)=upper(?) AND filed_at<=? "
            "ORDER BY period_end, filed_at, acceptance_at, accession",
            (ticker, as_of.isoformat()),
        ).fetchall()
        if not rows:
            raise SourceValidationError(FailureCode.PERIOD_UNAVAILABLE, "截至该日没有已披露的财报。")
        newest = max(rows, key=lambda row: (row["period_end"], row["filed_at"], row["acceptance_at"] or ""))
        since = _history_start(date.fromisoformat(newest["filed_at"]), history_years).isoformat()
        groups: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            groups.setdefault(row["original_accession"], []).append(row)
        selected = []
        for members in groups.values():
            if max(member["filed_at"] for member in members) < since:
                continue
            chosen = max(members, key=lambda row: (row["registered_count"], row["filed_at"],
                                                   row["acceptance_at"] or "", row["accession"]))
            selected.append(chosen)
        selected.sort(key=lambda row: (row["period_end"], row["filed_at"], row["accession"]))
        boundaries = tuple(FilingBoundary(
            ticker=row["ticker"], accession=row["accession"],
            filed_at=date.fromisoformat(row["filed_at"]), form=row["form"],
            fiscal_year=row["fiscal_year"], fiscal_period=row["fiscal_period"],
            period_end=date.fromisoformat(row["period_end"]), source="sec_filing_metadata",
        ) for row in selected)
        facts: list[FinancialFact] = []
        for row in selected:
            saved = connection.execute(
                "SELECT filed_at, retrieved_at, payload_json FROM financial_fact_snapshots "
                "WHERE accession=? ORDER BY ordinal", (row["accession"],),
            ).fetchall()
            for stored in saved:
                if stored["filed_at"] != row["filed_at"] or stored["retrieved_at"] != row["retrieved_at"]:
                    raise SourceValidationError(FailureCode.INVALID_CONTEXT, "snapshot provenance mismatch")
                fact = _fact_from_dict(json.loads(stored["payload_json"]))
                if fact.accession != row["accession"] or fact.filed_at > as_of:
                    raise SourceValidationError(FailureCode.INVALID_CONTEXT, "snapshot fact leaks past as_of")
                facts.append(fact)
    finally:
        connection.close()
    selected_latest = max(selected, key=lambda row: (row["period_end"], row["filed_at"], row["accession"]))
    stage1 = Stage1Boundary(
        ticker=selected_latest["ticker"], cik=selected_latest["cik"],
        accession=selected_latest["accession"], form=selected_latest["form"],
        period_end=date.fromisoformat(selected_latest["period_end"]),
        filed_at=date.fromisoformat(selected_latest["filed_at"]),
    )
    return LoadedSnapshot(
        inputs=Stage3Inputs(stage1, boundaries, tuple(facts)),
        issuer_sic=selected_latest["issuer_sic"],
        retrieved_at=max(row["retrieved_at"] for row in selected),
    )
