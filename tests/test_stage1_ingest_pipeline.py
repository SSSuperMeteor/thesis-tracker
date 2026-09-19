"""Tests for the selected-filing Stage 1 ingest coordinator."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path

from thesis_tracker.ingest.filing_selection import (
    FilingFamily,
    FilingMetadata,
    SelectionRequest,
    SourceFiling,
)
from thesis_tracker.ingest.pipeline import IngestCoordinator, IngestPaths
from thesis_tracker.ingest.sec_adapter import CanonicalDoc, Chunk, sha256
from thesis_tracker.retrieve.vector import IndexStats


def source(
    accession: str,
    *,
    form: str = "10-Q",
    filed: date = date(2026, 5, 1),
    amended: str | None = None,
) -> SourceFiling:
    metadata = FilingMetadata(
        ticker="AAA",
        cik="0000000001",
        accession=accession,
        form=form,
        base_form=form.removesuffix("/A"),
        is_amendment=form.endswith("/A"),
        amends_accession=amended,
        filing_date=filed,
        report_date=date(2026, 3, 31),
        primary_document=f"{accession}.htm",
    )
    return SourceFiling(metadata, object(), object())


def family(number: int, *, amendment: bool = False) -> FilingFamily:
    original = source(f"0000000001-26-{number:06d}")
    amendments = ()
    if amendment:
        amendments = (
            source(
                f"0000000001-26-{number + 1:06d}",
                form="10-Q/A",
                filed=date(2026, 5, 10),
                amended=original.metadata.accession,
            ),
        )
    return FilingFamily(original, amendments)


def document(metadata: FilingMetadata) -> CanonicalDoc:
    text = f"complete filing text for {metadata.accession}"
    chunk = Chunk(
        chunk_id=f"{metadata.accession}::item_1::chunk_000",
        doc_hash=sha256(text),
        kind="section",
        section_key="part_i_item_1",
        title="Item 1",
        text=text,
        text_hash=sha256(text),
        char_span=(0, len(text)),
        span_verified=True,
        extraction_method="test|chunker-v2-overlap",
    )
    return CanonicalDoc(
        cik=metadata.cik,
        ticker=metadata.ticker,
        form_type=metadata.form,
        accession=metadata.accession,
        period_end=metadata.report_date.isoformat(),
        filing_date=metadata.filing_date.isoformat(),
        fetched_at="2026-05-10T00:00:00+00:00",
        doc_hash=sha256(text),
        parser_version="test-parser",
        normalizer_version="norm-1",
        schema_version=3,
        full_text=text,
        primary_document=metadata.primary_document,
        base_form=metadata.base_form,
        is_amendment=metadata.is_amendment,
        amends_accession=metadata.amends_accession,
        chunks=[chunk],
    )


class FakeSelector:
    def __init__(self, families: tuple[FilingFamily, ...], events: list[str]) -> None:
        self.families = families
        self.events = events

    def select(self, request: SelectionRequest, *, today: date):
        self.events.append("selected")
        return self.families


class FakeIndexer:
    def __init__(self, db_path: Path, events: list[str]) -> None:
        self.db_path = db_path
        self.events = events
        self.seen: set[str] = set()

    def index(self) -> IndexStats:
        self.events.append("indexed")
        with sqlite3.connect(self.db_path) as connection:
            ids = {
                row[0]
                for row in connection.execute("SELECT chunk_id FROM chunks")
            }
        changed = ids - self.seen
        self.seen = ids
        return IndexStats(len(ids), len(changed), len(ids - changed), 1, 0)


def coordinator(
    tmp_path: Path,
    families: tuple[FilingFamily, ...],
    events: list[str],
    *,
    fail_accession: str | None = None,
) -> tuple[IngestCoordinator, FakeIndexer]:
    paths = IngestPaths(
        raw_dir=tmp_path / "raw",
        db_path=tmp_path / "corpus.db",
        vector_path=tmp_path / "vectors",
    )
    indexer = FakeIndexer(paths.db_path, events)

    def builder(selected: SourceFiling, raw_dir: Path) -> CanonicalDoc:
        assert events and events[0] == "selected"
        events.append(f"built:{selected.metadata.form}")
        if selected.metadata.accession == fail_accession:
            raise RuntimeError("simulated build failure")
        return document(selected.metadata)

    return (
        IngestCoordinator(
            selector=FakeSelector(families, events),
            paths=paths,
            document_builder=builder,
            sanity_checker=lambda doc: [] if doc.chunks else ["no chunks"],
            indexer_factory=lambda _: indexer,
        ),
        indexer,
    )


def counts(db_path: Path) -> tuple[int, int]:
    with sqlite3.connect(db_path) as connection:
        return (
            connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        )


def test_selects_then_builds_family_in_order_and_indexes_once(tmp_path: Path) -> None:
    events: list[str] = []
    ingest, _ = coordinator(tmp_path, (family(1, amendment=True),), events)

    result = ingest.ingest(SelectionRequest("AAA"), today=date(2026, 9, 19))

    assert events == ["selected", "built:10-Q", "built:10-Q/A", "indexed"]
    assert result.stage2_ready is True
    assert result.families[0].document_count == 2
    assert result.families[0].chunk_count == 2
    assert counts(ingest.paths.db_path) == (2, 2)


def test_no_vector_index_is_explicitly_not_stage2_ready(tmp_path: Path) -> None:
    events: list[str] = []
    ingest, _ = coordinator(tmp_path, (family(1),), events)

    result = ingest.ingest(
        SelectionRequest("AAA"),
        today=date(2026, 9, 19),
        no_vector_index=True,
    )

    assert result.stage2_ready is False
    assert result.index_stats is None
    assert "indexed" not in events
    assert not ingest.paths.vector_path.exists()


def test_repeat_is_idempotent_and_embeds_zero_new_chunks(tmp_path: Path) -> None:
    events: list[str] = []
    ingest, _ = coordinator(tmp_path, (family(1),), events)

    first = ingest.ingest(SelectionRequest("AAA"), today=date(2026, 9, 19))
    second = ingest.ingest(SelectionRequest("AAA"), today=date(2026, 9, 19))

    assert counts(ingest.paths.db_path) == (1, 1)
    assert first.index_stats.embedded_chunks == 1
    assert second.index_stats.embedded_chunks == 0


def test_new_latest_accession_preserves_prior_family(tmp_path: Path) -> None:
    events: list[str] = []
    first_family = family(1)
    ingest, _ = coordinator(tmp_path, (first_family,), events)
    ingest.ingest(SelectionRequest("AAA"), today=date(2026, 9, 19))
    ingest.selector.families = (family(3),)

    ingest.ingest(SelectionRequest("AAA"), today=date(2026, 9, 19))

    assert counts(ingest.paths.db_path) == (2, 2)


def test_failed_amendment_invalidates_family_and_skips_index(tmp_path: Path) -> None:
    events: list[str] = []
    selected = family(1, amendment=True)
    failed_accession = selected.amendments[0].metadata.accession
    ingest, _ = coordinator(
        tmp_path,
        (selected,),
        events,
        fail_accession=failed_accession,
    )

    result = ingest.ingest(SelectionRequest("AAA"), today=date(2026, 9, 19))

    with sqlite3.connect(ingest.paths.db_path) as connection:
        statuses = connection.execute(
            "SELECT ingestion_status FROM documents ORDER BY accession"
        ).fetchall()
    assert statuses == [("failed",), ("failed",)]
    assert counts(ingest.paths.db_path) == (2, 0)
    assert result.families[0].status == "failed"
    assert result.stage2_ready is False
    assert "indexed" not in events


def test_sanity_failure_invalidates_a_previously_successful_family(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    selected = family(1, amendment=True)
    ingest, _ = coordinator(tmp_path, (selected,), events)
    ingest.ingest(SelectionRequest("AAA"), today=date(2026, 9, 19))
    original_builder = ingest.document_builder

    def invalid_builder(selected_filing: SourceFiling, raw_dir: Path) -> CanonicalDoc:
        built = original_builder(selected_filing, raw_dir)
        if selected_filing.metadata.is_amendment:
            return replace(built, chunks=[])
        return built

    ingest.document_builder = invalid_builder
    result = ingest.ingest(SelectionRequest("AAA"), today=date(2026, 9, 19))

    assert counts(ingest.paths.db_path) == (2, 0)
    assert result.stage2_ready is False
