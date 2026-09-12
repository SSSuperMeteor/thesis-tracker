"""Deterministic BM25 retrieval over verified SEC filing chunks."""

from __future__ import annotations

import argparse
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from rank_bm25 import BM25Okapi

DEFAULT_DB_PATH = Path("data/corpus.db")
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """Split text into deterministic, case-insensitive alphanumeric tokens."""
    return _TOKEN_RE.findall(text.casefold())


@dataclass(frozen=True, slots=True)
class BM25Result:
    """A ranked chunk returned by :class:`BM25Retriever`."""

    chunk_id: str
    ticker: str
    form_type: str
    accession: str
    section: str | None
    title: str
    bm25_score: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _Candidate:
    chunk_id: str
    ticker: str
    form_type: str
    accession: str
    section: str | None
    title: str
    text: str


class BM25Retriever:
    """Build a small in-memory BM25 index from filtered SQLite chunks."""

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
    ) -> None:
        self.db_path = Path(db_path)

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        ticker: str | None = None,
        form_type: str | None = None,
    ) -> list[BM25Result]:
        """Return the highest-scoring chunks that share a token with ``query``."""
        if top_k <= 0:
            raise ValueError("top_k must be greater than zero")

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        candidates = self._load_candidates(
            ticker=ticker,
            form_type=form_type,
        )
        if not candidates:
            return []

        tokenized_corpus = [
            tokenize(candidate.text)
            for candidate in candidates
        ]
        query_token_set = set(query_tokens)
        matching_indices = [
            index
            for index, tokens in enumerate(tokenized_corpus)
            if query_token_set.intersection(tokens)
        ]
        if not matching_indices:
            return []

        scores = BM25Okapi(
            tokenized_corpus
        ).get_scores(query_tokens)
        ranked_indices = sorted(
            matching_indices,
            key=lambda index: float(scores[index]),
            reverse=True,
        )[:top_k]

        return [
            BM25Result(
                chunk_id=candidates[index].chunk_id,
                ticker=candidates[index].ticker,
                form_type=candidates[index].form_type,
                accession=candidates[index].accession,
                section=candidates[index].section,
                title=candidates[index].title,
                bm25_score=float(scores[index]),
                text=candidates[index].text,
            )
            for index in ranked_indices
        ]

    def count_candidates(
        self,
        *,
        ticker: str | None = None,
        form_type: str | None = None,
    ) -> int:
        """Return the filtered child-chunk corpus size."""
        return len(self._load_candidates(ticker=ticker, form_type=form_type))

    def _load_candidates(
        self,
        *,
        ticker: str | None,
        form_type: str | None,
    ) -> list[_Candidate]:
        if not self.db_path.is_file():
            raise FileNotFoundError(
                f"BM25 database not found: {self.db_path}"
            )

        clauses = [
            "d.ingestion_status = 'success'",
            "c.span_verified = 1",
        ]
        parameters: list[str] = []

        if ticker:
            clauses.append("UPPER(d.ticker) = ?")
            parameters.append(ticker.upper())

        if form_type:
            clauses.append("UPPER(d.form_type) = ?")
            parameters.append(form_type.upper())

        where = " AND ".join(clauses)
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
                    d.accession,
                    c.section_key,
                    c.title,
                    c.text
                FROM chunks AS c
                JOIN documents AS d
                    ON d.accession = c.accession
                WHERE {where}
                ORDER BY
                    d.filing_date DESC,
                    c.ord ASC,
                    c.chunk_id ASC
                """,
                parameters,
            ).fetchall()

        return [
            _Candidate(
                chunk_id=str(row[0]),
                ticker=str(row[1]),
                form_type=str(row[2]),
                accession=str(row[3]),
                section=(
                    str(row[4])
                    if row[4] is not None
                    else None
                ),
                title=str(row[5]),
                text=str(row[6]),
            )
            for row in rows
        ]


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Search verified SEC chunks with BM25.",
    )
    parser.add_argument("query")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--ticker")
    parser.add_argument("--form-type")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
    )
    args = parser.parse_args()

    results = BM25Retriever(args.db).search(
        args.query,
        top_k=args.top_k,
        ticker=args.ticker,
        form_type=args.form_type,
    )

    if not results:
        print("No matching chunks.")
        return

    for rank, result in enumerate(results, start=1):
        preview = " ".join(
            result.text.split()
        )[:240]
        print(
            f"{rank}. {result.ticker} {result.form_type} "
            f"score={result.bm25_score:.4f}"
        )
        print(
            f"   {result.chunk_id} | "
            f"{result.section or '-'} | {result.title}"
        )
        print(f"   {preview}")


if __name__ == "__main__":
    _main()
