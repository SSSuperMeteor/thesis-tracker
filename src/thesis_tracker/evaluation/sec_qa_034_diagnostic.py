"""Trace claim-generation nondeterminism for retrieval-stage2-034."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from thesis_tracker.embedding.dashscope import DashScopeEmbeddingClient
from thesis_tracker.evaluation.retrieval_stage2_chunkv2 import COLLECTION_NAME
from thesis_tracker.evaluation.sec_qa_acceptance import (
    QA_TOP_K,
    load_frozen_cases,
)
from thesis_tracker.qa.sec_qa import (
    SYSTEM_PROMPT,
    ClaimGenerationResponseError,
    DeepSeekClaimGenerationProvider,
    _parse_candidates,
    answer_sec_question,
)
from thesis_tracker.retrieve.bm25 import BM25Retriever
from thesis_tracker.retrieve.claim_support import DeepSeekClaimSupportProvider
from thesis_tracker.retrieve.hybrid import HybridRetriever
from thesis_tracker.retrieve.vector import VectorRetriever

CASE_ID = "retrieval-stage2-034"
DEFAULT_RUNS = 10
RESULTS_PATH = Path("eval/sec_qa_034_nondeterminism_results.json")


class TracingClaimProvider:
    """Observe the existing provider without changing its request behavior."""

    def __init__(self) -> None:
        self.provider = DeepSeekClaimGenerationProvider()
        self.model_name = self.provider.model_name
        self.traces: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        http_client = self.provider._client._client
        http_client.event_hooks["request"].append(self._on_request)
        http_client.event_hooks["response"].append(self._on_response)

    def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        trace: dict[str, Any] = {
            "call_kind": (
                "claim_generation"
                if system_prompt == SYSTEM_PROMPT
                else "grounding_repair"
            ),
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "prompt_hash": _sha256(system_prompt + "\0" + user_prompt),
            "retrieval_fingerprint": _retrieval_fingerprint(user_prompt),
            "prompt_chars": len(system_prompt) + len(user_prompt),
            "prompt_bytes": len(
                (system_prompt + "\0" + user_prompt).encode("utf-8")
            ),
            "http_requests": [],
            "http_responses": [],
            "returned_content": None,
            "raw_response_state": None,
            "parse_state": None,
            "parsed_candidate_count": None,
            "invalid_claim_count": None,
            "exception": None,
        }
        self.traces.append(trace)
        self._current = trace
        try:
            content = self.provider.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            trace["returned_content"] = content
            _audit_content(trace, content)
            return content
        except Exception as error:
            trace["exception"] = {
                "type": type(error).__name__,
                "message": str(error),
            }
            if trace["raw_response_state"] is None:
                trace["raw_response_state"] = "provider_exception"
                trace["parse_state"] = "not_parsed"
            raise
        finally:
            trace["http_request_count"] = len(trace["http_requests"])
            trace["sdk_retry_count"] = max(0, len(trace["http_requests"]) - 1)
            trace["truncated"] = any(
                response.get("finish_reason") in {"length", "max_tokens"}
                for response in trace["http_responses"]
            )
            self._current = None

    def _on_request(self, request: httpx.Request) -> None:
        if self._current is None:
            return
        body = request.content.decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}
        self._current["http_requests"].append(
            {
                "method": request.method,
                "url": str(request.url),
                "retry_count_header": request.headers.get(
                    "x-stainless-retry-count"
                ),
                "body_hash": _sha256(body),
                "parameters": {
                    key: payload[key]
                    for key in (
                        "model",
                        "temperature",
                        "top_p",
                        "seed",
                        "max_tokens",
                        "max_completion_tokens",
                        "response_format",
                    )
                    if key in payload
                },
                "omitted_parameters": [
                    key
                    for key in (
                        "top_p",
                        "seed",
                        "max_tokens",
                        "max_completion_tokens",
                    )
                    if key not in payload
                ],
            }
        )

    def _on_response(self, response: httpx.Response) -> None:
        if self._current is None:
            return
        try:
            raw_body = response.read().decode("utf-8", errors="replace")
        except Exception as error:  # Diagnostic capture must not alter the API call.
            raw_body = f"<response capture failed: {type(error).__name__}: {error}>"
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            payload = {}
        choice = (payload.get("choices") or [{}])[0]
        self._current["http_responses"].append(
            {
                "status_code": response.status_code,
                "raw_body": raw_body,
                "body_hash": _sha256(raw_body),
                "finish_reason": choice.get("finish_reason"),
                "model": payload.get("model"),
                "usage": payload.get("usage"),
            }
        )


def _audit_content(trace: dict[str, Any], content: str) -> None:
    if not content:
        trace["raw_response_state"] = "empty_content"
        trace["parse_state"] = "not_parsed"
        return
    try:
        candidates, invalid = _parse_candidates(content)
    except ClaimGenerationResponseError as error:
        trace["raw_response_state"] = "invalid_structured_output"
        trace["parse_state"] = f"error: {error}"
        return
    trace["parsed_candidate_count"] = len(candidates)
    trace["invalid_claim_count"] = len(invalid)
    trace["parse_state"] = "success"
    trace["raw_response_state"] = (
        "valid_json_empty_claims"
        if not candidates and not invalid
        else "valid_json_with_claims"
    )


def _retrieval_fingerprint(user_prompt: str) -> str | None:
    try:
        payload = json.loads(user_prompt.split("\n", 1)[1])
        chunks = payload["sec_chunks"]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        return None
    serialized = json.dumps(
        chunks,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _sha256(serialized)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _case() -> dict[str, Any]:
    return next(case for case in load_frozen_cases() if case["case_id"] == CASE_ID)


def run_diagnostic(
    *,
    runs: int = DEFAULT_RUNS,
    results_path: Path = RESULTS_PATH,
) -> dict[str, Any]:
    if runs <= 0:
        raise ValueError("runs must be greater than zero")
    case = _case()
    retriever = HybridRetriever(
        BM25Retriever(),
        VectorRetriever(
            DashScopeEmbeddingClient(),
            collection_name=COLLECTION_NAME,
        ),
    )
    provider = TracingClaimProvider()
    support_provider = DeepSeekClaimSupportProvider()
    client = provider.provider._client
    payload: dict[str, Any] = {
        "title": "SEC QA retrieval-stage2-034 Claim Generation Diagnostic",
        "started_at": datetime.now(UTC).isoformat(),
        "case": case,
        "config": {
            "runs": runs,
            "qa_top_k": QA_TOP_K,
            "vector_collection": COLLECTION_NAME,
            "model": provider.model_name,
            "client_timeout": str(client.timeout),
            "client_max_retries": client.max_retries,
            "retrieval_changed": False,
            "generation_prompt_changed": False,
            "fallback_changed": False,
        },
        "runs": [],
        "run_status": "running",
    }
    _write_json(payload, results_path)

    for run_number in range(1, runs + 1):
        trace_offset = len(provider.traces)
        result = answer_sec_question(
            case["question"],
            case["ticker"],
            form_type=case["form_type"],
            top_k=QA_TOP_K,
            retriever=retriever,
            claim_provider=provider,
            support_provider=support_provider,
        )
        traces = provider.traces[trace_offset:]
        generation = next(
            trace for trace in traces if trace["call_kind"] == "claim_generation"
        )
        retrieved = result["retrieved_chunks"]
        rejected = result["rejected_claims"]
        record = {
            "run": run_number,
            "retrieved_parent_ids": [
                item.get("logical_parent_id") or item["chunk_id"] for item in retrieved
            ],
            "retrieved_child_ids": [item["chunk_id"] for item in retrieved],
            "retrieved_metadata": [
                {
                    key: item.get(key)
                    for key in (
                        "chunk_id",
                        "logical_parent_id",
                        "ticker",
                        "form_type",
                        "accession",
                        "section",
                        "title",
                    )
                }
                for item in retrieved
            ],
            "retrieval_fingerprint": generation["retrieval_fingerprint"],
            "prompt_hash": generation["prompt_hash"],
            "generation_trace": generation,
            "additional_generation_calls": traces[1:],
            "candidate_claim_count": len(result["candidate_claims"]),
            "initial_grounding_pass_count": sum(
                item.get("initial_layer_a_result") == "exact_match"
                for item in result["candidate_claims"]
            ),
            "supported_count": len(result["verified_claims"]),
            "partial_count": sum(
                item.get("layer_b") == "partial" for item in rejected
            ),
            "unsupported_count": sum(
                item.get("layer_b") == "unsupported" for item in rejected
            ),
            "rejected_claims": rejected,
            "final_status": result["status"],
            "final_output_mode": result["final_output_mode"],
            "pipeline_error": result["error"],
        }
        payload["runs"].append(record)
        _write_json(payload, results_path)
        print(
            f"[{run_number}/{runs}] raw={generation['raw_response_state']} "
            f"parse={generation['parse_state']} "
            f"candidates={record['candidate_claim_count']} "
            f"supported={record['supported_count']} final={record['final_status']}"
        )

    payload["finished_at"] = datetime.now(UTC).isoformat()
    payload["run_status"] = "complete"
    payload["summary"] = _summary(payload["runs"])
    _write_json(payload, results_path)
    return payload


def _summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "runs": len(runs),
        "distinct_retrieval_fingerprints": len(
            {item["retrieval_fingerprint"] for item in runs}
        ),
        "distinct_prompt_hashes": len({item["prompt_hash"] for item in runs}),
        "empty_claim_runs": sum(item["candidate_claim_count"] == 0 for item in runs),
        "parse_error_runs": sum(
            item["generation_trace"]["parse_state"] != "success" for item in runs
        ),
        "api_error_runs": sum(item["pipeline_error"] is not None for item in runs),
        "truncated_runs": sum(
            bool(item["generation_trace"]["truncated"]) for item in runs
        ),
        "sdk_retry_count": sum(
            int(item["generation_trace"]["sdk_retry_count"]) for item in runs
        ),
        "candidate_claim_counts": [item["candidate_claim_count"] for item in runs],
        "final_statuses": [item["final_status"] for item in runs],
    }


def _write_json(payload: dict[str, Any], path: Path) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--results", type=Path, default=RESULTS_PATH)
    return parser


def main() -> None:
    args = _parser().parse_args()
    payload = run_diagnostic(runs=args.runs, results_path=args.results)
    print(json.dumps(payload["summary"], indent=2))


if __name__ == "__main__":
    main()
