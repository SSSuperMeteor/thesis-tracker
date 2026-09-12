"""Tests for the verified SEC end-to-end QA pipeline."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from thesis_tracker.qa.sec_qa import (
    REPAIR_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    _build_parser,
    _print_cli_result,
    answer_sec_question,
)
from thesis_tracker.retrieve.hybrid import HybridResult


def chunk(chunk_id: str = "chunk-a", text: str = "Demand remains strong.") -> HybridResult:
    return HybridResult(
        chunk_id=chunk_id,
        ticker="NVDA",
        form_type="10-Q",
        accession="nvda-q",
        section="part_i_item_2",
        title="Management's Discussion and Analysis",
        rrf_score=0.03,
        bm25_rank=1,
        vector_rank=1,
        bm25_score=4.2,
        vector_distance=0.1,
        text=text,
    )


class FakeRetriever:
    def __init__(self, results: list[HybridResult]) -> None:
        self.results = results
        self.calls: list[dict[str, Any]] = []

    def search(
        self,
        query: str,
        *,
        top_k: int,
        ticker: str | None,
        form_type: str | None,
    ) -> list[HybridResult]:
        self.calls.append(
            {
                "query": query,
                "top_k": top_k,
                "ticker": ticker,
                "form_type": form_type,
            }
        )
        return self.results


class FakeClaimProvider:
    model_name = "fake-generator"

    def __init__(self, responses: str | Exception | list[str | Exception]) -> None:
        self.responses = responses if isinstance(responses, list) else [responses]
        self.index = 0
        self.calls: list[tuple[str, str]] = []

    def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        response = self.responses[min(self.index, len(self.responses) - 1)]
        self.index += 1
        if isinstance(response, Exception):
            raise response
        return response


class FakeLayerA:
    def __init__(self, valid: bool | list[bool] = True) -> None:
        self.results = valid if isinstance(valid, list) else [valid]
        self.index = 0
        self.calls: list[tuple[str, str]] = []

    def __call__(
        self, chunk_id: str, evidence: str, *, db_path: str | Path
    ) -> dict[str, Any]:
        self.calls.append((chunk_id, evidence))
        valid = self.results[min(self.index, len(self.results) - 1)]
        self.index += 1
        return {
            "valid": valid,
            "reason": "exact_match" if valid else "evidence_not_found",
        }


class FakeLayerB:
    def __init__(self, statuses: list[str]) -> None:
        self.statuses = iter(statuses)
        self.calls: list[str] = []

    def __call__(
        self,
        claim: str,
        evidence_chunk_id: str,
        evidence_text: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(claim)
        status = next(self.statuses)
        return {
            "grounding_valid": True,
            "grounding_reason": "exact_match",
            "support_status": status,
            "reason": f"classified as {status}",
            "confidence": "high",
        }


def claims_response(*claims: tuple[str, str, str]) -> str:
    return json.dumps(
        {
            "claims": [
                {
                    "claim": claim,
                    "evidence_chunk_id": chunk_id,
                    "evidence_text": evidence,
                }
                for claim, chunk_id, evidence in claims
            ]
        }
    )


def repair_response(chunk_id: str, evidence: str) -> str:
    return json.dumps(
        {
            "status": "repaired",
            "evidence_chunk_id": chunk_id,
            "evidence_text": evidence,
        }
    )


def run_pipeline(
    response: str | Exception,
    *,
    chunks: list[HybridResult] | None = None,
    layer_a: FakeLayerA | None = None,
    layer_b: FakeLayerB | None = None,
    repair: str | Exception | None = None,
    max_grounding_retries: int = 1,
) -> tuple[dict[str, Any], FakeClaimProvider, FakeLayerA, FakeLayerB]:
    responses = [response] if repair is None else [response, repair]
    provider = FakeClaimProvider(responses)
    grounding = layer_a or FakeLayerA()
    support = layer_b or FakeLayerB(["supported"])
    result = answer_sec_question(
        "What does NVIDIA say about demand?",
        "nvda",
        retriever=FakeRetriever(chunks if chunks is not None else [chunk()]),
        claim_provider=provider,
        layer_a_verifier=grounding,
        layer_b_verifier=support,
        max_grounding_retries=max_grounding_retries,
    )
    return result, provider, grounding, support


def test_supported_claim_reaches_final_answer() -> None:
    result, provider, grounding, support = run_pipeline(
        claims_response(("NVIDIA says demand remains strong.", "chunk-a", "Demand remains strong."))
    )

    assert result["status"] == "success"
    assert len(result["verified_claims"]) == 1
    assert result["verified_claims"][0]["layer_a"] == "exact_match"
    assert result["verified_claims"][0]["layer_b"] == "supported"
    assert "NVIDIA says demand remains strong." in result["final_answer"]
    assert provider.calls[0][0] == SYSTEM_PROMPT
    assert '"chunk_id": "chunk-a"' in provider.calls[0][1]
    assert len(grounding.calls) == 1
    assert support.calls == ["NVIDIA says demand remains strong."]


def test_layer_a_failure_discards_claim_without_calling_layer_b() -> None:
    result, _, _, support = run_pipeline(
        claims_response(("Demand is strong.", "chunk-a", "Fabricated quote.")),
        layer_a=FakeLayerA(valid=False),
        max_grounding_retries=0,
    )

    assert result["verified_claims"] == []
    assert result["rejected_claims"][0]["reason"] == "grounding_failed"
    assert support.calls == []


def test_layer_b_unsupported_is_not_in_final_answer() -> None:
    result, _, _, _ = run_pipeline(
        claims_response(("Demand is guaranteed.", "chunk-a", "Demand remains strong.")),
        layer_b=FakeLayerB(["unsupported"]),
    )

    assert result["verified_claims"] == []
    assert result["rejected_claims"][0]["reason"] == "unsupported"
    assert result["status"] == "evidence_only"
    assert "Demand is guaranteed." not in result["final_answer"]
    assert "Demand remains strong." in result["final_answer"]


def test_layer_b_partial_is_not_in_final_answer() -> None:
    result, _, _, _ = run_pipeline(
        claims_response(("All demand is strong.", "chunk-a", "Demand remains strong.")),
        layer_b=FakeLayerB(["partial"]),
    )

    assert result["verified_claims"] == []
    assert result["rejected_claims"][0]["reason"] == "partial_support"
    assert result["status"] == "evidence_only"
    assert "All demand is strong." not in result["final_answer"]
    assert "Demand remains strong." in result["final_answer"]


def test_only_supported_of_two_claims_reaches_final_answer() -> None:
    result, _, _, _ = run_pipeline(
        claims_response(
            ("Demand remains strong.", "chunk-a", "Demand remains strong."),
            ("Demand will double.", "chunk-b", "Demand may improve."),
        ),
        chunks=[chunk(), chunk("chunk-b", "Demand may improve.")],
        layer_b=FakeLayerB(["supported", "unsupported"]),
    )

    assert [item["claim"] for item in result["verified_claims"]] == [
        "Demand remains strong."
    ]
    assert "Demand will double." not in result["final_answer"]
    assert result["rejected_claims"][0]["reason"] == "unsupported"


def test_unretrieved_evidence_chunk_id_is_rejected() -> None:
    result, _, grounding, support = run_pipeline(
        claims_response(("Demand remains strong.", "unknown", "Demand remains strong."))
    )

    assert result["rejected_claims"][0]["reason"] == "invalid_model_output"
    assert result["rejected_claims"][0]["detail"] == "chunk_id_not_retrieved"
    assert grounding.calls == []
    assert support.calls == []


def test_empty_retrieval_returns_no_evidence_without_generation() -> None:
    result, provider, grounding, support = run_pipeline(
        claims_response(("unused", "chunk-a", "unused")), chunks=[]
    )

    assert result["status"] == "no_evidence"
    assert result["final_answer"] == ""
    assert provider.calls == []
    assert grounding.calls == []
    assert support.calls == []


def test_invalid_json_returns_explicit_safe_error() -> None:
    result, _, _, support = run_pipeline("not-json")

    assert result["status"] == "error"
    assert result["error"]["type"] == "invalid_model_output"
    assert result["rejected_claims"][0]["reason"] == "invalid_model_output"
    assert result["final_answer"] == ""
    assert support.calls == []


def test_empty_claims_returns_raw_evidence() -> None:
    result, _, grounding, support = run_pipeline('{"claims": []}')

    assert result["status"] == "evidence_only"
    assert result["candidate_claims"] == []
    assert result["evidence_only"]["reason"] == "no_supported_claims"
    assert len(result["evidence_only"]["evidence"]) == 1
    assert result["final_output_mode"] == "raw_evidence"
    assert "Demand remains strong." in result["final_answer"]
    assert grounding.calls == []
    assert support.calls == []


def test_no_retrieval_evidence_stays_insufficient() -> None:
    result, _, _, _ = run_pipeline('{"claims": []}', chunks=[])

    assert result["status"] == "no_evidence"
    assert result["final_answer"] == ""
    assert result["evidence_only"] is None


def test_fallback_does_not_depend_on_route_ranks() -> None:
    """Removing the route-consensus gate: weak route ranks still export evidence."""
    top = replace(chunk(), bm25_rank=9, vector_rank=17, bm25_parent_rank=None, vector_parent_rank=None)
    result, _, _, _ = run_pipeline('{"claims": []}', chunks=[top])

    assert result["status"] == "evidence_only"
    assert result["final_output_mode"] == "raw_evidence"
    assert result["evidence_only"]["reason"] == "no_supported_claims"
    assert len(result["evidence_only"]["evidence"]) == 1


def test_verified_claim_never_triggers_the_fallback() -> None:
    result, _, _, _ = run_pipeline(
        claims_response(("Demand remains strong.", "chunk-a", "Demand remains strong."))
    )

    assert result["status"] == "success"
    assert result["final_output_mode"] == "verified_claims"
    assert result["evidence_only"] is None


def test_api_error_returns_explicit_safe_error() -> None:
    result, _, _, support = run_pipeline(RuntimeError("service unavailable"))

    assert result["status"] == "error"
    assert result["error"] == {
        "stage": "claim_generation",
        "type": "api_error",
        "message": "service unavailable",
    }
    assert result["rejected_claims"][0]["reason"] == "api_error"
    assert result["verified_claims"] == []
    assert result["final_answer"] == ""
    assert support.calls == []


def test_missing_candidate_field_is_rejected_while_valid_claim_continues() -> None:
    raw = json.dumps(
        {
            "claims": [
                {"claim": "Missing evidence.", "evidence_chunk_id": "chunk-a"},
                {
                    "claim": "Demand remains strong.",
                    "evidence_chunk_id": "chunk-a",
                    "evidence_text": "Demand remains strong.",
                },
            ]
        }
    )
    result, _, _, support = run_pipeline(raw)

    assert len(result["verified_claims"]) == 1
    assert result["rejected_claims"][0]["reason"] == "invalid_model_output"
    assert support.calls == ["Demand remains strong."]


def test_initial_layer_a_pass_does_not_call_repair() -> None:
    result, provider, _, _ = run_pipeline(
        claims_response(("Demand remains strong.", "chunk-a", "Demand remains strong."))
    )

    candidate = result["candidate_claims"][0]
    assert len(provider.calls) == 1
    assert candidate["grounding_attempts"] == 1
    assert candidate["initial_layer_a_result"] == "exact_match"
    assert candidate["repair_attempted"] is False
    assert candidate["repair_result"] is None
    assert candidate["final_layer_a_result"] == "exact_match"


def test_failed_grounding_is_repaired_then_enters_layer_b() -> None:
    result, provider, grounding, support = run_pipeline(
        claims_response(("Demand remains strong.", "chunk-a", "Demand remains stronk.")),
        repair=repair_response("chunk-a", "Demand remains strong."),
        layer_a=FakeLayerA([False, True]),
    )

    candidate = result["candidate_claims"][0]
    assert len(provider.calls) == 2
    assert provider.calls[1][0] == REPAIR_SYSTEM_PROMPT
    assert grounding.calls == [
        ("chunk-a", "Demand remains stronk."),
        ("chunk-a", "Demand remains strong."),
    ]
    assert candidate["grounding_attempts"] == 2
    assert candidate["repair_result"] == "repaired"
    assert candidate["final_layer_a_result"] == "exact_match"
    assert support.calls == ["Demand remains strong."]


def test_failed_grounding_after_repair_is_rejected_without_layer_b() -> None:
    result, _, _, support = run_pipeline(
        claims_response(("Demand remains strong.", "chunk-a", "Wrong evidence.")),
        repair=repair_response("chunk-a", "Still wrong evidence."),
        layer_a=FakeLayerA([False, False]),
    )

    rejected = result["rejected_claims"][0]
    assert rejected["reason"] == "grounding_failed_after_retry"
    assert rejected["grounding_attempts"] == 2
    assert rejected["repair_attempted"] is True
    assert support.calls == []


def test_repair_no_valid_evidence_rejects_without_layer_b() -> None:
    result, _, _, support = run_pipeline(
        claims_response(("Demand will triple.", "chunk-a", "Invented quote.")),
        repair='{"status":"no_valid_evidence"}',
        layer_a=FakeLayerA(False),
    )

    rejected = result["rejected_claims"][0]
    assert rejected["reason"] == "no_valid_evidence"
    assert rejected["grounding_attempts"] == 1
    assert rejected["repair_result"] == "no_valid_evidence"
    assert support.calls == []


def test_repair_with_unretrieved_chunk_id_is_rejected() -> None:
    result, _, grounding, support = run_pipeline(
        claims_response(("Demand remains strong.", "chunk-a", "Wrong evidence.")),
        repair=repair_response("not-retrieved", "Demand remains strong."),
        layer_a=FakeLayerA(False),
    )

    rejected = result["rejected_claims"][0]
    assert rejected["reason"] == "invalid_repair_output"
    assert rejected["detail"] == "repair chunk_id_not_retrieved"
    assert len(grounding.calls) == 1
    assert support.calls == []


def test_invalid_repair_json_is_rejected() -> None:
    result, _, grounding, support = run_pipeline(
        claims_response(("Demand remains strong.", "chunk-a", "Wrong evidence.")),
        repair="not-json",
        layer_a=FakeLayerA(False),
    )

    rejected = result["rejected_claims"][0]
    assert rejected["reason"] == "invalid_repair_output"
    assert rejected["repair_result"] == "invalid_repair_output"
    assert len(grounding.calls) == 1
    assert support.calls == []


def test_repair_changes_evidence_but_keeps_claim_unchanged() -> None:
    original_claim = "NVIDIA says demand remains strong."
    result, provider, _, support = run_pipeline(
        claims_response((original_claim, "chunk-a", "Demand remains very strong.")),
        repair=repair_response("chunk-a", "Demand remains strong."),
        layer_a=FakeLayerA([False, True]),
    )

    candidate = result["candidate_claims"][0]
    assert candidate["claim"] == original_claim
    assert candidate["evidence_text"] == "Demand remains strong."
    assert support.calls == [original_claim]
    repair_prompt = json.loads(provider.calls[1][1].split("\n", 1)[1])
    assert repair_prompt["unchanged_claim"] == original_claim


def test_repaired_supported_claim_reaches_final_answer() -> None:
    claim = "NVIDIA says demand remains strong."
    result, _, _, _ = run_pipeline(
        claims_response((claim, "chunk-a", "Demand remains strong!")),
        repair=repair_response("chunk-a", "Demand remains strong."),
        layer_a=FakeLayerA([False, True]),
        layer_b=FakeLayerB(["supported"]),
    )

    assert result["status"] == "success"
    assert result["verified_claims"][0]["repair_attempted"] is True
    assert result["verified_claims"][0]["layer_b"] == "supported"
    assert claim in result["final_answer"]
    assert "Demand remains strong." in result["final_answer"]


def cli_result(
    *,
    status: str = "success",
    final_answer: str = "- Verified claim",
    error: dict[str, str] | None = None,
    rejected_claims: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "question": "What does NVIDIA say about demand?",
        "ticker": "NVDA",
        "form_type": "10-Q",
        "status": status,
        "retrieved_chunks": [{"chunk_id": "chunk-a", "text": "Source text."}],
        "candidate_claims": [
            {
                "claim": "Verified claim",
                "grounding_attempts": 1,
                "initial_layer_a_result": "exact_match",
                "repair_attempted": False,
                "repair_result": None,
                "final_layer_a_result": "exact_match",
            }
        ],
        "verified_claims": [
            {"claim": "Verified claim", "layer_b": "supported"}
        ],
        "rejected_claims": rejected_claims or [],
        "final_answer": final_answer,
        "error": error,
    }


def test_default_cli_success_prints_only_final_answer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _print_cli_result(cli_result(), debug=False)

    output = capsys.readouterr().out
    assert output == "FINAL ANSWER\n- Verified claim\n"
    assert "RETRIEVED CHUNKS" not in output
    assert "CANDIDATE CLAIMS" not in output
    assert "VERIFIED CLAIMS" not in output
    assert "REJECTED CLAIMS" not in output


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            cli_result(
                status="error",
                final_answer="",
                error={"stage": "retrieval", "type": "retrieval_error"},
            ),
            "ERROR: retrieval failed\n",
        ),
        (
            cli_result(
                status="error",
                final_answer="",
                error={"stage": "claim_generation", "type": "api_error"},
            ),
            "ERROR: claim generation failed\n",
        ),
        (
            cli_result(
                status="insufficient_evidence",
                final_answer="",
                rejected_claims=[{"reason": "layer_b_error", "detail": "timeout"}],
            ),
            "ERROR: Layer B API error\n",
        ),
        (
            cli_result(status="insufficient_evidence", final_answer=""),
            "ERROR: no verified claims\n",
        ),
    ],
)
def test_default_cli_failure_prints_only_brief_error(
    result: dict[str, Any],
    expected: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _print_cli_result(result, debug=False)

    assert capsys.readouterr().out == expected


def test_debug_cli_restores_complete_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _print_cli_result(cli_result(), debug=True)

    output = capsys.readouterr().out
    for heading in (
        "QUESTION",
        "STATUS",
        "RETRIEVED CHUNKS",
        "CANDIDATE CLAIMS",
        "VERIFIED CLAIMS",
        "REJECTED CLAIMS",
        "FINAL ANSWER",
    ):
        assert heading in output
    assert '"grounding_attempts": 1' in output
    assert '"repair_attempted": false' in output
    assert '"layer_b": "supported"' in output


def test_debug_flag_is_opt_in() -> None:
    parser = _build_parser()

    default_args = parser.parse_args(["question", "--ticker", "NVDA"])
    debug_args = parser.parse_args(["question", "--ticker", "NVDA", "--debug"])

    assert default_args.debug is False
    assert debug_args.debug is True


def test_cli_rendering_does_not_change_core_result(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = cli_result()
    original = deepcopy(result)

    _print_cli_result(result, debug=False)
    capsys.readouterr()
    _print_cli_result(result, debug=True)
    capsys.readouterr()

    assert result == original
