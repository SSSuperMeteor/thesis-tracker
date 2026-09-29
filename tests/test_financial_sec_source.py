from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import thesis_tracker.financial.sec_source as sec_source
from thesis_tracker.financial.models import (
    FailureCode,
    FilingBoundary,
    ResolvedFact,
    Unit,
)
from thesis_tracker.financial.resolver import resolve_quarterly_fact
from thesis_tracker.financial.sec_source import (
    SourceValidationError,
    _DateBoundCompany,
    _eligible_members,
    _select_financial_member,
    extract_filing_facts,
    load_stage1_boundary,
    load_stage3_inputs,
)


class FakeFacts:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def get_facts(self) -> list[dict[str, object]]:
        return self._rows


class FakeContext:
    def __init__(self, dimensions: dict[str, str]) -> None:
        self.dimensions = dimensions


class FakeXbrl:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.facts = FakeFacts(rows)
        self.contexts = {
            "consolidated": FakeContext({}),
            "segment": FakeContext({"us-gaap:SegmentAxis": "test:CloudMember"}),
        }


class FakeMetadata:
    def __init__(self, accession: str) -> None:
        self.accession = accession


class FakeFiling:
    def __init__(self, xbrl: FakeXbrl) -> None:
        self._xbrl = xbrl

    def xbrl(self) -> FakeXbrl:
        return self._xbrl


class FakeMember:
    def __init__(self, accession: str, xbrl: FakeXbrl) -> None:
        self.metadata = FakeMetadata(accession)
        self.filing = FakeFiling(xbrl)


class FakeCompany:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    def get_filings(self, **kwargs: object) -> tuple[()]:
        self.kwargs = kwargs
        return ()


def test_date_bound_company_applies_filter_before_history_validation() -> None:
    company = FakeCompany()
    bounded = _DateBoundCompany(
        company,
        since=date(2022, 8, 26),
        until=date(2026, 8, 26),
    )

    bounded.get_filings(form=["10-Q", "10-K"], trigger_full_load=True)

    assert company.kwargs["filing_date"] == (
        date(2022, 8, 26),
        date(2026, 8, 26),
    )


def test_amendment_without_registered_facts_falls_back_to_original_member() -> None:
    financial_row = {
        "concept": "us-gaap:Revenues",
        "period_type": "duration",
    }
    original = FakeMember("original", FakeXbrl([financial_row]))
    amendment = FakeMember("amendment", FakeXbrl([]))

    selected, _, used_fallback = _select_financial_member((original, amendment))

    assert selected.metadata.accession == "original"
    assert used_fallback is True


def test_family_without_registered_facts_keeps_latest_xbrl_for_diagnostics() -> None:
    original = FakeMember("original", FakeXbrl([]))
    latest = FakeMember(
        "latest",
        FakeXbrl(
            [
                {
                    "concept": "test:UnregisteredRevenue",
                    "period_type": "duration",
                }
            ]
        ),
    )

    selected, selected_xbrl, used_fallback = _select_financial_member(
        (original, latest)
    )

    assert selected is latest
    assert selected_xbrl is latest.filing.xbrl()
    assert used_fallback is False


def test_as_of_boundary_excludes_unrelated_same_day_accessions() -> None:
    selected = SimpleNamespace(
        accession="selected",
        filed_at=date(2026, 8, 26),
    )
    selected_family = SimpleNamespace(
        members=(
            SimpleNamespace(
                metadata=SimpleNamespace(
                    accession="original",
                    filing_date=date(2026, 8, 26),
                )
            ),
            SimpleNamespace(
                metadata=SimpleNamespace(
                    accession="selected",
                    filing_date=date(2026, 8, 26),
                )
            ),
            SimpleNamespace(
                metadata=SimpleNamespace(
                    accession="later-amendment",
                    filing_date=date(2026, 8, 26),
                )
            ),
        )
    )
    unrelated = SimpleNamespace(
        members=(
            SimpleNamespace(
                metadata=SimpleNamespace(
                    accession="same-day-other",
                    filing_date=date(2026, 8, 26),
                )
            ),
        )
    )

    eligible = _eligible_members(selected_family, selected)

    assert [member.metadata.accession for member in eligible] == [
        "original",
        "selected",
    ]
    assert _eligible_members(unrelated, selected) == ()


def test_extract_filing_facts_preserves_context_and_deduplicates_roles() -> None:
    boundary = FilingBoundary(
        ticker="TEST",
        accession="acc-1",
        filed_at=date(2026, 5, 1),
        form="10-Q",
        fiscal_year=2026,
        fiscal_period="Q1",
        period_end=date(2026, 3, 31),
        source="sec_filing_metadata",
    )
    row = {
        "concept": "us-gaap:Revenues",
        "context_ref": "consolidated",
        "value": "1,234",
        "unit_ref": "usd",
        "currency": "USD",
        "period_type": "duration",
        "period_start": "2026-01-01",
        "period_end": "2026-03-31",
        "fiscal_period": "Q1",
        "fiscal_year": 2026,
    }

    facts = extract_filing_facts(boundary, FakeXbrl([row, dict(row)]))

    assert len(facts) == 1
    assert facts[0].context_id == "consolidated"
    assert facts[0].dimensions == ()
    assert facts[0].unit is Unit.USD
    assert str(facts[0].value) == "1234"


def test_extract_filing_facts_keeps_dimensioned_candidate_for_diagnostics() -> None:
    boundary = FilingBoundary(
        ticker="TEST",
        accession="acc-1",
        filed_at=date(2026, 5, 1),
        form="10-Q",
        fiscal_year=2026,
        fiscal_period="Q1",
        period_end=date(2026, 3, 31),
        source="sec_filing_metadata",
    )
    row = {
        "concept": "us-gaap:Revenues",
        "context_ref": "segment",
        "value": "300",
        "unit_ref": "usd",
        "currency": "USD",
        "period_type": "duration",
        "period_start": "2026-01-01",
        "period_end": "2026-03-31",
        "fiscal_period": "Q1",
        "fiscal_year": 2026,
    }

    facts = extract_filing_facts(boundary, FakeXbrl([row]))

    assert facts[0].dimensions == (
        ("us-gaap:SegmentAxis", "test:CloudMember"),
    )


def test_extract_filing_facts_defers_conflicting_context_to_the_resolver() -> None:
    boundary = FilingBoundary(
        ticker="TEST",
        accession="acc-1",
        filed_at=date(2026, 5, 1),
        form="10-Q",
        fiscal_year=2026,
        fiscal_period="Q1",
        period_end=date(2026, 3, 31),
        source="sec_filing_metadata",
    )
    prior = FilingBoundary(
        ticker="TEST",
        accession="acc-0",
        filed_at=date(2026, 2, 1),
        form="10-K",
        fiscal_year=2025,
        fiscal_period="Q4",
        period_end=date(2025, 12, 31),
        source="sec_filing_metadata",
    )
    row = {
        "concept": "us-gaap:Revenues",
        "context_ref": "consolidated",
        "value": "100",
        "unit_ref": "usd",
        "currency": "USD",
        "period_type": "duration",
        "period_start": "2026-01-01",
        "period_end": "2026-03-31",
        "fiscal_period": "Q1",
        "fiscal_year": 2026,
    }
    conflicting = dict(row, value="101")

    # The loader keeps both reported values instead of aborting the filing; the
    # resolver is what fails the concept closed as ambiguous.
    facts = extract_filing_facts(boundary, FakeXbrl([row, conflicting]))

    assert len(facts) == 2
    assert {str(fact.value) for fact in facts} == {"100", "101"}
    resolution = resolve_quarterly_fact(
        facts=facts,
        boundaries=(prior, boundary),
        target=boundary,
        canonical_concept="revenue",
    )
    assert resolution.diagnostic.final_failure is FailureCode.AMBIGUOUS


def test_load_stage1_boundary_requires_unique_successful_selected_filing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "corpus.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE documents (
                accession TEXT,
                cik TEXT,
                ticker TEXT,
                form_type TEXT,
                period_end TEXT,
                filing_date TEXT,
                ingestion_status TEXT,
                is_amendment INTEGER,
                amends_accession TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "a",
                    "1",
                    "TEST",
                    "10-Q",
                    "2026-03-31",
                    "2026-05-01",
                    "success",
                    0,
                    None,
                ),
                (
                    "b",
                    "1",
                    "TEST",
                    "10-Q",
                    "2026-06-30",
                    "2026-08-01",
                    "success",
                    0,
                    None,
                ),
            ],
        )

    with pytest.raises(SourceValidationError) as error:
        load_stage1_boundary(db_path, "TEST")

    assert error.value.code is FailureCode.AMBIGUOUS


def test_stage3_default_history_keeps_yoy_predecessor_beyond_twelve_filings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The oldest YoY comparison still has its immediately prior boundary."""

    quarter_specs = (
        (2023, "Q2", date(2023, 4, 1), date(2023, 6, 30)),
        (2023, "Q3", date(2023, 7, 1), date(2023, 9, 30)),
        (2023, "Q4", date(2023, 10, 1), date(2023, 12, 31)),
        (2024, "Q1", date(2024, 1, 1), date(2024, 3, 31)),
        (2024, "Q2", date(2024, 4, 1), date(2024, 6, 30)),
        (2024, "Q3", date(2024, 7, 1), date(2024, 9, 30)),
        (2024, "Q4", date(2024, 10, 1), date(2024, 12, 31)),
        (2025, "Q1", date(2025, 1, 1), date(2025, 3, 31)),
        (2025, "Q2", date(2025, 4, 1), date(2025, 6, 30)),
        (2025, "Q3", date(2025, 7, 1), date(2025, 9, 30)),
        (2025, "Q4", date(2025, 10, 1), date(2025, 12, 31)),
        (2026, "Q1", date(2026, 1, 1), date(2026, 3, 31)),
        (2026, "Q2", date(2026, 4, 1), date(2026, 6, 30)),
    )
    families = []
    for index, (fiscal_year, fiscal_period, period_start, period_end) in enumerate(
        quarter_specs,
        start=1,
    ):
        accession = f"q{index}"
        form = "10-K" if fiscal_period == "Q4" else "10-Q"
        xbrl = FakeXbrl(
            [
                {
                    "concept": "us-gaap:Revenues",
                    "context_ref": "consolidated",
                    "value": "100",
                    "unit_ref": "usd",
                    "currency": "USD",
                    "period_type": "duration",
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "fiscal_period": fiscal_period,
                    "fiscal_year": fiscal_year,
                }
            ]
        )
        xbrl.entity_info = {
            "fiscal_period": fiscal_period,
            "fiscal_year": fiscal_year,
        }
        member = SimpleNamespace(
            metadata=SimpleNamespace(
                accession=accession,
                filing_date=period_end + timedelta(days=30),
                report_date=period_end,
                base_form=form,
                form=form,
            ),
            filing=FakeFiling(xbrl),
        )
        families.append(SimpleNamespace(members=(member,)))

    class FakeSelector:
        def __init__(self, source: object) -> None:
            del source

        def select(self, request: object, *, today: date) -> tuple[object, ...]:
            del request, today
            return tuple(families)

    monkeypatch.setattr(sec_source, "FilingSelector", FakeSelector)
    db_path = tmp_path / "corpus.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE documents (
                accession TEXT,
                cik TEXT,
                ticker TEXT,
                form_type TEXT,
                period_end TEXT,
                filing_date TEXT,
                ingestion_status TEXT,
                is_amendment INTEGER,
                amends_accession TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "q13",
                "1",
                "TEST",
                "10-Q",
                "2026-06-30",
                "2026-07-30",
                "success",
                0,
                None,
            ),
        )

    loaded = load_stage3_inputs("TEST", db_path=db_path)
    oldest_yoy_target = next(
        boundary for boundary in loaded.boundaries if boundary.accession == "q2"
    )
    result = resolve_quarterly_fact(
        facts=loaded.facts,
        boundaries=loaded.boundaries,
        target=oldest_yoy_target,
        canonical_concept="revenue",
    )

    assert len(loaded.boundaries) == 13
    assert loaded.boundaries[0].accession == "q1"
    assert isinstance(result, ResolvedFact)
    assert result.fact.accession == "q2"
    assert result.fact.context_id == "consolidated"
