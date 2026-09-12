"""Tests for BM25 + Vector Reciprocal Rank Fusion."""

from __future__ import annotations

from typing import Any

import pytest

from thesis_tracker.retrieve.bm25 import BM25Result
from thesis_tracker.retrieve.hybrid import (
    HybridRetriever,
    collapse_children_to_parents,
    dynamic_child_candidate_k,
    logical_parent_id,
)
from thesis_tracker.retrieve.vector import VectorResult


def _bm25(
    chunk_id: str,
    score: float,
) -> BM25Result:
    return BM25Result(
        chunk_id=chunk_id,
        ticker="NVDA",
        form_type="10-Q",
        accession="nvda-q",
        section="part_i_item_2",
        title=f"BM25 {chunk_id}",
        bm25_score=score,
        text=f"Text for {chunk_id}",
    )


def _vector(
    chunk_id: str,
    distance: float,
) -> VectorResult:
    return VectorResult(
        chunk_id=chunk_id,
        ticker="NVDA",
        form_type="10-Q",
        accession="nvda-q",
        section="part_i_item_2",
        title=f"Vector {chunk_id}",
        distance=distance,
        text=f"Text for {chunk_id}",
    )


class FakeSearcher:
    def __init__(self, results: list[Any]) -> None:
        self.results = results
        self.calls: list[dict[str, Any]] = []

    def search(
        self,
        query: str,
        *,
        top_k: int,
        ticker: str | None,
        form_type: str | None,
    ) -> list[Any]:
        self.calls.append(
            {
                "query": query,
                "top_k": top_k,
                "ticker": ticker,
                "form_type": form_type,
            }
        )
        return self.results[:top_k]


class CountingSearcher(FakeSearcher):
    def __init__(self, results: list[Any], corpus_size: int) -> None:
        super().__init__(results)
        self.corpus_size = corpus_size
        self.count_calls: list[dict[str, str | None]] = []

    def count_candidates(
        self,
        *,
        ticker: str | None,
        form_type: str | None,
    ) -> int:
        self.count_calls.append({"ticker": ticker, "form_type": form_type})
        return self.corpus_size


def test_rrf_merges_dual_and_single_source_chunks() -> None:
    bm25 = FakeSearcher([
        _bm25("shared", 9.0),
        _bm25("bm25-only", 5.0),
    ])
    vector = FakeSearcher([
        _vector("vector-only", 0.1),
        _vector("shared", 0.2),
    ])
    retriever = HybridRetriever(
        bm25,
        vector,
    )

    results = retriever.search(
        "data center demand",
        top_k=3,
    )

    assert [result.chunk_id for result in results] == [
        "shared",
        "vector-only",
        "bm25-only",
    ]
    shared = results[0]
    assert shared.rrf_score == pytest.approx(
        (1 / 61) + (1 / 62)
    )
    assert shared.bm25_rank == 1
    assert shared.vector_rank == 2
    assert shared.bm25_score == 9.0
    assert shared.vector_distance == 0.2

    assert results[1].bm25_rank is None
    assert results[1].vector_rank == 1
    assert results[1].rrf_score == pytest.approx(
        1 / 61
    )
    assert results[2].bm25_rank == 2
    assert results[2].vector_rank is None
    assert results[2].rrf_score == pytest.approx(
        1 / 62
    )


def test_filters_and_candidate_k_reach_both_retrievers() -> None:
    bm25 = FakeSearcher([_bm25("a", 1.0)])
    vector = FakeSearcher([_vector("b", 0.1)])
    retriever = HybridRetriever(
        bm25,
        vector,
        candidate_k=20,
    )

    retriever.search(
        "outlook",
        ticker="NVDA",
        form_type="10-Q",
    )

    expected = {
        "query": "outlook",
        "top_k": 20,
        "ticker": "NVDA",
        "form_type": "10-Q",
    }
    assert bm25.calls == [expected]
    assert vector.calls == [expected]


def test_top_k_limits_fused_results() -> None:
    bm25 = FakeSearcher([
        _bm25("a", 3.0),
        _bm25("b", 2.0),
        _bm25("c", 1.0),
    ])
    vector = FakeSearcher([
        _vector("c", 0.1),
        _vector("b", 0.2),
        _vector("a", 0.3),
    ])

    results = HybridRetriever(
        bm25,
        vector,
    ).search(
        "query",
        top_k=2,
    )

    assert len(results) == 2


def test_empty_query_and_no_results_are_reasonable() -> None:
    bm25 = FakeSearcher([])
    vector = FakeSearcher([])
    retriever = HybridRetriever(
        bm25,
        vector,
    )

    assert retriever.search("   ") == []
    assert bm25.calls == []
    assert vector.calls == []
    assert retriever.search("no results") == []


def test_parent_collapse_preserves_first_best_child_order() -> None:
    children = [
        _bm25("A::chunk_001", 9.0),
        _bm25("B::chunk_001", 8.0),
        _bm25("A::chunk_002", 7.0),
        _bm25("C::chunk_001", 6.0),
        _bm25("C::chunk_002", 5.0),
        _bm25("C::chunk_003", 4.0),
    ]

    parents = collapse_children_to_parents(children)

    assert [item.logical_parent_id for item in parents] == ["A", "B", "C"]
    assert [item.best_child.chunk_id for item in parents] == [
        "A::chunk_001",
        "B::chunk_001",
        "C::chunk_001",
    ]
    assert [item.original_child_rank for item in parents] == [1, 2, 4]


def test_many_siblings_do_not_bonus_a_lower_ranked_parent() -> None:
    children = [
        _bm25("A::chunk_000", 10.0),
        _bm25("B::chunk_000", 9.0),
        *[_bm25(f"C::chunk_{index:03d}", 8.0 - index) for index in range(8)],
    ]

    parents = collapse_children_to_parents(children)

    assert [item.logical_parent_id for item in parents] == ["A", "B", "C"]
    assert parents[1].parent_rank == 2
    assert parents[2].parent_rank == 3


def test_dynamic_pool_uses_small_corpus_size() -> None:
    assert dynamic_child_candidate_k(17) == 17


def test_dynamic_pool_expands_with_large_corpus_ratio() -> None:
    assert dynamic_child_candidate_k(204) == 102
    assert dynamic_child_candidate_k(400) == 200


def test_dynamic_pool_never_exceeds_corpus_size() -> None:
    assert dynamic_child_candidate_k(0) == 0
    assert dynamic_child_candidate_k(49) == 49
    assert dynamic_child_candidate_k(100) == 50


def test_default_search_uses_dynamic_pool_and_preserves_filters() -> None:
    bm25 = CountingSearcher([_bm25("A::chunk_000", 1.0)], corpus_size=204)
    vector = FakeSearcher([])

    HybridRetriever(bm25, vector).search(
        "demand",
        ticker="NVDA",
        form_type="10-Q",
    )

    assert bm25.calls[0]["top_k"] == 102
    assert vector.calls[0]["top_k"] == 102
    assert bm25.count_calls == [{"ticker": "NVDA", "form_type": "10-Q"}]


def test_parent_candidate_k_means_twenty_distinct_parents() -> None:
    bm25 = FakeSearcher(
        [
            result
            for index in range(25)
            for result in (
                _bm25(f"P{index:02d}::chunk_000", 100.0 - index),
                _bm25(f"P{index:02d}::chunk_001", 99.5 - index),
            )
        ]
    )

    results = HybridRetriever(bm25, FakeSearcher([])).search("query", top_k=20)

    assert len(results) == 20
    assert len({result.logical_parent_id for result in results}) == 20


def test_vector_parent_collapse_is_deterministic() -> None:
    children = [
        _vector("A::chunk_002", 0.1),
        _vector("A::chunk_001", 0.2),
        _vector("B::chunk_000", 0.3),
    ]

    first = collapse_children_to_parents(children)
    second = collapse_children_to_parents(children)

    assert first == second
    assert first[0].best_child.chunk_id == "A::chunk_002"
    assert first[0].original_child_rank == 1


def test_rrf_fuses_different_children_by_logical_parent() -> None:
    results = HybridRetriever(
        FakeSearcher([_bm25("A::chunk_001", 5.0)]),
        FakeSearcher([_vector("A::chunk_002", 0.1)]),
    ).search("query")

    assert len(results) == 1
    result = results[0]
    assert result.logical_parent_id == "A"
    assert result.rrf_score == pytest.approx(2 / 61)
    assert result.bm25_best_chunk_id == "A::chunk_001"
    assert result.vector_best_chunk_id == "A::chunk_002"


def test_representative_child_uses_best_original_rank_then_bm25_tie() -> None:
    results = HybridRetriever(
        FakeSearcher(
            [
                _bm25("B::chunk_000", 9.0),
                _bm25("A::chunk_001", 8.0),
            ]
        ),
        FakeSearcher([_vector("A::chunk_002", 0.1)]),
    ).search("query", top_k=2)

    parent_a = next(result for result in results if result.logical_parent_id == "A")
    assert parent_a.chunk_id == "A::chunk_002"
    assert parent_a.representative_chunk_id == "A::chunk_002"
    assert logical_parent_id(parent_a.representative_chunk_id) == "A"

    tied = HybridRetriever(
        FakeSearcher([_bm25("A::chunk_001", 5.0)]),
        FakeSearcher([_vector("A::chunk_002", 0.1)]),
    ).search("query")[0]
    assert tied.representative_chunk_id == "A::chunk_001"


def test_v1_ids_remain_compatible_with_parent_collapse() -> None:
    assert logical_parent_id("accession::part_i_item_2") == (
        "accession::part_i_item_2"
    )
    results = collapse_children_to_parents(
        [_bm25("legacy-a", 2.0), _bm25("legacy-b", 1.0)]
    )
    assert [result.logical_parent_id for result in results] == [
        "legacy-a",
        "legacy-b",
    ]
