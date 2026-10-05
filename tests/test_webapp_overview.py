"""Read-only coverage queries behind the local web app's overview page."""

from __future__ import annotations

import pytest
from webapp_fixtures import (
    AMENDMENT,
    COLLISION_QUARTER,
    COMPANIES,
    QUARTER_COLLISION,
    build_fixture,
)


@pytest.fixture
def fixture(tmp_path):
    return build_fixture(tmp_path / "fixture")


@pytest.fixture
def overview(fixture):
    from thesis_tracker.webapp.service import overview as build_overview

    return build_overview(fact_db=fixture.fact_db, price_db=fixture.price_db,
                          card_db=fixture.card_db, reference_date=fixture.reference_date)


def cell(overview, ticker, quarter):
    return overview["companies"][ticker]["periods"].get(quarter)


def test_overview_lists_every_company_in_the_fact_database(overview):
    assert sorted(overview["companies"]) == sorted(COMPANIES)
    assert overview["quarters"] == sorted(overview["quarters"])
    for ticker, company in overview["companies"].items():
        assert company["ticker"] == ticker
        assert company["cik"] == {"AAPL": "0000320193", "NVDA": "0001045810",
                                  "MSFT": "0000789019"}[ticker]
        assert company["latest_period_end"]
        assert company["filing_count"] > 0


def test_quarter_keys_follow_the_calendar_quarter_of_the_period_end(overview):
    """AAPL's March quarter and NVDA's April quarter are different columns."""
    aapl = overview["companies"]["AAPL"]["periods"]
    assert aapl["2024Q4"]["accession"] == "0000320193-25-000008"
    assert aapl["2024Q4"]["period_end"] == "2024-12-28"
    assert aapl["2025Q1"]["period_end"] == "2025-03-29"
    assert aapl["2025Q2"]["period_end"] == "2025-06-28"
    # The April quarter belongs to the February-April column, not to Q1 or Q3.
    nvda = overview["companies"]["NVDA"]["periods"]
    assert nvda["2025Q2"]["period_end"] == "2025-04-27"
    assert nvda["2025Q3"]["period_end"] == "2025-07-27"
    assert "2025Q1" not in nvda
    msft = overview["companies"]["MSFT"]["periods"]
    assert msft["2025Q1"]["period_end"] == "2025-03-31"
    assert msft["2026Q2"]["period_end"] == "2026-06-30"


def test_a_10k_and_a_10q_landing_in_one_calendar_quarter_share_the_cell(overview):
    entry = cell(overview, "AAPL", COLLISION_QUARTER)
    assert entry is not None
    # The newest period end in the column represents the cell ...
    assert entry["period_end"] == "2026-03-28"
    assert entry["form"] == "10-Q"
    # ... and the 10-K sharing the column stays visible in the same cell.
    assert sorted(item["key_form"] for item in entry["entries"]) == ["10-K", "10-Q"]
    other = next(item for item in entry["entries"]
                 if item["accession"] == QUARTER_COLLISION[0])
    assert other["period_end"] == QUARTER_COLLISION[2]
    assert other["filed_at"] == QUARTER_COLLISION[3]
    assert other["is_amendment"] is False


def test_a_sparse_amendment_is_flagged_and_never_replaces_the_original(overview):
    entry = cell(overview, "NVDA", "2026Q1")
    assert entry["accession"] == AMENDMENT[7]
    assert entry["is_amendment"] is False
    assert entry["amended_by"] == [AMENDMENT[0]]
    original = overview["companies"]["NVDA"]["filings"]
    amendments = [item for item in original if item["is_amendment"]]
    assert [item["accession"] for item in amendments] == [AMENDMENT[0]]
    assert amendments[0]["original_accession"] == AMENDMENT[7]
    assert amendments[0]["key_form"] == "10-K"


def test_missing_quarters_are_absent_from_the_row_not_zeroed(overview):
    """The fixture has no filing whose period ends in the May-July 2024 window."""
    for ticker in COMPANIES:
        assert "2024Q2" not in overview["companies"][ticker]["periods"]


def test_every_row_carries_the_price_window_latest_date_and_expiry(fixture, overview):
    expected = fixture.expectations["prices"]
    aapl = overview["companies"]["AAPL"]["price"]
    assert aapl["available"] is True
    assert aapl["start_date"] == expected["AAPL"]["start"]
    assert aapl["end_date"] == expected["AAPL"]["end"]
    assert aapl["rows"] == expected["AAPL"]["rows"]
    assert aapl["latest_close"] == f"{expected['AAPL']['latest_close']:.2f} 美元/股"
    assert aapl["stale"] is False
    assert aapl["age_days"] == 0

    nvda = overview["companies"]["NVDA"]["price"]
    assert nvda["available"] is True
    assert nvda["end_date"] == expected["NVDA"]["end"]
    assert nvda["stale"] is True
    assert nvda["age_days"] > overview["stale_after_days"]

    msft = overview["companies"]["MSFT"]["price"]
    assert msft["available"] is False
    assert msft["stale"] is True
    assert msft["start_date"] is None and msft["end_date"] is None
    assert msft["latest_close"] is None
    assert msft["reason"] == "no_data"


def test_expiry_threshold_comes_from_the_validator_D03_constant(fixture, overview):
    from thesis_tracker.decision import core

    assert overview["stale_after_days"] == core.PRICE_STALENESS_DAYS
    boundary = fixture.expectations["prices"]["NVDA"]["end"]
    from datetime import date, timedelta

    at_limit = (date.fromisoformat(boundary) + timedelta(days=core.PRICE_STALENESS_DAYS))
    beyond = at_limit + timedelta(days=1)
    from thesis_tracker.webapp.service import overview as build_overview

    inside = build_overview(fact_db=fixture.fact_db, price_db=fixture.price_db,
                            card_db=fixture.card_db, reference_date=at_limit.isoformat())
    outside = build_overview(fact_db=fixture.fact_db, price_db=fixture.price_db,
                             card_db=fixture.card_db, reference_date=beyond.isoformat())
    assert inside["companies"]["NVDA"]["price"]["stale"] is False
    assert outside["companies"]["NVDA"]["price"]["stale"] is True


def test_card_counts_come_from_the_archive(fixture, overview):
    assert overview["companies"]["AAPL"]["card_count"] == 1
    assert overview["companies"]["NVDA"]["card_count"] == 0
    assert overview["companies"]["MSFT"]["card_count"] == 0
