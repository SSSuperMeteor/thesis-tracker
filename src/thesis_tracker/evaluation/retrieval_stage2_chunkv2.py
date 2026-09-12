"""Run the frozen Stage 2 retrieval benchmark against chunking v2 data.

Chunking v2 ("chunker-v2-overlap") replaces every canonical section/note chunk
with one or more ``<logical_parent_id>::chunk_NNN`` subchunks. The fixed
ground-truth manifest still names the original logical parent, so a retrieved
subchunk counts as a hit when its logical parent is the expected chunk id.

Only evaluation-time ID resolution lives here; BM25, vector retrieval, RRF, the
candidate depth, and the chunking configuration are used exactly as configured.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Sequence

import chromadb
from tqdm.auto import tqdm

from thesis_tracker.embedding.dashscope import (
    EMBEDDING_DIMENSION,
    MODEL_NAME,
    DashScopeEmbeddingClient,
    EmbeddingAPIError,
)
from thesis_tracker.evaluation.retrieval_metrics import (
    METHODS,
    calculate_metrics,
    format_delta,
    format_rank,
    group_metrics,
    method_record,
    metric_table,
    rank_of,
)
from thesis_tracker.evaluation.retrieval_stage2 import (
    DEFAULT_QUESTIONS_PATH,
    DocumentStats,
    _irrelevant_section_counts,
    audit_questions,
    load_database_stats,
)
from thesis_tracker.retrieve.bm25 import BM25Retriever
from thesis_tracker.retrieve.hybrid import DEFAULT_RRF_K, HybridRetriever
from thesis_tracker.retrieve.vector import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_DB_PATH,
    DEFAULT_VECTOR_PATH,
    VectorRetrievalError,
    VectorRetriever,
)

RESULTS_PATH = Path("eval/retrieval_stage2_chunkv2_results.json")
REPORT_PATH = Path("eval/retrieval_stage2_chunkv2_report.md")
BASELINE_RESULTS_PATH = Path("eval/retrieval_stage2_results.json")
CHUNKING_VERSION = "chunker-v2-overlap"
TARGET_CHUNK_CHARS = 3000
OVERLAP_CHARS = 500
COLLECTION_NAME = "sec_chunks_qwen3_vl_embedding_1024_chunkv2"
RETRIEVAL_LIMIT = 20
TOP_K_LEVELS = (1, 3, 5, 10, 20)
METRIC_KEYS = (
    "recall_at_1",
    "recall_at_3",
    "recall_at_5",
    "recall_at_10",
    "recall_at_20",
    "mrr",
)
_SUBCHUNK_SUFFIX = re.compile(r"::chunk_\d{3}$")


def logical_parent_id(chunk_id: str) -> str:
    """Return the v2 logical parent of a subchunk id (identity for parents)."""
    return _SUBCHUNK_SUFFIX.sub("", chunk_id)


def load_chunkv2_catalog(
    db_path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Return the subchunk catalog and its logical-parent catalog."""
    with sqlite3.connect(_database_uri(db_path), uri=True) as connection:
        rows = connection.execute(
            """
            SELECT
                c.chunk_id, d.ticker, d.form_type, d.accession,
                c.section_key, c.title, LENGTH(c.text)
            FROM chunks AS c
            JOIN documents AS d ON d.accession = c.accession
            WHERE d.ingestion_status = 'success' AND c.span_verified = 1
            """
        ).fetchall()

    subchunks: dict[str, dict[str, Any]] = {}
    parents: dict[str, dict[str, Any]] = {}
    for row in rows:
        chunk_id = str(row[0])
        metadata = {
            "chunk_id": chunk_id,
            "ticker": str(row[1]),
            "form_type": str(row[2]),
            "accession": str(row[3]),
            "section": str(row[4]) if row[4] is not None else None,
            "title": str(row[5]),
            "subchunk_chars": int(row[6]),
        }
        subchunks[chunk_id] = metadata
        parent_id = logical_parent_id(chunk_id)
        entry = parents.setdefault(
            parent_id,
            {
                **metadata,
                "chunk_id": parent_id,
                "subchunk_count": 0,
                "subchunk_chars": 0,
            },
        )
        entry["subchunk_count"] += 1
        entry["subchunk_chars"] += metadata["subchunk_chars"]
    return subchunks, parents


def _database_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def _method_record(
    results: Sequence[Any],
    expected_ids: list[str],
    *,
    resolve: Callable[[str], str],
    preview_chars: int,
) -> dict[str, Any]:
    """Score one method, accepting any subchunk of an expected logical parent."""
    expected_parents = {resolve(chunk_id) for chunk_id in expected_ids}
    rank = rank_of(results, lambda chunk_id: resolve(chunk_id) in expected_parents)
    return method_record(
        results,
        expected_ids,
        rank=rank,
        preview_chars=preview_chars,
    )


def _parent_ranks(results: Sequence[Any], resolve: Callable[[str], str]) -> dict[str, int]:
    """Map each logical parent to the best rank it achieved in a result list."""
    best: dict[str, int] = {}
    for rank, result in enumerate(results, start=1):
        parent_id = resolve(result.chunk_id)
        best.setdefault(parent_id, rank)
    return best


def _crowding_for_method(
    record: dict[str, Any],
    method: str,
    *,
    resolve: Callable[[str], str],
) -> dict[str, Any]:
    """Quantify how many Top-5 slots one logical parent consumes for a case."""
    expected_parents = {resolve(chunk_id) for chunk_id in record["expected_chunk_ids"]}
    top5 = record["retrieval"][method]["top20"][:5]
    counts = Counter(resolve(item["chunk_id"]) for item in top5)
    distinct = len(counts)
    sibling_slots = 5 - distinct
    return {
        "unique_parents_top5": distinct,
        "sibling_slots": sibling_slots,
        "max_parent_subchunks": max(counts.values(), default=0),
        "crowded_parent": counts.most_common(1)[0][0] if counts and sibling_slots else None,
        "crowded_parent_is_ground_truth": any(
            parent in expected_parents for parent in counts if counts[parent] > 1
        ),
    }


def _crowding_analysis(
    records: Sequence[dict[str, Any]],
    resolve: Callable[[str], str],
) -> dict[str, Any]:
    """Summarize duplicate crowding by overlapping subchunks in Top-5 results."""
    per_method: dict[str, Any] = {}
    for method in METHODS:
        rows = [(_crowding_for_method(record, method, resolve=resolve), record) for record in records]
        count = len(rows) or 1
        crowded = [row for row in rows if row[0]["sibling_slots"] > 0]
        blocked = [
            (crowding, record)
            for crowding, record in rows
            if crowding["unique_parents_top5"] < 5
            and record["retrieval"][method]["expected_rank"] is not None
            and record["retrieval"][method]["expected_rank"] > 5
        ]
        per_method[method] = {
            "cases": len(rows),
            "cases_with_duplicate_parent_in_top5": len(crowded),
            "cases_with_duplicate_parent_in_top5_rate": len(crowded) / count,
            "top5_slots_consumed_by_duplicates": sum(row[0]["sibling_slots"] for row in crowded),
            "top5_duplicate_slot_rate": (
                sum(row[0]["sibling_slots"] for row in crowded) / (5 * count)
            ),
            "mean_unique_parents_top5": (
                sum(row[0]["unique_parents_top5"] for row in rows) / count
            ),
            "max_subchunks_from_one_parent_in_top5": max(
                (row[0]["max_parent_subchunks"] for row in rows),
                default=0,
            ),
            "cases_with_ground_truth_parent_crowding": sum(
                1 for row, _ in rows if row["crowded_parent_is_ground_truth"]
            ),
            "crowding_cases_where_ground_truth_missed_top5": len(blocked),
            "blocked_cases": [
                {
                    "id": record["id"],
                    "ticker": record["ticker"],
                    "question_type": record["question_type"],
                    "expected_rank": record["retrieval"][method]["expected_rank"],
                    "unique_parents_top5": crowding["unique_parents_top5"],
                    "crowded_parent": crowding["crowded_parent"],
                    "crowded_parent_is_ground_truth": crowding["crowded_parent_is_ground_truth"],
                    "distinct_parents_top5": sorted(
                        {
                            resolve(item["chunk_id"])
                            for item in record["retrieval"][method]["top20"][:5]
                        }
                    ),
                }
                for crowding, record in blocked
            ],
        }
    return {
        "definition": (
            "Top-5 duplicate crowding occurs when two or more retrieved subchunks "
            "share one logical parent, consuming slots that another section could use."
        ),
        "per_method": per_method,
    }


def _risk_cases(
    records: Sequence[dict[str, Any]],
    resolve: Callable[[str], str],
) -> dict[str, Any]:
    """List every risk case with per-method rank, deltas, and crowding."""
    cases = []
    for record in records:
        if record["question_type"] != "risk":
            continue
        entry: dict[str, Any] = {
            "id": record["id"],
            "ticker": record["ticker"],
            "expected_section": record["expected_section"],
            "expected_chunk_id": record["expected_chunk_ids"][0],
            "question": record["question"],
            "ranks": {
                method: record["retrieval"][method]["expected_rank"] for method in METHODS
            },
            "baseline_ranks": {
                method: record["baseline"]["retrieval"][method]["expected_rank"]
                for method in METHODS
            },
            "top5_parents": {
                method: [
                    resolve(item["chunk_id"])
                    for item in record["retrieval"][method]["top20"][:5]
                ]
                for method in METHODS
            },
            "crowding": {
                method: _crowding_for_method(record, method, resolve=resolve) for method in METHODS
            },
        }
        entry["rank_deltas"] = {
            method: _rank_delta(
                entry["baseline_ranks"][method],
                entry["ranks"][method],
            )
            for method in METHODS
        }
        cases.append(entry)
    return {
        "count": len(cases),
        "cases": cases,
        "metrics": {
            method: calculate_metrics(
                [record for record in records if record["question_type"] == "risk"],
                method,
            )
            for method in METHODS
        },
    }


def _rank_delta(before: int | None, after: int | None) -> int | None:
    """Positive means the expected parent moved closer to rank 1."""
    if before is None and after is None:
        return 0
    if before is None:
        return None
    if after is None:
        return None
    return before - after


def _baseline_deltas(
    metrics_before: dict[str, dict[str, float | int]],
    metrics_after: dict[str, dict[str, float | int]],
) -> dict[str, dict[str, float]]:
    return {
        method: {
            key: float(metrics_after[method][key]) - float(metrics_before[method][key])
            for key in METRIC_KEYS
        }
        for method in METHODS
    }


def _before_after(
    before: dict[str, dict[str, float | int]],
    after: dict[str, dict[str, float | int]],
) -> dict[str, Any]:
    return {
        "before": before,
        "after": after,
        "delta": _baseline_deltas(before, after),
    }


def _baseline_index(baseline_path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    return {
        str(record.get("id") or f"stage2-{index:03d}"): record
        for index, record in enumerate(payload["questions"], start=1)
    }


def _baseline_summary(record: dict[str, Any]) -> dict[str, Any]:
    """Keep baseline ranks and logical-parent Top-20 sets, not duplicate text.

    The frozen baseline payload already stores its own Top-20 detail; re-embedding
    it here would roughly quadruple the size of the v2 results file.
    """
    return {
        "id": record["id"],
        "retrieval": {
            method: {
                **{
                    key: record["retrieval"][method][key]
                    for key in record["retrieval"][method]
                    if key != "top20"
                },
                "logical_parents_top20": sorted(
                    {
                        logical_parent_id(str(item["chunk_id"]))
                        for item in record["retrieval"][method]["top20"][:20]
                    }
                ),
            }
            for method in METHODS
        },
    }


def _ranked_outcomes(
    records: Sequence[dict[str, Any]],
    methods: Sequence[str] = METHODS,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Classify each case per method as improved, regressed, or unchanged."""
    outcomes: dict[str, dict[str, list[dict[str, Any]]]] = {
        method: {"improved": [], "regressed": [], "unchanged": []} for method in methods
    }
    for record in records:
        for method in methods:
            before = record["baseline"]["retrieval"][method]["expected_rank"]
            after = record["retrieval"][method]["expected_rank"]
            if before == after:
                outcomes[method]["unchanged"].append(
                    {"id": record["id"], "baseline_rank": before, "rank": after}
                )
                continue
            improved = (before is None and after is not None) or (
                before is not None and after is not None and after < before
            )
            bucket = "improved" if improved else "regressed"
            outcomes[method][bucket].append(
                {
                    "id": record["id"],
                    "ticker": record["ticker"],
                    "question_type": record["question_type"],
                    "question": record["question"],
                    "expected_section": record["expected_section"],
                    "baseline_rank": before,
                    "rank": after,
                    "delta": _rank_delta(before, after),
                }
            )
    return outcomes


def _top20_parent_sets(
    record: dict[str, Any],
    method: str,
    *,
    resolve: Callable[[str], str],
) -> tuple[set[str], set[str]]:
    """Return the v1 and v2 Top-20 logical-parent sets for one case/method."""
    before = set(record["baseline"]["retrieval"][method]["logical_parents_top20"])
    after = {
        resolve(str(item["chunk_id"]))
        for item in record["retrieval"][method]["top20"][:20]
    }
    return before, after


def _fetch_rank_records(
    questions: Sequence[dict[str, Any]],
    bm25: BM25Retriever,
    vector: VectorRetriever,
    *,
    limit: int,
    preview_chars: int,
) -> list[dict[str, Any]]:
    resolve = logical_parent_id
    records = []
    for question in tqdm(questions, desc="Retrieval regression (chunking v2)", unit="question"):
        query = str(question["question"])
        filters = {
            "ticker": str(question["ticker"]),
            "form_type": str(question["form_type"]),
        }
        bm25_results = bm25.search(query, top_k=limit, **filters)
        vector_results = vector.search(query, top_k=limit, **filters)
        hybrid_results = HybridRetriever(
            _SavedSearch(bm25_results),
            _SavedSearch(vector_results),
            rrf_k=DEFAULT_RRF_K,
            candidate_k=limit,
        ).search(query, top_k=limit, **filters)
        expected_ids = [str(question["expected_chunk_id"])]
        expected_ids.extend(str(value) for value in question.get("acceptable_chunk_ids", []))
        expected_ids = list(dict.fromkeys(expected_ids))
        records.append(
            {
                **question,
                "expected_chunk_ids": expected_ids,
                "retrieval": {
                    "bm25": _method_record(
                        bm25_results,
                        expected_ids,
                        resolve=resolve,
                        preview_chars=preview_chars,
                    ),
                    "vector": _method_record(
                        vector_results,
                        expected_ids,
                        resolve=resolve,
                        preview_chars=preview_chars,
                    ),
                    "hybrid": _method_record(
                        hybrid_results,
                        expected_ids,
                        resolve=resolve,
                        preview_chars=preview_chars,
                    ),
                },
            }
        )
    return records


class _SavedSearch:
    """Replay an already-computed result list inside the hybrid fusion."""

    def __init__(self, results: Sequence[Any]) -> None:
        self.results = list(results)

    def search(self, *_args: Any, top_k: int, **_kwargs: Any) -> list[Any]:
        return self.results[:top_k]


def _indexed_counts(vector_path: Path, collection_name: str) -> dict[str, int]:
    collection = chromadb.PersistentClient(path=str(vector_path)).get_collection(
        collection_name,
        embedding_function=None,
    )
    response = collection.get(include=["metadatas"])
    return dict(
        sorted(Counter(str(metadata["ticker"]) for metadata in response["metadatas"]).items())
    )


def _company_and_type_sections(payload: dict[str, Any]) -> list[str]:
    lines = ["## 6. By company", ""]
    for ticker, item in payload["per_company"]["after"].items():
        before = payload["per_company"]["before"][ticker]
        lines.extend(
            [
                f"### {ticker}",
                "",
                f"- Cases: {item['hybrid']['questions']}",
                "",
                *metric_table(item),
                "",
                "| Method | Recall@5 before | Recall@5 after | Δ Recall@5 | MRR before | MRR after | Δ MRR |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for method in METHODS:
            lines.append(
                f"| {method.title()} | {before[method]['recall_at_5']:.3f} | "
                f"{item[method]['recall_at_5']:.3f} | "
                f"{format_delta(float(item[method]['recall_at_5']) - float(before[method]['recall_at_5']))} | "
                f"{before[method]['mrr']:.3f} | {item[method]['mrr']:.3f} | "
                f"{format_delta(float(item[method]['mrr']) - float(before[method]['mrr']))} |"
            )
        lines.append("")

    lines.extend(["## 7. By question type", ""])
    for question_type, item in payload["per_question_type"]["after"].items():
        before = payload["per_question_type"]["before"][question_type]
        lines.extend(
            [
                f"### {question_type}",
                "",
                f"- Cases: {item['hybrid']['questions']}",
                "",
                *metric_table(item),
                "",
                "| Method | Recall@5 before | Recall@5 after | Δ Recall@5 | MRR before | MRR after | Δ MRR |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for method in METHODS:
            lines.append(
                f"| {method.title()} | {before[method]['recall_at_5']:.3f} | "
                f"{item[method]['recall_at_5']:.3f} | "
                f"{format_delta(float(item[method]['recall_at_5']) - float(before[method]['recall_at_5']))} | "
                f"{before[method]['mrr']:.3f} | {item[method]['mrr']:.3f} | "
                f"{format_delta(float(item[method]['mrr']) - float(before[method]['mrr']))} |"
            )
        lines.append("")
    return lines


def _crowding_section(payload: dict[str, Any]) -> list[str]:
    crowding = payload["duplicate_crowding"]
    lines = [
        "## 9. Duplicate crowding analysis",
        "",
        crowding["definition"],
        "",
        "| Method | Cases with duplicate parent in Top-5 | Rate | Top-5 slots lost to siblings | Slot rate | Mean unique parents in Top-5 | Max subchunks of one parent | GT-parent crowding | GT missed@5 while crowded |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for method, item in crowding["per_method"].items():
        lines.append(
            f"| {method.title()} | {item['cases_with_duplicate_parent_in_top5']} | "
            f"{item['cases_with_duplicate_parent_in_top5_rate']:.1%} | "
            f"{item['top5_slots_consumed_by_duplicates']} | "
            f"{item['top5_duplicate_slot_rate']:.1%} | "
            f"{item['mean_unique_parents_top5']:.2f} | "
            f"{item['max_subchunks_from_one_parent_in_top5']} | "
            f"{item['cases_with_ground_truth_parent_crowding']} | "
            f"{item['crowding_cases_where_ground_truth_missed_top5']} |"
        )
    lines.extend(
        [
            "",
            "A case counts as *GT-parent crowding* when the ground-truth parent itself "
            "contributes the duplicate slots, meaning sibling overlap did not displace the "
            "correct parent. *GT missed@5 while crowded* counts cases where some parent "
            "consumed duplicate Top-5 slots and the expected parent still ranked 6 or worse.",
            "",
            "### Cases where the expected parent was outside Top-5 while Top-5 was crowded",
            "",
            "| Method | Case | Ticker | Type | Expected rank | Unique parents | Crowded parent | Crowded parent is GT |",
            "|---|---|---|---|---:|---:|---|---|",
        ]
    )
    blocked_rows = 0
    for method, item in crowding["per_method"].items():
        for case in item["blocked_cases"]:
            blocked_rows += 1
            lines.append(
                f"| {method.title()} | `{case['id']}` | {case['ticker']} | "
                f"{case['question_type']} | {case['expected_rank']} | "
                f"{case['unique_parents_top5']} | `{case['crowded_parent']}` | "
                f"{'yes' if case['crowded_parent_is_ground_truth'] else 'no'} |"
            )
    if not blocked_rows:
        lines.append("| - | - | - | - | - | - | - | - |")
    lines.append("")
    return lines


def _risk_section(payload: dict[str, Any]) -> list[str]:
    risk = payload["risk_cases"]
    lines = [
        "## 8. Risk cases",
        "",
        f"Risk cases: {risk['count']}",
        "",
        *metric_table(risk["metrics"]),
        "",
        "| Case | Ticker | Expected section | BM25 before→after | Vector before→after | Hybrid before→after | Hybrid crowding (unique parents / GT-parent) |",
        "|---|---|---|---|---|---|---|",
    ]
    for case in risk["cases"]:
        ranks = " | ".join(
            f"{format_rank(case['baseline_ranks'][method])}→{format_rank(case['ranks'][method])}"
            for method in METHODS
        )
        crowding = case["crowding"]["hybrid"]
        lines.append(
            f"| `{case['id']}` | {case['ticker']} | {case['expected_section']} | {ranks} | "
            f"{crowding['unique_parents_top5']} / "
            f"{'yes' if crowding['crowded_parent_is_ground_truth'] else 'no'} |"
        )
    lines.append("")
    return lines


def _change_section(
    title: str,
    outcomes: dict[str, dict[str, list[dict[str, Any]]]],
    records: Sequence[dict[str, Any]],
    resolve: Callable[[str], str],
    *,
    limit: int = 12,
) -> list[str]:
    records_by_id = {record["id"]: record for record in records}
    lines = [f"## {title}", ""]
    for method in METHODS:
        for bucket in ("improved", "regressed"):
            entries = outcomes[method][bucket]
            label = "improved" if bucket == "improved" else "regressed"
            lines.extend(
                [
                    f"### {method.title()} — {label} ({len(entries)})",
                    "",
                ]
            )
            if not entries:
                lines.extend(["None.", ""])
                continue
            lines.extend(
                [
                    "| Case | Ticker | Type | Baseline rank | v2 rank | Δ (positive = closer) | Expected section | Top-20 parents gained | Top-20 parents lost |",
                    "|---|---|---|---:|---:|---:|---|---|---|",
                ]
            )
            ordered = sorted(
                entries,
                key=lambda item: (
                    item["delta"] is None,
                    (item["delta"] or 0) if bucket == "regressed" else -(item["delta"] or 0),
                ),
            )
            for entry in ordered[:limit]:
                record = records_by_id[entry["id"]]
                before, after = _top20_parent_sets(record, method, resolve=resolve)
                gained = sorted(after - before)
                lost = sorted(before - after)
                lines.append(
                    f"| `{entry['id']}` | {entry['ticker']} | {entry['question_type']} | "
                    f"{format_rank(entry['baseline_rank'])} | {format_rank(entry['rank'])} | "
                    f"{'-' if entry['delta'] is None else entry['delta']} | "
                    f"{entry['expected_section']} | {_short_list(gained)} | {_short_list(lost)} |"
                )
            if len(ordered) > limit:
                lines.append(
                    f"| … | | | | | | | {len(ordered) - limit} more {label} cases | |"
                )
            lines.append("")
    return lines


def _short_list(values: Sequence[str], *, limit: int = 3) -> str:
    if not values:
        return "-"
    shown = ", ".join(f"`{logical_parent_id(value)}`" for value in values[:limit])
    if len(values) > limit:
        shown += f" (+{len(values) - limit})"
    return shown


def build_report(payload: dict[str, Any]) -> str:
    config = payload["config"]
    overall = payload["overall"]
    lines = [
        "# Stage 2 Retrieval Regression — Chunking v2 (overlap)",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "## 1. Configuration",
        "",
        f"- Chunking: `{config['chunking_version']}` "
        f"(target_chunk_chars={config['target_chunk_chars']}, overlap_chars={config['overlap_chars']})",
        f"- Embedding: `{config['embedding_model']}` / {config['embedding_dimension']} dims",
        f"- Vector collection: `{config['collection']}`",
        f"- Retrieval depth: Top {config['retrieval_limit']}; RRF k={config['rrf_k']}",
        f"- Ground truth: `{config['ground_truth_source']}` (unchanged, 70 cases)",
        "- Matching rule: a retrieved `parent::chunk_NNN` counts as a hit when its "
        "logical parent is the expected chunk id.",
        "",
        "## 2. Corpus and index",
        "",
        f"- Valid v2 subchunks: {payload['corpus']['valid_subchunks']}",
        f"- Logical parents: {payload['corpus']['logical_parents']}",
        f"- Subchunks per ticker: `{json.dumps(payload['corpus']['subchunks_by_ticker'], sort_keys=True)}`",
        f"- Logical parents per ticker: "
        f"`{json.dumps(payload['corpus']['parents_by_ticker'], sort_keys=True)}`",
        f"- Indexed vectors per ticker: "
        f"`{json.dumps(payload['corpus']['indexed_by_ticker'], sort_keys=True)}`",
        f"- Index stats: `{json.dumps(payload['corpus']['index_stats'], sort_keys=True)}`",
        "",
        "## 3. Overall metrics",
        "",
        *metric_table(overall["after"]),
        "",
        "## 4. Before / after",
        "",
        "Baseline: v1 logical sections (`eval/retrieval_stage2_results.json`). "
        "After: chunking v2 with the same queries, filters, retrievers, and depth.",
        "",
        "| Method | Metric | Before | After | Δ |",
        "|---|---|---:|---:|---:|",
    ]
    for method in METHODS:
        for key in METRIC_KEYS:
            lines.append(
                f"| {method.title()} | {key} | {float(overall['before'][method][key]):.3f} | "
                f"{float(overall['after'][method][key]):.3f} | "
                f"{format_delta(float(overall['delta'][method][key]))} |"
            )

    lines.extend(["", "### Rank movement per method", ""])
    for method in METHODS:
        outcomes = payload["outcomes"][method]
        lines.append(
            f"- {method.title()}: improved {len(outcomes['improved'])}, "
            f"regressed {len(outcomes['regressed'])}, "
            f"unchanged {len(outcomes['unchanged'])}"
        )

    lines.extend(
        [
            "",
            "### Recall@20 cases lost versus the v1 baseline",
            "",
            "| Method | Case | Ticker | Type | Expected section | v1 rank | v2 rank | Siblings in v2 Top-20 | Duplicate parents in v2 Top-20 |",
            "|---|---|---|---|---|---:|---:|---:|---:|",
        ]
    )
    lost_any = False
    for method in METHODS:
        for case in payload["overlap_focus"]["top20_misses"][method]:
            lost_any = True
            lines.append(
                f"| {method.title()} | `{case['id']}` | {case['ticker']} | {case['question_type']} | "
                f"{case['expected_section']} | {format_rank(case['baseline_rank'])} | "
                f"{format_rank(case['rank'])} | {case['sibling_subchunks_top20']} | "
                f"{case['duplicate_slots_top20']} |"
            )
    if not lost_any:
        lines.append("| - | - | - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "### Candidate-pool diversity inside Top-20",
            "",
            "In v1 every logical parent was one chunk, so Top-20 always carried 20 distinct "
            "sections. Overlapping subchunks let siblings repeat, shrinking the pool of "
            "distinct sections that a downstream reranker or reader could use.",
            "",
            "| Method | Mean distinct parents in Top-20 | Min | Max | Mean duplicate slots | Mean slots lost vs v1 depth |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for method, item in payload["overlap_focus"]["top20_diversity"].items():
        lines.append(
            f"| {method.title()} | {item['mean_distinct_parents']:.2f} | "
            f"{item['min_distinct_parents']} | {item['max_distinct_parents']} | "
            f"{item['mean_duplicate_slots']:.2f} | "
            f"{item['mean_slots_lost_vs_v1_depth']:.2f} |"
        )

    lines.extend(
        [
            "",
            "### Highest-crowding Hybrid cases",
            "",
            "| Case | Ticker | Type | Expected section | Distinct parents in Top-20 | Duplicate slots | Largest single-parent share | Expected rank |",
            "|---|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for case in payload["overlap_focus"]["worst_crowding_cases"]:
        lines.append(
            f"| `{case['id']}` | {case['ticker']} | {case['question_type']} | "
            f"{case['expected_section']} | {case['distinct_parents_top20']} | "
            f"{case['duplicate_slots_top20']} | {case['largest_parent_share']} | "
            f"{format_rank(case['expected_rank'])} |"
        )

    lines.extend(["", "## 5. Focus checks on overlap side effects", ""])
    focus = payload["overlap_focus"]
    lines.extend(
        [
            f"1. Hybrid Recall@5: {focus['hybrid_recall_at_5']['before']:.3f} → "
            f"{focus['hybrid_recall_at_5']['after']:.3f} "
            f"({format_delta(focus['hybrid_recall_at_5']['delta'])}) — "
            f"{'decreased' if focus['hybrid_recall_at_5']['delta'] < 0 else 'not decreased'}",
            f"2. Recall@20 complete: BM25 {focus['recall_at_20']['bm25']['before']:.3f} → "
            f"{focus['recall_at_20']['bm25']['after']:.3f}; Vector "
            f"{focus['recall_at_20']['vector']['before']:.3f} → "
            f"{focus['recall_at_20']['vector']['after']:.3f}; Hybrid "
            f"{focus['recall_at_20']['hybrid']['before']:.3f} → "
            f"{focus['recall_at_20']['hybrid']['after']:.3f}",
            f"3. Risk cases (n={payload['risk_cases']['count']}) — see section 8 for detail",
            f"4. Duplicate Top-K: Top-5 slots consumed by sibling subchunks "
            f"{focus['duplicate_topk']['hybrid']['top5_slots_consumed_by_duplicates']} "
            f"(Hybrid), rate {focus['duplicate_topk']['hybrid']['top5_duplicate_slot_rate']:.1%}",
            f"5. Duplicate crowding: cases with any duplicate parent in Top-5 — "
            f"BM25 {focus['duplicate_topk']['bm25']['cases_with_duplicate_parent_in_top5']}, "
            f"Vector {focus['duplicate_topk']['vector']['cases_with_duplicate_parent_in_top5']}, "
            f"Hybrid {focus['duplicate_topk']['hybrid']['cases_with_duplicate_parent_in_top5']}",
            "",
        ]
    )

    lines.extend(_company_and_type_sections(payload))
    lines.extend(_risk_section(payload))
    lines.extend(_crowding_section(payload))
    lines.extend(
        _change_section(
            "10. Notable regressions",
            payload["outcomes"],
            payload["questions"],
            logical_parent_id,
        )
    )
    lines.extend(
        _change_section(
            "11. Notable improvements",
            payload["outcomes"],
            payload["questions"],
            logical_parent_id,
        )
    )
    lines.extend(
        [
            "## 12. Unchanged-baseline noise check",
            "",
            "Occurrence counts of typically irrelevant sections inside non-ground-truth "
            "Top-5 results (inspection only).",
            "",
            "```json",
            json.dumps(payload["potentially_unrelated_sections"], indent=2, sort_keys=True),
            "```",
            "",
            "## 13. Conclusion",
            "",
            f"- Verdict: **{payload['verdict']['label']}**",
            f"- Rationale: {payload['verdict']['rationale']}",
            f"- Duplicate crowding detected: "
            f"{'yes' if payload['verdict']['duplicate_crowding_detected'] else 'no'} "
            "(analysis only — no retrieval or chunking parameters were changed).",
            "",
        ]
    )
    return "\n".join(lines)


def _verdict(overall: dict[str, Any], crowding: dict[str, Any]) -> dict[str, Any]:
    deltas = overall["delta"]
    hybrid_r5 = float(deltas["hybrid"]["recall_at_5"])
    hybrid_mrr = float(deltas["hybrid"]["mrr"])
    recall20_intact = all(
        float(overall["after"][method]["recall_at_20"]) >= 0.99 for method in METHODS
    )
    crowding_detected = any(
        item["cases_with_duplicate_parent_in_top5"] > 0
        for item in crowding["per_method"].values()
    )
    if hybrid_r5 > 0.01 or (hybrid_r5 >= -0.005 and hybrid_mrr > 0.005):
        label = "improved"
    elif hybrid_r5 < -0.02 or hybrid_mrr < -0.02:
        label = "regressed"
    else:
        label = "neutral"
    rationale = (
        f"Hybrid Recall@5 Δ={hybrid_r5:+.3f}, Hybrid MRR Δ={hybrid_mrr:+.3f}; "
        f"Recall@20 {'held' if recall20_intact else 'did not hold'} at "
        f"{float(overall['after']['hybrid']['recall_at_20']):.3f} for Hybrid."
    )
    return {
        "label": label,
        "rationale": rationale,
        "recall_at_20_intact": recall20_intact,
        "duplicate_crowding_detected": crowding_detected,
    }


def _top20_misses(records: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Return cases whose expected parent left the Top 20 after the v2 switch."""
    misses: dict[str, list[dict[str, Any]]] = {method: [] for method in METHODS}
    for record in records:
        for method in METHODS:
            before = record["baseline"]["retrieval"][method]["expected_rank"]
            after = record["retrieval"][method]["expected_rank"]
            if before is None or (after is not None and after <= 20):
                continue
            parents = [
                logical_parent_id(str(item["chunk_id"]))
                for item in record["retrieval"][method]["top20"][:20]
            ]
            counts = Counter(parents)
            misses[method].append(
                {
                    "id": record["id"],
                    "ticker": record["ticker"],
                    "question_type": record["question_type"],
                    "expected_section": record["expected_section"],
                    "baseline_rank": before,
                    "rank": after,
                    "sibling_subchunks_top20": sum(count - 1 for count in counts.values()),
                    "duplicate_slots_top20": sum(
                        1 for count in counts.values() if count > 1
                    ),
                }
            )
    return misses


def _top20_diversity(records: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Count distinct logical parents inside Top-20 per method.

    In v1 each logical parent was one chunk, so a Top-20 always carried 20
    distinct sections. Subchunk overlap means a v2 Top-20 can carry far fewer,
    which shrinks the reranker candidate pool even at unchanged depth.
    """
    result: dict[str, dict[str, Any]] = {}
    for method in METHODS:
        distinct = []
        duplicate_slots = []
        for record in records:
            parents = [
                logical_parent_id(str(item["chunk_id"]))
                for item in record["retrieval"][method]["top20"][:20]
            ]
            distinct.append(len(set(parents)))
            duplicate_slots.append(len(parents) - len(set(parents)))
        count = len(distinct) or 1
        result[method] = {
            "mean_distinct_parents": sum(distinct) / count,
            "min_distinct_parents": min(distinct, default=0),
            "max_distinct_parents": max(distinct, default=0),
            "mean_duplicate_slots": sum(duplicate_slots) / count,
            "total_duplicate_slots": sum(duplicate_slots),
            "mean_slots_lost_vs_v1_depth": 20 - (sum(distinct) / count),
        }
    return result


def _worst_crowding_cases(
    records: Sequence[dict[str, Any]],
    *,
    method: str = "hybrid",
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Rank cases by how few distinct parents survived inside Top-20."""
    ranked = []
    for record in records:
        parents = [
            logical_parent_id(str(item["chunk_id"]))
            for item in record["retrieval"][method]["top20"][:20]
        ]
        counts = Counter(parents)
        ranked.append(
            {
                "id": record["id"],
                "ticker": record["ticker"],
                "question_type": record["question_type"],
                "expected_section": record["expected_section"],
                "distinct_parents_top20": len(set(parents)),
                "duplicate_slots_top20": len(parents) - len(set(parents)),
                "largest_parent_share": max(counts.values(), default=0),
                "expected_rank": record["retrieval"][method]["expected_rank"],
            }
        )
    return sorted(
        ranked,
        key=lambda item: (
            item["largest_parent_share"],
            item["duplicate_slots_top20"],
        ),
        reverse=True,
    )[:limit]


def _overlap_focus(
    overall: dict[str, Any],
    crowding: dict[str, Any],
    records: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "hybrid_recall_at_5": {
            "before": float(overall["before"]["hybrid"]["recall_at_5"]),
            "after": float(overall["after"]["hybrid"]["recall_at_5"]),
            "delta": float(overall["delta"]["hybrid"]["recall_at_5"]),
        },
        "recall_at_20": {
            method: {
                "before": float(overall["before"][method]["recall_at_20"]),
                "after": float(overall["after"][method]["recall_at_20"]),
            }
            for method in METHODS
        },
        "top20_misses": _top20_misses(records),
        "top20_diversity": _top20_diversity(records),
        "worst_crowding_cases": _worst_crowding_cases(records),
        "duplicate_topk": crowding["per_method"],
    }


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    db_path = Path(args.db)
    subchunks, parents = load_chunkv2_catalog(db_path)
    document_stats: list[DocumentStats] = load_database_stats(db_path)
    questions, invalid_questions = audit_questions(Path(args.questions), parents)
    if invalid_questions:
        first = invalid_questions[0]
        raise ValueError(f"question {first['source_index']} invalid: {first['reason']}")
    print(
        f"Ground truth audited: valid={len(questions)}, invalid={len(invalid_questions)}, "
        f"subchunks={len(subchunks)}, logical_parents={len(parents)}"
    )

    provider = DashScopeEmbeddingClient(timeout=args.timeout, max_retries=args.max_retries)
    vector = VectorRetriever(
        provider,
        db_path=db_path,
        vector_path=args.vector_path,
        collection_name=args.collection,
        batch_size=args.batch_size,
    )
    index_stats = vector.index()
    bm25 = BM25Retriever(db_path)

    records = _fetch_rank_records(
        questions,
        bm25,
        vector,
        limit=RETRIEVAL_LIMIT,
        preview_chars=args.preview_chars,
    )
    baseline_by_id = _baseline_index(Path(args.baseline))
    for position, record in enumerate(records, start=1):
        baseline = baseline_by_id.get(str(record["id"])) or baseline_by_id.get(
            f"stage2-{position:03d}"
        )
        if baseline is None:
            raise ValueError(f"no baseline record for case {record['id']}")
        record["baseline"] = _baseline_summary(baseline)

    overall_after = {method: calculate_metrics(records, method) for method in METHODS}
    # The frozen baseline payload stores the same 70 cases; recompute its metrics
    # from stored hit flags so both sides use identical aggregation math.
    baseline_records = [
        {
            "retrieval": {
                method: baseline_by_id[record["baseline"]["id"]]["retrieval"][method]
                for method in METHODS
            }
        }
        for record in records
    ]
    overall_before = {
        method: calculate_metrics(baseline_records, method) for method in METHODS
    }
    overall = _before_after(overall_before, overall_after)
    crowding = _crowding_analysis(records, logical_parent_id)
    outcomes = _ranked_outcomes(records)
    per_company_before = group_metrics(
        [
            {**record, "retrieval": record["baseline"]["retrieval"]}
            for record in records
        ],
        "ticker",
    )
    per_company_after = group_metrics(records, "ticker")
    per_type_before = group_metrics(
        [
            {**record, "retrieval": record["baseline"]["retrieval"]}
            for record in records
        ],
        "question_type",
    )
    per_type_after = group_metrics(records, "question_type")
    verdict = _verdict(overall, crowding)

    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "config": {
            "chunking_version": CHUNKING_VERSION,
            "target_chunk_chars": TARGET_CHUNK_CHARS,
            "overlap_chars": OVERLAP_CHARS,
            "embedding_model": MODEL_NAME,
            "embedding_dimension": EMBEDDING_DIMENSION,
            "collection": args.collection,
            "retrieval_limit": RETRIEVAL_LIMIT,
            "rrf_k": DEFAULT_RRF_K,
            "candidate_k": RETRIEVAL_LIMIT,
            "ground_truth_source": str(args.questions),
            "baseline_source": str(args.baseline),
            "matching_rule": "retrieved subchunk matches when logical_parent_id(subchunk) is expected",
        },
        "corpus": {
            "valid_subchunks": len(subchunks),
            "logical_parents": len(parents),
            "subchunks_by_ticker": dict(
                sorted(Counter(item["ticker"] for item in subchunks.values()).items())
            ),
            "parents_by_ticker": dict(
                sorted(Counter(item["ticker"] for item in parents.values()).items())
            ),
            "documents": [asdict(item) for item in document_stats],
            "index_stats": asdict(index_stats),
            "indexed_by_ticker": _indexed_counts(Path(args.vector_path), args.collection),
            "subchunk_count_distribution": dict(
                sorted(Counter(item["subchunk_count"] for item in parents.values()).items())
            ),
        },
        "question_count": len(records),
        "ground_truth": {
            "total_cases": len(questions) + len(invalid_questions),
            "valid_cases": len(questions),
            "invalid_cases": len(invalid_questions),
            "invalid_ground_truth": invalid_questions,
            "cases_by_ticker": dict(
                sorted(Counter(record["ticker"] for record in records).items())
            ),
            "cases_by_question_type": dict(
                sorted(Counter(record["question_type"] for record in records).items())
            ),
        },
        "overall": overall,
        "overlap_focus": _overlap_focus(overall, crowding, records),
        "verdict": verdict,
        "per_company": {"before": per_company_before, "after": per_company_after},
        "per_question_type": {"before": per_type_before, "after": per_type_after},
        "risk_cases": _risk_cases(records, logical_parent_id),
        "duplicate_crowding": crowding,
        "outcomes": outcomes,
        "potentially_unrelated_sections": _irrelevant_section_counts(records),
        "questions": records,
    }
    results_path = Path(args.results)
    report_path = Path(args.report)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(
        json.dumps(_rounded(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(build_report(payload), encoding="utf-8")
    return payload


def _rounded(value: Any, *, digits: int = 6) -> Any:
    """Trim float noise so the stored JSON stays compact and diff-friendly."""
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, dict):
        return {key: _rounded(item, digits=digits) for key, item in value.items()}
    if isinstance(value, list):
        return [_rounded(item, digits=digits) for item in value]
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Stage 2 retrieval benchmark against chunking v2 data.",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--vector-path", type=Path, default=DEFAULT_VECTOR_PATH)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS_PATH)
    parser.add_argument("--baseline", type=Path, default=BASELINE_RESULTS_PATH)
    parser.add_argument("--results", type=Path, default=RESULTS_PATH)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--collection", default=COLLECTION_NAME)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--preview-chars", type=int, default=240)
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    try:
        payload = run_benchmark(args)
    except (
        EmbeddingAPIError,
        VectorRetrievalError,
        FileNotFoundError,
        ValueError,
    ) as error:
        parser.exit(status=1, message=f"error: {error}\n")
    print(f"Wrote {args.results} and {args.report}")
    for method in METHODS:
        metrics = payload["overall"]["after"][method]
        print(
            f"{method}: R@1={metrics['recall_at_1']:.3f} "
            f"R@5={metrics['recall_at_5']:.3f} "
            f"R@10={metrics['recall_at_10']:.3f} "
            f"R@20={metrics['recall_at_20']:.3f} MRR={metrics['mrr']:.3f}"
        )


if __name__ == "__main__":
    main()
