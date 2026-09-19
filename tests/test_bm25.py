"""Tests for the SQLite-backed BM25 baseline."""

import sqlite3
from pathlib import Path

import pytest

from thesis_tracker.retrieve.bm25 import BM25Retriever, tokenize


@pytest.fixture
def corpus_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "corpus.db"

    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                accession TEXT PRIMARY KEY,
                ticker TEXT,
                form_type TEXT,
                base_form TEXT,
                filing_date TEXT,
                ingestion_status TEXT
            );

            CREATE TABLE chunks (
                chunk_id TEXT PRIMARY KEY,
                accession TEXT,
                section_key TEXT,
                title TEXT,
                text TEXT,
                span_verified INTEGER,
                ord INTEGER
            );
            """
        )
        connection.executemany(
            """
            INSERT INTO documents
                (accession, ticker, form_type, base_form, filing_date,
                 ingestion_status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("amd-q", "AMD", "10-Q", "10-Q", "2026-08-05", "success"),
                ("amd-k", "AMD", "10-K", "10-K", "2026-02-04", "success"),
                ("nvda-q", "NVDA", "10-Q", "10-Q", "2026-08-26", "success"),
                ("stale-q", "AMD", "10-Q", "10-Q", "2026-09-01", "failed"),
            ],
        )
        connection.executemany(
            """
            INSERT INTO chunks
                (chunk_id, accession, section_key, title, text, span_verified, ord)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "amd-q::item-2",
                    "amd-q",
                    "part_i_item_2",
                    "Management's Discussion",
                    "Data center accelerator revenue grew during the quarter.",
                    1,
                    1,
                ),
                (
                    "amd-q::inventory",
                    "amd-q",
                    None,
                    "Inventories",
                    "Inventory write-downs affected gross margin.",
                    1,
                    2,
                ),
                (
                    "amd-k::item-1",
                    "amd-k",
                    "part_i_item_1",
                    "Business",
                    "Annual cloud revenue includes enterprise software sales.",
                    1,
                    1,
                ),
                (
                    "nvda-q::item-2",
                    "nvda-q",
                    "part_i_item_2",
                    "Management's Discussion",
                    "Data center revenue reflects demand for AI infrastructure.",
                    1,
                    1,
                ),
                (
                    "stale-q::item-2",
                    "stale-q",
                    "part_i_item_2",
                    "Stale result",
                    "Data center revenue stale record.",
                    1,
                    1,
                ),
            ],
        )

    return db_path


def test_tokenize_is_simple_and_deterministic() -> None:
    assert tokenize("Data-center REVENUE_2026") == [
        "data",
        "center",
        "revenue",
        "2026",
    ]


def test_search_returns_top_k(corpus_db: Path) -> None:
    results = BM25Retriever(corpus_db).search(
        "data center revenue",
        top_k=2,
    )

    assert len(results) == 2
    assert results[0].bm25_score >= results[1].bm25_score
    assert all(result.text for result in results)
    assert all(result.accession != "stale-q" for result in results)


def test_ticker_filter(corpus_db: Path) -> None:
    results = BM25Retriever(corpus_db).search(
        "revenue",
        top_k=10,
        ticker="amd",
    )

    assert {result.chunk_id for result in results} == {
        "amd-q::item-2",
        "amd-k::item-1",
    }
    assert all(result.ticker == "AMD" for result in results)


def test_form_type_filter(corpus_db: Path) -> None:
    results = BM25Retriever(corpus_db).search(
        "revenue",
        top_k=10,
        form_type="10-k",
    )

    assert [result.chunk_id for result in results] == [
        "amd-k::item-1"
    ]
    assert results[0].form_type == "10-K"


def test_base_form_filter_returns_original_and_amendment(corpus_db: Path) -> None:
    with sqlite3.connect(corpus_db) as connection:
        connection.execute(
            """
            INSERT INTO documents
                (accession, ticker, form_type, base_form, filing_date,
                 ingestion_status)
            VALUES ('amd-q-a', 'AMD', '10-Q/A', '10-Q', '2026-08-10', 'success')
            """
        )
        connection.execute(
            """
            INSERT INTO chunks
                (chunk_id, accession, section_key, title, text, span_verified, ord)
            VALUES ('amd-q-a::item-2', 'amd-q-a', 'part_i_item_2',
                    'Amended discussion', 'Accelerator disclosure was amended.', 1, 1)
            """
        )

    results = BM25Retriever(corpus_db).search(
        "accelerator",
        top_k=10,
        form_type="10-Q",
    )

    assert {result.accession for result in results} == {"amd-q", "amd-q-a"}
    assert {result.form_type for result in results} == {"10-Q", "10-Q/A"}


def test_empty_query_and_no_matches(corpus_db: Path) -> None:
    retriever = BM25Retriever(corpus_db)

    assert retriever.search("   ") == []
    assert retriever.search("xyzzynotincorpus") == []
    assert retriever.search("revenue", ticker="ORCL") == []


def test_non_positive_top_k_is_rejected(corpus_db: Path) -> None:
    with pytest.raises(ValueError, match="top_k"):
        BM25Retriever(corpus_db).search("revenue", top_k=0)
