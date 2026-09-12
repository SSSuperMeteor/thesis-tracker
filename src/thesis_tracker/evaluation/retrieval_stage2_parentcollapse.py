"""Evaluate chunking v2 retrieval with best-child-only parent collapse."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from tqdm.auto import tqdm

from thesis_tracker.embedding.dashscope import (
    EMBEDDING_DIMENSION,
    MODEL_NAME,
    DashScopeEmbeddingClient,
)
from thesis_tracker.evaluation.retrieval_metrics import (
    METHODS,
    calculate_metrics,
    metric_table,
)
from thesis_tracker.evaluation.retrieval_stage2 import (
    DEFAULT_QUESTIONS_PATH,
    audit_questions,
)
from thesis_tracker.evaluation.retrieval_stage2_chunkv2 import (
    BASELINE_RESULTS_PATH,
    COLLECTION_NAME,
    _method_record,
    load_chunkv2_catalog,
    logical_parent_id,
)
from thesis_tracker.evaluation.retrieval_stage2_chunkv2 import (
    RESULTS_PATH as RAW_V2_RESULTS_PATH,
)
from thesis_tracker.retrieve.bm25 import BM25Retriever
from thesis_tracker.retrieve.hybrid import (
    CHILD_CANDIDATE_RATIO,
    DEFAULT_PARENT_CANDIDATE_K,
    DEFAULT_RRF_K,
    MIN_CHILD_CANDIDATES,
    HybridRetriever,
    collapse_children_to_parents,
    dynamic_child_candidate_k,
)
from thesis_tracker.retrieve.vector import (
    DEFAULT_DB_PATH,
    DEFAULT_VECTOR_PATH,
    VectorRetriever,
)

RESULTS_PATH = Path("eval/retrieval_stage2_chunkv2_parentcollapse_results.json")
REPORT_PATH = Path("eval/retrieval_stage2_chunkv2_parentcollapse_report.md")
DISPLACEMENT_CASE_IDS = (
    "stage2-013",
    "stage2-037",
    "stage2-040",
    "stage2-043",
    "stage2-049",
    "stage2-063",
    "stage2-066",
)
METRIC_KEYS = (
    "recall_at_1",
    "recall_at_3",
    "recall_at_5",
    "recall_at_10",
    "recall_at_20",
    "mrr",
)


class _SavedSearch:
    def __init__(self, results: Sequence[Any]) -> None:
        self.results = list(results)

    def search(self, *_args: Any, top_k: int, **_kwargs: Any) -> list[Any]:
        return self.results[:top_k]


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    """Run the frozen 70 cases once and compare v1, raw-v2, and collapse."""
    db_path = Path(args.db)
    subchunks, parents = load_chunkv2_catalog(db_path)
    questions, invalid = audit_questions(Path(args.questions), parents)
    if invalid:
        raise ValueError(f"question {invalid[0]['source_index']} invalid")

    raw_payload = json.loads(Path(args.raw_v2).read_text(encoding="utf-8"))
    v1_payload = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    raw_by_id = {record["id"]: record for record in raw_payload["questions"]}
    v1_by_id = {record["id"]: record for record in v1_payload["questions"]}
    corpus_sizes = dict(Counter(item["ticker"] for item in subchunks.values()))
    child_candidate_k = {
        ticker: dynamic_child_candidate_k(size)
        for ticker, size in sorted(corpus_sizes.items())
    }

    embedding = DashScopeEmbeddingClient(
        timeout=args.timeout,
        max_retries=args.max_retries,
    )
    bm25 = BM25Retriever(db_path)
    vector = VectorRetriever(
        embedding,
        db_path=db_path,
        vector_path=args.vector_path,
        collection_name=args.collection,
    )
    records = []
    for question in tqdm(
        questions,
        desc="Parent-collapse regression",
        unit="question",
    ):
        query = str(question["question"])
        ticker = str(question["ticker"])
        filters = {"ticker": ticker, "form_type": str(question["form_type"])}
        depth = child_candidate_k[ticker]
        bm25_children = bm25.search(query, top_k=depth, **filters)
        vector_children = vector.search(query, top_k=depth, **filters)
        bm25_parents = collapse_children_to_parents(
            bm25_children,
            parent_candidate_k=DEFAULT_PARENT_CANDIDATE_K,
        )
        vector_parents = collapse_children_to_parents(
            vector_children,
            parent_candidate_k=DEFAULT_PARENT_CANDIDATE_K,
        )
        bm25_results = [route.best_child for route in bm25_parents]
        vector_results = [route.best_child for route in vector_parents]
        hybrid_results = HybridRetriever(
            _SavedSearch(bm25_children),
            _SavedSearch(vector_children),
            rrf_k=DEFAULT_RRF_K,
            candidate_k=depth,
            parent_candidate_k=DEFAULT_PARENT_CANDIDATE_K,
        ).search(query, top_k=DEFAULT_PARENT_CANDIDATE_K, **filters)
        expected_ids = [str(question["expected_chunk_id"])]
        expected_ids.extend(
            str(value) for value in question.get("acceptable_chunk_ids", [])
        )
        expected_ids = list(dict.fromkeys(expected_ids))
        records.append(
            {
                **question,
                "child_candidate_k": depth,
                "expected_chunk_ids": expected_ids,
                "retrieval": {
                    "bm25": _method_record(
                        bm25_results,
                        expected_ids,
                        resolve=logical_parent_id,
                        preview_chars=args.preview_chars,
                    ),
                    "vector": _method_record(
                        vector_results,
                        expected_ids,
                        resolve=logical_parent_id,
                        preview_chars=args.preview_chars,
                    ),
                    "hybrid": _method_record(
                        hybrid_results,
                        expected_ids,
                        resolve=logical_parent_id,
                        preview_chars=args.preview_chars,
                    ),
                },
            }
        )

    collapse_metrics = {
        method: calculate_metrics(records, method) for method in METHODS
    }
    v1_metrics = _stored_metrics(v1_by_id, records)
    raw_metrics = _stored_metrics(raw_by_id, records)
    diversity = {
        "raw_v2": _diversity(raw_payload["questions"]),
        "parent_collapse": _diversity(records),
    }
    displacement = _displacement(records, raw_by_id, v1_by_id)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "config": {
            "questions": str(args.questions),
            "question_count": len(records),
            "collection": args.collection,
            "embedding_model": MODEL_NAME,
            "embedding_dimension": EMBEDDING_DIMENSION,
            "rrf_k": DEFAULT_RRF_K,
            "min_child_candidates": MIN_CHILD_CANDIDATES,
            "child_candidate_ratio": CHILD_CANDIDATE_RATIO,
            "parent_candidate_k": DEFAULT_PARENT_CANDIDATE_K,
            "ranking_signal": "best child only; no sibling or multi-hit bonus",
            "representative_rule": (
                "lowest original child rank across route best children; "
                "BM25 wins exact ties"
            ),
        },
        "corpus": {
            "child_count": len(subchunks),
            "logical_parent_count": len(parents),
            "child_count_by_ticker": corpus_sizes,
            "child_candidate_k_by_ticker": child_candidate_k,
        },
        "metrics": {
            "v1": v1_metrics,
            "raw_v2": raw_metrics,
            "parent_collapse": collapse_metrics,
        },
        "crowding": diversity,
        "displacement_cases": displacement,
        "questions": records,
    }
    Path(args.results).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    Path(args.report).write_text(build_report(payload), encoding="utf-8")
    return payload


def _stored_metrics(
    stored_by_id: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
) -> dict[str, dict[str, float | int]]:
    metric_records = [stored_by_id[record["id"]] for record in records]
    return {
        method: calculate_metrics(metric_records, method) for method in METHODS
    }


def _diversity(records: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    output = {}
    for method in METHODS:
        top5_counts = []
        top20_counts = []
        top5_duplicates = []
        top20_duplicates = []
        for record in records:
            results = record["retrieval"][method]["top20"]
            top5 = [logical_parent_id(item["chunk_id"]) for item in results[:5]]
            top20 = [logical_parent_id(item["chunk_id"]) for item in results[:20]]
            top5_counts.append(len(set(top5)))
            top20_counts.append(len(set(top20)))
            top5_duplicates.append(len(top5) - len(set(top5)))
            top20_duplicates.append(len(top20) - len(set(top20)))
        count = len(records)
        output[method] = {
            "mean_distinct_parents_top5": sum(top5_counts) / count,
            "mean_distinct_parents_top20": sum(top20_counts) / count,
            "duplicate_slots_top5": sum(top5_duplicates),
            "duplicate_slots_top20": sum(top20_duplicates),
            "mean_duplicate_slots_top5": sum(top5_duplicates) / count,
            "mean_duplicate_slots_top20": sum(top20_duplicates) / count,
        }
    return output


def _displacement(
    records: list[dict[str, Any]],
    raw_by_id: dict[str, dict[str, Any]],
    v1_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    collapsed_by_id = {record["id"]: record for record in records}
    return [
        {
            "case_id": case_id,
            "ticker": collapsed_by_id[case_id]["ticker"],
            "question": collapsed_by_id[case_id]["question"],
            "methods": {
                method: {
                    "v1": _rank_flags(
                        v1_by_id[case_id]["retrieval"][method]["expected_rank"]
                    ),
                    "raw_v2": _rank_flags(
                        raw_by_id[case_id]["retrieval"][method]["expected_rank"]
                    ),
                    "parent_collapse": _rank_flags(
                        collapsed_by_id[case_id]["retrieval"][method][
                            "expected_rank"
                        ]
                    ),
                }
                for method in METHODS
            },
        }
        for case_id in DISPLACEMENT_CASE_IDS
    ]


def _rank_flags(rank: int | None) -> dict[str, Any]:
    return {
        "rank": rank,
        "hit_at_5": rank is not None and rank <= 5,
        "hit_at_20": rank is not None and rank <= 20,
    }


def build_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Stage 2 Retrieval — Chunking v2 Parent Collapse",
        "",
        "## Configuration",
        "",
        f"- Dynamic child pool: `min(N, max({MIN_CHILD_CANDIDATES}, "
        f"ceil(N * {CHILD_CANDIDATE_RATIO:.2f})))`",
        f"- Parent candidate depth: {DEFAULT_PARENT_CANDIDATE_K}",
        f"- RRF k: {DEFAULT_RRF_K}; equal route weights",
        "- Parent signal: best child only; no multi-hit bonus",
        "",
        "## Metrics",
        "",
    ]
    for version in ("v1", "raw_v2", "parent_collapse"):
        lines.extend([f"### {version}", "", *metric_table(payload["metrics"][version]), ""])
    lines.extend(
        [
            "## Crowding",
            "",
            "| Version | Method | Mean distinct Top-5 | Mean distinct Top-20 | Duplicate slots Top-5 | Duplicate slots Top-20 |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for version, methods in payload["crowding"].items():
        for method, item in methods.items():
            lines.append(
                f"| {version} | {method} | "
                f"{item['mean_distinct_parents_top5']:.2f} | "
                f"{item['mean_distinct_parents_top20']:.2f} | "
                f"{item['duplicate_slots_top5']} | "
                f"{item['duplicate_slots_top20']} |"
            )
    lines.extend(
        [
            "",
            "## Displacement Cases",
            "",
            "| Case | Method | v1 rank / @5 / @20 | raw-v2 rank / @5 / @20 | collapse rank / @5 / @20 |",
            "|---|---|---|---|---|",
        ]
    )
    for case in payload["displacement_cases"]:
        for method, versions in case["methods"].items():
            lines.append(
                f"| {case['case_id']} | {method} | {_format_flags(versions['v1'])} | "
                f"{_format_flags(versions['raw_v2'])} | "
                f"{_format_flags(versions['parent_collapse'])} |"
            )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "The 70 frozen questions and logical-parent ground truth were unchanged. "
            "Chunk size, overlap, embedding, BM25, RRF k, and route weights were unchanged.",
            "",
        ]
    )
    return "\n".join(lines)


def _format_flags(item: dict[str, Any]) -> str:
    rank = item["rank"] if item["rank"] is not None else "miss"
    return f"{rank} / {'Y' if item['hit_at_5'] else 'N'} / {'Y' if item['hit_at_20'] else 'N'}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--vector-path", type=Path, default=DEFAULT_VECTOR_PATH)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS_PATH)
    parser.add_argument("--baseline", type=Path, default=BASELINE_RESULTS_PATH)
    parser.add_argument("--raw-v2", type=Path, default=RAW_V2_RESULTS_PATH)
    parser.add_argument("--collection", default=COLLECTION_NAME)
    parser.add_argument("--results", type=Path, default=RESULTS_PATH)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--preview-chars", type=int, default=240)
    return parser


def main() -> None:
    args = _parser().parse_args()
    payload = run_benchmark(args)
    print(f"Wrote {args.results} and {args.report}")
    for method in METHODS:
        metrics = payload["metrics"]["parent_collapse"][method]
        print(
            f"{method}: R@1={metrics['recall_at_1']:.3f} "
            f"R@5={metrics['recall_at_5']:.3f} "
            f"R@20={metrics['recall_at_20']:.3f} MRR={metrics['mrr']:.3f}"
        )


if __name__ == "__main__":
    main()
