"""Tests for deterministic citation source grounding."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from thesis_tracker.retrieve.citation import verify_evidence


@pytest.fixture
def citation_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "corpus.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                accession TEXT PRIMARY KEY,
                ingestion_status TEXT NOT NULL
            );
            CREATE TABLE chunks (
                chunk_id TEXT PRIMARY KEY,
                accession TEXT NOT NULL,
                text TEXT NOT NULL,
                span_verified INTEGER NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT INTO documents (accession, ingestion_status) VALUES (?, ?)",
            [("filing-a", "success"), ("filing-b", "success")],
        )
        connection.executemany(
            """
            INSERT INTO chunks (chunk_id, accession, text, span_verified)
            VALUES (?, ?, ?, 1)
            """,
            [
                (
                    "chunk-a",
                    "filing-a",
                    "Opening sentence. Revenue increased\n70% year over year. "
                    "Demand remained strong. Closing sentence.",
                ),
                (
                    "chunk-b",
                    "filing-b",
                    "A different source discusses operating expenses.",
                ),
                (
                    "chunk-unicode",
                    "filing-a",
                    "Management\u00a0said \u201cdemand\u200bremained\u201d\u2014strong.",
                ),
            ],
        )
    return db_path


def test_exact_quote_passes(citation_db: Path) -> None:
    result = verify_evidence(
        "chunk-a",
        "Demand remained strong.",
        db_path=citation_db,
    )

    assert result["valid"] is True
    assert result["reason"] == "exact_match"


def test_whitespace_and_newline_difference_passes(citation_db: Path) -> None:
    result = verify_evidence(
        "chunk-a",
        "Revenue increased 70% year over year.",
        db_path=citation_db,
    )

    assert result["valid"] is True
    assert result["reason"] == "exact_match"


def test_unicode_nbsp_and_punctuation_normalization_passes(
    citation_db: Path,
) -> None:
    result = verify_evidence(
        "chunk-unicode",
        'Management said "demandremained"-strong.',
        db_path=citation_db,
    )

    assert result["valid"] is True
    assert result["reason"] == "exact_match"


def test_one_word_modification_is_rejected(citation_db: Path) -> None:
    result = verify_evidence(
        "chunk-a",
        "Demand remained very strong.",
        db_path=citation_db,
    )

    assert result["valid"] is False
    assert result["reason"] == "evidence_not_found"


def test_one_character_modification_is_rejected(citation_db: Path) -> None:
    result = verify_evidence(
        "chunk-a",
        "Demand remained stronk.",
        db_path=citation_db,
    )

    assert result["valid"] is False
    assert result["reason"] == "evidence_not_found"


def test_number_modification_is_rejected(citation_db: Path) -> None:
    result = verify_evidence(
        "chunk-a",
        "Revenue increased 80% year over year.",
        db_path=citation_db,
    )

    assert result["valid"] is False
    assert result["reason"] == "evidence_not_found"


def test_fabricated_evidence_is_rejected(citation_db: Path) -> None:
    result = verify_evidence(
        "chunk-a",
        "Management expects revenue to double next quarter.",
        db_path=citation_db,
    )

    assert result["valid"] is False
    assert result["reason"] == "evidence_not_found"


def test_correct_text_with_wrong_chunk_is_rejected(citation_db: Path) -> None:
    result = verify_evidence(
        "chunk-b",
        "Demand remained strong.",
        db_path=citation_db,
    )

    assert result["valid"] is False
    assert result["reason"] == "evidence_not_found"


def test_nonexistent_chunk_is_not_found(citation_db: Path) -> None:
    result = verify_evidence(
        "missing-chunk",
        "Demand remained strong.",
        db_path=citation_db,
    )

    assert result["valid"] is False
    assert result["reason"] == "chunk_not_found"


def test_empty_evidence_is_rejected(citation_db: Path) -> None:
    result = verify_evidence(
        "chunk-a",
        " \u00a0\u200b\n\t",
        db_path=citation_db,
    )

    assert result["valid"] is False
    assert result["reason"] == "empty_evidence"


def test_middle_substring_passes(citation_db: Path) -> None:
    result = verify_evidence(
        "chunk-a",
        "Revenue increased\n70% year over year.",
        db_path=citation_db,
    )

    assert result == {
        "valid": True,
        "reason": "exact_match",
        "chunk_id": "chunk-a",
        "evidence_text": "Revenue increased\n70% year over year.",
    }
