"""Command-line entry point for amendment-aware SEC filing ingestion."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

from thesis_tracker.ingest.filing_selection import SelectionRequest
from thesis_tracker.ingest.pipeline import IngestCoordinator, IngestPaths

CoordinatorFactory = Callable[[IngestPaths], IngestCoordinator]


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be YYYY-MM-DD") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest complete SEC periodic filing families.",
    )
    parser.add_argument("tickers", nargs="+")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--latest", action="store_true")
    mode.add_argument("--years", type=_positive_int)
    mode.add_argument("--since", type=_iso_date)
    parser.add_argument("--no-vector-index", action="store_true")
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--db", type=Path, default=Path("data/corpus.db"))
    parser.add_argument(
        "--vector-path",
        type=Path,
        default=Path("store/vectors"),
    )
    return parser


def _production_coordinator(paths: IngestPaths) -> IngestCoordinator:
    return IngestCoordinator(paths=paths)


def run(
    argv: Sequence[str] | None = None,
    *,
    coordinator_factory: CoordinatorFactory = _production_coordinator,
) -> int:
    args = _parser().parse_args(argv)
    paths = IngestPaths(args.raw_dir, args.db, args.vector_path)
    coordinator = coordinator_factory(paths)
    exit_code = 0
    for ticker in args.tickers:
        request = SelectionRequest(
            ticker,
            latest=args.latest,
            years=args.years,
            since=args.since,
        )
        result = coordinator.ingest(
            request,
            today=date.today(),
            no_vector_index=args.no_vector_index,
        )
        print(f"Ticker: {request.ticker}")
        for family in result.families:
            print(
                f"Family effective accession: {family.effective_accession} "
                f"status={family.status} documents={family.document_count} "
                f"chunks={family.chunk_count}"
            )
            for member in getattr(family, "members", ()):
                print(
                    f"  {member.accession} form={member.form} "
                    f"filingDate={member.filing_date.isoformat()} "
                    f"reportDate={member.report_date.isoformat()} "
                    f"amends={member.amends_accession or '-'} "
                    f"primaryDocument={member.primary_document}"
                )
            for failure in getattr(family, "failures", ()):
                print(f"  failure={failure}")
            if family.status != "success":
                exit_code = 1
        if result.index_stats is not None:
            stats = result.index_stats
            print(
                "Vector index: "
                f"total={stats.total_chunks} embedded={stats.embedded_chunks} "
                f"unchanged={stats.skipped_unchanged} "
                f"removed={stats.removed_stale}"
            )
        print(f"Stage 2 ready: {'yes' if result.stage2_ready else 'no'}")
    return exit_code


def main() -> int:
    return run()
