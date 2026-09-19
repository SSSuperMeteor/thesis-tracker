"""Tests for generic raw-filing filename sanitization."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from thesis_tracker.ingest.sec_adapter import (
    RAW_DIR,
    CanonicalDoc,
    _raw_filing_path,
    _sanitize_filename_component,
)


@dataclass
class FakeCompany:
    tickers: list[str]
    cik: int = 34088


@dataclass
class FakeFiling:
    form: object = "10-Q"
    period_of_report: object = "2026-06-30"
    accession_no: object = "0000034088-26-000093"


def test_normal_components_are_unchanged() -> None:
    assert _sanitize_filename_component("NVDA") == "NVDA"
    assert _sanitize_filename_component("10-Q") == "10-Q"
    assert _sanitize_filename_component("2026-06-27") == "2026-06-27"
    assert _sanitize_filename_component("0000002488-26-000123") == "0000002488-26-000123"


def test_forward_slash_is_replaced_not_written() -> None:
    assert _sanitize_filename_component("10-Q/A", field="form") == "10-Q-A"


def test_backslash_is_replaced_not_written() -> None:
    assert _sanitize_filename_component("10-Q\\A", field="form") == "10-Q-A"


def test_none_is_rejected_without_stringifying() -> None:
    with pytest.raises(ValueError, match="missing"):
        _sanitize_filename_component(None, field="period_end")


@pytest.mark.parametrize("value", ["", " ", "   ", "\t"])
def test_blank_values_are_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="empty|control"):
        _sanitize_filename_component(value, field="ticker")


def test_path_traversal_tokens_are_rejected() -> None:
    with pytest.raises(ValueError, match="path traversal"):
        _sanitize_filename_component("..", field="form")
    with pytest.raises(ValueError, match="path traversal"):
        _sanitize_filename_component(".", field="form")


def test_metadata_is_not_mutated() -> None:
    company = FakeCompany(tickers=["AMD"])
    filing = FakeFiling(form="10-Q/A")
    original_form = filing.form

    path = _raw_filing_path(company, filing, ticker_fallback="AMD")

    assert filing.form == original_form
    assert company.tickers == ["AMD"]
    assert path.name.endswith("_10-Q-A_2026-06-30_0000034088-26-000093.txt")


def test_form_with_separator_produces_safe_filename() -> None:
    company = FakeCompany(tickers=["XOM"])
    filing = FakeFiling(form="10-Q/A")

    path = _raw_filing_path(company, filing, ticker_fallback="XOM")

    assert path.parent == RAW_DIR
    assert path.name == "XOM_10-Q-A_2026-06-30_0000034088-26-000093.txt"
    assert "/" not in path.name
    assert "\\" not in path.name


def test_final_path_stays_inside_raw_dir() -> None:
    company = FakeCompany(tickers=["XOM"])
    filing = FakeFiling()

    path = _raw_filing_path(company, filing, ticker_fallback="XOM")

    assert path.parent == RAW_DIR
    assert path.resolve().parent == RAW_DIR.resolve()


def test_traversal_attempt_cannot_escape_raw_dir() -> None:
    company = FakeCompany(tickers=["AMD"])
    filing = FakeFiling(form="../evil")

    path = _raw_filing_path(company, filing, ticker_fallback="AMD")

    assert path.parent == RAW_DIR
    assert path.resolve().parent == RAW_DIR.resolve()
    assert ".." not in path.name
    assert path.name == "AMD_evil_2026-06-30_0000034088-26-000093.txt"


def test_accession_is_preserved() -> None:
    company = FakeCompany(tickers=["XOM"])
    filing = FakeFiling(accession_no="0000034088-26-000093")

    path = _raw_filing_path(company, filing, ticker_fallback="XOM")

    assert "0000034088-26-000093" in path.name


def test_missing_ticker_metadata_falls_back_to_requested_ticker() -> None:
    company = FakeCompany(tickers=[])
    filing = FakeFiling()

    path = _raw_filing_path(company, filing, ticker_fallback="xom")

    assert path.name == "XOM_10-Q_2026-06-30_0000034088-26-000093.txt"


def test_missing_ticker_metadata_without_fallback_is_rejected() -> None:
    company = FakeCompany(tickers=[])
    filing = FakeFiling()

    with pytest.raises(ValueError, match="ticker"):
        _raw_filing_path(company, filing)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("form", None),
        ("period_of_report", None),
        ("accession_no", None),
    ],
)
def test_none_filing_metadata_is_rejected(field: str, value: None) -> None:
    company = FakeCompany(tickers=["AMD"])
    filing = FakeFiling(**{field: value})

    with pytest.raises(ValueError, match="missing"):
        _raw_filing_path(company, filing, ticker_fallback="AMD")


def test_none_never_becomes_literal_none_in_filename() -> None:
    company = FakeCompany(tickers=["AMD"])
    filing = FakeFiling(period_of_report=None)

    with pytest.raises(ValueError, match="period_end"):
        _raw_filing_path(company, filing, ticker_fallback="AMD")


@pytest.mark.parametrize(
    ("ticker", "form", "period_end", "accession", "expected"),
    [
        (
            "NVDA",
            "10-Q",
            "2026-07-26",
            "0001045810-26-000075",
            "NVDA_10-Q_2026-07-26_0001045810-26-000075.txt",
        ),
        (
            "AMD",
            "10-Q",
            "2026-06-27",
            "0000002488-26-000123",
            "AMD_10-Q_2026-06-27_0000002488-26-000123.txt",
        ),
    ],
)
def test_existing_issuer_filename_behavior_does_not_regress(
    ticker: str,
    form: str,
    period_end: str,
    accession: str,
    expected: str,
) -> None:
    company = FakeCompany(tickers=[ticker])
    filing = FakeFiling(
        form=form,
        period_of_report=period_end,
        accession_no=accession,
    )

    path = _raw_filing_path(company, filing, ticker_fallback=ticker)

    assert path.name == expected
    assert path.parent == RAW_DIR


def test_stored_raw_filenames_are_stable_under_sanitization() -> None:
    """Sanitizing an already-written filename must be a no-op."""
    for name in (
        "NVDA_10-Q_2026-07-26_0001045810-26-000075.txt",
        "AMD_10-Q_2026-06-27_0000002488-26-000123.txt",
        "GOOGL_10-Q_2026-06-30_0001652044-26-000071.txt",
    ):
        assert _sanitize_filename_component(name) == name


def test_raw_dir_type_is_path() -> None:
    assert isinstance(RAW_DIR, Path)


def test_raw_filing_path_can_target_an_isolated_directory(
    tmp_path: Path,
) -> None:
    company = FakeCompany(tickers=["NVDA"])
    filing = FakeFiling()
    raw_dir = tmp_path / "raw"

    path = _raw_filing_path(
        company,
        filing,
        ticker_fallback="NVDA",
        raw_dir=raw_dir,
    )

    assert path.parent == raw_dir
    assert path.resolve().parent == raw_dir.resolve()


def test_canonical_doc_retains_selected_filing_family_metadata() -> None:
    doc = CanonicalDoc(
        cik="0000000001",
        ticker="AAA",
        form_type="10-Q/A",
        accession="0000000001-26-000002",
        period_end="2026-03-31",
        filing_date="2026-05-10",
        fetched_at="2026-05-10T00:00:00+00:00",
        doc_hash="hash",
        parser_version="parser",
        normalizer_version="normalizer",
        schema_version=3,
        full_text="complete filing text",
        primary_document="q1a.htm",
        base_form="10-Q",
        is_amendment=True,
        amends_accession="0000000001-26-000001",
    )

    assert doc.primary_document == "q1a.htm"
    assert doc.base_form == "10-Q"
    assert doc.is_amendment is True
    assert doc.amends_accession == "0000000001-26-000001"
