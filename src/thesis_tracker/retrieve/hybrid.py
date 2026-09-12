"""Hybrid BM25 and vector retrieval with Reciprocal Rank Fusion."""

from __future__ import annotations

import argparse
import math
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from thesis_tracker.embedding.dashscope import (
    DashScopeEmbeddingClient,
    EmbeddingAPIError,
)
from thesis_tracker.retrieve.bm25 import (
    DEFAULT_DB_PATH,
    BM25Result,
    BM25Retriever,
)
from thesis_tracker.retrieve.vector import (
    DEFAULT_VECTOR_PATH,
    VectorResult,
    VectorRetrievalError,
    VectorRetriever,
)

DEFAULT_RRF_K = 60
DEFAULT_CANDIDATE_K = 20
MIN_CHILD_CANDIDATES = 50
CHILD_CANDIDATE_RATIO = 0.50
DEFAULT_PARENT_CANDIDATE_K = 20
_SUBCHUNK_SUFFIX_RE = re.compile(r"::chunk_\d+$")


class _BM25Searcher(Protocol):
    def search(
        self,
        query: str,
        *,
        top_k: int,
        ticker: str | None,
        form_type: str | None,
    ) -> list[BM25Result]: ...

    def count_candidates(
        self,
        *,
        ticker: str | None,
        form_type: str | None,
    ) -> int: ...


class _VectorSearcher(Protocol):
    def search(
        self,
        query: str,
        *,
        top_k: int,
        ticker: str | None,
        form_type: str | None,
    ) -> list[VectorResult]: ...

    def count_candidates(
        self,
        *,
        ticker: str | None,
        form_type: str | None,
    ) -> int: ...


@dataclass(frozen=True, slots=True)
class HybridResult:
    chunk_id: str
    ticker: str
    form_type: str
    accession: str
    section: str | None
    title: str
    rrf_score: float
    bm25_rank: int | None
    vector_rank: int | None
    bm25_score: float | None
    vector_distance: float | None
    text: str
    logical_parent_id: str | None = None
    representative_chunk_id: str | None = None
    bm25_best_chunk_id: str | None = None
    vector_best_chunk_id: str | None = None
    bm25_parent_rank: int | None = None
    vector_parent_rank: int | None = None
    bm25_original_child_rank: int | None = None
    vector_original_child_rank: int | None = None
    representative_original_child_rank: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ParentRouteResult:
    """Best-child-only representation of one parent in one retrieval route."""

    logical_parent_id: str
    best_child: BM25Result | VectorResult
    original_child_rank: int
    parent_rank: int


@dataclass(slots=True)
class _FusedCandidate:
    logical_parent_id: str
    rrf_score: float = 0.0
    bm25: ParentRouteResult | None = None
    vector: ParentRouteResult | None = None

    def result(self) -> HybridResult:
        representative_route = self._representative_route()
        representative = representative_route.best_child
        bm25_child = self.bm25.best_child if self.bm25 is not None else None
        vector_child = self.vector.best_child if self.vector is not None else None
        return HybridResult(
            chunk_id=representative.chunk_id,
            ticker=representative.ticker,
            form_type=representative.form_type,
            accession=representative.accession,
            section=representative.section,
            title=representative.title,
            rrf_score=self.rrf_score,
            bm25_rank=self.bm25.parent_rank if self.bm25 else None,
            vector_rank=self.vector.parent_rank if self.vector else None,
            bm25_score=(
                bm25_child.bm25_score
                if isinstance(bm25_child, BM25Result)
                else None
            ),
            vector_distance=(
                vector_child.distance
                if isinstance(vector_child, VectorResult)
                else None
            ),
            text=representative.text,
            logical_parent_id=self.logical_parent_id,
            representative_chunk_id=representative.chunk_id,
            bm25_best_chunk_id=bm25_child.chunk_id if bm25_child else None,
            vector_best_chunk_id=vector_child.chunk_id if vector_child else None,
            bm25_parent_rank=self.bm25.parent_rank if self.bm25 else None,
            vector_parent_rank=self.vector.parent_rank if self.vector else None,
            bm25_original_child_rank=(
                self.bm25.original_child_rank if self.bm25 else None
            ),
            vector_original_child_rank=(
                self.vector.original_child_rank if self.vector else None
            ),
            representative_original_child_rank=(
                representative_route.original_child_rank
            ),
        )

    def _representative_route(self) -> ParentRouteResult:
        routes = [route for route in (self.bm25, self.vector) if route is not None]
        return min(
            routes,
            key=lambda route: (
                route.original_child_rank,
                0 if route is self.bm25 else 1,
                route.best_child.chunk_id,
            ),
        )


def logical_parent_id(chunk_id: str) -> str:
    """Map a v2 child ID to its parent, preserving v1 IDs unchanged."""
    return _SUBCHUNK_SUFFIX_RE.sub("", chunk_id)


def dynamic_child_candidate_k(
    corpus_size: int,
    *,
    minimum: int = MIN_CHILD_CANDIDATES,
    ratio: float = CHILD_CANDIDATE_RATIO,
) -> int:
    """Choose a bounded child depth from the filtered corpus size."""
    if corpus_size < 0:
        raise ValueError("corpus_size must be non-negative")
    if minimum <= 0:
        raise ValueError("minimum must be greater than zero")
    if not 0 < ratio <= 1:
        raise ValueError("ratio must be between zero and one")
    return min(corpus_size, max(minimum, math.ceil(corpus_size * ratio)))


def collapse_children_to_parents(
    results: list[BM25Result] | list[VectorResult],
    *,
    parent_candidate_k: int = DEFAULT_PARENT_CANDIDATE_K,
) -> list[ParentRouteResult]:
    """Keep only the first/best-ranked child for each logical parent."""
    if parent_candidate_k <= 0:
        raise ValueError("parent_candidate_k must be greater than zero")
    best: dict[str, tuple[BM25Result | VectorResult, int]] = {}
    for child_rank, child in enumerate(results, start=1):
        best.setdefault(logical_parent_id(child.chunk_id), (child, child_rank))
    return [
        ParentRouteResult(
            logical_parent_id=parent_id,
            best_child=child,
            original_child_rank=child_rank,
            parent_rank=parent_rank,
        )
        for parent_rank, (parent_id, (child, child_rank)) in enumerate(
            list(best.items())[:parent_candidate_k],
            start=1,
        )
    ]


class HybridRetriever:
    """Run BM25 and vector retrieval concurrently, then fuse their ranks."""

    def __init__(
        self,
        bm25_retriever: _BM25Searcher,
        vector_retriever: _VectorSearcher,
        *,
        rrf_k: int = DEFAULT_RRF_K,
        candidate_k: int | None = None,
        parent_candidate_k: int = DEFAULT_PARENT_CANDIDATE_K,
    ) -> None:
        if rrf_k < 0:
            raise ValueError(
                "rrf_k must be non-negative"
            )
        if candidate_k is not None and candidate_k <= 0:
            raise ValueError(
                "candidate_k must be greater than zero"
            )
        if parent_candidate_k <= 0:
            raise ValueError("parent_candidate_k must be greater than zero")

        self.bm25_retriever = bm25_retriever
        self.vector_retriever = vector_retriever
        self.rrf_k = rrf_k
        self.candidate_k = candidate_k
        self.parent_candidate_k = parent_candidate_k

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        ticker: str | None = None,
        form_type: str | None = None,
        candidate_k: int | None = None,
    ) -> list[HybridResult]:
        """Return fused results from equal-weight BM25 and vector rankings."""
        if top_k <= 0:
            raise ValueError(
                "top_k must be greater than zero"
            )
        if not query.strip():
            return []

        corpus_size = self._corpus_size(ticker=ticker, form_type=form_type)
        explicit_k = candidate_k if candidate_k is not None else self.candidate_k
        per_source_k = (
            min(corpus_size, explicit_k)
            if explicit_k is not None and corpus_size is not None
            else explicit_k
            if explicit_k is not None
            else dynamic_child_candidate_k(corpus_size)
            if corpus_size is not None
            else MIN_CHILD_CANDIDATES
        )
        if per_source_k <= 0:
            raise ValueError(
                "candidate_k must be greater than zero"
            )

        search_arguments = {
            "top_k": per_source_k,
            "ticker": ticker,
            "form_type": form_type,
        }
        with ThreadPoolExecutor(
            max_workers=2
        ) as executor:
            bm25_future = executor.submit(
                self.bm25_retriever.search,
                query,
                **search_arguments,
            )
            vector_future = executor.submit(
                self.vector_retriever.search,
                query,
                **search_arguments,
            )
            bm25_results = bm25_future.result()
            vector_results = vector_future.result()

        bm25_parents = collapse_children_to_parents(
            bm25_results,
            parent_candidate_k=self.parent_candidate_k,
        )
        vector_parents = collapse_children_to_parents(
            vector_results,
            parent_candidate_k=self.parent_candidate_k,
        )
        candidates: dict[str, _FusedCandidate] = {}

        for route in bm25_parents:
            candidate = candidates.setdefault(
                route.logical_parent_id,
                _FusedCandidate(route.logical_parent_id),
            )
            candidate.rrf_score += 1.0 / (self.rrf_k + route.parent_rank)
            candidate.bm25 = route

        for route in vector_parents:
            candidate = candidates.setdefault(
                route.logical_parent_id,
                _FusedCandidate(route.logical_parent_id),
            )
            candidate.rrf_score += 1.0 / (self.rrf_k + route.parent_rank)
            candidate.vector = route

        ranked = sorted(
            candidates.values(),
            key=lambda candidate: (
                -candidate.rrf_score,
                min(
                    candidate.bm25.parent_rank
                    if candidate.bm25
                    else self.parent_candidate_k + 1,
                    candidate.vector.parent_rank
                    if candidate.vector
                    else self.parent_candidate_k + 1,
                ),
                candidate.logical_parent_id,
            ),
        )
        return [
            candidate.result()
            for candidate in ranked[:top_k]
        ]

    def _corpus_size(
        self,
        *,
        ticker: str | None,
        form_type: str | None,
    ) -> int | None:
        counter = getattr(self.bm25_retriever, "count_candidates", None)
        if not callable(counter):
            counter = getattr(self.vector_retriever, "count_candidates", None)
        if not callable(counter):
            return None
        return int(counter(ticker=ticker, form_type=form_type))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Search SEC chunks with BM25 + Vector + RRF.",
    )
    parser.add_argument("query")
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
    )
    parser.add_argument(
        "--candidate-k",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--rrf-k",
        type=int,
        default=DEFAULT_RRF_K,
    )
    parser.add_argument("--ticker")
    parser.add_argument("--form-type")
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
    return parser


def _main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        hybrid = HybridRetriever(
            BM25Retriever(args.db),
            VectorRetriever(
                DashScopeEmbeddingClient(),
                db_path=args.db,
                vector_path=args.vector_path,
            ),
            rrf_k=args.rrf_k,
            candidate_k=args.candidate_k,
        )
        results = hybrid.search(
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
            bm25_rank = (
                str(result.bm25_rank)
                if result.bm25_rank is not None
                else "-"
            )
            vector_rank = (
                str(result.vector_rank)
                if result.vector_rank is not None
                else "-"
            )
            print(
                f"{rank}. {result.ticker} "
                f"{result.form_type} "
                f"rrf={result.rrf_score:.6f} "
                f"bm25_rank={bm25_rank} "
                f"vector_rank={vector_rank}"
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
