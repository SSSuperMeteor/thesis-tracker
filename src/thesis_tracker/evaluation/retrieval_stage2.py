"""Run the Stage 2 retrieval benchmark against the current SEC corpus."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

import chromadb
from tqdm.auto import tqdm

from thesis_tracker.embedding.dashscope import (
    EMBEDDING_DIMENSION,
    MODEL_NAME,
    DashScopeEmbeddingClient,
    EmbeddingAPIError,
)
from thesis_tracker.evaluation.retrieval_metrics import (
    METHODS as SHARED_METHODS,
)
from thesis_tracker.evaluation.retrieval_metrics import (
    calculate_metrics,
    method_record,
    metric_table,
    winner,
)
from thesis_tracker.retrieve.bm25 import BM25Result, BM25Retriever
from thesis_tracker.retrieve.hybrid import (
    DEFAULT_RRF_K,
    HybridRetriever,
)
from thesis_tracker.retrieve.vector import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_COLLECTION_NAME,
    DEFAULT_DB_PATH,
    DEFAULT_VECTOR_PATH,
    VectorResult,
    VectorRetrievalError,
    VectorRetriever,
)

DEFAULT_QUESTIONS_PATH = Path("eval/retrieval_stage2_questions.json")
DEFAULT_RESULTS_PATH = Path("eval/retrieval_stage2_results.json")
DEFAULT_REPORT_PATH = Path("eval/retrieval_stage2_report.md")
RETRIEVAL_LIMIT = 20
EXPECTED_QUESTIONS_PER_TICKER = 10
IRRELEVANT_SECTION_NAMES = (
    "exhibits",
    "leases",
    "risk factors",
    "controls and procedures",
    "legal proceedings",
)
METHODS = SHARED_METHODS


@dataclass(frozen=True, slots=True)
class DocumentStats:
    ticker: str
    form_type: str
    accession: str
    ingestion_status: str
    valid_chunks: int
    invalid_chunks: int


class _SavedBM25:
    def __init__(self, results: Sequence[BM25Result]) -> None:
        self.results = list(results)

    def search(self, *_args: Any, top_k: int, **_kwargs: Any) -> list[BM25Result]:
        return self.results[:top_k]


class _SavedVector:
    def __init__(self, results: Sequence[VectorResult]) -> None:
        self.results = list(results)

    def search(self, *_args: Any, top_k: int, **_kwargs: Any) -> list[VectorResult]:
        return self.results[:top_k]


def _database_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def load_database_stats(db_path: Path) -> list[DocumentStats]:
    """Return valid and invalid saved chunk counts for every document."""
    with sqlite3.connect(_database_uri(db_path), uri=True) as connection:
        rows = connection.execute(
            """
            SELECT
                d.ticker,
                d.form_type,
                d.accession,
                d.ingestion_status,
                SUM(
                    CASE WHEN d.ingestion_status = 'success'
                              AND c.span_verified = 1
                         THEN 1 ELSE 0 END
                ) AS valid_chunks,
                SUM(
                    CASE WHEN c.chunk_id IS NOT NULL
                              AND NOT (
                                  d.ingestion_status = 'success'
                                  AND c.span_verified = 1
                              )
                         THEN 1 ELSE 0 END
                ) AS invalid_chunks
            FROM documents AS d
            LEFT JOIN chunks AS c ON c.accession = d.accession
            GROUP BY d.accession
            ORDER BY d.ticker, d.filing_date DESC, d.accession
            """
        ).fetchall()
    return [
        DocumentStats(
            ticker=str(row[0]),
            form_type=str(row[1]),
            accession=str(row[2]),
            ingestion_status=str(row[3]),
            valid_chunks=int(row[4] or 0),
            invalid_chunks=int(row[5] or 0),
        )
        for row in rows
    ]


def load_valid_chunk_catalog(db_path: Path) -> dict[str, dict[str, Any]]:
    """Load metadata needed to validate the hand-authored evidence manifest."""
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
    return {
        str(row[0]): {
            "ticker": str(row[1]),
            "form_type": str(row[2]),
            "accession": str(row[3]),
            "section": str(row[4]) if row[4] is not None else None,
            "title": str(row[5]),
            "text_chars": int(row[6]),
        }
        for row in rows
    }


def load_and_validate_questions(
    questions_path: Path,
    catalog: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Load fixed ground truth and reject any invalid cases."""
    valid, invalid = audit_questions(questions_path, catalog)
    if invalid:
        first = invalid[0]
        raise ValueError(
            f"question {first['source_index']} invalid: {first['reason']}"
        )
    return valid


def audit_questions(
    questions_path: Path,
    catalog: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return valid and explicitly marked invalid fixed ground-truth cases."""
    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    if not isinstance(questions, list):
        raise ValueError("question manifest must contain a JSON list")

    corpus_tickers = {item["ticker"] for item in catalog.values()}
    counts = Counter(str(question.get("ticker", "")) for question in questions)
    if set(counts) != corpus_tickers:
        missing = sorted(corpus_tickers - set(counts))
        extra = sorted(set(counts) - corpus_tickers)
        raise ValueError(
            "question manifest ticker mismatch: "
            f"missing={missing}, extra={extra}"
        )
    wrong_counts = {
        ticker: count
        for ticker, count in counts.items()
        if count != EXPECTED_QUESTIONS_PER_TICKER
    }
    if wrong_counts:
        raise ValueError(f"each ticker must have 10 questions: {wrong_counts}")

    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for number, question in enumerate(questions, start=1):
        required = {
            "ticker",
            "form_type",
            "question_type",
            "question",
            "expected_section",
        }
        missing_fields = sorted(required - set(question))
        reasons = []
        if missing_fields:
            reasons.append(f"missing fields: {missing_fields}")
        expected_ids = _expected_ids(question)
        if not expected_ids:
            reasons.append("no expected_chunk_id or acceptable_chunk_ids")
        for chunk_id in expected_ids:
            metadata = catalog.get(chunk_id)
            if metadata is None:
                reasons.append(f"expected invalid/missing chunk: {chunk_id}")
                continue
            if metadata["ticker"] != question.get("ticker"):
                reasons.append(f"ticker does not match {chunk_id}")
            if metadata["form_type"] != question.get("form_type"):
                reasons.append(f"form does not match {chunk_id}")
        primary_id = str(question.get("expected_chunk_id") or "")
        if not primary_id and expected_ids:
            primary_id = expected_ids[0]
        primary = catalog.get(primary_id)
        if primary and primary["title"] != question.get("expected_section"):
            reasons.append("expected_section does not match primary chunk title")
        case = {**question, "id": str(question.get("id") or f"stage2-{number:03d}")}
        if reasons:
            invalid.append(
                {
                    **case,
                    "source_index": number,
                    "status": "invalid_ground_truth",
                    "reason": "; ".join(dict.fromkeys(reasons)),
                }
            )
        else:
            valid.append(case)
    return valid, invalid


def _expected_ids(question: dict[str, Any]) -> list[str]:
    values = []
    if question.get("expected_chunk_id"):
        values.append(str(question["expected_chunk_id"]))
    values.extend(str(value) for value in question.get("acceptable_chunk_ids", []))
    return list(dict.fromkeys(values))


def _method_record(results: Sequence[Any], expected_ids: list[str]) -> dict[str, Any]:
    return method_record(results, expected_ids)


def _group_metrics(records: Sequence[dict[str, Any]], key: str) -> dict[str, dict[str, dict[str, float | int]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[str(record[key])].append(record)
    return {
        group: {method: calculate_metrics(items, method) for method in METHODS}
        for group, items in sorted(groups.items())
    }


def _question_type_analysis(
    metrics_by_type: dict[str, dict[str, dict[str, float | int]]],
) -> dict[str, Any]:
    details = {}
    type_winners = {method: [] for method in METHODS}
    strongest_types = {method: [] for method in METHODS}
    improved_mrr = []
    worse_than_best_single = []
    strongest_scores = {
        method: max(
            (
                (
                    float(metrics[method]["recall_at_5"]),
                    float(metrics[method]["mrr"]),
                )
                for metrics in metrics_by_type.values()
            ),
            default=(0.0, 0.0),
        )
        for method in METHODS
    }
    for question_type, metrics in metrics_by_type.items():
        winner = _winner(metrics)
        for method in METHODS:
            if method in winner.split(", "):
                type_winners[method].append(question_type)
            method_score = (
                float(metrics[method]["recall_at_5"]),
                float(metrics[method]["mrr"]),
            )
            if method_score == strongest_scores[method]:
                strongest_types[method].append(question_type)
        best_recall_baseline = max(
            float(metrics["bm25"]["recall_at_5"]),
            float(metrics["vector"]["recall_at_5"]),
        )
        best_mrr_baseline = max(
            float(metrics["bm25"]["mrr"]),
            float(metrics["vector"]["mrr"]),
        )
        recall_delta = (
            float(metrics["hybrid"]["recall_at_5"]) - best_recall_baseline
        )
        mrr_delta = float(metrics["hybrid"]["mrr"]) - best_mrr_baseline
        details[question_type] = {
            "winner": winner,
            "hybrid_recall_at_5_delta_vs_best_single_route": recall_delta,
            "hybrid_mrr_delta_vs_best_single_route": mrr_delta,
        }
        if mrr_delta > 0:
            improved_mrr.append(question_type)
        if recall_delta < 0 or mrr_delta < 0:
            worse_than_best_single.append(question_type)
    return {
        "strongest_types_by_method": strongest_types,
        "type_winners_by_method": type_winners,
        "hybrid_improved_mrr_types": improved_mrr,
        "hybrid_worse_than_best_single_types": worse_than_best_single,
        "details": details,
    }


def _failure_reason(record: dict[str, Any], expected_chars: int) -> tuple[str, str]:
    bm25_rank = record["retrieval"]["bm25"]["expected_rank"]
    vector_rank = record["retrieval"]["vector"]["expected_rank"]
    hybrid_rank = record["retrieval"]["hybrid"]["expected_rank"]
    question = str(record["question"]).casefold()
    expected_section = str(record["expected_section"])
    expected_accession = str(record["expected_chunk_ids"][0]).split("::", 1)[0]
    hybrid_top = record["retrieval"]["hybrid"]["top20"]
    first = hybrid_top[0] if hybrid_top else {}
    if (
        first.get("title") == expected_section
        and first.get("accession") != expected_accession
    ):
        return (
            "ground-truth ambiguity",
            "A matching section from another filing ranks first; the query does not identify the target filing period.",
        )
    if hybrid_rank is None:
        if expected_chars >= 50_000:
            return (
                "chunk too coarse",
                "The expected section is very large, so its relevant evidence is diluted and both candidate routes miss it.",
            )
        if bm25_rank is None and vector_rank is None:
            return (
                "query wording mismatch",
                "Neither lexical nor semantic retrieval put the expected evidence in Top 20.",
            )
        return (
            "RRF consensus error",
            "Only one route found the evidence, and RRF consensus pushed it outside the fused Top 20.",
        )
    if hybrid_rank > 5:
        if expected_chars < 1_000:
            return (
                "filing has weak evidence",
                "The expected section is a short cross-reference or no-change disclosure, so richer topical sections rank above it.",
            )
        if "inventory" in question and "inventory" not in expected_section.casefold():
            return (
                "metadata limitation",
                "The inventory table is embedded in a generically titled note, weakening both lexical and semantic ranking signals.",
            )
        if bm25_rank is not None and bm25_rank <= 5:
            return (
                "semantic false positive",
                "Vector neighbors lowered a strong lexical result during RRF fusion.",
            )
        if vector_rank is not None and vector_rank <= 5:
            return (
                "keyword noise",
                "Lexical candidates lowered a strong semantic result during RRF fusion.",
            )
        if record["question_type"] == "risk":
            return (
                "keyword noise",
                "Topical operating and commitment sections match supply, customer, and demand terms more strongly than the risk section.",
            )
        return (
            "RRF consensus error",
            "Medium-ranked candidates accumulated above the expected evidence during fusion.",
        )
    if bm25_rank is None:
        return (
            "query wording mismatch",
            "BM25 missed the wording while vector semantics recovered the evidence.",
        )
    if vector_rank is None:
        return (
            "semantic false positive",
            "Vector missed the evidence while BM25 recovered it.",
        )
    if hybrid_rank and hybrid_rank > 1 and expected_section.casefold() not in {
        "part i, item 1",
        "part i, item 2",
    }:
        return (
            "wrong section promoted",
            "Broad filing sections accumulated stronger cross-route support than the more specific expected note.",
        )
    return (
        "RRF consensus error",
        "The lexical and semantic ranks disagree substantially, making the fused rank sensitive to consensus.",
    )


def _select_failures(records: Sequence[dict[str, Any]], catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    def priority(record: dict[str, Any]) -> tuple[int, int, int]:
        hybrid_rank = record["retrieval"]["hybrid"]["expected_rank"]
        bm25_rank = record["retrieval"]["bm25"]["expected_rank"]
        vector_rank = record["retrieval"]["vector"]["expected_rank"]
        return (
            2 if hybrid_rank is None else 1 if hybrid_rank > 5 else 0,
            hybrid_rank or 21,
            abs((bm25_rank or 21) - (vector_rank or 21)),
        )

    failures = []
    for record in sorted(records, key=priority, reverse=True)[:10]:
        primary = str(record["expected_chunk_ids"][0])
        category, explanation = _failure_reason(
            record,
            int(catalog[primary]["text_chars"]),
        )
        failures.append(
            {
                "ticker": record["ticker"],
                "question": record["question"],
                "question_type": record["question_type"],
                "expected_chunk": primary,
                "expected_section": record["expected_section"],
                "bm25_rank": record["retrieval"]["bm25"]["expected_rank"],
                "vector_rank": record["retrieval"]["vector"]["expected_rank"],
                "hybrid_rank": record["retrieval"]["hybrid"]["expected_rank"],
                "hybrid_top5": record["retrieval"]["hybrid"]["top20"][:5],
                "reason_category": category,
                "reason": explanation,
            }
        )
    return failures


def _irrelevant_section_counts(records: Sequence[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts = {method: Counter() for method in METHODS}
    for record in records:
        expected_ids = set(record["expected_chunk_ids"])
        for method in METHODS:
            for result in record["retrieval"][method]["top20"][:5]:
                if result["chunk_id"] in expected_ids:
                    continue
                label = f"{result.get('section') or ''} {result.get('title') or ''}".casefold()
                for name in IRRELEVANT_SECTION_NAMES:
                    if name in label:
                        counts[method][name] += 1
    return {method: dict(sorted(values.items())) for method, values in counts.items()}


def _indexed_counts(vector_path: Path) -> dict[str, int]:
    collection = chromadb.PersistentClient(path=str(vector_path)).get_collection(
        DEFAULT_COLLECTION_NAME,
        embedding_function=None,
    )
    response = collection.get(include=["metadatas"])
    return dict(sorted(Counter(str(metadata["ticker"]) for metadata in response["metadatas"]).items()))


def _metric_table(metrics: dict[str, dict[str, float | int]]) -> list[str]:
    return metric_table(metrics)


def _winner(metrics: dict[str, dict[str, float | int]]) -> str:
    return winner(metrics)


def build_report(payload: dict[str, Any]) -> str:
    ground_truth = payload["ground_truth"]
    config = payload["config"]
    lines = [
        "# Stage 2 Retrieval Benchmark",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "## 1. Corpus overview",
        "",
        f"Tickers: {payload['database']['ticker_count']}",
        f"; valid chunks: {payload['database']['total_valid_chunks']}",
        f"; valid benchmark cases: {ground_truth['valid_cases']}.",
        "",
        "| Ticker | Form | Accession | Status | Valid | Invalid |",
        "|---|---|---|---|---:|---:|",
    ]
    for item in payload["database"]["documents"]:
        lines.append(
            f"| {item['ticker']} | {item['form_type']} | {item['accession']} | "
            f"{item['ingestion_status']} | {item['valid_chunks']} | {item['invalid_chunks']} |"
        )

    lines.extend(
        [
            "",
            "## 2. Embedding configuration",
            "",
            f"- Model: `{config['embedding_model']}`",
            f"- Dimension: {config['embedding_dimension']}",
            f"- Collection: `{config['collection']}`",
            f"- Retrieval depth: Top {config['retrieval_limit']}",
            f"- RRF k: {config['rrf_k']}",
            "",
            "## 3. Ground-truth overview",
            "",
            f"- Source cases: {ground_truth['total_cases']}",
            f"- Valid cases: {ground_truth['valid_cases']}",
            f"- Invalid cases excluded from denominator: {ground_truth['invalid_cases']}",
            f"- Cases per ticker: `{json.dumps(ground_truth['cases_by_ticker'], sort_keys=True)}`",
            f"- Cases per question type: `{json.dumps(ground_truth['cases_by_question_type'], sort_keys=True)}`",
            "",
            "### Invalid ground truth",
            "",
        ]
    )
    if ground_truth["invalid_ground_truth"]:
        for invalid in ground_truth["invalid_ground_truth"]:
            lines.append(
                f"- `{invalid['id']}` ({invalid.get('ticker', '-')}) — {invalid['reason']}"
            )
    else:
        lines.append("None.")

    lines.extend(
        [
            "",
            "## 4. Overall metrics",
            "",
            *_metric_table(payload["overall_metrics"]),
            "",
            "## 5. Ticker-level metrics",
            "",
        ]
    )
    for ticker, metrics in payload["per_company_metrics"].items():
        lines.extend([f"### {ticker}", "", *_metric_table(metrics), "", f"Best: {_winner(metrics)}", ""])

    lines.extend(["## 6. Question-type metrics", ""])
    for question_type, metrics in payload["question_type_metrics"].items():
        lines.extend([f"### {question_type}", "", *_metric_table(metrics), ""])
    type_analysis = payload["question_type_analysis"]
    type_summary = payload["question_type_summary"]
    lines.extend(
        [
            "### Type comparison",
            "",
            f"- Best Hybrid type(s): {type_summary['best_hybrid_types']}",
            f"- Worst Hybrid type(s): {type_summary['worst_hybrid_types']}",
            f"- Strongest types by method (Recall@5, then MRR): "
            f"`{json.dumps(type_analysis['strongest_types_by_method'], sort_keys=True)}`",
            f"- Types won by each method: "
            f"`{json.dumps(type_analysis['type_winners_by_method'], sort_keys=True)}`",
            f"- Hybrid MRR improved over the best single route: "
            f"{type_analysis['hybrid_improved_mrr_types'] or 'none'}",
            f"- Hybrid worsened on Recall@5 or MRR: "
            f"{type_analysis['hybrid_worse_than_best_single_types'] or 'none'}",
            "",
        ]
    )

    lines.extend(
        [
            "## 7. Ranking vs recall diagnosis",
            "",
        ]
    )
    assessment = payload["reranker_assessment"]
    lines.extend(
        [
            f"- Hybrid Top 20 hit but Top 5 miss: {assessment['top20_not_top5']} "
            f"({assessment['top20_not_top5_rate']:.1%})",
            f"- Hybrid Top 20 miss: {assessment['missing_top20']} "
            f"({assessment['missing_top20_rate']:.1%})",
            f"- Hybrid Top 10 hit but Top 5 miss: {assessment['top10_not_top5']}",
            f"- Hybrid Top 5 hit but Top 1 miss: {assessment['top5_not_top1']}",
            "",
            "## 8. Ten representative failure cases",
            "",
            "| Ticker | Type | Question | Expected section/chunk | BM25 | Vector | Hybrid | Category | Diagnosis | Hybrid Top 5 |",
            "|---|---|---|---|---:|---:|---:|---|---|---|",
        ]
    )
    for failure in payload["failures"]:
        ranks = [failure[f"{method}_rank"] or "not found" for method in METHODS]
        question = failure["question"].replace("|", "\\|")
        top5 = "; ".join(
            f"{item['rank']}. `{item['chunk_id']}` ({item.get('title') or item.get('section') or '-'})"
            for item in failure["hybrid_top5"]
        ).replace("|", "\\|")
        lines.append(
            f"| {failure['ticker']} | {failure['question_type']} | {question} | "
            f"{failure['expected_section']} / `{failure['expected_chunk']}` | {ranks[0]} | "
            f"{ranks[1]} | {ranks[2]} | {failure['reason_category']} | "
            f"{failure['reason']} | {top5} |"
        )

    lines.extend(
        [
            "",
            "## 9. Reranker recommendation",
            "",
            f"- Recommendation: {assessment['recommendation']}",
            f"- Primary issue: {assessment['primary_issue']}",
            f"- Evidence: {assessment['rationale']}",
            "",
            "### Potentially noisy sections in non-ground-truth Top 5 results",
            "",
            "These are occurrence counts for inspection, not automatic error labels.",
            "",
            "```json",
            json.dumps(payload["potentially_unrelated_sections"], indent=2, sort_keys=True),
            "```",
            "",
            "## 10. Next recommended action",
            "",
            assessment["next_action"],
            "",
        ]
    )
    return "\n".join(lines)


def _reranker_assessment(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    ranks = [record["retrieval"]["hybrid"]["expected_rank"] for record in records]
    count = len(ranks)
    top20_not_top5 = sum(rank is not None and rank > 5 for rank in ranks)
    missing_top20 = sum(rank is None for rank in ranks)
    top10_not_top5 = sum(rank is not None and 5 < rank <= 10 for rank in ranks)
    top5_not_top1 = sum(rank is not None and 1 < rank <= 5 for rank in ranks)
    metrics = calculate_metrics(records, "hybrid")
    ticker_metrics = _group_metrics(records, "ticker")
    minimum_ticker_recall_at_5 = min(
        (float(item["hybrid"]["recall_at_5"]) for item in ticker_metrics.values()),
        default=0.0,
    )
    top20_not_top5_rate = top20_not_top5 / count if count else 0.0
    missing_top20_rate = missing_top20 / count if count else 0.0
    if (
        float(metrics["recall_at_5"]) >= 0.85
        and float(metrics["mrr"]) >= 0.70
        and top20_not_top5_rate <= 0.08
        and minimum_ticker_recall_at_5 >= 0.70
    ):
        recommendation = "NO"
        primary_issue = "already good enough"
        rationale = (
            f"Hybrid Recall@5={metrics['recall_at_5']:.3f}, MRR={metrics['mrr']:.3f}, "
            f"and only {top20_not_top5}/{count} cases sit at ranks 6-20."
        )
        next_action = "Keep the current retrieval stack and investigate only the remaining outlier cases."
    elif (
        float(metrics["recall_at_20"]) - float(metrics["recall_at_5"]) >= 0.10
        and top20_not_top5 >= max(3, missing_top20)
    ):
        recommendation = "YES"
        primary_issue = "ranking"
        rationale = (
            f"Hybrid Recall@20={metrics['recall_at_20']:.3f} materially exceeds "
            f"Recall@5={metrics['recall_at_5']:.3f}, with {top20_not_top5} correct candidates at ranks 6-20."
        )
        next_action = "Evaluate a reranker on the frozen Top-20 candidates without changing first-stage retrieval."
    else:
        recommendation = "NOT YET"
        primary_issue = "recall" if missing_top20 >= top20_not_top5 else "mixed"
        rationale = (
            f"Hybrid has {missing_top20} Top-20 misses versus {top20_not_top5} cases found only at ranks 6-20."
        )
        next_action = (
            "Investigate candidate recall and ground-truth-aligned query/chunk failure modes before testing a reranker."
        )
    return {
        "top20_not_top5": top20_not_top5,
        "top20_not_top5_rate": top20_not_top5_rate,
        "missing_top20": missing_top20,
        "missing_top20_rate": missing_top20_rate,
        "top10_not_top5": top10_not_top5,
        "top5_not_top1": top5_not_top1,
        "minimum_ticker_recall_at_5": minimum_ticker_recall_at_5,
        "recommendation": recommendation,
        "primary_issue": primary_issue,
        "rationale": rationale,
        "next_action": next_action,
    }


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    db_path = Path(args.db)
    catalog = load_valid_chunk_catalog(db_path)
    document_stats = load_database_stats(db_path)

    # Ground truth is audited before constructing or calling a retriever. Invalid
    # cases remain unchanged, are reported, and are excluded from every metric.
    questions, invalid_questions = audit_questions(Path(args.questions), catalog)
    tickers = sorted({metadata["ticker"] for metadata in catalog.values()})
    print(
        f"Ground truth audited: valid={len(questions)}, "
        f"invalid={len(invalid_questions)}, tickers={len(tickers)}"
    )

    provider = DashScopeEmbeddingClient(timeout=args.timeout, max_retries=args.max_retries)
    vector = VectorRetriever(
        provider,
        db_path=db_path,
        vector_path=args.vector_path,
        batch_size=args.batch_size,
    )
    index_stats = vector.index()
    indexed_by_ticker = _indexed_counts(Path(args.vector_path))
    bm25 = BM25Retriever(db_path)

    records = []
    for question in tqdm(questions, desc="Stage 2 retrieval benchmark", unit="question"):
        query = str(question["question"])
        filters = {"ticker": str(question["ticker"]), "form_type": str(question["form_type"])}
        bm25_results = bm25.search(query, top_k=RETRIEVAL_LIMIT, **filters)
        vector_results = vector.search(query, top_k=RETRIEVAL_LIMIT, **filters)
        hybrid_results = HybridRetriever(
            _SavedBM25(bm25_results),
            _SavedVector(vector_results),
            rrf_k=DEFAULT_RRF_K,
            candidate_k=RETRIEVAL_LIMIT,
        ).search(query, top_k=RETRIEVAL_LIMIT, **filters)
        expected_ids = _expected_ids(question)
        records.append(
            {
                **question,
                "expected_chunk_ids": expected_ids,
                "retrieval": {
                    "bm25": _method_record(bm25_results, expected_ids),
                    "vector": _method_record(vector_results, expected_ids),
                    "hybrid": _method_record(hybrid_results, expected_ids),
                },
            }
        )

    overall = {method: calculate_metrics(records, method) for method in METHODS}
    per_company = _group_metrics(records, "ticker")
    per_type = _group_metrics(records, "question_type")
    hybrid_type_scores = {
        question_type: (
            float(metrics["hybrid"]["recall_at_5"]),
            float(metrics["hybrid"]["mrr"]),
        )
        for question_type, metrics in per_type.items()
    }
    best_type_score = max(hybrid_type_scores.values(), default=(0.0, 0.0))
    worst_type_score = min(hybrid_type_scores.values(), default=(0.0, 0.0))
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "config": {
            "embedding_model": MODEL_NAME,
            "embedding_dimension": EMBEDDING_DIMENSION,
            "collection": DEFAULT_COLLECTION_NAME,
            "retrieval_limit": RETRIEVAL_LIMIT,
            "rrf_k": DEFAULT_RRF_K,
            "ground_truth_source": str(args.questions),
        },
        "database": {
            "ticker_count": len(tickers),
            "tickers": tickers,
            "total_valid_chunks": len(catalog),
            "documents": [asdict(item) for item in document_stats],
        },
        "vector_index": {**asdict(index_stats), "indexed_chunks_by_ticker": indexed_by_ticker},
        "question_count": len(records),
        "ground_truth": {
            "total_cases": len(questions) + len(invalid_questions),
            "valid_cases": len(questions),
            "invalid_cases": len(invalid_questions),
            "invalid_ground_truth": invalid_questions,
            "cases_by_ticker": dict(sorted(Counter(record["ticker"] for record in records).items())),
            "cases_by_question_type": dict(
                sorted(Counter(record["question_type"] for record in records).items())
            ),
        },
        "overall_metrics": overall,
        "per_company_metrics": per_company,
        "question_type_metrics": per_type,
        "question_type_analysis": _question_type_analysis(per_type),
        "question_type_summary": {
            "best_hybrid_types": sorted(
                name for name, score in hybrid_type_scores.items() if score == best_type_score
            ),
            "worst_hybrid_types": sorted(
                name for name, score in hybrid_type_scores.items() if score == worst_type_score
            ),
        },
        "failures": _select_failures(records, catalog),
        "potentially_unrelated_sections": _irrelevant_section_counts(records),
        "reranker_assessment": _reranker_assessment(records),
        "questions": records,
    }
    results_path = Path(args.results)
    report_path = Path(args.report)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(build_report(payload), encoding="utf-8")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the real Stage 2 retrieval benchmark.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--vector-path", type=Path, default=DEFAULT_VECTOR_PATH)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS_PATH)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-retries", type=int, default=3)
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
        metrics = payload["overall_metrics"][method]
        print(
            f"{method}: R@5={metrics['recall_at_5']:.3f} "
            f"R@10={metrics['recall_at_10']:.3f} "
            f"R@20={metrics['recall_at_20']:.3f} MRR={metrics['mrr']:.3f}"
        )


if __name__ == "__main__":
    main()
