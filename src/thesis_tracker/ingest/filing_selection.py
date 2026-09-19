"""Select SEC periodic filing families from official filing metadata."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Protocol

from edgar import Company, set_identity

from thesis_tracker.config import load_settings

SUPPORTED_BASE_FORMS = frozenset({"10-Q", "10-K"})
SUPPORTED_FORMS = frozenset({"10-Q", "10-K", "10-Q/A", "10-K/A"})
_ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")


class SelectionFailureCode(StrEnum):
    """Structured failure codes for Stage 1 filing selection."""

    INVALID_REQUEST = "invalid_request"
    COMPANY_UNAVAILABLE = "company_unavailable"
    SEC_METADATA_UNAVAILABLE = "sec_metadata_unavailable"
    INVALID_METADATA = "invalid_metadata"
    NO_MATCHING_FILINGS = "no_matching_filings"


class FilingSelectionError(RuntimeError):
    """Fail-closed error raised before filing bodies are downloaded."""

    def __init__(
        self,
        code: SelectionFailureCode,
        message: str,
    ) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SelectionRequest:
    """One mutually exclusive latest or historical selection request."""

    ticker: str
    latest: bool = False
    years: int | None = None
    since: date | None = None

    def __post_init__(self) -> None:
        ticker = self.ticker.strip().upper()
        if not ticker:
            raise FilingSelectionError(
                SelectionFailureCode.INVALID_REQUEST,
                "ticker must not be blank",
            )
        object.__setattr__(self, "ticker", ticker)

        selected_modes = sum(
            (
                bool(self.latest),
                self.years is not None,
                self.since is not None,
            )
        )
        if selected_modes > 1:
            raise FilingSelectionError(
                SelectionFailureCode.INVALID_REQUEST,
                "latest, years, and since are mutually exclusive",
            )
        if self.years is not None and (
            isinstance(self.years, bool)
            or not isinstance(self.years, int)
            or self.years <= 0
        ):
            raise FilingSelectionError(
                SelectionFailureCode.INVALID_REQUEST,
                "years must be a positive integer",
            )
        if self.since is not None and not isinstance(self.since, date):
            raise FilingSelectionError(
                SelectionFailureCode.INVALID_REQUEST,
                "since must be a date",
            )

    @property
    def is_latest(self) -> bool:
        """Return whether this is the default or explicit latest mode."""
        return self.years is None and self.since is None

    def lower_bound(self, today: date) -> date | None:
        """Return the inclusive filing-date lower bound for history mode."""
        if self.since is not None:
            return self.since
        if self.years is None:
            return None
        target_year = today.year - self.years
        try:
            return today.replace(year=target_year)
        except ValueError:
            return today.replace(year=target_year, day=28)


@dataclass(frozen=True, slots=True)
class FilingMetadata:
    """Validated SEC metadata for one accession."""

    ticker: str
    cik: str
    accession: str
    form: str
    base_form: str
    is_amendment: bool
    amends_accession: str | None
    filing_date: date
    report_date: date
    primary_document: str
    acceptance_datetime: datetime | None = None


@dataclass(frozen=True, slots=True)
class SourceFiling:
    """Metadata paired with the edgartools handles needed after selection."""

    metadata: FilingMetadata
    company: object = field(compare=False, repr=False)
    filing: object = field(compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class FilingFamily:
    """One complete original filing plus its ordered amendments."""

    original: SourceFiling
    amendments: tuple[SourceFiling, ...]

    @property
    def members(self) -> tuple[SourceFiling, ...]:
        return (self.original, *self.amendments)

    @property
    def effective(self) -> SourceFiling:
        return self.members[-1]


class FilingSource(Protocol):
    """Metadata-only SEC source used by the deterministic selector."""

    def load(
        self,
        ticker: str,
        *,
        include_history: bool,
    ) -> tuple[SourceFiling, ...]: ...


class FilingSelector:
    """Validate and select amendment-aware periodic filing families."""

    def __init__(self, source: FilingSource) -> None:
        self.source = source

    def select(
        self,
        request: SelectionRequest,
        *,
        today: date,
    ) -> tuple[FilingFamily, ...]:
        filings = self.source.load(
            request.ticker,
            include_history=not request.is_latest,
        )
        unique = self._validate_and_deduplicate(filings, request.ticker)
        families = self._build_families(unique)
        lower_bound = request.lower_bound(today)
        if lower_bound is not None:
            families = tuple(
                family
                for family in families
                if family.effective.metadata.filing_date >= lower_bound
            )
        if not families:
            raise FilingSelectionError(
                SelectionFailureCode.NO_MATCHING_FILINGS,
                f"no matching periodic filings for {request.ticker}",
            )
        ordered = tuple(
            sorted(
                families,
                key=lambda family: (
                    _metadata_order_key(family.effective.metadata),
                ),
                reverse=True,
            )
        )
        return ordered[:1] if request.is_latest else ordered

    @staticmethod
    def _validate_and_deduplicate(
        filings: Sequence[SourceFiling],
        ticker: str,
    ) -> tuple[SourceFiling, ...]:
        unique: dict[str, SourceFiling] = {}
        for source_filing in filings:
            metadata = source_filing.metadata
            FilingSelector._validate_metadata(metadata, ticker)
            existing = unique.get(metadata.accession)
            if existing is not None:
                if existing.metadata != metadata:
                    raise FilingSelectionError(
                        SelectionFailureCode.INVALID_METADATA,
                        "conflicting metadata for accession "
                        f"{metadata.accession}",
                    )
                continue
            unique[metadata.accession] = source_filing
        return tuple(unique.values())

    @staticmethod
    def _validate_metadata(metadata: FilingMetadata, ticker: str) -> None:
        if metadata.ticker.strip().upper() != ticker:
            raise FilingSelectionError(
                SelectionFailureCode.INVALID_METADATA,
                f"ticker mismatch for accession {metadata.accession!r}",
            )
        if not metadata.cik.strip() or not metadata.cik.isdigit():
            raise FilingSelectionError(
                SelectionFailureCode.INVALID_METADATA,
                f"invalid CIK for accession {metadata.accession!r}",
            )
        if not _ACCESSION_RE.fullmatch(metadata.accession):
            raise FilingSelectionError(
                SelectionFailureCode.INVALID_METADATA,
                f"invalid accession {metadata.accession!r}",
            )
        if metadata.form not in SUPPORTED_FORMS:
            raise FilingSelectionError(
                SelectionFailureCode.INVALID_METADATA,
                f"unsupported form {metadata.form!r}",
            )
        expected_base = metadata.form.removesuffix("/A")
        expected_amendment = metadata.form.endswith("/A")
        if (
            metadata.base_form != expected_base
            or metadata.base_form not in SUPPORTED_BASE_FORMS
            or metadata.is_amendment is not expected_amendment
        ):
            raise FilingSelectionError(
                SelectionFailureCode.INVALID_METADATA,
                f"inconsistent form metadata for {metadata.accession}",
            )
        if metadata.amends_accession is not None:
            raise FilingSelectionError(
                SelectionFailureCode.INVALID_METADATA,
                "source metadata must not preselect an amended accession",
            )
        if not isinstance(metadata.filing_date, date) or not isinstance(
            metadata.report_date,
            date,
        ):
            raise FilingSelectionError(
                SelectionFailureCode.INVALID_METADATA,
                f"invalid dates for accession {metadata.accession}",
            )
        if not metadata.primary_document.strip():
            raise FilingSelectionError(
                SelectionFailureCode.INVALID_METADATA,
                f"missing primary document for accession {metadata.accession}",
            )
        if (
            metadata.acceptance_datetime is not None
            and metadata.acceptance_datetime.utcoffset() is None
        ):
            raise FilingSelectionError(
                SelectionFailureCode.INVALID_METADATA,
                "acceptance datetime must include a timezone for "
                f"{metadata.accession}",
            )

    @staticmethod
    def _build_families(
        filings: Sequence[SourceFiling],
    ) -> tuple[FilingFamily, ...]:
        grouped: dict[tuple[str, str, date], list[SourceFiling]] = {}
        for source_filing in filings:
            metadata = source_filing.metadata
            key = (metadata.cik, metadata.base_form, metadata.report_date)
            grouped.setdefault(key, []).append(source_filing)

        families: list[FilingFamily] = []
        for key, members in grouped.items():
            originals = [
                member
                for member in members
                if not member.metadata.is_amendment
            ]
            amendments = [
                member
                for member in members
                if member.metadata.is_amendment
            ]
            if not originals:
                raise FilingSelectionError(
                    SelectionFailureCode.INVALID_METADATA,
                    f"orphan amendment family {key}",
                )
            if len(originals) > 1:
                raise FilingSelectionError(
                    SelectionFailureCode.INVALID_METADATA,
                    f"multiple originals for filing family {key}",
                )
            original = originals[0]
            family_members = [original, *amendments]
            members_by_date: dict[date, list[SourceFiling]] = {}
            for member in family_members:
                members_by_date.setdefault(
                    member.metadata.filing_date,
                    [],
                ).append(member)
            if any(
                len(same_day) > 1
                and any(
                    member.metadata.acceptance_datetime is None
                    for member in same_day
                )
                for same_day in members_by_date.values()
            ):
                raise FilingSelectionError(
                    SelectionFailureCode.INVALID_METADATA,
                    "same-day filing family requires SEC acceptance datetime "
                    f"for {original.metadata.accession}",
                )
            ordered_amendments = sorted(
                amendments,
                key=lambda item: _metadata_order_key(item.metadata),
            )
            if any(
                _metadata_order_key(amendment.metadata)
                <= _metadata_order_key(original.metadata)
                for amendment in ordered_amendments
            ):
                raise FilingSelectionError(
                    SelectionFailureCode.INVALID_METADATA,
                    "amendment must be filed later than original "
                    f"{original.metadata.accession}",
                )
            linked = tuple(
                replace(
                    amendment,
                    metadata=replace(
                        amendment.metadata,
                        amends_accession=original.metadata.accession,
                    ),
                )
                for amendment in ordered_amendments
            )
            families.append(
                FilingFamily(
                    original=original,
                    amendments=linked,
                )
            )
        return tuple(families)


class EdgarFilingSource:
    """Load official SEC filing metadata through edgartools."""

    def __init__(
        self,
        *,
        company_factory: Callable[[str], object] = Company,
        identity: str | None = None,
        identity_setter: Callable[[str], object] = set_identity,
    ) -> None:
        self.company_factory = company_factory
        self.identity = identity
        self.identity_setter = identity_setter

    def load(
        self,
        ticker: str,
        *,
        include_history: bool,
    ) -> tuple[SourceFiling, ...]:
        normalized_ticker = ticker.strip().upper()
        identity = self.identity or load_settings().edgar_identity
        if not identity:
            raise FilingSelectionError(
                SelectionFailureCode.SEC_METADATA_UNAVAILABLE,
                "EDGAR_IDENTITY is required for SEC metadata access",
            )
        self.identity_setter(identity)
        try:
            company = self.company_factory(normalized_ticker)
        except Exception as error:  # noqa: BLE001
            raise FilingSelectionError(
                SelectionFailureCode.COMPANY_UNAVAILABLE,
                f"could not resolve company for {normalized_ticker}: "
                f"{type(error).__name__}",
            ) from error
        try:
            filings = company.get_filings(
                form=["10-Q", "10-K"],
                amendments=True,
                trigger_full_load=include_history,
            )
        except Exception as error:  # noqa: BLE001
            raise FilingSelectionError(
                SelectionFailureCode.SEC_METADATA_UNAVAILABLE,
                f"could not load SEC filing metadata for {normalized_ticker}: "
                f"{type(error).__name__}",
            ) from error

        cik = str(getattr(company, "cik", "")).zfill(10)
        mapped: list[SourceFiling] = []
        for filing in filings:
            try:
                form = str(filing.form)
                filing_date = _as_date(filing.filing_date, "filing_date")
                report_date = _as_date(
                    filing.period_of_report,
                    "report_date",
                )
                metadata = FilingMetadata(
                    ticker=normalized_ticker,
                    cik=cik,
                    accession=str(filing.accession_no),
                    form=form,
                    base_form=form.removesuffix("/A"),
                    is_amendment=form.endswith("/A"),
                    amends_accession=None,
                    filing_date=filing_date,
                    report_date=report_date,
                    primary_document=str(filing.primary_document),
                    acceptance_datetime=_as_datetime(
                        getattr(filing, "acceptance_datetime", None),
                        "acceptance_datetime",
                    ),
                )
            except FilingSelectionError:
                raise
            except Exception as error:  # noqa: BLE001
                raise FilingSelectionError(
                    SelectionFailureCode.INVALID_METADATA,
                    "could not map SEC filing metadata: "
                    f"{type(error).__name__}",
                ) from error
            mapped.append(
                SourceFiling(
                    metadata=metadata,
                    company=company,
                    filing=filing,
                )
            )
        return tuple(mapped)


def _as_date(value: object, field_name: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as error:
        raise FilingSelectionError(
            SelectionFailureCode.INVALID_METADATA,
            f"invalid {field_name}: {value!r}",
        ) from error


def _as_datetime(value: object, field_name: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as error:
            raise FilingSelectionError(
                SelectionFailureCode.INVALID_METADATA,
                f"invalid {field_name}: {value!r}",
            ) from error
    if parsed.utcoffset() is None:
        raise FilingSelectionError(
            SelectionFailureCode.INVALID_METADATA,
            f"{field_name} must include a timezone",
        )
    return parsed.astimezone(UTC)


def _metadata_order_key(metadata: FilingMetadata) -> tuple[date, datetime, str]:
    return (
        metadata.filing_date,
        metadata.acceptance_datetime or datetime.min.replace(tzinfo=UTC),
        metadata.accession,
    )
