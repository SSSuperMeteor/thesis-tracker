"""Tests for atomic persistence of original filings and amendments."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from thesis_tracker.ingest.filing_selection import FilingMetadata
from thesis_tracker.ingest.sec_adapter import (
    CanonicalDoc,
    Chunk,
    _ensure_schema,
    save_family,
    sha256,
)


def metadata(
    accession: str,
    form: str,
    filing_date: date,
    *,
    amends_accession: str | None = None,
) -> FilingMetadata:
    return FilingMetadata(
        ticker="AAA",
        cik="0000000001",
        accession=accession,
        form=form,
        base_form=form.removesuffix("/A"),
        is_amendment=form.endswith("/A"),
        amends_accession=amends_accession,
        filing_date=filing_date,
        report_date=date(2026, 3, 31),
        primary_document="q1a.htm" if form.endswith("/A") else "q1.htm",
    )


def document(item: FilingMetadata, text: str) -> CanonicalDoc:
    chunk = Chunk(
        chunk_id=f"{item.accession}::part_i_item_1::chunk_000",
        doc_hash=sha256(text),
        kind="section",
        section_key="part_i_item_1",
        title="Part I, Item 1",
        text=text,
        text_hash=sha256(text),
        char_span=(0, len(text)),
        span_verified=True,
        extraction_method="test|chunker-v2-overlap",
    )
    return CanonicalDoc(
        cik=item.cik,
        ticker=item.ticker,
        form_type=item.form,
        accession=item.accession,
        period_end=item.report_date.isoformat(),
        filing_date=item.filing_date.isoformat(),
        fetched_at="2026-05-10T00:00:00+00:00",
        doc_hash=sha256(text),
        parser_version="test-parser",
        normalizer_version="norm-1",
        schema_version=3,
        full_text=text,
        primary_document=item.primary_document,
        base_form=item.base_form,
        is_amendment=item.is_amendment,
        amends_accession=item.amends_accession,
        chunks=[chunk],
    )


def family() -> tuple[
    tuple[FilingMetadata, FilingMetadata],
    dict[str, CanonicalDoc],
]:
    original = metadata(
        "0000000001-26-000001",
        "10-Q",
        date(2026, 5, 1),
    )
    amendment = metadata(
        "0000000001-26-000002",
        "10-Q/A",
        date(2026, 5, 10),
        amends_accession=original.accession,
    )
    return (
        (original, amendment),
        {
            original.accession: document(original, "original complete filing"),
            amendment.accession: document(amendment, "amended disclosure"),
        },
    )


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})")
    }


def test_fresh_schema_contains_filing_family_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    with sqlite3.connect(db_path) as connection:
        _ensure_schema(connection)
        columns = table_columns(connection, "documents")

    assert {
        "primary_document",
        "base_form",
        "is_amendment",
        "amends_accession",
    } <= columns


def test_legacy_schema_migrates_without_losing_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                accession TEXT PRIMARY KEY,
                ingestion_status TEXT NOT NULL DEFAULT 'success'
            );
            CREATE TABLE chunks (
                chunk_id TEXT PRIMARY KEY,
                accession TEXT,
                kind TEXT,
                extraction_method TEXT NOT NULL DEFAULT 'legacy',
                parent_id TEXT
            );
            INSERT INTO documents (accession) VALUES ('legacy-accession');
            """
        )
        _ensure_schema(connection)
        columns = table_columns(connection, "documents")
        rows = connection.execute(
            "SELECT accession FROM documents"
        ).fetchall()

    assert rows == [("legacy-accession",)]
    assert {
        "primary_document",
        "base_form",
        "is_amendment",
        "amends_accession",
    } <= columns


def test_successful_family_coexists_and_reingest_is_idempotent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "corpus.db"
    members, docs = family()

    save_family(members, docs, db_path)
    save_family(members, docs, db_path)

    with sqlite3.connect(db_path) as connection:
        document_rows = connection.execute(
            """
            SELECT accession, form_type, base_form, is_amendment,
                   amends_accession, primary_document, ingestion_status
            FROM documents
            ORDER BY filing_date, accession
            """
        ).fetchall()
        chunk_rows = connection.execute(
            "SELECT accession, chunk_id FROM chunks ORDER BY accession"
        ).fetchall()

    assert document_rows == [
        (
            members[0].accession,
            "10-Q",
            "10-Q",
            0,
            None,
            "q1.htm",
            "success",
        ),
        (
            members[1].accession,
            "10-Q/A",
            "10-Q",
            1,
            members[0].accession,
            "q1a.htm",
            "success",
        ),
    ]
    assert chunk_rows == [
        (
            members[0].accession,
            f"{members[0].accession}::part_i_item_1::chunk_000",
        ),
        (
            members[1].accession,
            f"{members[1].accession}::part_i_item_1::chunk_000",
        ),
    ]


def test_one_sanity_failure_invalidates_every_family_member(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "corpus.db"
    members, docs = family()
    save_family(members, docs, db_path)

    save_family(
        members,
        docs,
        db_path,
        failures_by_accession={members[1].accession: ["amendment incomplete"]},
    )

    with sqlite3.connect(db_path) as connection:
        statuses = connection.execute(
            "SELECT accession, ingestion_status FROM documents ORDER BY accession"
        ).fetchall()
        chunk_count = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    assert statuses == [
        (members[0].accession, "failed"),
        (members[1].accession, "failed"),
    ]
    assert chunk_count == 0


def test_missing_built_amendment_stores_metadata_failure_and_clears_old_chunks(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "corpus.db"
    members, docs = family()
    save_family(members, docs, db_path)

    save_family(
        members,
        {members[0].accession: docs[members[0].accession]},
        db_path,
        failures_by_accession={members[1].accession: ["build RuntimeError"]},
    )

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT accession, form_type, ingestion_status, n_chunks, warnings_json
            FROM documents
            ORDER BY accession
            """
        ).fetchall()
        chunk_count = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    assert [(row[0], row[1], row[2], row[3]) for row in rows] == [
        (members[0].accession, "10-Q", "failed", 0),
        (members[1].accession, "10-Q/A", "failed", 0),
    ]
    assert "build RuntimeError" in rows[1][4]
    assert chunk_count == 0
