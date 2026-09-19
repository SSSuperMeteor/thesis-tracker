"""Tests for ChromaDB vector indexing and retrieval."""

from __future__ import annotations

import hashlib
import sqlite3
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace

import pytest

import thesis_tracker.embedding.dashscope as dashscope_module
from thesis_tracker.embedding.dashscope import (
    MODEL_NAME,
    DashScopeEmbeddingClient,
    EmbeddingAPIError,
)
from thesis_tracker.retrieve.vector import (
    DEFAULT_COLLECTION_NAME,
    VectorRetriever,
)


def _hash(text: str) -> str:
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


class FakeEmbeddingProvider:
    model_name = "qwen3-vl-embedding"
    dimension = 1024

    def __init__(self) -> None:
        self.document_batches: list[list[str]] = []
        self.query_calls: list[str] = []

    def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        self.document_batches.append(list(texts))
        return [self._vector(text) for text in texts]

    def embed_query(
        self,
        text: str,
    ) -> list[float]:
        self.query_calls.append(text)
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> list[float]:
        lowered = text.casefold()
        vector = [0.0] * 1024
        vector[0] = float(lowered.count("data center"))
        vector[1] = float(lowered.count("demand"))
        vector[2] = float(lowered.count("revenue"))
        vector[3] = float(lowered.count("inventory"))
        if not any(vector):
            vector[4] = 1.0
        return vector


class FailingEmbeddingProvider(FakeEmbeddingProvider):
    def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        raise EmbeddingAPIError(
            "simulated DashScope API failure"
        )


def test_model_and_collection_are_isolated_from_old_vectors() -> None:
    assert MODEL_NAME == "qwen3-vl-embedding"
    assert DEFAULT_COLLECTION_NAME == (
        "sec_chunks_qwen3_vl_embedding_1024"
    )


@pytest.fixture
def vector_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "corpus.db"
    rows = [
        (
            "amd-q::item-2",
            "amd-q",
            "part_i_item_2",
            "Management's Discussion",
            "Data center revenue and future accelerator demand grew.",
            1,
            1,
        ),
        (
            "amd-k::item-1",
            "amd-k",
            "part_i_item_1",
            "Business",
            "Annual software revenue and cloud services.",
            1,
            1,
        ),
        (
            "nvda-q::item-2",
            "nvda-q",
            "part_i_item_2",
            "Management's Discussion",
            "Management expects future data center demand to remain strong.",
            1,
            1,
        ),
        (
            "nvda-q::inventory",
            "nvda-q",
            None,
            "Inventories",
            "Inventory provisions affected gross margin.",
            1,
            2,
        ),
        (
            "stale-q::item-2",
            "stale-q",
            "part_i_item_2",
            "Stale result",
            "Data center demand from a stale filing.",
            1,
            1,
        ),
        (
            "nvda-q::unverified",
            "nvda-q",
            "part_ii_item_9",
            "Unverified",
            "Data center demand from an unverified chunk.",
            0,
            3,
        ),
    ]

    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                accession TEXT PRIMARY KEY,
                ticker TEXT,
                form_type TEXT,
                base_form TEXT,
                is_amendment INTEGER,
                filing_date TEXT,
                ingestion_status TEXT
            );

            CREATE TABLE chunks (
                chunk_id TEXT PRIMARY KEY,
                accession TEXT,
                section_key TEXT,
                title TEXT,
                text TEXT,
                text_hash TEXT,
                span_verified INTEGER,
                ord INTEGER
            );
            """
        )
        connection.executemany(
            """
            INSERT INTO documents
                (accession, ticker, form_type, base_form, is_amendment,
                 filing_date, ingestion_status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("amd-q", "AMD", "10-Q", "10-Q", 0, "2026-08-05", "success"),
                ("amd-k", "AMD", "10-K", "10-K", 0, "2026-02-04", "success"),
                ("nvda-q", "NVDA", "10-Q", "10-Q", 0, "2026-08-26", "success"),
                ("stale-q", "NVDA", "10-Q", "10-Q", 0, "2026-09-01", "failed"),
            ],
        )
        connection.executemany(
            """
            INSERT INTO chunks
                (chunk_id, accession, section_key, title, text,
                 text_hash, span_verified, ord)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (*row[:5], _hash(row[4]), *row[5:])
                for row in rows
            ],
        )

    return db_path


@pytest.fixture
def vector_retriever(
    vector_db: Path,
    tmp_path: Path,
) -> tuple[VectorRetriever, FakeEmbeddingProvider]:
    provider = FakeEmbeddingProvider()
    retriever = VectorRetriever(
        provider,
        db_path=vector_db,
        vector_path=tmp_path / "vectors",
        batch_size=2,
    )
    return retriever, provider


def test_index_builds_and_batches(
    vector_retriever: tuple[
        VectorRetriever,
        FakeEmbeddingProvider,
    ],
) -> None:
    retriever, provider = vector_retriever

    stats = retriever.index()

    assert stats.total_chunks == 4
    assert stats.embedded_chunks == 4
    assert stats.batches == 2
    assert sum(
        len(batch)
        for batch in provider.document_batches
    ) == 4


def test_repeated_index_skips_unchanged_embeddings(
    vector_retriever: tuple[
        VectorRetriever,
        FakeEmbeddingProvider,
    ],
) -> None:
    retriever, provider = vector_retriever
    retriever.index()
    initial_batches = len(
        provider.document_batches
    )

    stats = retriever.index()

    assert stats.embedded_chunks == 0
    assert stats.skipped_unchanged == 4
    assert stats.batches == 0
    assert len(provider.document_batches) == initial_batches


def test_changed_text_hash_reembeds_only_changed_chunk(
    vector_retriever: tuple[
        VectorRetriever,
        FakeEmbeddingProvider,
    ],
    vector_db: Path,
) -> None:
    retriever, provider = vector_retriever
    retriever.index()
    changed_text = (
        "Inventory provisions improved after demand increased."
    )
    with sqlite3.connect(vector_db) as connection:
        connection.execute(
            """
            UPDATE chunks
            SET text = ?, text_hash = ?
            WHERE chunk_id = ?
            """,
            (
                changed_text,
                _hash(changed_text),
                "nvda-q::inventory",
            ),
        )

    stats = retriever.index()

    assert stats.embedded_chunks == 1
    assert stats.skipped_unchanged == 3
    assert provider.document_batches[-1] == [
        changed_text
    ]


def test_query_returns_top_k(
    vector_retriever: tuple[
        VectorRetriever,
        FakeEmbeddingProvider,
    ],
) -> None:
    retriever, _ = vector_retriever
    retriever.index()

    results = retriever.search(
        "future data center demand",
        top_k=2,
    )

    assert len(results) == 2
    assert results[0].distance <= results[1].distance
    assert results[0].chunk_id == "nvda-q::item-2"
    assert all(result.text for result in results)


def test_query_filters(
    vector_retriever: tuple[
        VectorRetriever,
        FakeEmbeddingProvider,
    ],
) -> None:
    retriever, _ = vector_retriever
    retriever.index()

    ticker_results = retriever.search(
        "revenue",
        top_k=10,
        ticker="amd",
    )
    form_results = retriever.search(
        "revenue",
        top_k=10,
        form_type="10-k",
    )

    assert ticker_results
    assert all(
        result.ticker == "AMD"
        for result in ticker_results
    )
    assert [
        result.chunk_id
        for result in form_results
    ] == ["amd-k::item-1"]


def test_base_form_filter_and_metadata_include_amendment(
    vector_retriever: tuple[VectorRetriever, FakeEmbeddingProvider],
    vector_db: Path,
) -> None:
    retriever, _ = vector_retriever
    text = "Accelerator disclosure was amended."
    with sqlite3.connect(vector_db) as connection:
        connection.execute(
            """
            INSERT INTO documents
                (accession, ticker, form_type, base_form, is_amendment,
                 filing_date, ingestion_status)
            VALUES ('amd-q-a', 'AMD', '10-Q/A', '10-Q', 1,
                    '2026-08-10', 'success')
            """
        )
        connection.execute(
            """
            INSERT INTO chunks
                (chunk_id, accession, section_key, title, text, text_hash,
                 span_verified, ord)
            VALUES ('amd-q-a::item-2', 'amd-q-a', 'part_i_item_2',
                    'Amended discussion', ?, ?, 1, 1)
            """,
            (text, _hash(text)),
        )

    first = retriever.index()
    results = retriever.search("accelerator", top_k=10, form_type="10-Q")
    stored = retriever._collection.get(
        ids=["amd-q-a::item-2"],
        include=["metadatas"],
    )["metadatas"][0]
    second = retriever.index()

    assert first.embedded_chunks == 5
    assert {result.form_type for result in results} == {"10-Q", "10-Q/A"}
    assert stored["base_form"] == "10-Q"
    assert stored["is_amendment"] == 1
    assert second.embedded_chunks == 0


def test_empty_query_and_no_matching_filter_are_reasonable(
    vector_retriever: tuple[
        VectorRetriever,
        FakeEmbeddingProvider,
    ],
) -> None:
    retriever, provider = vector_retriever
    retriever.index()

    assert retriever.search("   ") == []
    assert retriever.search(
        "revenue",
        ticker="ORCL",
    ) == []
    assert provider.query_calls == []


def test_embedding_api_error_is_propagated(
    vector_db: Path,
    tmp_path: Path,
) -> None:
    retriever = VectorRetriever(
        FailingEmbeddingProvider(),
        db_path=vector_db,
        vector_path=tmp_path / "failing-vectors",
    )

    with pytest.raises(
        EmbeddingAPIError,
        match="simulated DashScope API failure",
    ):
        retriever.index()


def test_dashscope_client_retries_and_wraps_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingMultiModalEmbedding:
        def __init__(self) -> None:
            self.calls = 0

        def call(self, **kwargs: object) -> object:
            self.calls += 1
            raise TimeoutError("simulated timeout")

    embedding_api = FailingMultiModalEmbedding()
    monkeypatch.setenv(
        "DASHSCOPE_API_KEY",
        "test-only-key",
    )
    monkeypatch.setattr(
        dashscope_module,
        "MultiModalEmbedding",
        embedding_api,
    )
    client = DashScopeEmbeddingClient(
        max_retries=3,
        retry_delay=0,
    )

    with pytest.raises(
        EmbeddingAPIError,
        match="failed after 3 attempts",
    ):
        client.embed_query("data center demand")

    assert embedding_api.calls == 3


def test_dashscope_client_uses_text_only_multimodal_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SuccessfulMultiModalEmbedding:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        def call(self, **kwargs: object) -> object:
            self.kwargs = kwargs
            return SimpleNamespace(
                status_code=HTTPStatus.OK,
                output={
                    "embeddings": [
                        {
                            "index": index,
                            "embedding": [float(index)] * 1024,
                        }
                        for index in range(2)
                    ]
                },
            )

    embedding_api = SuccessfulMultiModalEmbedding()
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-only-key")
    monkeypatch.setattr(
        dashscope_module,
        "MultiModalEmbedding",
        embedding_api,
    )
    client = DashScopeEmbeddingClient(timeout=12.5)

    embeddings = client.embed_documents(["first", "second"])

    assert len(embeddings) == 2
    assert embedding_api.kwargs["model"] == "qwen3-vl-embedding"
    assert embedding_api.kwargs["input"] == [
        {"text": "first"},
        {"text": "second"},
    ]
    assert embedding_api.kwargs["dimension"] == 1024
    assert embedding_api.kwargs["base_address"] == (
        "https://dashscope.aliyuncs.com/api/v1"
    )
    assert embedding_api.kwargs["request_timeout"] == 12.5
