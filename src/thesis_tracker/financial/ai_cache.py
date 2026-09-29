"""Standalone SQLite cache for non-authoritative AI concept proposals."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from thesis_tracker.financial.ai_concepts import (
    ConceptProposal,
    ConceptProviderResponse,
    canonical_json,
    parse_concept_proposal,
    sha256_text,
)


class ProposalCacheError(ValueError):
    """Cached audit data failed integrity or schema validation."""


@dataclass(frozen=True, slots=True)
class ProposalCacheKey:
    key_hash: str
    accession: str
    target: str
    input_json: str
    input_hash: str
    prompt_version: str
    model_name: str

    @classmethod
    def build(
        cls,
        *,
        accession: str,
        target: str,
        input_payload: object,
        prompt_version: str,
        model_name: str,
    ) -> ProposalCacheKey:
        input_json = canonical_json(input_payload)
        input_hash = sha256_text(input_json)
        material = canonical_json(
            {
                "accession": accession,
                "target": target,
                "input_hash": input_hash,
                "prompt_version": prompt_version,
                "model_name": model_name,
            }
        )
        return cls(
            key_hash=sha256_text(material),
            accession=accession,
            target=target,
            input_json=input_json,
            input_hash=input_hash,
            prompt_version=prompt_version,
            model_name=model_name,
        )


@dataclass(frozen=True, slots=True)
class CachedConceptProposal:
    key: ProposalCacheKey
    raw_response: str
    response_hash: str
    proposal: ConceptProposal
    created_at: datetime
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


class SqliteConceptProposalCache:
    """Cache model proposals; deterministic validation always runs again."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS concept_proposal_cache (
                    key_hash TEXT PRIMARY KEY,
                    accession TEXT NOT NULL,
                    target TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    raw_response TEXT NOT NULL,
                    response_hash TEXT NOT NULL,
                    proposal_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    total_tokens INTEGER
                )
                """
            )

    def get(self, key: ProposalCacheKey) -> CachedConceptProposal | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                """
                SELECT accession, target, input_json, input_hash, prompt_version,
                       model_name, raw_response, response_hash, proposal_json,
                       created_at, prompt_tokens, completion_tokens, total_tokens
                FROM concept_proposal_cache
                WHERE key_hash = ?
                """,
                (key.key_hash,),
            ).fetchone()
        if row is None:
            return None
        stored_key = ProposalCacheKey(
            key_hash=key.key_hash,
            accession=str(row[0]),
            target=str(row[1]),
            input_json=str(row[2]),
            input_hash=str(row[3]),
            prompt_version=str(row[4]),
            model_name=str(row[5]),
        )
        if stored_key != key:
            raise ProposalCacheError("cache key material does not match key hash")
        raw_response = str(row[6])
        response_hash = str(row[7])
        if sha256_text(raw_response) != response_hash:
            raise ProposalCacheError("cached response hash is invalid")
        try:
            proposal = parse_concept_proposal(
                str(row[8]), expected_target=key.target
            )
            created_at = datetime.fromisoformat(str(row[9]))
        except Exception as error:
            raise ProposalCacheError("cached proposal is invalid") from error
        return CachedConceptProposal(
            key=stored_key,
            raw_response=raw_response,
            response_hash=response_hash,
            proposal=proposal,
            created_at=created_at,
            prompt_tokens=row[10],
            completion_tokens=row[11],
            total_tokens=row[12],
        )

    def put(
        self,
        key: ProposalCacheKey,
        response: ConceptProviderResponse,
        proposal: ConceptProposal,
        *,
        created_at: datetime,
    ) -> None:
        if response.model_name != key.model_name:
            raise ProposalCacheError("provider model does not match cache key")
        proposal_json = canonical_json(proposal)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO concept_proposal_cache (
                    key_hash, accession, target, input_json, input_hash,
                    prompt_version, model_name, raw_response, response_hash,
                    proposal_json, created_at, prompt_tokens, completion_tokens,
                    total_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key.key_hash,
                    key.accession,
                    key.target,
                    key.input_json,
                    key.input_hash,
                    key.prompt_version,
                    key.model_name,
                    response.raw_response,
                    sha256_text(response.raw_response),
                    proposal_json,
                    created_at.isoformat(),
                    response.prompt_tokens,
                    response.completion_tokens,
                    response.total_tokens,
                ),
            )
