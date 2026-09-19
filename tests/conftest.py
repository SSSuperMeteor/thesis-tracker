"""Shared deterministic test fixtures.

The Stage 2 retrieval benchmark manifest is historical and fixed, while
``data/corpus.db`` is local runtime data that is intentionally not committed.
Benchmark tests therefore build the corpus they validate against instead of
reading whatever the developer happens to have ingested locally.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STAGE2_QUESTIONS_PATH = ROOT / "eval/retrieval_stage2_questions.json"


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE documents (
            accession TEXT PRIMARY KEY,
            ticker TEXT,
            form_type TEXT,
            filing_date TEXT,
            ingestion_status TEXT NOT NULL DEFAULT 'success'
        );

        CREATE TABLE chunks (
            chunk_id TEXT PRIMARY KEY,
            accession TEXT,
            section_key TEXT,
            title TEXT,
            text TEXT,
            span_verified INTEGER,
            FOREIGN KEY(accession) REFERENCES documents(accession)
        );
        """
    )


def build_stage2_benchmark_corpus(db_path: Path) -> Path:
    """Create a corpus holding exactly the accessions the benchmark names.

    Every logical parent referenced by the manifest is stored as one chunking-v2
    subchunk (``<logical_parent>::chunk_000``) whose title is the manifest's
    ``expected_section``.  This keeps the benchmark expected values and the
    retrieval algorithms untouched while removing the dependency on runtime data.
    """
    questions = json.loads(STAGE2_QUESTIONS_PATH.read_text(encoding="utf-8"))
    documents: dict[str, tuple[str, str]] = {}
    chunks: dict[str, tuple[str, str, str, str]] = {}
    for question in questions:
        chunk_ids = [
            str(question["expected_chunk_id"]),
            *(str(value) for value in question.get("acceptable_chunk_ids", [])),
        ]
        for chunk_id in chunk_ids:
            accession, _, section_key = chunk_id.partition("::")
            documents.setdefault(accession, (question["ticker"], question["form_type"]))
            chunks.setdefault(
                f"{chunk_id}::chunk_000",
                (
                    accession,
                    section_key,
                    question["expected_section"],
                    f"{question['expected_section']} benchmark fixture text",
                ),
            )

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        _create_schema(connection)
        connection.executemany(
            "INSERT INTO documents "
            "(accession, ticker, form_type, filing_date, ingestion_status) "
            "VALUES (?, ?, ?, ?, 'success')",
            [
                (accession, ticker, form_type, "2026-01-01")
                for accession, (ticker, form_type) in sorted(documents.items())
            ],
        )
        connection.executemany(
            "INSERT INTO chunks "
            "(chunk_id, accession, section_key, title, text, span_verified) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            [
                (chunk_id, accession, section_key, title, text)
                for chunk_id, (accession, section_key, title, text) in sorted(
                    chunks.items()
                )
            ],
        )
    return db_path


@pytest.fixture
def stage2_benchmark_corpus(tmp_path: Path) -> Path:
    """Path to a deterministic Stage 2 benchmark corpus, built per test."""
    return build_stage2_benchmark_corpus(tmp_path / "stage2_benchmark.db")
