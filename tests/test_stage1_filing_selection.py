"""Tests for SEC-metadata-first Stage 1 filing selection."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from thesis_tracker.ingest.filing_selection import (
    EdgarFilingSource,
    FilingMetadata,
    FilingSelectionError,
    FilingSelector,
    SelectionFailureCode,
    SelectionRequest,
    SourceFiling,
)

TODAY = date(2026, 9, 19)


@pytest.mark.parametrize(
    "selection_request",
    [
        SelectionRequest(ticker=" nvda "),
        SelectionRequest(ticker="NVDA", latest=True),
    ],
)
def test_no_history_range_means_latest(
    selection_request: SelectionRequest,
) -> None:
    assert selection_request.ticker == "NVDA"
    assert selection_request.lower_bound(TODAY) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"latest": True, "years": 1},
        {"latest": True, "since": date(2025, 1, 1)},
        {"years": 1, "since": date(2025, 1, 1)},
    ],
)
def test_selection_modes_are_mutually_exclusive(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(FilingSelectionError) as caught:
        SelectionRequest(ticker="NVDA", **kwargs)

    assert caught.value.code is SelectionFailureCode.INVALID_REQUEST


@pytest.mark.parametrize("years", [0, -1])
def test_years_must_be_positive(years: int) -> None:
    with pytest.raises(FilingSelectionError) as caught:
        SelectionRequest(ticker="NVDA", years=years)

    assert caught.value.code is SelectionFailureCode.INVALID_REQUEST


def test_since_is_the_inclusive_lower_bound() -> None:
    lower_bound = date(2023, 1, 1)

    assert SelectionRequest(ticker="NVDA", since=lower_bound).lower_bound(
        TODAY
    ) == lower_bound


def test_years_uses_calendar_years_and_clamps_leap_day() -> None:
    request = SelectionRequest(ticker="NVDA", years=1)

    assert request.lower_bound(date(2028, 2, 29)) == date(2027, 2, 28)


def test_blank_ticker_fails_closed() -> None:
    with pytest.raises(FilingSelectionError) as caught:
        SelectionRequest(ticker="  ")

    assert caught.value.code is SelectionFailureCode.INVALID_REQUEST


class FakeSource:
    def __init__(self, filings: list[SourceFiling]) -> None:
        self.filings = filings
        self.calls: list[tuple[str, bool]] = []

    def load(
        self,
        ticker: str,
        *,
        include_history: bool,
    ) -> tuple[SourceFiling, ...]:
        self.calls.append((ticker, include_history))
        return tuple(self.filings)


def source_filing(
    accession: str,
    form: str,
    filing_date: date,
    report_date: date,
    *,
    ticker: str = "AAA",
    cik: str = "0000000001",
    primary_document: str = "report.htm",
    base_form: str | None = None,
    acceptance_datetime: datetime | None = None,
) -> SourceFiling:
    resolved_base_form = base_form or form.removesuffix("/A")
    return SourceFiling(
        metadata=FilingMetadata(
            ticker=ticker,
            cik=cik,
            accession=accession,
            form=form,
            base_form=resolved_base_form,
            is_amendment=form.endswith("/A"),
            amends_accession=None,
            filing_date=filing_date,
            report_date=report_date,
            primary_document=primary_document,
            acceptance_datetime=acceptance_datetime,
        ),
        company=object(),
        filing=object(),
    )


def test_latest_uses_effective_family_filing_date() -> None:
    q1 = source_filing(
        "0000000001-26-000001",
        "10-Q",
        date(2026, 5, 1),
        date(2026, 3, 31),
    )
    annual = source_filing(
        "0000000001-26-000002",
        "10-K",
        date(2026, 2, 1),
        date(2025, 12, 31),
    )
    amendment = source_filing(
        "0000000001-26-000003",
        "10-K/A",
        date(2026, 6, 1),
        date(2025, 12, 31),
    )
    source = FakeSource([q1, annual, amendment])

    families = FilingSelector(source).select(
        SelectionRequest("AAA"),
        today=TODAY,
    )

    assert source.calls == [("AAA", False)]
    assert len(families) == 1
    assert [item.metadata.accession for item in families[0].members] == [
        annual.metadata.accession,
        amendment.metadata.accession,
    ]
    assert families[0].effective.metadata.accession == amendment.metadata.accession
    assert families[0].amendments[0].metadata.amends_accession == (
        annual.metadata.accession
    )


def test_history_since_is_inclusive_and_keeps_complete_amended_family() -> None:
    original = source_filing(
        "0000000001-25-000001",
        "10-Q",
        date(2025, 11, 1),
        date(2025, 9, 30),
    )
    boundary_amendment = source_filing(
        "0000000001-26-000001",
        "10-Q/A",
        date(2026, 1, 1),
        date(2025, 9, 30),
    )
    later = source_filing(
        "0000000001-26-000002",
        "10-K",
        date(2026, 2, 1),
        date(2025, 12, 31),
    )
    source = FakeSource([original, boundary_amendment, later])

    families = FilingSelector(source).select(
        SelectionRequest("AAA", since=date(2026, 1, 1)),
        today=TODAY,
    )

    assert source.calls == [("AAA", True)]
    assert [family.effective.metadata.accession for family in families] == [
        later.metadata.accession,
        boundary_amendment.metadata.accession,
    ]
    assert families[1].original.metadata.accession == original.metadata.accession


def test_history_years_uses_inclusive_effective_date_and_deterministic_tie() -> None:
    lower_accession = source_filing(
        "0000000001-25-000001",
        "10-Q",
        date(2025, 9, 19),
        date(2025, 6, 30),
    )
    higher_accession = source_filing(
        "0000000001-25-000009",
        "10-K",
        date(2025, 9, 19),
        date(2025, 3, 31),
    )
    too_old = source_filing(
        "0000000001-25-000010",
        "10-Q",
        date(2025, 9, 18),
        date(2025, 1, 31),
    )

    families = FilingSelector(
        FakeSource([lower_accession, too_old, higher_accession])
    ).select(
        SelectionRequest("AAA", years=1),
        today=TODAY,
    )

    assert [family.effective.metadata.accession for family in families] == [
        higher_accession.metadata.accession,
        lower_accession.metadata.accession,
    ]


def test_multiple_amendments_are_ordered_and_identical_duplicates_deduplicate() -> None:
    original = source_filing(
        "0000000001-26-000001",
        "10-Q",
        date(2026, 5, 1),
        date(2026, 3, 31),
    )
    first = source_filing(
        "0000000001-26-000002",
        "10-Q/A",
        date(2026, 5, 10),
        date(2026, 3, 31),
    )
    second = source_filing(
        "0000000001-26-000003",
        "10-Q/A",
        date(2026, 5, 20),
        date(2026, 3, 31),
    )

    family = FilingSelector(
        FakeSource([second, original, first, original])
    ).select(SelectionRequest("AAA"), today=TODAY)[0]

    assert [item.metadata.accession for item in family.members] == [
        original.metadata.accession,
        first.metadata.accession,
        second.metadata.accession,
    ]


def test_same_day_amendment_uses_sec_acceptance_time() -> None:
    filing_day = date(2026, 2, 4)
    original = source_filing(
        "0000000001-26-000018",
        "10-K",
        filing_day,
        date(2025, 12, 31),
        acceptance_datetime=datetime(2026, 2, 3, 23, 14, 52, tzinfo=UTC),
    )
    amendment = source_filing(
        "0000000001-26-000021",
        "10-K/A",
        filing_day,
        date(2025, 12, 31),
        acceptance_datetime=datetime(2026, 2, 4, 20, 6, 57, tzinfo=UTC),
    )

    selected = FilingSelector(FakeSource([amendment, original])).select(
        SelectionRequest("AAA"),
        today=TODAY,
    )

    assert [item.metadata.accession for item in selected[0].members] == [
        original.metadata.accession,
        amendment.metadata.accession,
    ]


def test_same_day_amendment_without_acceptance_time_fails_closed() -> None:
    filing_day = date(2026, 2, 4)
    original = source_filing(
        "0000000001-26-000018",
        "10-K",
        filing_day,
        date(2025, 12, 31),
    )
    amendment = source_filing(
        "0000000001-26-000021",
        "10-K/A",
        filing_day,
        date(2025, 12, 31),
    )

    with pytest.raises(FilingSelectionError, match="acceptance") as caught:
        FilingSelector(FakeSource([original, amendment])).select(
            SelectionRequest("AAA"),
            today=TODAY,
        )

    assert caught.value.code is SelectionFailureCode.INVALID_METADATA


@pytest.mark.parametrize(
    ("filings", "message"),
    [
        (
            [
                source_filing(
                    "0000000001-26-000002",
                    "10-Q/A",
                    date(2026, 5, 2),
                    date(2026, 3, 31),
                )
            ],
            "orphan",
        ),
        (
            [
                source_filing(
                    "0000000001-26-000001",
                    "10-Q",
                    date(2026, 5, 1),
                    date(2026, 3, 31),
                ),
                source_filing(
                    "0000000001-26-000009",
                    "10-Q",
                    date(2026, 5, 2),
                    date(2026, 3, 31),
                ),
            ],
            "multiple originals",
        ),
        (
            [
                source_filing(
                    "0000000001-26-000001",
                    "10-Q",
                    date(2026, 5, 2),
                    date(2026, 3, 31),
                ),
                source_filing(
                    "0000000001-26-000002",
                    "10-Q/A",
                    date(2026, 5, 1),
                    date(2026, 3, 31),
                ),
            ],
            "later",
        ),
        (
            [
                source_filing(
                    "0000000001-26-000001",
                    "8-K",
                    date(2026, 5, 1),
                    date(2026, 3, 31),
                )
            ],
            "unsupported",
        ),
        (
            [
                source_filing(
                    "",
                    "10-Q",
                    date(2026, 5, 1),
                    date(2026, 3, 31),
                )
            ],
            "accession",
        ),
    ],
)
def test_invalid_filing_families_fail_closed(
    filings: list[SourceFiling],
    message: str,
) -> None:
    with pytest.raises(FilingSelectionError, match=message) as caught:
        FilingSelector(FakeSource(filings)).select(
            SelectionRequest("AAA"),
            today=TODAY,
        )

    assert caught.value.code is SelectionFailureCode.INVALID_METADATA


def test_conflicting_duplicate_accession_fails_closed() -> None:
    first = source_filing(
        "0000000001-26-000001",
        "10-Q",
        date(2026, 5, 1),
        date(2026, 3, 31),
    )
    conflicting = source_filing(
        first.metadata.accession,
        "10-K",
        first.metadata.filing_date,
        first.metadata.report_date,
    )

    with pytest.raises(FilingSelectionError, match="conflicting") as caught:
        FilingSelector(FakeSource([first, conflicting])).select(
            SelectionRequest("AAA"),
            today=TODAY,
        )

    assert caught.value.code is SelectionFailureCode.INVALID_METADATA


def test_no_periodic_filing_is_a_structured_failure() -> None:
    with pytest.raises(FilingSelectionError) as caught:
        FilingSelector(FakeSource([])).select(
            SelectionRequest("AAA"),
            today=TODAY,
        )

    assert caught.value.code is SelectionFailureCode.NO_MATCHING_FILINGS


class FakeEdgarFiling:
    def __init__(
        self,
        *,
        accession_no: str,
        form: str,
        filing_date: str,
        period_of_report: str,
        primary_document: str,
        acceptance_datetime: datetime | None = None,
    ) -> None:
        self.accession_no = accession_no
        self.form = form
        self.filing_date = filing_date
        self.period_of_report = period_of_report
        self.primary_document = primary_document
        self.acceptance_datetime = acceptance_datetime


class FakeCompany:
    cik = 1
    tickers = ["AAA"]

    def __init__(self, filings: list[FakeEdgarFiling]) -> None:
        self.filings = filings
        self.calls: list[dict[str, object]] = []

    def get_filings(self, **kwargs: object) -> list[FakeEdgarFiling]:
        self.calls.append(kwargs)
        return self.filings


def test_edgar_source_requests_amendments_and_maps_complete_metadata() -> None:
    company = FakeCompany(
        [
            FakeEdgarFiling(
                accession_no="0000000001-26-000001",
                form="10-Q",
                filing_date="2026-05-01",
                period_of_report="2026-03-31",
                primary_document="q1.htm",
                acceptance_datetime=datetime(2026, 5, 1, 20, tzinfo=UTC),
            ),
            FakeEdgarFiling(
                accession_no="0000000001-26-000002",
                form="10-Q/A",
                filing_date="2026-05-10",
                period_of_report="2026-03-31",
                primary_document="q1a.htm",
            ),
        ]
    )
    identities: list[str] = []
    source = EdgarFilingSource(
        company_factory=lambda ticker: company,
        identity="tester test@example.com",
        identity_setter=identities.append,
    )

    filings = source.load("aaa", include_history=True)

    assert company.calls == [
        {
            "form": ["10-Q", "10-K"],
            "amendments": True,
            "trigger_full_load": True,
        }
    ]
    assert identities == ["tester test@example.com"]
    assert [item.metadata.form for item in filings] == ["10-Q", "10-Q/A"]
    assert filings[0].metadata == FilingMetadata(
        ticker="AAA",
        cik="0000000001",
        accession="0000000001-26-000001",
        form="10-Q",
        base_form="10-Q",
        is_amendment=False,
        amends_accession=None,
        filing_date=date(2026, 5, 1),
        report_date=date(2026, 3, 31),
        primary_document="q1.htm",
        acceptance_datetime=datetime(2026, 5, 1, 20, tzinfo=UTC),
    )
