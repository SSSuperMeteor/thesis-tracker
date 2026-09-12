"""Re-chunk saved SEC canonical spans with section-aware overlap."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from thesis_tracker.ingest.sec_adapter import (
    CHUNKING_VERSION,
    DB_PATH,
    Chunk,
    apply_chunking_v2,
)

DEFAULT_BACKUP_PATH = Path("data/corpus_pre_chunkv2.db")


@dataclass(frozen=True)
class RechunkStats:
    """Integrity and count results for one database re-chunk pass."""

    already_v2: bool
    old_chunk_count: int
    new_chunk_count: int
    old_counts_by_ticker: dict[str, int]
    new_counts_by_ticker: dict[str, int]
    duplicate_chunk_id_count: int
    invalid_span_count: int


def rechunk_database(
    db_path: Path = DB_PATH,
    *,
    backup_path: Path | None = DEFAULT_BACKUP_PATH,
) -> RechunkStats:
    """Atomically replace v1 chunks while preserving documents and raw filings."""
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    documents = connection.execute(
        """
        SELECT accession, ticker
        FROM documents
        WHERE ingestion_status = 'success'
        ORDER BY ticker, filing_date, accession
        """
    ).fetchall()
    old_rows = connection.execute(
        """
        SELECT c.*, d.ticker
        FROM chunks AS c
        JOIN documents AS d ON d.accession = c.accession
        WHERE d.ingestion_status = 'success'
        ORDER BY d.ticker, d.filing_date, c.ord, c.chunk_id
        """
    ).fetchall()
    old_counts = Counter(str(row["ticker"]) for row in old_rows)
    version_flags = [CHUNKING_VERSION in str(row["extraction_method"]) for row in old_rows]
    if version_flags and all(version_flags):
        count = len(old_rows)
        connection.close()
        return RechunkStats(
            already_v2=True,
            old_chunk_count=count,
            new_chunk_count=count,
            old_counts_by_ticker=dict(old_counts),
            new_counts_by_ticker=dict(old_counts),
            duplicate_chunk_id_count=_duplicate_count(row["chunk_id"] for row in old_rows),
            invalid_span_count=0,
        )
    if any(version_flags):
        connection.close()
        raise RuntimeError("database contains mixed v1 and v2 chunks")

    if backup_path is not None and not backup_path.exists():
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        connection.commit()
        shutil.copy2(db_path, backup_path)

    rows_by_accession: dict[str, list[sqlite3.Row]] = {
        str(document["accession"]): [] for document in documents
    }
    for row in old_rows:
        rows_by_accession[str(row["accession"])].append(row)

    expanded_by_accession: dict[str, list[Chunk]] = {}
    invalid_spans = 0
    all_ids: list[str] = []
    for accession, rows in rows_by_accession.items():
        base_chunks = [_row_to_chunk(row) for row in rows]
        expanded = apply_chunking_v2(base_chunks)
        expanded_by_accession[accession] = expanded
        all_ids.extend(chunk.chunk_id for chunk in expanded)
        invalid_spans += _validate_lineage(base_chunks, expanded)

    duplicate_count = _duplicate_count(all_ids)
    if duplicate_count or invalid_spans:
        connection.close()
        raise RuntimeError(
            f"rechunk integrity failed: duplicates={duplicate_count}, "
            f"invalid_spans={invalid_spans}"
        )

    with connection:
        for accession, chunks in expanded_by_accession.items():
            connection.execute("DELETE FROM chunks WHERE accession = ?", (accession,))
            connection.executemany(
                """
                INSERT INTO chunks (
                    chunk_id, accession, doc_hash, kind, section_key, title,
                    text, text_hash, span_start, span_end, span_verified,
                    extraction_method, parent_id, prev_id, next_id, ord
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        chunk.chunk_id,
                        accession,
                        chunk.doc_hash,
                        chunk.kind,
                        chunk.section_key,
                        chunk.title,
                        chunk.text,
                        chunk.text_hash,
                        chunk.char_span[0],
                        chunk.char_span[1],
                        int(chunk.span_verified),
                        chunk.extraction_method,
                        chunk.parent_id,
                        chunk.prev_id,
                        chunk.next_id,
                        chunk.order,
                    )
                    for chunk in chunks
                ],
            )
            connection.execute(
                "UPDATE documents SET n_chunks = ? WHERE accession = ?",
                (len(chunks), accession),
            )

    new_rows = connection.execute(
        """
        SELECT d.ticker, c.chunk_id
        FROM chunks AS c
        JOIN documents AS d ON d.accession = c.accession
        WHERE d.ingestion_status = 'success'
        """
    ).fetchall()
    new_counts = Counter(str(row["ticker"]) for row in new_rows)
    connection.close()
    return RechunkStats(
        already_v2=False,
        old_chunk_count=len(old_rows),
        new_chunk_count=len(new_rows),
        old_counts_by_ticker=dict(old_counts),
        new_counts_by_ticker=dict(new_counts),
        duplicate_chunk_id_count=_duplicate_count(row["chunk_id"] for row in new_rows),
        invalid_span_count=invalid_spans,
    )


def _row_to_chunk(row: sqlite3.Row) -> Chunk:
    return Chunk(
        chunk_id=str(row["chunk_id"]),
        doc_hash=str(row["doc_hash"]),
        kind=str(row["kind"]),
        section_key=str(row["section_key"]) if row["section_key"] else None,
        title=str(row["title"]),
        text=str(row["text"]),
        text_hash=str(row["text_hash"]),
        char_span=(int(row["span_start"]), int(row["span_end"])),
        span_verified=bool(row["span_verified"]),
        extraction_method=str(row["extraction_method"]),
        parent_id=str(row["parent_id"]) if row["parent_id"] else None,
        prev_id=str(row["prev_id"]) if row["prev_id"] else None,
        next_id=str(row["next_id"]) if row["next_id"] else None,
        order=int(row["ord"]),
    )


def _validate_lineage(base_chunks: list[Chunk], expanded: list[Chunk]) -> int:
    bases = {base.chunk_id: base for base in base_chunks}
    invalid = 0
    for child in expanded:
        base_id = child.chunk_id.rsplit("::chunk_", 1)[0]
        base = bases.get(base_id)
        if base is None or not child.span_verified:
            invalid += 1
            continue
        local_start = child.char_span[0] - base.char_span[0]
        local_end = child.char_span[1] - base.char_span[0]
        if (
            local_start < 0
            or local_end > len(base.text)
            or local_start >= local_end
            or base.text[local_start:local_end] != child.text
        ):
            invalid += 1
    return invalid


def _duplicate_count(values: Any) -> int:
    counts = Counter(values)
    return sum(count - 1 for count in counts.values() if count > 1)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--backup", type=Path, default=DEFAULT_BACKUP_PATH)
    return parser


def _main() -> None:
    args = _build_parser().parse_args()
    stats = rechunk_database(args.db, backup_path=args.backup)
    print(json.dumps(asdict(stats), indent=2))


if __name__ == "__main__":
    _main()
