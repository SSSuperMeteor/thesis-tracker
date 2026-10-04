"""Evaluate the five fixed difficult SEC QA cases on chunking v2."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from thesis_tracker.embedding.dashscope import DashScopeEmbeddingClient
from thesis_tracker.evaluation.sec_qa_acceptance import infrastructure_errors
from thesis_tracker.qa.sec_qa import (
    DeepSeekClaimGenerationProvider,
    answer_sec_question,
)
from thesis_tracker.retrieve.bm25 import BM25Retriever
from thesis_tracker.retrieve.claim_support import (
    DeepSeekClaimSupportProvider,
    verify_sec_claim,
)
from thesis_tracker.retrieve.hybrid import HybridRetriever
from thesis_tracker.retrieve.vector import VectorRetriever

COLLECTION_NAME = "sec_chunks_qwen3_vl_embedding_1024_chunkv2"
DIFFICULT_CASE_IDS = (
    "retrieval-stage2-001",
    "retrieval-stage2-031",
    "retrieval-stage2-033",
    "retrieval-stage2-057",
    "retrieval-stage2-064",
)
OLD_RESULTS_PATH = Path("eval/sec_qa_stage2_acceptance_30_results.json")
RESULTS_PATH = Path("eval/sec_qa_chunkv2_difficult5_results.json")
REPORT_PATH = Path("eval/sec_qa_chunkv2_difficult5_report.md")


class TrackingLayerB:
    """Record existing Layer B outputs without changing classification behavior."""

    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        result = verify_sec_claim(*args, **kwargs)
        self.results.append(result)
        return result


def run_difficult_five() -> dict[str, Any]:
    """Run only the frozen five cases and write JSON plus Markdown outputs."""
    old_payload = json.loads(OLD_RESULTS_PATH.read_text(encoding="utf-8"))
    old_by_id = {case["case_id"]: case for case in old_payload["case_results"]}
    selected = [old_by_id[case_id] for case_id in DIFFICULT_CASE_IDS]

    retriever = HybridRetriever(
        BM25Retriever(),
        VectorRetriever(
            DashScopeEmbeddingClient(),
            collection_name=COLLECTION_NAME,
        ),
    )
    claim_provider = DeepSeekClaimGenerationProvider()
    support_provider = DeepSeekClaimSupportProvider()
    payload: dict[str, Any] = {
        "title": "SEC QA Chunking v2 — Five Difficult Cases",
        "started_at": datetime.now(UTC).isoformat(),
        "chunking": {
            "version": "chunker-v2-overlap",
            "target_chunk_chars": 3000,
            "overlap_chars": 500,
            "vector_collection": COLLECTION_NAME,
        },
        "case_ids": list(DIFFICULT_CASE_IDS),
        "case_results": [],
    }
    _write_json(payload)

    for position, old in enumerate(selected, start=1):
        error_history = []
        result: dict[str, Any] = {}
        tracker = TrackingLayerB()
        attempts = 0
        incomplete = False
        for attempt in (1, 2):
            attempts = attempt
            tracker = TrackingLayerB()
            result = answer_sec_question(
                old["question"],
                old["ticker"],
                form_type=old["form_type"],
                top_k=5,
                retriever=retriever,
                claim_provider=claim_provider,
                support_provider=support_provider,
                layer_b_verifier=tracker,
            )
            errors = infrastructure_errors(result)
            if not errors:
                break
            error_history.append({"attempt": attempt, "errors": errors})
            if attempt == 2:
                incomplete = True

        after = _metrics(result)
        layer_b_lookup = {
            (item["claim"], item["chunk_id"], item["evidence_text"]): item
            for item in tracker.results
        }
        claims = []
        for claim in [*result["verified_claims"], *result["rejected_claims"]]:
            tracked = layer_b_lookup.get(
                (
                    claim.get("claim"),
                    claim.get("evidence_chunk_id"),
                    claim.get("evidence_text"),
                )
            )
            claims.append(
                {
                    **claim,
                    "layer_b_reason": tracked.get("reason") if tracked else None,
                }
            )

        expected_prefix = old["expected_chunk_id"] + "::chunk_"
        retrieved_ids = [
            chunk["chunk_id"] for chunk in result.get("retrieved_chunks", [])
        ]
        record = {
            "case_id": old["case_id"],
            "ticker": old["ticker"],
            "form_type": old["form_type"],
            "question_type": old["question_type"],
            "question": old["question"],
            "expected_chunk_id_v1": old["expected_chunk_id"],
            "execution_attempts": attempts,
            "completed": not incomplete,
            "infrastructure_errors": error_history,
            "retrieved_chunk_ids": retrieved_ids,
            "expected_section_retrieved": any(
                chunk_id.startswith(expected_prefix) for chunk_id in retrieved_ids
            ),
            "candidate_claims": result.get("candidate_claims", []),
            "claims": claims,
            "verified_claims": result.get("verified_claims", []),
            "rejected_claims": result.get("rejected_claims", []),
            "final_answer": result.get("final_answer", ""),
            "before": _old_metrics(old),
            "after": after,
        }
        payload["case_results"].append(record)
        _write_json(payload)
        print(
            f"[{position}/5] {old['case_id']} "
            f"S/P/U={after['supported']}/{after['partial']}/"
            f"{after['unsupported']} verified={after['verified_claims']} "
            f"empty={after['final_answer_empty']}"
        )

    payload["finished_at"] = datetime.now(UTC).isoformat()
    payload["summary"] = _summary(payload["case_results"])
    _write_json(payload)
    _write_report(payload)
    print(json.dumps(payload["summary"], indent=2))
    return payload


def _metrics(result: dict[str, Any]) -> dict[str, Any]:
    verified = result.get("verified_claims", [])
    rejected = result.get("rejected_claims", [])
    return {
        "candidate_claims": len(result.get("candidate_claims", [])),
        "supported": sum(item.get("layer_b") == "supported" for item in verified),
        "partial": sum(item.get("layer_b") == "partial" for item in rejected),
        "unsupported": sum(
            item.get("layer_b") == "unsupported" for item in rejected
        ),
        "verified_claims": len(verified),
        "rejected_claims": len(rejected),
        "final_answer_empty": not bool(result.get("final_answer")),
    }


def _old_metrics(case: dict[str, Any]) -> dict[str, Any]:
    metrics = case["metrics"]
    return {
        "supported": metrics["supported_count"],
        "partial": metrics["partial_count"],
        "unsupported": metrics["unsupported_count"],
        "verified_claims": metrics["verified_claim_count"],
        "final_answer_empty": metrics["final_answer_empty"],
    }


def _summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "cases": len(cases),
        "completed": sum(case["completed"] for case in cases),
        "infrastructure_error_cases": sum(
            bool(case["infrastructure_errors"]) for case in cases
        ),
        "before": {
            "supported": sum(case["before"]["supported"] for case in cases),
            "partial": sum(case["before"]["partial"] for case in cases),
            "unsupported": sum(case["before"]["unsupported"] for case in cases),
            "verified_claims": sum(
                case["before"]["verified_claims"] for case in cases
            ),
            "empty_answers": sum(
                case["before"]["final_answer_empty"] for case in cases
            ),
        },
        "after": {
            "supported": sum(case["after"]["supported"] for case in cases),
            "partial": sum(case["after"]["partial"] for case in cases),
            "unsupported": sum(case["after"]["unsupported"] for case in cases),
            "verified_claims": sum(
                case["after"]["verified_claims"] for case in cases
            ),
            "empty_answers": sum(
                case["after"]["final_answer_empty"] for case in cases
            ),
            "expected_section_retrieved": sum(
                case["expected_section_retrieved"] for case in cases
            ),
        },
    }


def _write_report(payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    lines = [
        "# SEC QA Chunking v2 — Five Difficult Cases",
        "",
        "## Summary",
        "",
        "| Metric | Before | After |",
        "|---|---:|---:|",
        f"| Supported | {summary['before']['supported']} | "
        f"{summary['after']['supported']} |",
        f"| Partial | {summary['before']['partial']} | "
        f"{summary['after']['partial']} |",
        f"| Unsupported | {summary['before']['unsupported']} | "
        f"{summary['after']['unsupported']} |",
        f"| Verified claims | {summary['before']['verified_claims']} | "
        f"{summary['after']['verified_claims']} |",
        f"| Empty answers | {summary['before']['empty_answers']} | "
        f"{summary['after']['empty_answers']} |",
        "",
        f"Expected v2 section retrieved in {summary['after']['expected_section_retrieved']}"
        f"/{summary['cases']} cases.",
        "",
        "## By Case",
        "",
        "| Case | Before S/P/U | After S/P/U | Verified before/after | Empty before/after |",
        "|---|---:|---:|---:|---:|",
    ]
    for case in payload["case_results"]:
        before = case["before"]
        after = case["after"]
        lines.append(
            f"| {case['case_id']} | {before['supported']}/{before['partial']}/"
            f"{before['unsupported']} | {after['supported']}/{after['partial']}/"
            f"{after['unsupported']} | {before['verified_claims']}/"
            f"{after['verified_claims']} | {before['final_answer_empty']}/"
            f"{after['final_answer_empty']} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This is a fixed five-case smoke comparison. No retrieval weights, QA "
            "prompts, Layer A, retry, Layer B, or filtering rules were changed.",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_json(payload: dict[str, Any]) -> None:
    RESULTS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    run_difficult_five()
