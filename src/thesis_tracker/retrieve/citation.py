"""Citation Layer A: deterministic grounding against saved SEC chunks."""

from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path
from typing import Literal, TypedDict

from thesis_tracker.ingest.sec_adapter import normalize

DEFAULT_DB_PATH = Path("data/corpus.db")
_WHITESPACE_RE = re.compile(r"\s+")

CitationReason = Literal[
    "exact_match",
    "evidence_not_found",
    "chunk_not_found",
    "empty_evidence",
]


class CitationVerificationResult(TypedDict):
    """JSON-compatible result from source-grounding verification."""

    valid: bool
    reason: CitationReason
    chunk_id: str
    evidence_text: str


def normalize_for_citation(text: str) -> str:
    """Apply Stage 1 normalization and canonicalize whitespace boundaries."""
    return _WHITESPACE_RE.sub(" ", normalize(text)).strip()


def verify_evidence(
    evidence_chunk_id: str,
    evidence_text: str,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> CitationVerificationResult:
    """Verify that evidence occurs verbatim after normalization in its chunk.

    This is source grounding only. It deliberately does not evaluate whether
    the evidence supports a claim.
    """
    normalized_evidence = normalize_for_citation(evidence_text)
    if not normalized_evidence:
        return _result(
            valid=False,
            reason="empty_evidence",
            chunk_id=evidence_chunk_id,
            evidence_text=evidence_text,
        )

    path = Path(db_path)
    if not path.is_file():
        return _result(
            valid=False,
            reason="chunk_not_found",
            chunk_id=evidence_chunk_id,
            evidence_text=evidence_text,
        )

    database_uri = f"{path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as connection:
        row = connection.execute(
            """
            SELECT c.text
            FROM chunks AS c
            JOIN documents AS d ON d.accession = c.accession
            WHERE c.chunk_id = ?
              AND d.ingestion_status = 'success'
              AND c.span_verified = 1
            """,
            (evidence_chunk_id,),
        ).fetchone()

    if row is None:
        return _result(
            valid=False,
            reason="chunk_not_found",
            chunk_id=evidence_chunk_id,
            evidence_text=evidence_text,
        )

    normalized_chunk = normalize_for_citation(str(row[0]))
    if normalized_evidence in normalized_chunk:
        return _result(
            valid=True,
            reason="exact_match",
            chunk_id=evidence_chunk_id,
            evidence_text=evidence_text,
        )
    return _result(
        valid=False,
        reason="evidence_not_found",
        chunk_id=evidence_chunk_id,
        evidence_text=evidence_text,
    )


def _result(
    *,
    valid: bool,
    reason: CitationReason,
    chunk_id: str,
    evidence_text: str,
) -> CitationVerificationResult:
    return {
        "valid": valid,
        "reason": reason,
        "chunk_id": chunk_id,
        "evidence_text": evidence_text,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify that cited evidence occurs in a saved SEC chunk.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_parser = subparsers.add_parser(
        "verify",
        help="Run deterministic Layer A source grounding.",
    )
    verify_parser.add_argument("--chunk-id", required=True)
    verify_parser.add_argument("--evidence", required=True)
    return parser


def _main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    result = verify_evidence(
        args.chunk_id,
        args.evidence,
        db_path=args.db,
    )
    if result["valid"]:
        print("PASS")
        return
    print(f"REJECT: {result['reason']}")


if __name__ == "__main__":
    _main()
