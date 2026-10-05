"""Company page queries: filings, price range, computable metrics, archived cards."""

from __future__ import annotations

import pytest
from webapp_fixtures import AMENDMENT, build_fixture


@pytest.fixture
def fixture(tmp_path):
    return build_fixture(tmp_path / "fixture")


@pytest.fixture
def page(fixture):
    from thesis_tracker.webapp.service import company_page

    return company_page(fact_db=fixture.fact_db, price_db=fixture.price_db,
                        card_db=fixture.card_db, ticker="AAPL",
                        as_of=fixture.expectations["metric_envelopes"]["AAPL"]["data_end_date"],
                        reference_date=fixture.reference_date)


def test_company_page_lists_every_filing_newest_period_first(fixture, page):
    assert page["ticker"] == "AAPL"
    assert page["cik"] == "0000320193"
    periods = [item["period_end"] for item in page["filings"]]
    assert periods == sorted(periods, reverse=True)
    assert periods[0] == "2026-06-27"
    assert page["filings"][0]["form"] == "10-Q"
    assert page["filings"][0]["key_form"] == "10-Q"
    assert page["filings"][0]["accession"] == "0000320193-26-000020"
    assert all(item["ticker"] == "AAPL" for item in page["filings"])


def test_amendments_stay_in_the_filing_list_of_their_own_ticker(fixture):
    from thesis_tracker.webapp.service import company_page

    nvda = company_page(fact_db=fixture.fact_db, price_db=fixture.price_db,
                        card_db=fixture.card_db, ticker="NVDA",
                        reference_date=fixture.reference_date)
    amendments = [item for item in nvda["filings"] if item["is_amendment"]]
    assert [item["accession"] for item in amendments] == [AMENDMENT[0]]
    originals = [item for item in nvda["filings"] if item["accession"] == AMENDMENT[7]]
    assert len(originals) == 1 and originals[0]["is_amendment"] is False


def test_price_section_reports_range_rows_and_d03_expiry(fixture, page):
    price = page["price"]
    assert price["available"] is True
    assert price["start_date"] and price["end_date"] and price["rows"] > 0
    assert price["stale_after_days"] if "stale_after_days" in price else True
    assert page["stale_after_days"] == 5
    assert price["stale"] is False
    assert price["latest_close"].endswith("美元/股")
    assert price["windows"] and price["windows"][0]["provider"] == "tiingo"


def test_metrics_come_from_the_existing_tool_with_shared_display_rules(fixture, page):
    expected = fixture.expectations["metric_envelopes"]["AAPL"]
    assert page["fundamentals"]["status"] == expected["status"]
    assert page["fundamentals"]["data_end_date"] == expected["data_end_date"]
    metrics = {item["name"]: item for item in page["fundamentals"]["metrics"]}
    assert list(metrics) == [
        "gross_margin_trend", "accruals_ratio", "cash_conversion",
        "ar_growth_vs_rev_growth", "net_buyback_yield", "diluted_share_count_yoy",
        "interest_coverage", "net_debt_to_ebitda",
    ]
    for name, value in expected["values"].items():
        item = metrics[name]
        assert item["status"] == "ok"
        assert item["fact_id"] and item["period_end"]
        assert item["display"] is not None and item["display"] != str(value)
    assert metrics["gross_margin_trend"]["display"].endswith("%")
    assert metrics["net_debt_to_ebitda"]["display"].endswith(" 倍")
    assert metrics["ar_growth_vs_rev_growth"]["display"].endswith(" 个百分点")
    # A metric the tool cannot compute keeps its reason instead of a guessed value.
    unavailable = [item for item in page["fundamentals"]["metrics"]
                   if item["status"] != "ok"]
    assert unavailable, "the fixture must cover an uncomputable metric"
    for item in unavailable:
        assert item["display"] is None
        assert item["reason"] and item["reason"]["code"]


def test_display_values_match_the_shared_evidence_formatter(fixture, page):
    """The page must not invent its own number formatting."""
    from thesis_tracker.decision.evidence import display_text
    from thesis_tracker.financial.tool import get_fundamental_metrics

    envelope = get_fundamental_metrics("AAPL", as_of=page["as_of"], db_path=fixture.fact_db)
    for item in page["fundamentals"]["metrics"]:
        metric = envelope["data"]["metrics"][item["name"]]
        assert item["display"] == display_text(metric["value"], metric["unit"],
                                               name=item["name"])


def test_company_page_includes_only_this_company_cards(fixture, page):
    assert [item["card_id"] for item in page["cards"]] == [fixture.card_id]
    assert page["cards"][0]["ticker"] == "AAPL"
    assert page["cards"][0]["action"] == "分批"
    assert page["cards"][0]["horizon"] == "中期"
    assert page["cards"][0]["validator_version"] == "decision-validator-3"
    assert page["cards"][0]["prompt_version"] == (
        "decision-agent-v5-entry-stop-rules-2026-10-04")


def test_a_ticker_without_filings_is_reported_as_missing(fixture):
    from thesis_tracker.webapp.service import company_page

    with pytest.raises(KeyError):
        company_page(fact_db=fixture.fact_db, price_db=fixture.price_db,
                     card_db=fixture.card_db, ticker="ZZZZ",
                     reference_date=fixture.reference_date)


def test_card_list_filters_by_company_and_horizon(fixture):
    from thesis_tracker.webapp.service import card_list

    assert len(card_list(card_db=fixture.card_db)) == 1
    assert card_list(card_db=fixture.card_db, ticker="NVDA") == []
    assert len(card_list(card_db=fixture.card_db, horizon="mid")) == 1
    assert card_list(card_db=fixture.card_db, horizon="short") == []


def test_missing_price_database_is_not_an_error(fixture, tmp_path):
    from thesis_tracker.webapp.service import company_page

    page = company_page(fact_db=fixture.fact_db, price_db=tmp_path / "absent.db",
                        card_db=fixture.card_db, ticker="AAPL",
                        reference_date=fixture.reference_date)
    assert page["price"]["available"] is False
    assert page["price"]["reason"] == "no_data"
    assert page["filings"]


def test_usage_average_comes_from_the_model_call_log(fixture):
    from thesis_tracker.webapp.service import usage_average

    usage = usage_average(fixture.card_db)
    assert usage["analyses"] == 1
    assert usage["input_tokens"] == 22262
    assert usage["output_tokens"] == 6415
    assert usage["cache_hit_tokens"] == 9344
    assert not any("usd" in key.lower() or "cost" in key.lower() or "amount" in key.lower()
                   for key in usage)


def test_usage_average_of_an_empty_archive_is_null_not_zero(tmp_path):
    from thesis_tracker.webapp.service import usage_average

    usage = usage_average(tmp_path / "absent.db")
    assert usage == {"analyses": 0, "input_tokens": None, "output_tokens": None,
                     "cache_hit_tokens": None, "window": 5}


def test_the_price_fetch_time_is_a_local_minute_string_not_an_iso_timestamp(page):
    window = page["price"]["windows"][0]
    assert window["retrieved_at_display"] is not None
    assert len(window["retrieved_at_display"]) == len("2026-10-05 00:40")
    assert "T" not in window["retrieved_at_display"]
    assert "+00:00" not in window["retrieved_at_display"]


def test_the_company_page_carries_a_chart_with_three_ranges(page):
    chart = page["chart"]
    assert chart["available"] is True
    assert [item["key"] for item in chart["ranges"]] == ["3m", "1y", "5y"]
    assert chart["ranges"][0]["levels"] == []   # a company page has no card levels


def test_the_chart_ends_on_the_newest_stored_price(page):
    assert page["chart"]["ranges"][0]["points"][-1]["date"] == page["price"]["end_date"]


def test_a_company_without_prices_has_no_chart_and_says_why(fixture):
    from thesis_tracker.webapp.service import company_page

    msft = company_page(fact_db=fixture.fact_db, price_db=fixture.price_db,
                        card_db=fixture.card_db, ticker="MSFT", as_of="2026-09-07",
                        reference_date=fixture.reference_date)
    assert msft["chart"]["available"] is False
    assert msft["chart"]["reason"]
