"""Integrity tests for section-aware SEC overlap chunking v2."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from thesis_tracker.ingest.rechunk_v2 import rechunk_database
from thesis_tracker.ingest.sec_adapter import (
    CHUNKING_VERSION,
    CanonicalDoc,
    Chunk,
    ChunkingConfig,
    apply_chunking_v2,
    save,
    sha256,
    split_text_spans,
)

TEST_CONFIG = ChunkingConfig(
    target_chunk_chars=100,
    overlap_chars=20,
    end_boundary_window=20,
    overlap_boundary_window=5,
)


def base_chunk(
    text: str,
    *,
    chunk_id: str = "accession-a::part_i_item_2",
    section_key: str = "part_i_item_2",
    start: int = 0,
) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        doc_hash="hash",
        kind="section",
        section_key=section_key,
        title=section_key,
        text=text,
        text_hash=sha256(text),
        char_span=(start, start + len(text)),
        span_verified=True,
        extraction_method="edgartools_exact",
    )


def test_long_section_generates_multiple_chunks() -> None:
    chunks = apply_chunking_v2([base_chunk("A" * 350)], TEST_CONFIG)

    assert len(chunks) > 1


def test_adjacent_chunks_have_overlap() -> None:
    chunks = apply_chunking_v2([base_chunk("A" * 350)], TEST_CONFIG)

    for left, right in zip(chunks, chunks[1:]):
        overlap = left.char_span[1] - right.char_span[0]
        assert overlap > 0
        assert left.text[-overlap:] == right.text[:overlap]


def test_overlap_size_stays_near_configured_value() -> None:
    text = ("A" * 84 + ".\n") * 6
    chunks = apply_chunking_v2([base_chunk(text)], TEST_CONFIG)

    overlaps = [
        left.char_span[1] - right.char_span[0]
        for left, right in zip(chunks, chunks[1:])
    ]
    assert overlaps
    assert all(15 <= overlap <= 25 for overlap in overlaps)


def test_overlap_does_not_cross_sections() -> None:
    first = base_chunk("A" * 240, start=0)
    second = base_chunk(
        "B" * 240,
        chunk_id="accession-a::part_ii_item_1a",
        section_key="part_ii_item_1a",
        start=240,
    )
    chunks = apply_chunking_v2([first, second], TEST_CONFIG)
    first_children = [c for c in chunks if c.section_key == "part_i_item_2"]
    second_children = [c for c in chunks if c.section_key == "part_ii_item_1a"]

    assert first_children[-1].char_span[1] <= second_children[0].char_span[0]
    assert all(set(child.text) == {"A"} for child in first_children)
    assert all(set(child.text) == {"B"} for child in second_children)


def test_overlap_does_not_cross_accessions() -> None:
    chunks = apply_chunking_v2(
        [
            base_chunk("A" * 240),
            base_chunk(
                "B" * 240,
                chunk_id="accession-b::part_i_item_2",
            ),
        ],
        TEST_CONFIG,
    )

    assert all(
        set(chunk.text) == ({"A"} if "accession-a" in chunk.chunk_id else {"B"})
        for chunk in chunks
    )


def test_short_section_is_not_split() -> None:
    chunks = apply_chunking_v2([base_chunk("Short section.")], TEST_CONFIG)

    assert len(chunks) == 1
    assert chunks[0].text == "Short section."


def test_paragraph_boundary_is_preferred_near_target() -> None:
    text = "A" * 90 + "\n\n" + "B" * 150

    spans = split_text_spans(text, TEST_CONFIG)

    assert spans[0][1] == 92


def test_char_spans_are_exact_canonical_slices() -> None:
    prefix = "ignored-prefix"
    text = ("Revenue row 100 90.\n" * 15).strip()
    base = base_chunk(text, start=len(prefix))
    canonical = prefix + text

    chunks = apply_chunking_v2([base], TEST_CONFIG)

    assert all(
        canonical[chunk.char_span[0] : chunk.char_span[1]] == chunk.text
        for chunk in chunks
    )


def test_chunk_ids_are_deterministic_and_indexed() -> None:
    base = base_chunk("A" * 350)

    first = apply_chunking_v2([base], TEST_CONFIG)
    second = apply_chunking_v2([base], TEST_CONFIG)

    assert [chunk.chunk_id for chunk in first] == [
        chunk.chunk_id for chunk in second
    ]
    assert first[0].chunk_id.endswith("::chunk_000")
    assert first[1].chunk_id.endswith("::chunk_001")


def test_rechunk_and_reingest_are_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "corpus.db"
    text = "A" * 7000
    doc = CanonicalDoc(
        cik="0000000001",
        ticker="TEST",
        form_type="10-Q",
        accession="accession-a",
        period_end="2026-06-30",
        filing_date="2026-07-31",
        fetched_at="2026-07-31T00:00:00+00:00",
        doc_hash="hash",
        parser_version="parser",
        normalizer_version="norm-1",
        schema_version=3,
        full_text=text,
        chunks=[base_chunk(text)],
    )
    save(doc, db_path)

    first = rechunk_database(db_path, backup_path=None)
    second = rechunk_database(db_path, backup_path=None)
    with sqlite3.connect(db_path) as connection:
        row_count = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        distinct_count = connection.execute(
            "SELECT COUNT(DISTINCT chunk_id) FROM chunks"
        ).fetchone()[0]

    assert first.new_chunk_count > first.old_chunk_count
    assert second.already_v2 is True
    assert second.new_chunk_count == first.new_chunk_count
    assert row_count == distinct_count == first.new_chunk_count


def test_every_chunk_records_chunking_version() -> None:
    chunks = apply_chunking_v2([base_chunk("A" * 350)], TEST_CONFIG)

    assert all(CHUNKING_VERSION in chunk.extraction_method for chunk in chunks)
