"""Run a targeted SEC QA subset to verify the evidence-only fallback.

This runner reuses the frozen acceptance cases, selection, and metric helpers.
It adds a case filter plus the evidence-only observability fields, and it never
changes retrieval, Layer A, Layer B, thresholds, or gold data.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from tqdm.auto import tqdm

from thesis_tracker.embedding.dashscope import DashScopeEmbeddingClient
from thesis_tracker.evaluation.retrieval_stage2_chunkv2 import COLLECTION_NAME
from thesis_tracker.evaluation.sec_qa_acceptance import (
    QA_TOP_K,
    aggregate_cases,
    infrastructure_errors,
    load_frozen_cases,
    summarize_case,
)
from thesis_tracker.qa.sec_qa import (
    DeepSeekClaimGenerationProvider,
    answer_sec_question,
)
from thesis_tracker.retrieve.bm25 import BM25Retriever
from thesis_tracker.retrieve.claim_support import DeepSeekClaimSupportProvider
from thesis_tracker.retrieve.hybrid import HybridRetriever
from thesis_tracker.retrieve.vector import VectorRetriever

QUESTIONS_PATH = Path("eval/retrieval_stage2_questions.json")
OLD_RESULTS_PATH = Path("eval/sec_qa_stage2_acceptance_30_results.json")
RESULTS_PATH = Path("eval/sec_qa_evidence_only_results.json")
REPORT_PATH = Path("eval/sec_qa_evidence_only_report.md")

# Four known problem cases plus four previously accepted controls that cover
# pure-supported, Layer B partial, evidence-selection, and rank 2-3 retrieval.
TARGET_CASE_IDS = (
    "retrieval-stage2-011",
    "retrieval-stage2-036",
    "retrieval-stage2-046",
    "retrieval-stage2-064",
    "retrieval-stage2-009",
    "retrieval-stage2-051",
    "retrieval-stage2-034",
    "retrieval-stage2-031",
)

OUTPUT_MODE_BY_STATUS = {
    "success": "verified_claims",
    "evidence_only": "raw_evidence",
    "insufficient_evidence": "insufficient_evidence",
    "no_evidence": "insufficient_evidence",
    "error": "error",
}


def run_targeted(
    case_ids: Sequence[str] = TARGET_CASE_IDS,
    *,
    questions_path: Path = QUESTIONS_PATH,
    old_results_path: Path = OLD_RESULTS_PATH,
    results_path: Path = RESULTS_PATH,
    report_path: Path = REPORT_PATH,
) -> dict[str, Any]:
    """Execute the frozen pipeline for the selected cases and write both outputs."""
    selected = _select_cases(case_ids, questions_path)
    old_by_id = _old_statuses(old_results_path, results_path)
    payload: dict[str, Any] = {
        "title": "SEC QA Evidence-Only Fallback Verification",
        "started_at": datetime.now(UTC).isoformat(),
        "selection": {
            "source": str(questions_path),
            "previous_run": str(old_results_path),
            "case_ids": [case["case_id"] for case in selected],
            "cases": selected,
        },
        "config": {
            "qa_top_k": QA_TOP_K,
            "vector_collection": COLLECTION_NAME,
            "chunking_version": "chunker-v2-overlap",
            "parent_collapse": True,
            "retrieval_parameters_changed": False,
        },
        "case_results": [],
        "run_status": "running",
    }
    _write_json(payload, results_path)

    embedding = DashScopeEmbeddingClient()
    retriever = HybridRetriever(
        BM25Retriever(),
        VectorRetriever(embedding, collection_name=COLLECTION_NAME),
    )
    claim_provider = DeepSeekClaimGenerationProvider()
    support_provider = DeepSeekClaimSupportProvider()

    for position, case in enumerate(
        tqdm(selected, desc="Evidence-only targeted QA", unit="case"),
        start=1,
    ):
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
        record.update(_fallback_observability(result))
        record["old"] = old_by_id.get(case["case_id"], {})
        payload["case_results"].append(record)
        _write_json(payload, results_path)
        print(
            f"[{position}/{len(selected)}] {case['case_id']} {case['ticker']} "
            f"{record['new_status']} rank={record['retrieval']['expected_parent_rank']} "
            f"candidates={record['metrics']['candidate_claim_count']} "
            f"verified={record['metrics']['verified_claim_count']} "
            f"mode={record['final_output_mode']}"
        )

    payload["finished_at"] = datetime.now(UTC).isoformat()
    payload["run_status"] = "complete"
    payload["summary"] = _summarize_subset(payload["case_results"])
    _write_json(payload, results_path)
    write_report(payload, report_path)
    return payload


def _select_cases(
    case_ids: Sequence[str],
    questions_path: Path,
) -> list[dict[str, Any]]:
    wanted = list(dict.fromkeys(case_ids))
    available = {case["case_id"]: case for case in load_frozen_cases(questions_path)}
    missing = [case_id for case_id in wanted if case_id not in available]
    if missing:
        raise ValueError(f"unknown case ids: {missing}")
    return [available[case_id] for case_id in wanted]


def _old_statuses(
    old_results_path: Path,
    current_results_path: Path,
) -> dict[str, dict[str, Any]]:
    if old_results_path.exists():
        source = old_results_path
    elif current_results_path.exists():
        source = current_results_path
    else:
        return {}
    payload = json.loads(source.read_text(encoding="utf-8"))
    return {
        case["case_id"]: {
            "acceptance_status": case.get("acceptance_status"),
            "pipeline_status": case.get("pipeline_status"),
            "failure_category": case.get("failure_category"),
            "expected_parent_rank": case.get("retrieval", {}).get("expected_parent_rank"),
            "verified_claim_count": case.get("metrics", {}).get("verified_claim_count"),
            "final_answer_empty": case.get("metrics", {}).get("final_answer_empty"),
        }
        for case in payload.get("case_results", [])
    }


def _fallback_observability(result: dict[str, Any]) -> dict[str, Any]:
    status = str(result.get("status"))
    evidence_only = result.get("evidence_only") or {}
    exported = evidence_only.get("evidence") or []
    return {
        "new_status": _new_status(status),
        "pipeline_status": status,
        "final_output_mode": result.get("final_output_mode")
        or OUTPUT_MODE_BY_STATUS.get(status, status),
        "evidence_only": {
            "triggered": status == "evidence_only",
            "reason": evidence_only.get("reason"),
            "evidence_count": evidence_only.get("evidence_count"),
            "exported_evidence_count": len(exported),
            "exported_evidence_ids": [item["chunk_id"] for item in exported],
            "excerpt_chars": max(
                (len(item["text"]) for item in exported),
                default=0,
            ),
        },
    }


def _new_status(status: str) -> str:
    if status == "success":
        return "answered"
    if status == "evidence_only":
        return "evidence_only"
    if status in {"insufficient_evidence", "no_evidence"}:
        return "insufficient_evidence"
    return "error"


def _summarize_subset(cases: list[dict[str, Any]]) -> dict[str, Any]:
    aggregate = aggregate_cases(cases)
    by_status = {
        status: sum(case["new_status"] == status for case in cases)
        for status in ("answered", "evidence_only", "insufficient_evidence", "error")
    }
    leakage_ids = [
        case["case_id"]
        for case in cases
        if case["metrics"]["unsupported_leakage_count"] > 0
    ]
    return {
        "cases": len(cases),
        "completed": aggregate["completed"],
        "accepted_cases": aggregate["accepted_cases"],
        "failed_cases": aggregate["failed_cases"],
        "answered_count": by_status["answered"],
        "evidence_only_count": by_status["evidence_only"],
        "insufficient_evidence_count": by_status["insufficient_evidence"],
        "error_count": by_status["error"],
        "empty_answer_count": aggregate["empty_final_answers"],
        "grounding_rate": aggregate["final_grounding_rate"],
        "candidate_claim_count": aggregate["candidate_claim_count"],
        "final_grounding_pass_count": aggregate["final_grounding_pass_count"],
        "verified_claim_count": aggregate["verified_claim_count"],
        "unsupported_leakage_count": aggregate["unsupported_leakage_count"],
        "unsupported_leakage_case_ids": leakage_ids,
        "infrastructure_error_count": aggregate["infrastructure_error_count"],
        "infrastructure_affected_case_ids": aggregate["infrastructure_affected_case_ids"],
        "status_by_case": {
            case["case_id"]: case["new_status"] for case in cases
        },
    }


def write_report(
    payload: dict[str, Any],
    path: Path = REPORT_PATH,
) -> None:
    summary = payload["summary"]
    cases = payload["case_results"]
    lines = [
        "# SEC QA — Evidence-Only Fallback Verification (8 Targeted Cases)",
        "",
        f"Generated: {payload['finished_at']}",
        "",
        "## Summary",
        "",
        f"- Cases: {summary['cases']} (completed {summary['completed']})",
        f"- answered_count: {summary['answered_count']}",
        f"- evidence_only_count: {summary['evidence_only_count']}",
        f"- insufficient_evidence_count: {summary['insufficient_evidence_count']}",
        f"- accepted_cases: {summary['accepted_cases']} / failed_cases: "
        f"{summary['failed_cases']} (evidence_only is an output mode, not an acceptance)",
        f"- empty_answer_count: {summary['empty_answer_count']}",
        f"- grounding_rate: {_percent(summary['grounding_rate'])} "
        f"({summary['final_grounding_pass_count']}/{summary['candidate_claim_count']})",
        f"- unsupported_leakage_count: {summary['unsupported_leakage_count']}",
        f"- infrastructure_error_count: {summary['infrastructure_error_count']}",
        "",
        "## Per-case results",
        "",
        "| Case | Old status | New status | Acceptance | Failure category | Retrieval state | Claim result | Final output mode |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for case in cases:
        lines.append(
            f"| {case['case_id']} | {_old_label(case)} | {case['new_status']} | "
            f"{case['acceptance_status']} | {case['failure_category'] or '-'} | "
            f"{_retrieval_label(case)} | {_claim_label(case)} | "
            f"{case.get('output_mode') or case['final_output_mode']} |"
        )

    lines.extend(
        [
            "",
            "## Evidence-only detail",
            "",
            "| Case | Triggered | Reason | Retrieved evidence | Exported evidence |",
            "|---|---|---|---:|---:|",
        ]
    )
    for case in cases:
        info = case["evidence_only"]
        lines.append(
            f"| {case['case_id']} | {'yes' if info['triggered'] else 'no'} | "
            f"{info['reason'] or '-'} | {case['retrieval']['retrieved_count']} | "
            f"{info['exported_evidence_count']} |"
        )

    lines.extend(["", "## Claim verification detail", ""])
    lines.extend(
        [
            "| Case | Candidates | Verified | Rejected reasons | Grounded | Unsupported leakage |",
            "|---|---:|---:|---|---:|---:|",
        ]
    )
    for case in cases:
        metrics = case["metrics"]
        reasons = ", ".join(
            dict.fromkeys(
                str(item.get("reason")) for item in case["rejected_claims"]
            )
        )
        lines.append(
            f"| {case['case_id']} | {metrics['candidate_claim_count']} | "
            f"{metrics['verified_claim_count']} | {reasons or '-'} | "
            f"{metrics['final_grounding_pass_count']} | "
            f"{metrics['unsupported_leakage_count']} |"
        )

    lines.extend(
        [
            "",
            "## Acceptance gates",
            "",
            "Grounding rate >= 95%: " + ("PASS" if _grounding_pass(summary) else "FAIL"),
            "",
            "Unsupported leakage == 0: "
            + ("PASS" if summary["unsupported_leakage_count"] == 0 else "FAIL"),
            "",
            "## Fallback rule",
            "",
            "- verified claims > 0: `answered`, output verified claims.",
            "- no verified claims and retrieved evidence is non-empty: `evidence_only`, "
            "output the raw retrieved evidence.",
            "- no verified claims and no retrieved evidence: `insufficient_evidence`.",
            "- No route-consensus, rank, or score gate is applied; `evidence_only` is an "
            "output mode and never counts as an accepted case.",
            "",
            "## Configuration unchanged",
            "",
            "- Retrieval depth Top-5, chunking v2, parent collapse, vector collection, "
            "Layer A, Layer B, thresholds, and gold data are unchanged.",
            "- Retrieval signals (distances, route ranks) are deterministic; claim "
            "generation is not, so a previously accepted case can still produce zero "
            "candidates on a rerun.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _old_label(case: dict[str, Any]) -> str:
    old = case.get("old") or {}
    category = old.get("failure_category")
    if category:
        return f"{old.get('acceptance_status', '-')} / {category}"
    return str(old.get("acceptance_status") or "unknown")


def _retrieval_label(case: dict[str, Any]) -> str:
    retrieval = case["retrieval"]
    rank = retrieval["expected_parent_rank"]
    return (
        f"expected parent {'miss' if rank is None else f'rank {rank}'} "
        f"of {retrieval['retrieved_count']} retrieved"
    )


def _claim_label(case: dict[str, Any]) -> str:
    metrics = case["metrics"]
    rejected = metrics["partial_count"] + metrics["unsupported_count"]
    return (
        f"{metrics['candidate_claim_count']} candidate / "
        f"{metrics['final_grounding_pass_count']} grounded / "
        f"{metrics['verified_claim_count']} verified / {rejected} rejected"
    )


def _grounding_pass(summary: dict[str, Any]) -> bool:
    rate = summary["grounding_rate"]
    return rate is not None and rate >= 0.95


def _percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"


def _write_json(payload: dict[str, Any], path: Path) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        nargs="+",
        default=list(TARGET_CASE_IDS),
        help="Case IDs to run, in order.",
    )
    parser.add_argument("--questions", type=Path, default=QUESTIONS_PATH)
    parser.add_argument("--old-results", type=Path, default=OLD_RESULTS_PATH)
    parser.add_argument("--results", type=Path, default=RESULTS_PATH)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    return parser


def _main() -> None:
    args = _build_parser().parse_args()
    payload = run_targeted(
        args.cases,
        questions_path=args.questions,
        old_results_path=args.old_results,
        results_path=args.results,
        report_path=args.report,
    )
    print(json.dumps(payload["summary"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _main()
