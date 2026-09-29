from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from thesis_tracker.financial.ai_cache import (
    ProposalCacheError,
    ProposalCacheKey,
    SqliteConceptProposalCache,
)
from thesis_tracker.financial.ai_concepts import (
    ConceptProviderResponse,
    parse_concept_proposal,
    sha256_text,
)


def raw_proposal(status: str) -> str:
    candidates = (
        [
            {
                "concept": "test:Cost",
                "semantic_relation": "equivalent",
                "reason": "aggregate",
                "evidence_fields": ["label"],
            }
        ]
        if status == "candidate"
        else []
    )
    return json.dumps(
        {
            "target": "cost_of_revenue",
            "status": status,
            "candidate_concepts": candidates,
        }
    )


def cache_key(**overrides: object) -> ProposalCacheKey:
    values = {
        "accession": "acc-1",
        "target": "cost_of_revenue",
        "input_payload": {"candidate": "test:Cost"},
        "prompt_version": "v1",
        "model_name": "model-a",
    }
    values.update(overrides)
    return ProposalCacheKey.build(**values)


@pytest.mark.parametrize(
    "status", ["candidate", "no_match", "insufficient_evidence"]
)
def test_cache_round_trip_preserves_audit_fields(
    tmp_path: Path, status: str
) -> None:
    raw = raw_proposal(status)
    response = ConceptProviderResponse(
        raw_response=raw,
        model_name="model-a",
        prompt_tokens=11,
        completion_tokens=7,
        total_tokens=18,
    )
    proposal = parse_concept_proposal(raw, expected_target="cost_of_revenue")
    key = cache_key()
    created_at = datetime(2026, 9, 19, 12, 30, tzinfo=timezone.utc)
    cache = SqliteConceptProposalCache(tmp_path / "nested" / "cache.sqlite3")

    cache.put(key, response, proposal, created_at=created_at)
    cached = cache.get(key)

    assert cached is not None
    assert cached.key == key
    assert cached.raw_response == raw
    assert cached.response_hash == sha256_text(raw)
    assert cached.proposal == proposal
    assert cached.created_at == created_at
    assert cached.prompt_tokens == 11
    assert cached.completion_tokens == 7
    assert cached.total_tokens == 18


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("accession", "acc-2"),
        ("target", "gross_profit_revenue"),
        ("input_payload", {"candidate": "test:Other"}),
        ("prompt_version", "v2"),
        ("model_name", "model-b"),
    ],
)
def test_cache_key_isolates_every_material_input(
    tmp_path: Path, field: str, changed: object
) -> None:
    raw = raw_proposal("candidate")
    original = cache_key()
    cache = SqliteConceptProposalCache(tmp_path / "cache.sqlite3")
    cache.put(
        original,
        ConceptProviderResponse(raw, "model-a"),
        parse_concept_proposal(raw, expected_target="cost_of_revenue"),
        created_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    assert cache.get(cache_key(**{field: changed})) is None


def test_corrupt_cached_proposal_fails_closed(tmp_path: Path) -> None:
    raw = raw_proposal("candidate")
    key = cache_key()
    path = tmp_path / "cache.sqlite3"
    cache = SqliteConceptProposalCache(path)
    cache.put(
        key,
        ConceptProviderResponse(raw, "model-a"),
        parse_concept_proposal(raw, expected_target="cost_of_revenue"),
        created_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE concept_proposal_cache SET proposal_json = ? WHERE key_hash = ?",
            ("not-json", key.key_hash),
        )

    with pytest.raises(ProposalCacheError, match="cached proposal"):
        cache.get(key)


def test_repeated_lookup_returns_cached_response(tmp_path: Path) -> None:
    raw = raw_proposal("no_match")
    key = cache_key()
    cache = SqliteConceptProposalCache(tmp_path / "cache.sqlite3")
    cache.put(
        key,
        ConceptProviderResponse(raw, "model-a"),
        parse_concept_proposal(raw, expected_target="cost_of_revenue"),
        created_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    first = cache.get(key)
    second = cache.get(key)

    assert first == second
    assert second is not None
    assert second.proposal.status.value == "no_match"
