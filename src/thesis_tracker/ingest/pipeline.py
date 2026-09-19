"""Coordinate SEC metadata selection, complete filing ingest, and indexing."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from thesis_tracker.embedding.dashscope import DashScopeEmbeddingClient
from thesis_tracker.ingest.filing_selection import (
    EdgarFilingSource,
    FilingFamily,
    FilingMetadata,
    FilingSelector,
    SelectionRequest,
    SourceFiling,
)
from thesis_tracker.ingest.sec_adapter import (
    CanonicalDoc,
    _build,
    assert_sane,
    save_family,
)
from thesis_tracker.retrieve.vector import IndexStats, VectorRetriever


@dataclass(frozen=True, slots=True)
class IngestPaths:
    raw_dir: Path = Path("data/raw")
    db_path: Path = Path("data/corpus.db")
    vector_path: Path = Path("store/vectors")


@dataclass(frozen=True, slots=True)
class FamilyIngestResult:
    effective_accession: str
    member_accessions: tuple[str, ...]
    document_count: int
    chunk_count: int
    status: str
    members: tuple[FilingMetadata, ...]
    failures: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IngestResult:
    families: tuple[FamilyIngestResult, ...]
    index_stats: IndexStats | None
    stage2_ready: bool


class _Selector(Protocol):
    def select(
        self,
        request: SelectionRequest,
        *,
        today: date,
    ) -> tuple[FilingFamily, ...]: ...


class _Indexer(Protocol):
    def index(self) -> IndexStats: ...


DocumentBuilder = Callable[[SourceFiling, Path], CanonicalDoc]
FamilySaver = Callable[
    [
        Sequence[FilingMetadata],
        Mapping[str, CanonicalDoc],
        Path,
        Mapping[str, Sequence[str]] | None,
    ],
    None,
]
SanityChecker = Callable[[CanonicalDoc], list[str]]
IndexerFactory = Callable[[IngestPaths], _Indexer]


def _default_builder(selected: SourceFiling, raw_dir: Path) -> CanonicalDoc:
    return _build(
        selected.company,
        selected.filing,
        metadata=selected.metadata,
        raw_dir=raw_dir,
    )


def _default_indexer(paths: IngestPaths) -> VectorRetriever:
    return VectorRetriever(
        DashScopeEmbeddingClient(),
        db_path=paths.db_path,
        vector_path=paths.vector_path,
    )


class IngestCoordinator:
    """Run selection completely before mutating any Stage 1 runtime store."""

    def __init__(
        self,
        *,
        selector: _Selector | None = None,
        paths: IngestPaths = IngestPaths(),
        document_builder: DocumentBuilder = _default_builder,
        family_saver: FamilySaver = save_family,
        sanity_checker: SanityChecker = assert_sane,
        indexer_factory: IndexerFactory = _default_indexer,
    ) -> None:
        self.selector = selector or FilingSelector(EdgarFilingSource())
        self.paths = paths
        self.document_builder = document_builder
        self.family_saver = family_saver
        self.sanity_checker = sanity_checker
        self.indexer_factory = indexer_factory

    def ingest(
        self,
        request: SelectionRequest,
        *,
        today: date | None = None,
        no_vector_index: bool = False,
    ) -> IngestResult:
        selected_families = self.selector.select(
            request,
            today=today or date.today(),
        )
        results: list[FamilyIngestResult] = []
        batch_succeeded = True

        for family in selected_families:
            members = family.members
            docs: dict[str, CanonicalDoc] = {}
            failures: dict[str, list[str]] = {}
            for selected in members:
                accession = selected.metadata.accession
                try:
                    doc = self.document_builder(selected, self.paths.raw_dir)
                except Exception as error:  # noqa: BLE001
                    failures[accession] = [
                        f"build {type(error).__name__}: {error}"
                    ]
                    continue
                docs[accession] = doc
                sanity_failures = self.sanity_checker(doc)
                if sanity_failures:
                    failures[accession] = sanity_failures

            metadata = tuple(member.metadata for member in members)
            self.family_saver(
                metadata,
                docs,
                self.paths.db_path,
                failures or None,
            )
            succeeded = not failures and len(docs) == len(members)
            batch_succeeded = batch_succeeded and succeeded
            results.append(
                FamilyIngestResult(
                    effective_accession=family.effective.metadata.accession,
                    member_accessions=tuple(
                        member.metadata.accession for member in members
                    ),
                    document_count=len(docs),
                    chunk_count=(
                        sum(len(doc.chunks) for doc in docs.values())
                        if succeeded
                        else 0
                    ),
                    status="success" if succeeded else "failed",
                    members=metadata,
                    failures=tuple(
                        f"{accession}: {failure}"
                        for accession, accession_failures in failures.items()
                        for failure in accession_failures
                    ),
                )
            )

        index_stats = None
        if batch_succeeded and not no_vector_index:
            index_stats = self.indexer_factory(self.paths).index()

        return IngestResult(
            families=tuple(results),
            index_stats=index_stats,
            stage2_ready=(batch_succeeded and index_stats is not None),
        )
