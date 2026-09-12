"""Tests for the isolated retrieval-stage2-034 diagnostic helpers."""

from thesis_tracker.evaluation.sec_qa_034_diagnostic import (
    _audit_content,
    _retrieval_fingerprint,
)


def test_audit_content_distinguishes_real_empty_claims() -> None:
    trace = {
        "raw_response_state": None,
        "parse_state": None,
        "parsed_candidate_count": None,
        "invalid_claim_count": None,
    }

    _audit_content(trace, '{"claims":[]}')

    assert trace["raw_response_state"] == "valid_json_empty_claims"
    assert trace["parse_state"] == "success"
    assert trace["parsed_candidate_count"] == 0
    assert trace["invalid_claim_count"] == 0


def test_retrieval_fingerprint_includes_order_and_text() -> None:
    prefix = "instruction\n"
    first = prefix + '{"sec_chunks":[{"chunk_id":"a","text":"one"}]}'
    same = prefix + '{"sec_chunks": [{"chunk_id": "a", "text": "one"}]}'
    changed = prefix + '{"sec_chunks":[{"chunk_id":"a","text":"two"}]}'

    assert _retrieval_fingerprint(first) == _retrieval_fingerprint(same)
    assert _retrieval_fingerprint(first) != _retrieval_fingerprint(changed)
