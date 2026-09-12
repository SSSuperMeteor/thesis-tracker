"""Tests for the fixed SEC QA acceptance evaluation runner."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from thesis_tracker.evaluation.sec_qa_acceptance import (
    aggregate_cases,
    infrastructure_errors,
    load_frozen_cases,
    summarize_case,
)


def test_frozen_selection_has_thirty_balanced_cases() -> None:
    cases = load_frozen_cases(Path("eval/retrieval_stage2_questions.json"))

    assert len(cases) == 30
    assert len({case["case_id"] for case in cases}) == 30
    assert Counter(case["ticker"] for case in cases) == {
        "AMD": 5,
        "LITE": 4,
        "NVDA": 4,
        "ORCL": 4,
        "SNDK": 4,
        "TSLA": 4,
        "VRT": 5,
    }
    assert Counter(case["question_type"] for case in cases)["risk"] == 7


def test_provider_and_retrieval_errors_are_infrastructure() -> None:
    result = {
        "error": {
            "stage": "retrieval",
            "type": "retrieval_error",
            "message": "network",
        },
        "rejected_claims": [
            {"reason": "layer_b_error", "detail": "timeout"},
            {"reason": "unsupported", "detail": "not entailed"},
        ],
    }

    errors = infrastructure_errors(result)

    assert len(errors) == 2
    assert all("unsupported" not in error for error in errors)


def test_case_summary_counts_grounding_layer_b_and_leakage() -> None:
    case = {
        "case_id": "case-1",
        "ticker": "NVDA",
        "form_type": "10-Q",
        "question_type": "risk",
        "question": "Question?",
        "source_index": 1,
        "expected_chunk_id": "chunk-a",
    }
    supported = {
        "claim": "Supported claim.",
        "initial_layer_a_result": "exact_match",
        "repair_attempted": False,
        "repair_result": None,
        "final_layer_a_result": "exact_match",
        "layer_b": "supported",
    }
    partial = {
        "claim": "Partial claim.",
        "initial_layer_a_result": "evidence_not_found",
        "repair_attempted": True,
        "repair_result": "repaired",
        "final_layer_a_result": "exact_match",
        "layer_b": "partial",
        "reason": "partial_support",
    }
    result: dict[str, Any] = {
        "status": "success",
        "error": None,
        "retrieved_chunks": [{"chunk_id": "chunk-a", "text": "not retained"}],
        "candidate_claims": [supported, partial],
        "verified_claims": [supported],
        "rejected_claims": [partial],
        "final_answer": "Supported claim.",
    }

    summary = summarize_case(
        case,
        result,
        attempts=1,
        infrastructure_history=[],
        incomplete=False,
    )

    assert summary["retrieval"]["retrieved_chunk_ids"] == ["chunk-a"]
    assert summary["retrieval"]["retrieved_parent_ids"] == ["chunk-a"]
    assert summary["retrieval"]["expected_parent_rank"] == 1
    assert summary["retrieval"]["expected_parent_retrieved"] is True
    assert summary["acceptance_status"] == "accepted"
    assert summary["failure_category"] == "claim_support_failure"
    assert summary["metrics"]["initial_grounding_pass_count"] == 1
    assert summary["metrics"]["repair_success_count"] == 1
    assert summary["metrics"]["final_grounding_pass_count"] == 2
    assert summary["metrics"]["partial_count"] == 1
    assert summary["metrics"]["unsupported_leakage_count"] == 0


def test_aggregate_applies_acceptance_gates() -> None:
    metrics = {
        "candidate_claim_count": 20,
        "initial_grounding_pass_count": 18,
        "initial_grounding_fail_count": 2,
        "repair_attempt_count": 2,
        "repair_success_count": 1,
        "no_valid_evidence_count": 0,
        "repair_failed_count": 1,
        "final_grounding_pass_count": 19,
        "final_grounding_fail_count": 1,
        "supported_count": 15,
        "partial_count": 3,
        "unsupported_count": 1,
        "layer_b_error_count": 0,
        "verified_claim_count": 15,
        "rejected_claim_count": 5,
        "unsupported_leakage_count": 0,
        "final_answer_empty": False,
    }
    case = {
        "case_id": "case-1",
        "ticker": "NVDA",
        "completed": True,
        "acceptance_status": "accepted",
        "infrastructure_errors": [],
        "metrics": metrics,
    }

    aggregate = aggregate_cases([case])

    assert aggregate["initial_grounding_rate"] == 0.9
    assert aggregate["final_grounding_rate"] == 0.95
    assert aggregate["citation_grounding_acceptance"] is True
    assert aggregate["unsupported_leakage_acceptance"] is True
    assert aggregate["overall_acceptance"] is True
    assert aggregate["accepted_cases"] == 1
    assert aggregate["failed_cases"] == 0
    assert aggregate["cases_with_partial_claims"] == 1


def test_evidence_only_output_mode_is_not_an_acceptance() -> None:
    case = {
        "case_id": "case-1",
        "ticker": "NVDA",
        "form_type": "10-Q",
        "question_type": "risk",
        "question": "Question?",
        "source_index": 1,
        "expected_chunk_id": "chunk-a",
    }
    result = {
        "status": "evidence_only",
        "error": None,
        "retrieved_chunks": [{"chunk_id": "chunk-a", "text": "not retained"}],
        "candidate_claims": [],
        "verified_claims": [],
        "rejected_claims": [],
        "final_answer": "evidence text",
        "final_output_mode": "raw_evidence",
        "evidence_only": {
            "reason": "no_supported_claims",
            "evidence": [{"chunk_id": "chunk-a", "text": "evidence text"}],
        },
    }

    summary = summarize_case(
        case,
        result,
        attempts=1,
        infrastructure_history=[],
        incomplete=False,
    )

    assert summary["acceptance_status"] == "failed"
    assert summary["output_mode"] == "raw_evidence"
    assert summary["evidence_only"]["reason"] == "no_supported_claims"
    assert summary["metrics"]["verified_claim_count"] == 0
    assert summary["metrics"]["unsupported_leakage_count"] == 0

    aggregate = aggregate_cases([summary])

    assert aggregate["accepted_cases"] == 0
    assert aggregate["failed_cases"] == 1
    assert aggregate["empty_final_answers"] == 0


def test_supported_answer_is_not_failure_when_gold_parent_is_not_retrieved() -> None:
    case = {
        "case_id": "case-alternative-evidence",
        "ticker": "VRT",
        "form_type": "10-Q",
        "question_type": "risk",
        "question": "Question?",
        "source_index": 1,
        "expected_chunk_id": "expected-parent",
    }
    supported = {
        "claim": "Supported from alternative evidence.",
        "evidence_chunk_id": "alternative-parent::chunk_000",
        "evidence_text": "Supported from alternative evidence.",
        "initial_layer_a_result": "exact_match",
        "repair_attempted": False,
        "repair_result": None,
        "final_layer_a_result": "exact_match",
        "layer_b": "supported",
    }
    result = {
        "status": "success",
        "error": None,
        "retrieved_chunks": [
            {
                "chunk_id": "alternative-parent::chunk_000",
                "logical_parent_id": "alternative-parent",
            }
        ],
        "candidate_claims": [supported],
        "verified_claims": [supported],
        "rejected_claims": [],
        "final_answer": "Supported from alternative evidence.",
    }

    summary = summarize_case(
        case,
        result,
        attempts=1,
        infrastructure_history=[],
        incomplete=False,
    )

    assert summary["retrieval"]["expected_parent_retrieved"] is False
    assert summary["acceptance_status"] == "accepted"
    assert summary["failure_category"] is None
