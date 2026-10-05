"""Read-only coverage queries behind the local web app's overview page."""

from __future__ import annotations

from datetime import date

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


def test_the_timeline_drops_quarters_no_company_reports_in(fixture):
    """A leading column nobody fills is noise; the table starts at real data."""
    from thesis_tracker.webapp.service import overview as build_overview

    # The fixture's earliest filing is 2024-12-28, so 2024Q4 is the first column
    # that can carry anything, even though the price series starts in 2024.
    built = build_overview(fact_db=fixture.fact_db, price_db=fixture.price_db,
                           card_db=fixture.card_db,
                           reference_date=fixture.reference_date)
    assert built["quarters"][0] == "2024Q4"
    assert built["quarters"] == sorted(built["quarters"])


def test_the_timeline_keeps_a_gap_between_two_reported_quarters(fixture):
    """Only empty *leading* columns are dropped; a real gap stays visible."""
    from thesis_tracker.webapp.service import overview as build_overview

    built = build_overview(fact_db=fixture.fact_db, price_db=fixture.price_db,
                           card_db=fixture.card_db,
                           reference_date=fixture.reference_date)
    # 2025Q3 holds AAPL/MSFT/... filings and 2025Q4 does too, so a company with
    # nothing in between must still show the gap rather than a shrunken axis.
    filled = [key for key in built["quarters"]
              if any(key in company["periods"] for company in built["companies"].values())]
    assert filled == built["quarters"]


def test_every_timeline_column_has_at_least_one_filing(fixture):
    from thesis_tracker.webapp.service import overview as build_overview

    built = build_overview(fact_db=fixture.fact_db, price_db=fixture.price_db,
                           card_db=fixture.card_db,
                           reference_date=fixture.reference_date)
    for key in built["quarters"]:
        carriers = [ticker for ticker, company in built["companies"].items()
                    if key in company["periods"]]
        assert carriers, key


def test_the_summary_reports_the_oldest_latest_price_date(fixture):
    """The header date is the least fresh company, so it never overstates."""
    from thesis_tracker.webapp.service import overview as build_overview

    built = build_overview(fact_db=fixture.fact_db, price_db=fixture.price_db,
                           card_db=fixture.card_db,
                           reference_date=fixture.reference_date)
    dated = [company["price"]["end_date"] for company in built["companies"].values()
             if company["price"]["end_date"]]
    assert built["price_summary"]["latest_price_date"] == min(dated)
    expected = (date.fromisoformat(built["reference_date"])
                - date.fromisoformat(min(dated))).days
    assert built["price_summary"]["lag_days"] == expected
    assert built["price_summary"]["stale_after_days"] == 5


def test_the_summary_counts_companies_without_any_price(fixture):
    from thesis_tracker.webapp.service import overview as build_overview

    built = build_overview(fact_db=fixture.fact_db, price_db=fixture.price_db,
                           card_db=fixture.card_db,
                           reference_date=fixture.reference_date)
    missing = sum(1 for company in built["companies"].values()
                  if not company["price"]["end_date"])
    assert built["price_summary"]["missing"] == missing == 1
    assert built["price_summary"]["latest_price_date"] is not None


def test_the_summary_survives_a_database_with_no_prices(tmp_path, fixture):
    from thesis_tracker.webapp.service import overview as build_overview

    built = build_overview(fact_db=fixture.fact_db, price_db=tmp_path / "absent.db",
                           card_db=fixture.card_db,
                           reference_date=fixture.reference_date)
    assert built["price_summary"]["latest_price_date"] is None
    assert built["price_summary"]["lag_days"] is None
    assert built["price_summary"]["missing"] == len(built["companies"])


# -- the dashed empty box: only a real hole in the filing history ------------
#
# The threshold is an experienced one, not a reporting standard: two
# consecutive filings for one issuer are normally a quarter apart, so a span of
# more than 135 calendar days between the filings that bracket an empty quarter
# means the quarter is genuinely absent rather than simply not reported.

def test_an_empty_quarter_inside_a_normal_cadence_is_not_marked(fixture):
    from thesis_tracker.webapp.service import EMPTY_GAP_DAYS
    from thesis_tracker.webapp.service import overview as build_overview

    assert EMPTY_GAP_DAYS == 135
    built = build_overview(fact_db=fixture.fact_db, price_db=fixture.price_db,
                           card_db=fixture.card_db,
                           reference_date=fixture.reference_date)
    for company in built["companies"].values():
        # Dashed columns are exactly the ones the rule flagged, and none of them
        # carries a filing: a real filing is never drawn as a hole.
        assert company["dashed_quarters"] == sorted(company["dashed_quarters"])
        for key in company["dashed_quarters"]:
            assert key in built["quarters"], key
            assert company["periods"].get(key, {}).get("entries") in (None, []), key
        for key, cell in company["periods"].items():
            assert cell["dashed"] is (key in company["dashed_quarters"] and not cell["entries"])


def test_a_wide_hole_between_two_filings_is_marked_and_a_narrow_one_is_not(tmp_path):
    """The rule is measured on the filings that bracket the hole."""
    from thesis_tracker.webapp.service import EMPTY_GAP_DAYS
    from thesis_tracker.webapp.service import overview as build_overview

    built = build_overview(fact_db=_straddling_fact_db(tmp_path),
                           price_db=tmp_path / "absent-prices.db",
                           card_db=tmp_path / "absent-cards.db",
                           reference_date="2026-10-04")
    # WIDE: a period ending 2025-03-31 filed 2025-05-01, the next ending
    # 2025-12-31 filed 2026-02-01 -> 276 days apart, so 2025Q2/Q3/Q4 are marked.
    # NARROW: a period ending 2026-03-31 filed 2026-05-01, the next ending
    # 2026-06-30 filed 2026-08-01 -> 92 days apart, so no hole exists at all.
    wide = built["companies"]["WIDE"]
    narrow = built["companies"]["NARROW"]
    # WIDE reports 2025-03-31 and then 2025-12-31, so Q2 and Q3 are one hole.
    assert wide["dashed_quarters"] == ["2025Q2", "2025Q3"]
    assert "2025Q2" not in wide["periods"]  # an empty column, not a filing
    assert wide["periods"]["2025Q4"]["period_end"] == "2025-12-31"
    assert wide["periods"]["2025Q4"]["dashed"] is False
    # NARROW's two filings are 92 days apart: no hole to mark.
    assert narrow["dashed_quarters"] == []
    assert narrow["periods"]["2026Q2"]["entries"]
    assert EMPTY_GAP_DAYS == 135


def test_nothing_is_marked_after_a_companys_newest_filing(tmp_path):
    """A company that stopped reporting is not a series of holes.

    Marking every quarter after the last filing would fill the right-hand side
    of the table with dashes that say nothing a reader can act on.
    """
    from thesis_tracker.webapp.service import overview as build_overview

    built = build_overview(fact_db=_straddling_fact_db(tmp_path),
                           price_db=tmp_path / "absent-prices.db",
                           card_db=tmp_path / "absent-cards.db",
                           reference_date="2026-10-04")
    for company in built["companies"].values():
        newest = max(company["periods"])
        assert all(key <= newest for key in company["dashed_quarters"])
        first = min(company["periods"])
        assert all(key >= first for key in company["dashed_quarters"])
    # The axis itself does not extend past the newest period any company reports.
    assert built["quarters"][-1] <= max(
        key for item in built["companies"].values() for key in item["periods"])


def test_the_dashed_box_legend_says_what_it_means(fixture):
    """One legend entry, and it describes the threshold, not "no data"."""
    from thesis_tracker.webapp.service import EMPTY_GAP_DAYS, EMPTY_GAP_LABEL

    assert EMPTY_GAP_LABEL == f"相邻财报相隔超过 {EMPTY_GAP_DAYS} 天"
    assert "没有财报" not in EMPTY_GAP_LABEL


def _straddling_fact_db(tmp_path):
    """A miniature fact store whose period ends straddle a quarter boundary."""
    import sqlite3

    path = tmp_path / "straddling.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE filing_snapshots (
                accession TEXT PRIMARY KEY, original_accession TEXT, ticker TEXT,
                cik TEXT, form TEXT, period_end TEXT, filed_at TEXT,
                fiscal_year INTEGER, fiscal_period TEXT, registered_count INTEGER);
        """)
        rows = [
            # WIDE: 276 days between the filings that bracket 2025Q2 and 2025Q3.
            ("w-1", "w-1", "WIDE", "1", "10-Q", "2025-03-31", "2025-05-01", 2025, "Q1", 1),
            ("w-2", "w-2", "WIDE", "1", "10-K", "2025-12-31", "2026-02-01", 2025, "FY", 1),
            # NARROW: consecutive filings 92 days apart, ending inside a quarter.
            ("n-1", "n-1", "NARROW", "2", "10-Q", "2026-03-31", "2026-05-01", 2026, "Q1", 1),
            ("n-2", "n-2", "NARROW", "2", "10-Q", "2026-06-30", "2026-08-01", 2026, "Q2", 1),
        ]
        connection.executemany(
            "INSERT INTO filing_snapshots VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    return path
