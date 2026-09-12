"""Tests for SEC Citation Layer B claim-support verification."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from thesis_tracker.retrieve.claim_support import (
    SYSTEM_PROMPT,
    ClaimSupportResponseError,
    verify_sec_claim,
)


class FakeProvider:
    model_name = "fake-sec-entailment"

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def classify(self, *, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self.response


def response(
    status: str,
    reason: str = "Test classification.",
    confidence: str = "high",
) -> str:
    return json.dumps(
        {"status": status, "reason": reason, "confidence": confidence}
    )


@pytest.fixture
def sec_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "corpus.db"
    evidence = (
        "Revenue increased 70% year over year. "
        "Revenue increased, while operating expenses also increased. "
        "We expect demand to remain strong next quarter. "
        "Supply constraints may adversely affect future revenue. "
        "Management believes the market opportunity is substantial. "
        "There was no material change in inventory during the quarter. "
        "Data Center revenue increased 40% year over year."
    )
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                accession TEXT PRIMARY KEY,
                ingestion_status TEXT NOT NULL
            );
            CREATE TABLE chunks (
                chunk_id TEXT PRIMARY KEY,
                accession TEXT NOT NULL,
                text TEXT NOT NULL,
                span_verified INTEGER NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO documents VALUES ('filing-a', 'success')"
        )
        connection.execute(
            "INSERT INTO chunks VALUES ('chunk-a', 'filing-a', ?, 1)",
            (evidence,),
        )
    return db_path


def run_case(
    sec_db: Path,
    *,
    claim: str,
    evidence: str,
    status: str,
) -> tuple[dict[str, object], FakeProvider]:
    provider = FakeProvider(response(status))
    result = verify_sec_claim(
        claim,
        "chunk-a",
        evidence,
        db_path=sec_db,
        provider=provider,
    )
    return result, provider


def test_exact_factual_support_is_supported(sec_db: Path) -> None:
    result, provider = run_case(
        sec_db,
        claim="Revenue increased 70% year over year.",
        evidence="Revenue increased 70% year over year.",
        status="supported",
    )

    assert result["grounding_valid"] is True
    assert result["support_status"] == "supported"
    assert result["confidence"] == "high"
    assert len(provider.calls) == 1


def test_partially_supported_claim_is_partial(sec_db: Path) -> None:
    result, _ = run_case(
        sec_db,
        claim="Revenue and profit increased.",
        evidence="Revenue increased, while operating expenses also increased.",
        status="partial",
    )
    assert result["support_status"] == "partial"


def test_unrelated_claim_is_unsupported(sec_db: Path) -> None:
    result, _ = run_case(
        sec_db,
        claim="The company repurchased common stock.",
        evidence="Revenue increased 70% year over year.",
        status="unsupported",
    )
    assert result["support_status"] == "unsupported"


def test_number_mismatch_is_unsupported(sec_db: Path) -> None:
    result, _ = run_case(
        sec_db,
        claim="Revenue increased 80% year over year.",
        evidence="Revenue increased 70% year over year.",
        status="unsupported",
    )
    assert result["support_status"] == "unsupported"


def test_actual_vs_expected_is_unsupported(sec_db: Path) -> None:
    result, _ = run_case(
        sec_db,
        claim="Demand remained strong during the quarter.",
        evidence="We expect demand to remain strong next quarter.",
        status="unsupported",
    )
    assert result["support_status"] == "unsupported"


def test_may_vs_will_is_unsupported(sec_db: Path) -> None:
    result, _ = run_case(
        sec_db,
        claim="Supply constraints will reduce revenue.",
        evidence="Supply constraints may adversely affect future revenue.",
        status="unsupported",
    )
    assert result["support_status"] == "unsupported"


def test_management_belief_is_not_promoted_to_objective_fact(
    sec_db: Path,
) -> None:
    result, _ = run_case(
        sec_db,
        claim="The market opportunity is objectively substantial.",
        evidence="Management believes the market opportunity is substantial.",
        status="partial",
    )
    assert result["support_status"] == "partial"


def test_negation_is_not_ignored(sec_db: Path) -> None:
    result, _ = run_case(
        sec_db,
        claim="Inventory changed materially during the quarter.",
        evidence="There was no material change in inventory during the quarter.",
        status="unsupported",
    )
    assert result["support_status"] == "unsupported"


def test_broader_scope_is_partial(sec_db: Path) -> None:
    result, _ = run_case(
        sec_db,
        claim="Company-wide revenue increased 40% year over year.",
        evidence="Data Center revenue increased 40% year over year.",
        status="partial",
    )
    assert result["support_status"] == "partial"


def test_layer_a_failure_never_calls_provider(sec_db: Path) -> None:
    provider = FakeProvider(response("supported"))

    result = verify_sec_claim(
        "Revenue increased 70% year over year.",
        "wrong-chunk",
        "Revenue increased 70% year over year.",
        db_path=sec_db,
        provider=provider,
    )

    assert result["grounding_valid"] is False
    assert result["support_status"] is None
    assert result["confidence"] is None
    assert result["reason"] == "grounding_failed"
    assert result["grounding_reason"] == "chunk_not_found"
    assert provider.calls == []


def test_empty_claim_is_invalid_and_does_not_call_provider(sec_db: Path) -> None:
    provider = FakeProvider(response("supported"))

    with pytest.raises(ValueError, match="claim must not be empty"):
        verify_sec_claim(
            "  ",
            "chunk-a",
            "Revenue increased 70% year over year.",
            db_path=sec_db,
            provider=provider,
        )

    assert provider.calls == []


def test_invalid_json_raises_explicit_error(sec_db: Path) -> None:
    provider = FakeProvider("not-json")

    with pytest.raises(ClaimSupportResponseError, match="invalid JSON"):
        verify_sec_claim(
            "Revenue increased.",
            "chunk-a",
            "Revenue increased 70% year over year.",
            db_path=sec_db,
            provider=provider,
        )


def test_invalid_status_raises_explicit_error(sec_db: Path) -> None:
    provider = FakeProvider(response("mostly_supported"))

    with pytest.raises(ClaimSupportResponseError, match="invalid support status"):
        verify_sec_claim(
            "Revenue increased.",
            "chunk-a",
            "Revenue increased 70% year over year.",
            db_path=sec_db,
            provider=provider,
        )


def test_invalid_confidence_raises_explicit_error(sec_db: Path) -> None:
    provider = FakeProvider(response("supported", confidence="0.95"))

    with pytest.raises(ClaimSupportResponseError, match="invalid confidence"):
        verify_sec_claim(
            "Revenue increased.",
            "chunk-a",
            "Revenue increased 70% year over year.",
            db_path=sec_db,
            provider=provider,
        )


def test_prompt_contains_sec_strictness_and_metadata(sec_db: Path) -> None:
    provider = FakeProvider(response("supported"))

    verify_sec_claim(
        "Revenue increased 70% year over year.",
        "chunk-a",
        "Revenue increased 70% year over year.",
        db_path=sec_db,
        provider=provider,
        chunk_title="Part I, Item 2",
        section="MD&A",
        ticker="NVDA",
        accession="filing-a",
        form_type="10-Q",
    )

    system_prompt, user_prompt = provider.calls[0]
    assert system_prompt == SYSTEM_PROMPT
    assert "not semantic similarity" in system_prompt
    assert "forecasts and expectations" in system_prompt
    assert "Preserve negation" in system_prompt
    assert '"ticker": "NVDA"' in user_prompt
    assert '"chunk_title": "Part I, Item 2"' in user_prompt
