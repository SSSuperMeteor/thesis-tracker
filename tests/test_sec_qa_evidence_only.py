"""Tests for the targeted evidence-only fallback verification runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from thesis_tracker.evaluation.sec_qa_evidence_only import (
    TARGET_CASE_IDS,
    _fallback_observability,
    _new_status,
    _select_cases,
    _summarize_subset,
)

QUESTIONS = Path("eval/retrieval_stage2_questions.json")


def _record(
    case_id: str,
    new_status: str,
    *,
    leakage: int = 0,
    empty: bool = False,
    candidates: int | None = None,
    grounded: int | None = None,
) -> dict[str, object]:
    answered = new_status == "answered"
    candidate_count = candidates if candidates is not None else (1 if answered else 0)
    grounded_count = grounded if grounded is not None else (1 if answered else 0)
    return {
        "case_id": case_id,
        "completed": True,
        "acceptance_status": "accepted" if answered else "failed",
        "infrastructure_errors": [],
        "new_status": new_status,
        "metrics": {
            "candidate_claim_count": candidate_count,
            "initial_grounding_pass_count": grounded_count,
            "initial_grounding_fail_count": candidate_count - grounded_count,
            "repair_attempt_count": 0,
            "repair_success_count": 0,
            "no_valid_evidence_count": 0,
            "repair_failed_count": 0,
            "final_grounding_pass_count": grounded_count,
            "final_grounding_fail_count": candidate_count - grounded_count,
            "supported_count": 1 if answered else 0,
            "partial_count": 0,
            "unsupported_count": 0,
            "layer_b_error_count": 0,
            "verified_claim_count": 1 if answered else 0,
            "rejected_claim_count": 0,
            "final_answer_empty": empty,
            "unsupported_leakage_count": leakage,
        },
    }


def test_target_selection_covers_four_problem_and_four_control_cases() -> None:
    assert len(TARGET_CASE_IDS) == 8
    assert len(set(TARGET_CASE_IDS)) == 8
    for case_id in (
        "retrieval-stage2-011",
        "retrieval-stage2-036",
        "retrieval-stage2-046",
        "retrieval-stage2-064",
    ):
        assert case_id in TARGET_CASE_IDS


def test_case_filter_selects_only_requested_cases_in_order() -> None:
    selected = _select_cases(
        ["retrieval-stage2-046", "retrieval-stage2-011"],
        QUESTIONS,
    )

    assert [case["case_id"] for case in selected] == [
        "retrieval-stage2-046",
        "retrieval-stage2-011",
    ]
    assert selected[0]["ticker"] == "SNDK"


def test_case_filter_rejects_unknown_ids() -> None:
    with pytest.raises(ValueError, match="unknown case ids"):
        _select_cases(["not-a-case"], QUESTIONS)


def test_new_status_mapping() -> None:
    assert _new_status("success") == "answered"
    assert _new_status("evidence_only") == "evidence_only"
    assert _new_status("insufficient_evidence") == "insufficient_evidence"
    assert _new_status("no_evidence") == "insufficient_evidence"
    assert _new_status("error") == "error"


def test_fallback_observability_records_reason_and_exports() -> None:
    result = {
        "status": "evidence_only",
        "final_output_mode": "raw_evidence",
        "retrieved_chunks": [{"chunk_id": "p::chunk_000"}],
        "evidence_only": {
            "reason": "no_supported_claims",
            "evidence_count": 5,
            "evidence": [{"chunk_id": "p::chunk_000", "text": "evidence"}],
        },
    }

    observability = _fallback_observability(result)

    assert observability["new_status"] == "evidence_only"
    assert observability["final_output_mode"] == "raw_evidence"
    assert observability["evidence_only"]["triggered"] is True
    assert observability["evidence_only"]["reason"] == "no_supported_claims"
    assert observability["evidence_only"]["evidence_count"] == 5
    assert observability["evidence_only"]["exported_evidence_count"] == 1
    assert observability["evidence_only"]["exported_evidence_ids"] == ["p::chunk_000"]


def test_summarize_subset_counts_statuses_and_leakage() -> None:
    summary = _summarize_subset(
        [
            _record("c1", "answered"),
            _record("c2", "evidence_only", candidates=2, grounded=1),
            _record("c3", "insufficient_evidence", empty=True),
            _record("c4", "evidence_only"),
        ]
    )

    assert summary["answered_count"] == 1
    assert summary["evidence_only_count"] == 2
    assert summary["insufficient_evidence_count"] == 1
    assert summary["empty_answer_count"] == 1
    assert summary["unsupported_leakage_count"] == 0
    assert summary["grounding_rate"] == 2 / 3
    assert summary["status_by_case"]["c3"] == "insufficient_evidence"
