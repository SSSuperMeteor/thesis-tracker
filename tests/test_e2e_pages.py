"""The workbench's pages in a real browser, on the miniature fixture databases.

AAPL has fresh prices and a card, NVDA has stale prices, MSFT has no prices at
all, so one run exercises the fresh, the expired and the empty branch of every
page.  No worker runs: queued jobs stay queued, which is what the polling and the
double-click checks need.
"""

from __future__ import annotations

import pytest
from e2e_support import requires_browser, run_scenario
from test_webapp_security import SENTINEL, TOKEN
from webapp_fixtures import build_fixture

pytestmark = requires_browser


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    import os

    root = tmp_path_factory.mktemp("pages")
    fixture = build_fixture(root / "fixture")
    os.environ["DEEPSEEK_API_KEY"] = SENTINEL
    from thesis_tracker.webapp.server import WebApp

    app = WebApp(fact_db=fixture.fact_db, price_db=fixture.price_db,
                 card_db=fixture.card_db, job_db=root / "jobs.db",
                 chat_db=root / "chat.db", pricing_path=root / "absent.json",
                 token=TOKEN, port=0, start_worker=False)
    app.start()
    try:
        conversation = app.chat.create_conversation("AAPL")
        message = app.chat.append_message(conversation["conversation_id"], role="user",
                                          text="现在怎么样？")
        job = app.store.create("chat_turn", {
            "conversation_id": conversation["conversation_id"],
            "message_id": message["message_id"], "ticker": "AAPL"})
        yield run_scenario("pages", app, token=TOKEN, extra={
            "card_id": fixture.card_id, "job_id": job["job_id"]}, timeout=240)
    finally:
        app.stop()
        os.environ.pop("DEEPSEEK_API_KEY", None)


@pytest.fixture
def checks(result):
    return result["checks"]


def test_the_overview_lists_every_company_and_leaks_nothing(checks):
    assert checks["overview_title"] == "总览"
    assert checks["overview_rows"] == 3
    assert checks["overview_leaks"] == []
    assert checks["overview_overflow_1440"] == 0


def test_the_price_state_is_a_sidebar_pill_that_opens_onto_the_command(checks):
    assert "过期" in checks["pill_text"]
    assert checks["pill_collapsed_at_first"] is True
    assert checks["pill_expanded_after_click"] is True
    assert checks["price_command_visible"] is True
    assert checks["copy_command_feedback"] is True
    assert checks["no_banner_in_the_page"] is True


def test_the_overview_is_a_matrix_of_blocks_with_a_sparkline_per_priced_company(checks):
    assert checks["matrix_blocks"] >= 10
    assert checks["matrix_has_a_10k_block"] is True
    assert checks["matrix_sparklines"] == 2          # AAPL and NVDA; MSFT has no prices
    assert checks["matrix_current_column"] is True
    assert checks["hover_lights_a_column"] is True


def test_the_narrow_overview_shows_filings_instead_of_a_blank_pane(checks):
    assert checks["overview_overflow_390"] == 0
    assert checks["narrow_visible_filing_cells"] >= 1


@pytest.mark.parametrize("tag, title", [("fresh", "AAPL"), ("stale", "NVDA"),
                                         ("noprice", "MSFT")])
def test_every_company_branch_opens_cleanly(checks, tag, title):
    assert checks[f"company_{tag}_title"] == title
    assert checks[f"company_{tag}_leaks"] == []


def test_the_company_page_has_a_chart_a_timeline_metric_blocks_and_card_tiles(checks):
    assert checks["company_chart_present"] == 1
    assert checks["company_chart_ranges"] == 3
    assert checks["company_timeline_blocks"] is True
    assert checks["company_metric_blocks"] == 8
    assert checks["company_card_tiles"] == 1
    assert checks["noprice_chart_is_a_sentence"] is True


def test_the_company_without_prices_says_so(checks):
    assert "没有价格数据" in checks["company_noprice_text"]


def test_the_stale_company_says_it_is_expired(checks):
    assert "已过期" in checks["company_stale_text"]


def test_no_company_page_logged_a_console_or_network_error(checks):
    assert checks["company_problems"] == 0


def test_a_fact_marker_on_a_card_leads_to_its_evidence_row(checks):
    assert checks["card_marker_count"] >= 1
    assert checks["card_evidence_rows"] >= 1
    assert checks["card_marker_highlights_row"] is True
    assert checks["card_has_disclaimer"] is True
    assert checks["card_leaks"] == []
    assert checks["card_problems"] == 0


def test_the_card_page_leads_with_a_verdict_and_four_metric_blocks_over_one_chart(checks):
    assert checks["card_verdict_badges"] == 2
    assert checks["card_metric_blocks"] == 4
    assert checks["card_chart_levels"] >= 3          # entry, stop, target (and the close)
    assert checks["card_has_no_second_band"] is True


def test_the_chart_switches_range_and_answers_to_the_pointer_and_the_keyboard(checks):
    assert checks["range_switch_pressed"] is True
    assert checks["keyboard_shows_tooltip"] == 1
    assert "复权收盘价" in checks["tooltip_text"] and "美元/股" in checks["tooltip_text"]
    assert checks["hover_shows_crosshair"] == 1


def test_a_card_can_be_followed_up_and_the_conversation_is_pinned_to_it(checks):
    assert checks["follow_up_opens_a_chat"] is True
    assert checks["follow_up_is_pinned"] is True


def test_the_job_list_shows_a_chat_turn_without_internal_identifiers(checks):
    assert checks["jobs_leaks"] == []
    assert "回答提问" in checks["jobs_text"]
    assert "公司 AAPL" in checks["jobs_text"]


def test_revisiting_a_job_page_does_not_stack_refresh_chains(checks):
    assert 2 <= checks["job_fetches_in_6s_after_revisits"] <= 5
    assert checks["job_leaks"] == []


def test_the_analysis_dialog_is_keyboard_safe_and_a_double_click_queues_one_job(checks):
    assert checks["dialog_focus_inside"] is True
    assert checks["dialog_closed_by_escape"] is True
    assert checks["dialog_focus_returns"] is True
    assert checks["dialog_warns_about_billing"] is True
    assert checks["double_click_creates_one_job"] == 1


def test_routing_handles_unknown_pages_history_and_overtaking_navigations(checks):
    assert checks["unknown_route_says_so"] is True
    assert checks["empty_company_route_says_so"] is True
    assert checks["back_returns_to_overview"] is True
    assert checks["forward_returns_to_company"] is True
    assert checks["last_navigation_wins"] == "任务"
    assert checks["reload_keeps_the_route"] == "任务"


@pytest.mark.parametrize("scale", ["1.25", "1.5"])
@pytest.mark.parametrize("page", ["overview", "company", "cards", "jobs"])
def test_browser_zoom_never_scrolls_the_page_sideways(checks, scale, page):
    assert checks[f"overflow_zoom_{scale}_{page}"] == 0
