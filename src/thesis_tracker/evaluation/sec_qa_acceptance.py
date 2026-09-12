"""Run the fixed 30-question Stage 2 SEC QA acceptance evaluation."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from thesis_tracker.embedding.dashscope import DashScopeEmbeddingClient
from thesis_tracker.evaluation.retrieval_stage2_chunkv2 import COLLECTION_NAME
from thesis_tracker.qa.sec_qa import (
    DeepSeekClaimGenerationProvider,
    answer_sec_question,
)
from thesis_tracker.retrieve.bm25 import BM25Retriever
from thesis_tracker.retrieve.claim_support import DeepSeekClaimSupportProvider
from thesis_tracker.retrieve.hybrid import (
    CHILD_CANDIDATE_RATIO,
    DEFAULT_PARENT_CANDIDATE_K,
    DEFAULT_RRF_K,
    MIN_CHILD_CANDIDATES,
    HybridRetriever,
    logical_parent_id,
)
from thesis_tracker.retrieve.vector import VectorRetriever

QUESTIONS_PATH = Path("eval/retrieval_stage2_questions.json")
RESULTS_PATH = Path("eval/sec_qa_stage2_acceptance_30_results.json")
REPORT_PATH = Path("eval/sec_qa_stage2_acceptance_30_report.md")
QA_TOP_K = 5
SELECTED_SOURCE_INDICES = (
    1,
    2,
    5,
    6,
    9,
    11,
    14,
    16,
    18,
    21,
    25,
    26,
    27,
    31,
    33,
    34,
    36,
    42,
    44,
    46,
    48,
    51,
    54,
    56,
    57,
    62,
    64,
    66,
    67,
    68,
)
INFRASTRUCTURE_REASONS = {
    "api_error",
    "invalid_model_output",
    "invalid_repair_output",
    "layer_b_error",
}


def load_frozen_cases(path: Path = QUESTIONS_PATH) -> list[dict[str, Any]]:
    """Load the fixed acceptance cases from the existing retrieval manifest."""
    questions = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for source_index in SELECTED_SOURCE_INDICES:
        question = questions[source_index - 1]
        cases.append(
            {
                "case_id": f"retrieval-stage2-{source_index:03d}",
                "source_index": source_index,
                "ticker": question["ticker"],
                "form_type": question["form_type"],
                "question_type": question["question_type"],
                "question": question["question"],
                "expected_chunk_id": question["expected_chunk_id"],
            }
        )
    return cases


def infrastructure_errors(result: dict[str, Any]) -> list[str]:
    """Return infrastructure/provider errors without treating them as QA failures."""
    errors: list[str] = []
    error = result.get("error")
    if isinstance(error, dict):
        errors.append(
            f"{error.get('stage', 'unknown')}: "
            f"{error.get('type', 'error')}: {error.get('message', '')}"
        )
    for rejected in result.get("rejected_claims", []):
        if rejected.get("reason") in INFRASTRUCTURE_REASONS:
            errors.append(
                f"claim: {rejected.get('reason')}: {rejected.get('detail', '')}"
            )
    return errors


def summarize_case(
    case: dict[str, Any],
    result: dict[str, Any],
    *,
    attempts: int,
    infrastructure_history: list[dict[str, Any]],
    incomplete: bool,
) -> dict[str, Any]:
    """Create a machine-readable case record without retrieved chunk text."""
    candidates = result.get("candidate_claims", [])
    verified = result.get("verified_claims", [])
    rejected = result.get("rejected_claims", [])
    initial_pass = sum(
        item.get("initial_layer_a_result") == "exact_match" for item in candidates
    )
    repair_attempts = sum(bool(item.get("repair_attempted")) for item in candidates)
    repair_successes = sum(
        item.get("repair_result") == "repaired"
        and item.get("final_layer_a_result") == "exact_match"
        for item in candidates
    )
    no_valid = sum(
        item.get("repair_result") == "no_valid_evidence" for item in candidates
    )
    final_pass = sum(
        item.get("final_layer_a_result") == "exact_match" for item in candidates
    )
    supported = sum(item.get("layer_b") == "supported" for item in verified)
    partial = sum(item.get("layer_b") == "partial" for item in rejected)
    unsupported = sum(item.get("layer_b") == "unsupported" for item in rejected)
    layer_b_errors = sum(item.get("reason") == "layer_b_error" for item in rejected)
    leakage = sum(
        bool(item.get("claim")) and item["claim"] in result.get("final_answer", "")
        for item in rejected
        if item.get("layer_b") in {"partial", "unsupported"}
    )
    repair_failed = max(0, repair_attempts - repair_successes - no_valid)
    retrieved = result.get("retrieved_chunks", [])
    retrieved_parent_ids = [
        str(item.get("logical_parent_id") or logical_parent_id(item["chunk_id"]))
        for item in retrieved
    ]
    expected_parent_id = logical_parent_id(str(case["expected_chunk_id"]))
    expected_rank = next(
        (
            rank
            for rank, parent_id in enumerate(retrieved_parent_ids, start=1)
            if parent_id == expected_parent_id
        ),
        None,
    )
    acceptance_status = (
        "incomplete"
        if incomplete
        else "accepted"
        if verified and result.get("status") == "success"
        else "failed"
    )
    # evidence_only is an output mode, not an acceptance. A retrieval miss that
    # falls back to raw evidence stays failed and keeps its failure category.
    output_mode = result.get("final_output_mode") or (
        "verified_claims" if verified else "insufficient_evidence"
    )
    failure_category = _failure_category(
        result=result,
        candidates=candidates,
        rejected=rejected,
        expected_parent_id=expected_parent_id,
        expected_rank=expected_rank,
        incomplete=incomplete,
    )

    return {
        **case,
        "execution_attempts": attempts,
        "completed": not incomplete,
        "acceptance_status": acceptance_status,
        "failure_category": failure_category,
        "infrastructure_errors": infrastructure_history,
        "retrieval": {
            "retrieved_chunk_ids": [item["chunk_id"] for item in retrieved],
            "retrieved_parent_ids": retrieved_parent_ids,
            "retrieved_count": len(retrieved),
            "expected_parent_id": expected_parent_id,
            "expected_parent_rank": expected_rank,
            "expected_parent_retrieved": expected_rank is not None,
            "evidence": [
                {
                    "rank": rank,
                    "chunk_id": item["chunk_id"],
                    "logical_parent_id": parent_id,
                    "representative_chunk_id": item.get(
                        "representative_chunk_id", item["chunk_id"]
                    ),
                }
                for rank, (item, parent_id) in enumerate(
                    zip(retrieved, retrieved_parent_ids, strict=True),
                    start=1,
                )
            ],
        },
        "metrics": {
            "candidate_claim_count": len(candidates),
            "initial_grounding_pass_count": initial_pass,
            "initial_grounding_fail_count": len(candidates) - initial_pass,
            "repair_attempt_count": repair_attempts,
            "repair_success_count": repair_successes,
            "no_valid_evidence_count": no_valid,
            "repair_failed_count": repair_failed,
            "final_grounding_pass_count": final_pass,
            "final_grounding_fail_count": len(candidates) - final_pass,
            "supported_count": supported,
            "partial_count": partial,
            "unsupported_count": unsupported,
            "layer_b_error_count": layer_b_errors,
            "verified_claim_count": len(verified),
            "rejected_claim_count": len(rejected),
            "final_answer_empty": not bool(result.get("final_answer")),
            "unsupported_leakage_count": leakage,
        },
        "claims": [*verified, *rejected],
        "candidate_claims": candidates,
        "verified_claims": verified,
        "rejected_claims": rejected,
        "final_answer": result.get("final_answer", ""),
        "pipeline_status": result.get("status"),
        "output_mode": output_mode,
        "evidence_only": result.get("evidence_only"),
        "pipeline_error": result.get("error"),
    }


def aggregate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate acceptance and coverage metrics over completed cases."""
    completed = [case for case in cases if case["completed"]]
    metric_names = (
        "candidate_claim_count",
        "initial_grounding_pass_count",
        "initial_grounding_fail_count",
        "repair_attempt_count",
        "repair_success_count",
        "no_valid_evidence_count",
        "repair_failed_count",
        "final_grounding_pass_count",
        "final_grounding_fail_count",
        "supported_count",
        "partial_count",
        "unsupported_count",
        "layer_b_error_count",
        "verified_claim_count",
        "rejected_claim_count",
        "unsupported_leakage_count",
    )
    totals = {
        name: sum(case["metrics"][name] for case in completed)
        for name in metric_names
    }
    candidates = totals["candidate_claim_count"]
    retries = totals["repair_attempt_count"]
    affected = [case["case_id"] for case in cases if case["infrastructure_errors"]]
    incomplete = [case["case_id"] for case in cases if not case["completed"]]
    non_empty = sum(not case["metrics"]["final_answer_empty"] for case in completed)
    accepted = sum(case["acceptance_status"] == "accepted" for case in cases)
    failed = sum(case["acceptance_status"] == "failed" for case in cases)
    cases_with_partial = sum(case["metrics"]["partial_count"] > 0 for case in cases)
    initial_rate = _rate(totals["initial_grounding_pass_count"], candidates)
    final_rate = _rate(totals["final_grounding_pass_count"], candidates)
    grounding_pass = final_rate is not None and final_rate >= 0.95
    leakage_pass = totals["unsupported_leakage_count"] == 0
    return {
        "questions": len(cases),
        "completed": len(completed),
        "incomplete": len(incomplete),
        "incomplete_case_ids": incomplete,
        "infrastructure_error_count": len(affected),
        "infrastructure_affected_case_ids": affected,
        "accepted_cases": accepted,
        "failed_cases": failed,
        "cases_with_partial_claims": cases_with_partial,
        "case_acceptance_rate": _rate(accepted, len(cases)),
        **totals,
        "initial_grounding_rate": initial_rate,
        "retry_success_rate": _rate(totals["repair_success_count"], retries),
        "final_grounding_rate": final_rate,
        "non_empty_final_answers": non_empty,
        "empty_final_answers": len(completed) - non_empty,
        "answer_coverage": _rate(non_empty, len(cases)),
        "citation_grounding_acceptance": grounding_pass,
        "unsupported_leakage_acceptance": leakage_pass,
        "overall_acceptance": grounding_pass and leakage_pass and not incomplete,
    }


def by_company(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Calculate the principal metrics for each company."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        grouped[case["ticker"]].append(case)
    return {ticker: aggregate_cases(items) for ticker, items in grouped.items()}


def difficult_cases(cases: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    """Return the five cases with the most observable pipeline difficulty."""
    ranked = sorted(
        cases,
        key=lambda case: (
            not case["completed"],
            case["metrics"]["final_answer_empty"],
            case["metrics"]["final_grounding_fail_count"],
            case["metrics"]["unsupported_count"] + case["metrics"]["partial_count"],
            case["metrics"]["repair_attempt_count"],
            case["metrics"]["rejected_claim_count"],
        ),
        reverse=True,
    )[:limit]
    return [
        {
            "case_id": case["case_id"],
            "ticker": case["ticker"],
            "question": case["question"],
            "metrics": case["metrics"],
            "reject_reasons": [
                item.get("reason") for item in case["rejected_claims"]
            ],
            "infrastructure_errors": case["infrastructure_errors"],
        }
        for case in ranked
    ]


def write_report(payload: dict[str, Any], path: Path = REPORT_PATH) -> None:
    """Write the human-readable acceptance report."""
    summary = payload["summary"]
    lines = [
        "# Stage 2 SEC QA Acceptance — 30 Questions",
        "",
        "## Summary",
        "",
        f"- Questions: {summary['questions']}",
        f"- Completed: {summary['completed']}",
        f"- Accepted cases: {summary['accepted_cases']}",
        f"- Failed cases: {summary['failed_cases']}",
        f"- Cases with Layer B partial claims: {summary['cases_with_partial_claims']}",
        f"- Case acceptance rate: {_percent(summary['case_acceptance_rate'])}",
        f"- Infrastructure failures: {summary['infrastructure_error_count']}",
        f"- Candidate claims: {summary['candidate_claim_count']}",
        f"- Initial grounding rate: {_percent(summary['initial_grounding_rate'])}",
        f"- Retry attempts: {summary['repair_attempt_count']}",
        f"- Retry successes: {summary['repair_success_count']}",
        f"- Retry success rate: {_percent(summary['retry_success_rate'])}",
        f"- Final grounding rate: {_percent(summary['final_grounding_rate'])}",
        f"- Layer B supported: {summary['supported_count']}",
        f"- Layer B partial: {summary['partial_count']}",
        f"- Layer B unsupported: {summary['unsupported_count']}",
        f"- Final verified claims: {summary['verified_claim_count']}",
        f"- Answer coverage: {_percent(summary['answer_coverage'])}",
        f"- Unsupported leakage: {summary['unsupported_leakage_count']}",
        "",
        "## Acceptance",
        "",
        "Citation grounding >= 95%: " + _pass_fail(
            summary["citation_grounding_acceptance"]
        ),
        "",
        "Unsupported leakage == 0: " + _pass_fail(
            summary["unsupported_leakage_acceptance"]
        ),
        "",
        "Overall Stage 2 QA acceptance: "
        + _pass_fail(summary["overall_acceptance"]),
        "",
        "## By Company",
        "",
    ]
    for ticker, metrics in payload["by_company"].items():
        lines.extend(
            [
                f"### {ticker}",
                "",
                f"- Questions/completed: {metrics['questions']}/{metrics['completed']}",
                f"- Candidate claims: {metrics['candidate_claim_count']}",
                f"- Initial grounding: {_percent(metrics['initial_grounding_rate'])}",
                f"- Retry attempts/successes: {metrics['repair_attempt_count']}/"
                f"{metrics['repair_success_count']}",
                f"- Final grounding: {_percent(metrics['final_grounding_rate'])}",
                f"- Supported/partial/unsupported: {metrics['supported_count']}/"
                f"{metrics['partial_count']}/{metrics['unsupported_count']}",
                f"- Verified claims: {metrics['verified_claim_count']}",
                f"- Answer coverage: {_percent(metrics['answer_coverage'])}",
                f"- Unsupported leakage: {metrics['unsupported_leakage_count']}",
                "",
            ]
        )
    lines.extend(
        [
            "## Case Outcomes",
            "",
            "| Case | Ticker | Status | Expected parent rank | Candidates | "
            "Grounded | Supported | Partial | Unsupported | Empty | Primary issue |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for case in payload["case_results"]:
        metrics = case["metrics"]
        expected_rank = case["retrieval"]["expected_parent_rank"]
        lines.append(
            f"| {case['case_id']} | {case['ticker']} | "
            f"{case['acceptance_status']} | "
            f"{expected_rank if expected_rank is not None else 'miss'} | "
            f"{metrics['candidate_claim_count']} | "
            f"{metrics['final_grounding_pass_count']} | "
            f"{metrics['supported_count']} | {metrics['partial_count']} | "
            f"{metrics['unsupported_count']} | "
            f"{str(metrics['final_answer_empty']).lower()} | "
            f"{case['failure_category'] or '-'} |"
        )
    lines.extend(
        [
            "## Failures / Difficult Cases",
            "",
            "- Final Layer A failures: "
            + _affected_case_summary(payload["case_results"], "final_grounding_fail_count"),
            "- Retry failures: "
            + _affected_case_summary(payload["case_results"], "repair_failed_count"),
            "- Layer B partial: "
            + _affected_case_summary(payload["case_results"], "partial_count"),
            "- Layer B unsupported: "
            + _affected_case_summary(payload["case_results"], "unsupported_count"),
            "- Empty final answers: "
            + _empty_answer_case_summary(payload["case_results"]),
            "- Infrastructure errors: "
            + _infrastructure_case_summary(payload["case_results"]),
            "",
            "Layer A items are citation-grounding failures; retry items are evidence "
            "repair failures; partial and unsupported items are Layer B entailment "
            "rejections. Empty answers contain no supported claim after filtering.",
            "",
            "### Five Most Difficult Cases",
            "",
        ]
    )
    for case in payload["difficult_cases"]:
        metrics = case["metrics"]
        issues = _case_issues(case)
        lines.extend(
            [
                f"#### {case['case_id']} — {case['ticker']}",
                "",
                case["question"],
                "",
                f"- Issues: {', '.join(issues) if issues else 'lowest relative difficulty'}",
                f"- Final grounding failures: {metrics['final_grounding_fail_count']}",
                f"- Retry attempts/failures: {metrics['repair_attempt_count']}/"
                f"{metrics['repair_failed_count']}",
                f"- Partial/unsupported: {metrics['partial_count']}/"
                f"{metrics['unsupported_count']}",
                f"- Empty final answer: {str(metrics['final_answer_empty']).lower()}",
                f"- Infrastructure errors: {len(case['infrastructure_errors'])}",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_acceptance(
    *,
    questions_path: Path = QUESTIONS_PATH,
    results_path: Path = RESULTS_PATH,
    report_path: Path = REPORT_PATH,
) -> dict[str, Any]:
    """Execute and checkpoint the frozen 30-question evaluation."""
    selected = load_frozen_cases(questions_path)
    payload: dict[str, Any] = {
        "title": "Stage 2 SEC QA Acceptance — 30 Questions",
        "started_at": datetime.now(UTC).isoformat(),
        "selection": {
            "source": str(questions_path),
            "selection_frozen_before_execution": True,
            "source_indices": list(SELECTED_SOURCE_INDICES),
            "case_ids": [case["case_id"] for case in selected],
            "cases": selected,
            "counts_by_ticker": dict(Counter(case["ticker"] for case in selected)),
            "counts_by_question_type": dict(
                Counter(case["question_type"] for case in selected)
            ),
        },
        "case_results": [],
        "run_status": "running",
        "config": {
            "qa_top_k": QA_TOP_K,
            "vector_collection": COLLECTION_NAME,
            "chunking_version": "chunker-v2-overlap",
            "parent_collapse": True,
            "min_child_candidates": MIN_CHILD_CANDIDATES,
            "child_candidate_ratio": CHILD_CANDIDATE_RATIO,
            "parent_candidate_k": DEFAULT_PARENT_CANDIDATE_K,
            "rrf_k": DEFAULT_RRF_K,
            "route_weights": "equal",
        },
    }
    _write_json(payload, results_path)

    embedding = DashScopeEmbeddingClient()
    retriever = HybridRetriever(
        BM25Retriever(),
        VectorRetriever(embedding, collection_name=COLLECTION_NAME),
    )
    claim_provider = DeepSeekClaimGenerationProvider()
    support_provider = DeepSeekClaimSupportProvider()

    for position, case in enumerate(selected, start=1):
        history: list[dict[str, Any]] = []
        result: dict[str, Any] = {}
        incomplete = False
        attempts = 0
        for attempt in (1, 2):
            attempts = attempt
            result = answer_sec_question(
                case["question"],
                case["ticker"],
                form_type=case["form_type"],
                top_k=QA_TOP_K,
                retriever=retriever,
                claim_provider=claim_provider,
                support_provider=support_provider,
            )
            errors = infrastructure_errors(result)
            if not errors:
                break
            history.append({"attempt": attempt, "errors": errors})
            if attempt == 2:
                incomplete = True

        record = summarize_case(
            case,
            result,
            attempts=attempts,
            infrastructure_history=history,
            incomplete=incomplete,
        )
        payload["case_results"].append(record)
        _write_json(payload, results_path)
        state = record["acceptance_status"].upper()
        print(
            f"[{position}/30] {case['ticker']} {case['case_id']} {state} "
            f"verified={record['metrics']['verified_claim_count']}"
        )

    payload["finished_at"] = datetime.now(UTC).isoformat()
    payload["run_status"] = "complete"
    payload["summary"] = aggregate_cases(payload["case_results"])
    payload["by_company"] = by_company(payload["case_results"])
    payload["difficult_cases"] = difficult_cases(payload["case_results"])
    _write_json(payload, results_path)
    write_report(payload, report_path)
    print(json.dumps(payload["summary"], indent=2))
    return payload


def _failure_category(
    *,
    result: dict[str, Any],
    candidates: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    expected_parent_id: str,
    expected_rank: int | None,
    incomplete: bool,
) -> str | None:
    """Classify the primary observable failure without changing QA grading."""
    if incomplete:
        return "infrastructure_error"
    if any(
        item.get("final_layer_a_result") not in {None, "exact_match"}
        for item in candidates
    ):
        return "citation_grounding_failure"
    if result.get("status") == "success" and not rejected:
        return None
    if expected_rank is None:
        return "retrieval_failure"
    if result.get("status") == "insufficient_evidence" and not candidates:
        return "answer_generation_failure"
    support_rejections = [
        item for item in rejected if item.get("layer_b") in {"partial", "unsupported"}
    ]
    if support_rejections:
        rejected_parents = {
            logical_parent_id(str(item["evidence_chunk_id"]))
            for item in support_rejections
            if item.get("evidence_chunk_id")
        }
        if rejected_parents and expected_parent_id not in rejected_parents:
            return "evidence_selection_failure"
        return "claim_support_failure"
    if result.get("status") != "success":
        return "answer_generation_failure"
    return None


def _case_issues(case: dict[str, Any]) -> list[str]:
    metrics = case["metrics"]
    issues = []
    if case["infrastructure_errors"]:
        issues.append("infrastructure error")
    if metrics["final_grounding_fail_count"]:
        issues.append("final Layer A failure")
    if metrics["repair_failed_count"]:
        issues.append("repair failure")
    if metrics["partial_count"]:
        issues.append("Layer B partial")
    if metrics["unsupported_count"]:
        issues.append("Layer B unsupported")
    if metrics["final_answer_empty"]:
        issues.append("empty final answer")
    return issues


def _affected_case_summary(cases: list[dict[str, Any]], metric: str) -> str:
    affected = [
        f"{case['case_id']} ({case['metrics'][metric]})"
        for case in cases
        if case["metrics"][metric]
    ]
    return ", ".join(affected) if affected else "none"


def _empty_answer_case_summary(cases: list[dict[str, Any]]) -> str:
    affected = [
        case["case_id"] for case in cases if case["metrics"]["final_answer_empty"]
    ]
    return ", ".join(affected) if affected else "none"


def _infrastructure_case_summary(cases: list[dict[str, Any]]) -> str:
    affected = [
        case["case_id"] for case in cases if case["infrastructure_errors"]
    ]
    return ", ".join(affected) if affected else "none"


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"


def _pass_fail(value: bool) -> str:
    return "PASS" if value else "FAIL"


def _write_json(payload: dict[str, Any], path: Path) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, default=QUESTIONS_PATH)
    parser.add_argument("--results", type=Path, default=RESULTS_PATH)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    return parser


def _main() -> None:
    args = _build_parser().parse_args()
    run_acceptance(
        questions_path=args.questions,
        results_path=args.results,
        report_path=args.report,
    )


if __name__ == "__main__":
    _main()
