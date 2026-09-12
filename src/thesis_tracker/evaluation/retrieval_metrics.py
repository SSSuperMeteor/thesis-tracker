"""Shared retrieval-metric helpers for the Stage 2 benchmark runners."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Iterable, Sequence

METHODS = ("bm25", "vector", "hybrid")


def rank_of(
    results: Sequence[Any],
    matches: Callable[[str], bool],
) -> int | None:
    """Return the 1-based rank of the first result accepted by ``matches``."""
    return next(
        (
            rank
            for rank, result in enumerate(results, start=1)
            if matches(result.chunk_id)
        ),
        None,
    )


def serialize_result(result: Any, rank: int, *, preview_chars: int = 500) -> dict[str, Any]:
    """Return a compact JSON-serializable record for one retrieved chunk."""
    record = result.to_dict()
    text = str(record.pop("text"))
    record["rank"] = rank
    record["text_preview"] = " ".join(text.split())[:preview_chars]
    return record


def method_record(
    results: Sequence[Any],
    expected_ids: Iterable[str],
    *,
    rank: int | None = None,
    preview_chars: int = 500,
) -> dict[str, Any]:
    """Build hit flags, reciprocal rank, and Top 20 detail for one method."""
    expected = set(expected_ids)
    if rank is None:
        rank = rank_of(results, expected.__contains__)
    return {
        "expected_rank": rank,
        "hit_at_1": rank is not None and rank <= 1,
        "hit_at_3": rank is not None and rank <= 3,
        "hit_at_5": rank is not None and rank <= 5,
        "hit_at_10": rank is not None and rank <= 10,
        "hit_at_20": rank is not None and rank <= 20,
        "reciprocal_rank": 0.0 if rank is None else 1.0 / rank,
        "top20": [
            serialize_result(result, result_rank, preview_chars=preview_chars)
            for result_rank, result in enumerate(results, start=1)
        ],
    }


def calculate_metrics(
    records: Sequence[dict[str, Any]],
    method: str,
) -> dict[str, float | int]:
    """Aggregate Recall@k and MRR for one method over already-scored records."""
    count = len(records)
    if not count:
        return {
            "questions": 0,
            "recall_at_1": 0.0,
            "recall_at_3": 0.0,
            "recall_at_5": 0.0,
            "recall_at_10": 0.0,
            "recall_at_20": 0.0,
            "mrr": 0.0,
        }
    method_records = [record["retrieval"][method] for record in records]
    return {
        "questions": count,
        "recall_at_1": sum(item["hit_at_1"] for item in method_records) / count,
        "recall_at_3": sum(item["hit_at_3"] for item in method_records) / count,
        "recall_at_5": sum(item["hit_at_5"] for item in method_records) / count,
        "recall_at_10": sum(item["hit_at_10"] for item in method_records) / count,
        "recall_at_20": sum(item["hit_at_20"] for item in method_records) / count,
        "mrr": sum(item["reciprocal_rank"] for item in method_records) / count,
    }


def group_metrics(
    records: Sequence[dict[str, Any]],
    key: str,
    *,
    methods: Sequence[str] = METHODS,
) -> dict[str, dict[str, dict[str, float | int]]]:
    """Aggregate per-group metrics for every requested method."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[str(record[key])].append(record)
    return {
        group: {method: calculate_metrics(items, method) for method in methods}
        for group, items in sorted(groups.items())
    }


def metric_table(
    metrics: dict[str, dict[str, float | int]],
    *,
    methods: Sequence[str] = METHODS,
) -> list[str]:
    """Render a Markdown recall/MRR table."""
    lines = [
        "| Method | Cases | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Recall@20 | MRR |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for method in methods:
        item = metrics[method]
        lines.append(
            f"| {method.title()} | {item['questions']} | {item['recall_at_1']:.3f} | "
            f"{item['recall_at_3']:.3f} | {item['recall_at_5']:.3f} | "
            f"{item['recall_at_10']:.3f} | {item['recall_at_20']:.3f} | {item['mrr']:.3f} |"
        )
    return lines


def format_rank(rank: int | None) -> str:
    """Render a rank for Markdown tables, marking retrieval misses."""
    return "miss" if rank is None else str(rank)


def format_delta(value: float, *, digits: int = 3) -> str:
    """Render a signed delta for Markdown tables."""
    return f"{value:+.{digits}f}"


def winner(
    metrics: dict[str, dict[str, float | int]],
    *,
    methods: Sequence[str] = METHODS,
) -> str:
    """Return the method(s) with the best Recall@5, then Recall@10, then MRR."""

    def score(method: str) -> tuple[float | int, float | int, float | int]:
        return (
            metrics[method]["recall_at_5"],
            metrics[method]["recall_at_10"],
            metrics[method]["mrr"],
        )

    best_score = max(score(method) for method in methods)
    return ", ".join(method for method in methods if score(method) == best_score)
