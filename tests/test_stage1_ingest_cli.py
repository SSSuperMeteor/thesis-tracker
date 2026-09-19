"""CLI parsing tests for Stage 1 filing ingestion."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from thesis_tracker.ingest.cli import run
from thesis_tracker.ingest.pipeline import IngestPaths


class FakeCoordinator:
    def __init__(self, paths: IngestPaths) -> None:
        self.paths = paths
        self.calls: list[tuple[object, bool]] = []

    def ingest(self, request, *, today, no_vector_index=False):
        self.calls.append((request, no_vector_index))
        return SimpleNamespace(
            families=(),
            index_stats=None,
            stage2_ready=not no_vector_index,
        )


@pytest.mark.parametrize("arguments", [["AAA"], ["AAA", "--latest"]])
def test_default_and_explicit_latest(arguments: list[str], capsys) -> None:
    holder: list[FakeCoordinator] = []

    def factory(paths: IngestPaths) -> FakeCoordinator:
        holder.append(FakeCoordinator(paths))
        return holder[0]

    assert run(arguments, coordinator_factory=factory) == 0
    request, _ = holder[0].calls[0]
    assert request.is_latest
    assert "Stage 2 ready: yes" in capsys.readouterr().out


def test_history_modes_and_paths_are_forwarded(tmp_path: Path) -> None:
    holder: list[FakeCoordinator] = []

    def factory(paths: IngestPaths) -> FakeCoordinator:
        holder.append(FakeCoordinator(paths))
        return holder[0]

    assert run(["AAA", "--years", "3"], coordinator_factory=factory) == 0
    assert holder[0].calls[0][0].years == 3

    holder.clear()
    assert run(
        [
            "AAA",
            "--since",
            "2023-01-01",
            "--raw-dir",
            str(tmp_path / "raw"),
            "--db",
            str(tmp_path / "db.sqlite"),
            "--vector-path",
            str(tmp_path / "vectors"),
        ],
        coordinator_factory=factory,
    ) == 0
    assert holder[0].calls[0][0].since == date(2023, 1, 1)
    assert holder[0].paths == IngestPaths(
        tmp_path / "raw",
        tmp_path / "db.sqlite",
        tmp_path / "vectors",
    )


def test_no_vector_index_output_is_not_ready(capsys) -> None:
    assert run(
        ["AAA", "--no-vector-index"],
        coordinator_factory=FakeCoordinator,
    ) == 0
    assert "Stage 2 ready: no" in capsys.readouterr().out


@pytest.mark.parametrize(
    "arguments",
    [
        ["AAA", "--latest", "--years", "3"],
        ["AAA", "--years", "0"],
        ["AAA", "--years", "-1"],
        ["AAA", "--since", "not-a-date"],
    ],
)
def test_invalid_requests_exit_before_coordinator(
    arguments: list[str],
) -> None:
    called = False

    def factory(paths: IngestPaths):
        nonlocal called
        called = True
        return FakeCoordinator(paths)

    with pytest.raises(SystemExit):
        run(arguments, coordinator_factory=factory)
    assert called is False
