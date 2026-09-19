"""ChromaDB vector retrieval over verified SEC filing chunks."""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import chromadb

from thesis_tracker.embedding import EmbeddingProvider
from thesis_tracker.embedding.dashscope import (
    EMBEDDING_DIMENSION,
    DashScopeEmbeddingClient,
    EmbeddingAPIError,
)

DEFAULT_DB_PATH = Path("data/corpus.db")
DEFAULT_VECTOR_PATH = Path("store/vectors")
DEFAULT_COLLECTION_NAME = (
    "sec_chunks_qwen3_vl_embedding_1024"
)
DEFAULT_BATCH_SIZE = 10


class VectorRetrievalError(RuntimeError):
    """Raised when vector indexing or querying cannot be completed."""


@dataclass(frozen=True, slots=True)
class IndexStats:
    total_chunks: int
    embedded_chunks: int
    skipped_unchanged: int
    batches: int
    removed_stale: int


@dataclass(frozen=True, slots=True)
class VectorResult:
    chunk_id: str
    ticker: str
    form_type: str
    accession: str
    section: str | None
    title: str
    distance: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _ChunkRecord:
    chunk_id: str
    ticker: str
    form_type: str
    base_form: str
    is_amendment: bool
    accession: str
    section: str | None
    title: str
    text: str
    text_hash: str

    def metadata(
        self,
        provider: EmbeddingProvider,
    ) -> dict[str, str | int]:
        return {
            "ticker": self.ticker,
            "form_type": self.form_type,
            "base_form": self.base_form,
            "is_amendment": int(self.is_amendment),
            "accession": self.accession,
            "section": self.section or "",
            "title": self.title,
            "text_hash": self.text_hash,
            "embedding_model": provider.model_name,
            "embedding_dimension": provider.dimension,
        }


class VectorRetriever:
    """Index SQLite chunks in Chroma and retrieve them by vector distance."""

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        *,
        db_path: str | Path = DEFAULT_DB_PATH,
        vector_path: str | Path = DEFAULT_VECTOR_PATH,
        collection_name: str = DEFAULT_COLLECTION_NAME,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        if batch_size <= 0:
            raise ValueError(
                "batch_size must be greater than zero"
            )
        if embedding_provider.dimension != EMBEDDING_DIMENSION:
            raise ValueError(
                "embedding provider dimension must be 1024"
            )

        self.embedding_provider = embedding_provider
        self.db_path = Path(db_path)
        self.vector_path = Path(vector_path)
        self.batch_size = batch_size
        self.vector_path.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._client = chromadb.PersistentClient(
            path=str(self.vector_path)
        )
        self._collection = (
            self._client.get_or_create_collection(
                name=collection_name,
                metadata={
                    "hnsw:space": "cosine",
                    "embedding_model": (
                        embedding_provider.model_name
                    ),
                    "embedding_dimension": (
                        embedding_provider.dimension
                    ),
                },
                embedding_function=None,
            )
        )

    def index(
        self,
        *,
        tickers: list[str] | None = None,
        form_type: str | None = None,
    ) -> IndexStats:
        """Upsert changed chunks and skip embeddings with the same text hash."""
        records = self._load_chunks(
            tickers=tickers,
            form_type=form_type,
        )
        existing = self._existing_metadata(
            [record.chunk_id for record in records]
        )
        changed = [
            record
            for record in records
            if not self._is_unchanged(
                record,
                existing.get(record.chunk_id),
            )
        ]
        unchanged = [
            record
            for record in records
            if record not in changed
        ]

        self._refresh_metadata(unchanged)

        batches = 0
        for offset in range(
            0,
            len(changed),
            self.batch_size,
        ):
            batch = changed[
                offset : offset + self.batch_size
            ]
            embeddings = (
                self.embedding_provider.embed_documents(
                    [record.text for record in batch]
                )
            )
            self._validate_embeddings(
                embeddings,
                expected_count=len(batch),
            )
            self._collection.upsert(
                ids=[record.chunk_id for record in batch],
                embeddings=embeddings,
                documents=[record.text for record in batch],
                metadatas=[
                    record.metadata(
                        self.embedding_provider
                    )
                    for record in batch
                ],
            )
            batches += 1

        removed_stale = 0
        if not tickers and not form_type:
            valid_ids = {
                record.chunk_id
                for record in records
            }
            stored_ids = set(
                self._collection.get(
                    include=[]
                )["ids"]
            )
            stale_ids = sorted(
                stored_ids - valid_ids
            )
            if stale_ids:
                self._collection.delete(
                    ids=stale_ids
                )
            removed_stale = len(stale_ids)

        return IndexStats(
            total_chunks=len(records),
            embedded_chunks=len(changed),
            skipped_unchanged=len(unchanged),
            batches=batches,
            removed_stale=removed_stale,
        )

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        ticker: str | None = None,
        form_type: str | None = None,
    ) -> list[VectorResult]:
        """Embed ``query`` and return the nearest filtered Chroma records."""
        if top_k <= 0:
            raise ValueError(
                "top_k must be greater than zero"
            )
        if not query.strip():
            return []

        where = self._where_filter(
            ticker=ticker,
            form_type=form_type,
        )
        get_arguments: dict[str, Any] = {
            "include": [],
        }
        if where is not None:
            get_arguments["where"] = where
        matching_ids = self._collection.get(
            **get_arguments
        )["ids"]
        if not matching_ids:
            return []

        query_embedding = (
            self.embedding_provider.embed_query(
                query
            )
        )
        if len(query_embedding) != (
            self.embedding_provider.dimension
        ):
            raise VectorRetrievalError(
                "embedding provider returned an unexpected "
                "query dimension"
            )

        query_arguments: dict[str, Any] = {
            "query_embeddings": [query_embedding],
            "n_results": min(
                top_k,
                len(matching_ids),
            ),
            "include": [
                "documents",
                "metadatas",
                "distances",
            ],
        }
        if where is not None:
            query_arguments["where"] = where

        response = self._collection.query(
            **query_arguments
        )
        ids = response["ids"][0]
        documents = response["documents"][0]
        metadatas = response["metadatas"][0]
        distances = response["distances"][0]

        return [
            VectorResult(
                chunk_id=str(chunk_id),
                ticker=str(metadata["ticker"]),
                form_type=str(metadata["form_type"]),
                accession=str(metadata["accession"]),
                section=(
                    str(metadata["section"])
                    if metadata.get("section")
                    else None
                ),
                title=str(metadata["title"]),
                distance=float(distance),
                text=str(document),
            )
            for chunk_id, document, metadata, distance
            in zip(
                ids,
                documents,
                metadatas,
                distances,
                strict=True,
            )
        ]

    def count_candidates(
        self,
        *,
        ticker: str | None = None,
        form_type: str | None = None,
    ) -> int:
        """Return the filtered child-chunk collection size."""
        where = self._where_filter(ticker=ticker, form_type=form_type)
        arguments: dict[str, Any] = {"include": []}
        if where is not None:
            arguments["where"] = where
        return len(self._collection.get(**arguments)["ids"])

    def _load_chunks(
        self,
        *,
        tickers: list[str] | None,
        form_type: str | None,
    ) -> list[_ChunkRecord]:
        if not self.db_path.is_file():
            raise FileNotFoundError(
                f"vector database not found: {self.db_path}"
            )

        clauses = [
            "d.ingestion_status = 'success'",
            "c.span_verified = 1",
        ]
        parameters: list[str] = []

        if tickers:
            normalized_tickers = [
                ticker.upper()
                for ticker in tickers
            ]
            placeholders = ",".join(
                "?"
                for _ in normalized_tickers
            )
            clauses.append(
                f"UPPER(d.ticker) IN ({placeholders})"
            )
            parameters.extend(
                normalized_tickers
            )

        if form_type:
            clauses.append(
                "UPPER(d.base_form) = ?"
            )
            parameters.append(
                form_type.upper()
            )

        database_uri = (
            f"{self.db_path.resolve().as_uri()}"
            "?mode=ro"
        )
        with sqlite3.connect(
            database_uri,
            uri=True,
        ) as connection:
            rows = connection.execute(
                f"""
                SELECT
                    c.chunk_id,
                    d.ticker,
                    d.form_type,
                    d.base_form,
                    d.is_amendment,
                    d.accession,
                    c.section_key,
                    c.title,
                    c.text,
                    c.text_hash
                FROM chunks AS c
                JOIN documents AS d
                    ON d.accession = c.accession
                WHERE {" AND ".join(clauses)}
                ORDER BY
                    d.filing_date DESC,
                    c.ord ASC,
                    c.chunk_id ASC
                """,
                parameters,
            ).fetchall()

        return [
            _ChunkRecord(
                chunk_id=str(row[0]),
                ticker=str(row[1]),
                form_type=str(row[2]),
                base_form=str(row[3]),
                is_amendment=bool(row[4]),
                accession=str(row[5]),
                section=(
                    str(row[6])
                    if row[6] is not None
                    else None
                ),
                title=(
                    str(row[7])
                    if row[7] is not None
                    else ""
                ),
                text=str(row[8]),
                text_hash=str(row[9]),
            )
            for row in rows
        ]

    def _existing_metadata(
        self,
        ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        stored = self._collection.get(
            ids=ids,
            include=["metadatas"],
        )
        return {
            str(chunk_id): metadata
            for chunk_id, metadata in zip(
                stored["ids"],
                stored["metadatas"],
                strict=True,
            )
        }

    def _is_unchanged(
        self,
        record: _ChunkRecord,
        metadata: dict[str, Any] | None,
    ) -> bool:
        return bool(
            metadata
            and metadata.get("text_hash")
            == record.text_hash
            and metadata.get("embedding_model")
            == self.embedding_provider.model_name
            and metadata.get("embedding_dimension")
            == self.embedding_provider.dimension
        )

    def _refresh_metadata(
        self,
        records: list[_ChunkRecord],
    ) -> None:
        for offset in range(
            0,
            len(records),
            self.batch_size,
        ):
            batch = records[
                offset : offset + self.batch_size
            ]
            self._collection.update(
                ids=[record.chunk_id for record in batch],
                metadatas=[
                    record.metadata(
                        self.embedding_provider
                    )
                    for record in batch
                ],
            )

    def _validate_embeddings(
        self,
        embeddings: list[list[float]],
        *,
        expected_count: int,
    ) -> None:
        if len(embeddings) != expected_count:
            raise VectorRetrievalError(
                "embedding provider returned an unexpected "
                "number of embeddings"
            )
        if any(
            len(embedding)
            != self.embedding_provider.dimension
            for embedding in embeddings
        ):
            raise VectorRetrievalError(
                "embedding provider returned an unexpected "
                "document dimension"
            )

    @staticmethod
    def _where_filter(
        *,
        ticker: str | None,
        form_type: str | None,
    ) -> dict[str, Any] | None:
        filters: list[dict[str, str]] = []
        if ticker:
            filters.append(
                {"ticker": ticker.upper()}
            )
        if form_type:
            filters.append(
                {"base_form": form_type.upper()}
            )
        if not filters:
            return None
        if len(filters) == 1:
            return filters[0]
        return {"$and": filters}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Index and search SEC chunks with ChromaDB.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
    )
    parser.add_argument(
        "--vector-path",
        type=Path,
        default=DEFAULT_VECTOR_PATH,
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    index_parser = subparsers.add_parser(
        "index",
        help="Build or update the vector index.",
    )
    index_parser.add_argument(
        "--ticker",
        action="append",
        dest="tickers",
    )
    index_parser.add_argument("--form-type")
    index_parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
    )

    query_parser = subparsers.add_parser(
        "query",
        help="Search the vector index.",
    )
    query_parser.add_argument("query")
    query_parser.add_argument(
        "--top-k",
        type=int,
        default=5,
    )
    query_parser.add_argument("--ticker")
    query_parser.add_argument("--form-type")

    return parser


def _main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        provider = DashScopeEmbeddingClient()
        retriever = VectorRetriever(
            provider,
            db_path=args.db,
            vector_path=args.vector_path,
            batch_size=getattr(
                args,
                "batch_size",
                DEFAULT_BATCH_SIZE,
            ),
        )

        if args.command == "index":
            stats = retriever.index(
                tickers=args.tickers,
                form_type=args.form_type,
            )
            print(
                "Index complete: "
                f"total={stats.total_chunks}, "
                f"embedded={stats.embedded_chunks}, "
                f"unchanged={stats.skipped_unchanged}, "
                f"batches={stats.batches}, "
                f"removed_stale={stats.removed_stale}"
            )
            return

        results = retriever.search(
            args.query,
            top_k=args.top_k,
            ticker=args.ticker,
            form_type=args.form_type,
        )
        if not results:
            print("No matching chunks.")
            return

        for rank, result in enumerate(
            results,
            start=1,
        ):
            preview = " ".join(
                result.text.split()
            )[:240]
            print(
                f"{rank}. {result.ticker} "
                f"{result.form_type} "
                f"distance={result.distance:.6f}"
            )
            print(
                f"   {result.chunk_id} | "
                f"{result.section or '-'} | "
                f"{result.title}"
            )
            print(f"   {preview}")
    except (
        EmbeddingAPIError,
        VectorRetrievalError,
        FileNotFoundError,
        ValueError,
    ) as error:
        parser.exit(
            status=1,
            message=f"error: {error}\n",
        )


if __name__ == "__main__":
    _main()
