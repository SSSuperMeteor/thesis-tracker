from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from thesis_tracker.financial.ai_concepts import (
    PROMPT_VERSION,
    ConceptProposalResponseError,
    DeepSeekConceptProposalProvider,
    ProposalStatus,
    SemanticRelation,
    build_proposal_prompts,
    canonical_json,
    parse_concept_proposal,
    sha256_text,
)


def proposal_json(
    *, status: str = "candidate", relation: str = "equivalent"
) -> str:
    candidates = (
        [
            {
                "concept": "issuer:CostOfRevenue",
                "semantic_relation": relation,
                "reason": "Presented as aggregate cost of revenue.",
                "evidence_fields": ["label", "parent_concept"],
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


@pytest.mark.parametrize(
    "status",
    ["candidate", "no_match", "insufficient_evidence"],
)
def test_parses_allowed_structured_proposal_statuses(status: str) -> None:
    proposal = parse_concept_proposal(
        proposal_json(status=status), expected_target="cost_of_revenue"
    )

    assert proposal.status is ProposalStatus(status)
    if status == "candidate":
        assert proposal.candidate_concepts[0].semantic_relation is (
            SemanticRelation.EQUIVALENT
        )
    else:
        assert proposal.candidate_concepts == ()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(approved=True),
        lambda payload: payload["candidate_concepts"][0].update(value="60"),
        lambda payload: payload.update(extra="not allowed"),
        lambda payload: payload["candidate_concepts"][0].update(
            evidence_fields=[]
        ),
        lambda payload: payload["candidate_concepts"].append(
            dict(payload["candidate_concepts"][0])
        ),
        lambda payload: payload["candidate_concepts"][0].update(
            semantic_relation="maybe"
        ),
    ],
)
def test_rejects_authority_fields_and_malformed_candidates(mutate) -> None:
    payload = json.loads(proposal_json())
    mutate(payload)

    with pytest.raises((ConceptProposalResponseError, ValidationError)):
        parse_concept_proposal(
            json.dumps(payload), expected_target="cost_of_revenue"
        )


def test_rejects_response_for_another_target() -> None:
    with pytest.raises(ConceptProposalResponseError, match="target"):
        parse_concept_proposal(
            proposal_json(), expected_target="gross_profit_revenue"
        )


def test_prompt_is_versioned_limited_json_and_denies_numeric_authority() -> None:
    payload = {
        "ticker": "TEST",
        "accession": "acc-1",
        "target": "cost_of_revenue",
        "candidates": [{"concept": "test:Cost", "label": "Cost"}],
    }

    system_prompt, user_prompt = build_proposal_prompts(payload)

    assert PROMPT_VERSION in system_prompt
    assert "must not return numeric facts" in system_prompt.lower()
    assert "must not approve" in system_prompt.lower()
    assert user_prompt.endswith(canonical_json(payload))
    assert "unrelated" not in user_prompt
    assert sha256_text("abc") == (
        "ba7816bf8f01cfea414140de5dae2223"
        "b00361a396177a9cb410ff61f20015ad"
    )


class FakeCompletions:
    def __init__(self, content: str | None) -> None:
        self.content = content
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
            usage=SimpleNamespace(
                prompt_tokens=101,
                completion_tokens=23,
                total_tokens=124,
            ),
        )


class FakeClient:
    def __init__(self, content: str | None) -> None:
        self.completions = FakeCompletions(content)
        self.chat = SimpleNamespace(completions=self.completions)


def test_provider_returns_raw_response_and_token_usage() -> None:
    client = FakeClient(proposal_json())
    provider = DeepSeekConceptProposalProvider(
        api_key="test-only-key",
        model_name="test-model",
        client=client,
    )

    response = provider.propose(system_prompt="system", user_prompt="user")

    assert response.raw_response == proposal_json()
    assert response.model_name == "test-model"
    assert response.prompt_tokens == 101
    assert response.completion_tokens == 23
    assert response.total_tokens == 124
    assert client.completions.calls[0]["temperature"] == 0
    assert client.completions.calls[0]["response_format"] == {
        "type": "json_object"
    }
    assert "test-only-key" not in repr(response)


def test_provider_rejects_empty_content() -> None:
    provider = DeepSeekConceptProposalProvider(
        api_key="test-only-key",
        client=FakeClient(None),
    )

    with pytest.raises(ConceptProposalResponseError, match="empty"):
        provider.propose(system_prompt="system", user_prompt="user")
