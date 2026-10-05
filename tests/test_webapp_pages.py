"""The pages and their assets are served, and the page content is driven by the API."""

from __future__ import annotations

import json
import re

import pytest
from test_webapp_security import TOKEN, request
from webapp_fixtures import build_fixture


@pytest.fixture
def fixture(tmp_path):
    return build_fixture(tmp_path / "fixture")


@pytest.fixture
def server(fixture, tmp_path):
    from thesis_tracker.webapp.server import WebApp

    app = WebApp(fact_db=fixture.fact_db, price_db=fixture.price_db,
                 card_db=fixture.card_db, job_db=tmp_path / "jobs.db",
                 token=TOKEN, port=0, start_worker=False)
    app.start()
    try:
        yield app
    finally:
        app.stop()


def get(server, path, **kwargs):
    status, headers, body = request(server, path, **kwargs)
    return status, headers, body


def test_the_shell_page_is_served_with_the_right_type(server):
    status, headers, body = get(server, "/")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    text = body.decode()
    assert '<html lang="zh-CN">' in text
    assert "app.js" in text and "app.css" in text


def test_the_script_and_stylesheet_are_served_with_their_types(server):
    status, headers, body = get(server, "/app.js")
    assert status == 200
    assert headers["Content-Type"].startswith("text/javascript")
    assert b"api(" in body
    status, headers, body = get(server, "/app.css")
    assert status == 200
    assert headers["Content-Type"].startswith("text/css")
    assert b"--paper" in body


def test_every_served_asset_carries_the_self_only_csp(server):
    for path in ("/", "/app.js", "/app.css"):
        _, headers, _ = get(server, path)
        assert headers["Content-Security-Policy"].startswith("default-src 'self'")


def test_assets_need_the_token_too(server):
    status, _, _ = get(server, "/app.js", token=None)
    assert status == 401


def test_a_path_traversal_attempt_is_refused(server):
    for path in ("/../server.py", "/..%2fserver.py", "/%2e%2e/app.css"):
        status, _, body = get(server, path)
        assert status in {404, 400}, path
        assert b"def " not in body


def test_an_unknown_asset_is_404(server):
    status, _, body = get(server, "/nothing.css")
    assert status == 404
    assert "没有这个文件" in body.decode()


def test_the_read_only_endpoints_serve_the_fixture_data(server, fixture):
    status, _, body = get(server, "/api/overview")
    payload = json.loads(body)
    assert status == 200
    assert sorted(payload["companies"]) == ["AAPL", "MSFT", "NVDA"]
    assert payload["stale_after_days"] == 5

    status, _, body = get(server, "/api/companies/AAPL")
    page = json.loads(body)
    assert status == 200 and page["ticker"] == "AAPL"
    assert page["fundamentals"]["metrics"]

    status, _, body = get(server, "/api/cards")
    assert [card["card_id"] for card in json.loads(body)["cards"]] == [fixture.card_id]

    status, _, body = get(server, f"/api/cards/{fixture.card_id}")
    detail = json.loads(body)
    assert status == 200 and detail["facts"] and detail["price_band"]

    status, _, body = get(server, "/api/companies/ZZZZ")
    assert status == 404


# Endpoints the page only ever calls with POST.  A GET must answer 405, which is
# what proves the route exists without pretending it is a read.
POST_ONLY_LITERALS = {"/api/analyze", "/api/companies/conversations",
                      "/api/proposals/confirm", "/api/proposals/dismiss"}
# Literals that also need a parameter to answer; they are checked separately so
# the loop below stays a plain "this route exists" check.
LITERALS_NEEDING_A_TICKER = {"/api/companies/conversations"}


def test_the_page_script_only_asks_for_endpoints_the_server_has(server):
    """Every URL the frontend builds must exist on the server.

    A POST-only endpoint answers a GET with 405, which proves the route exists;
    anything else must be a real read endpoint, never a 404.  A literal whose
    path carries an id is not in the script's fixed set, so this stays a check of
    the routes the page names outright.
    """
    _, _, script = get(server, "/app.js")
    literals = set(re.findall(r'"(/api/[a-z/]*)"', script.decode()))
    assert "/api/overview" in literals and "/api/analyze" in literals
    assert "/api/companies/conversations" in literals
    for literal in sorted(literals - LITERALS_NEEDING_A_TICKER):
        status, _, _ = get(server, literal)
        if literal in POST_ONLY_LITERALS:
            assert status == 405, (literal, status)
        else:
            assert status == 200, (literal, status)
    # The company conversation list is a real read once it knows the company,
    # and a GET stays refused where only a POST is meaningful.
    status, _, body = get(server, "/api/companies/conversations?ticker=AAPL")
    assert status == 200 and json.loads(body)["ticker"] == "AAPL"
    status, _, _ = get(server, "/api/companies/conversations")
    assert status == 404  # no company named, so nothing to list


def test_the_company_page_numbers_match_the_overview(server):
    _, _, body = get(server, "/api/companies/AAPL")
    page = json.loads(body)
    _, _, overview_body = get(server, "/api/overview")
    company = json.loads(overview_body)["companies"]["AAPL"]
    assert page["price"]["start_date"] == company["price"]["start_date"]
    assert page["price"]["end_date"] == company["price"]["end_date"]
    assert page["price"]["latest_close"] == company["price"]["latest_close"]
    assert len(page["cards"]) == company["card_count"]


def test_no_page_or_api_response_contains_a_secret(server):
    from test_webapp_security import SENTINEL

    for path in ("/", "/app.js", "/app.css", "/api/overview", "/api/companies/AAPL",
                 f"/api/cards/{'c' * 36}", "/api/usage", "/api/jobs"):
        _, _, body = get(server, path)
        assert SENTINEL not in body.decode(), path


def test_the_page_header_wraps_instead_of_clipping_its_second_line():
    """The creation time must never be cut off; it wraps on a narrow window.

    A single-line header with `overflow: hidden` truncated it silently, which is
    the kind of loss a reader cannot even notice.
    """
    from pathlib import Path

    css = (Path(__file__).resolve().parents[1]
           / "src/thesis_tracker/webapp/static/app.css").read_text(encoding="utf-8")
    block = re.search(r"\.page-head\s*\{([^}]*)\}", css)
    assert block is not None
    rules = block.group(1)
    assert "flex-wrap: wrap" in rules
    assert "overflow: hidden" not in rules
    meta = re.search(r"\.page-head \.meta\s*\{([^}]*)\}", css)
    assert meta is not None
    assert "white-space: nowrap" not in meta.group(1)



def test_a_locked_database_is_reported_as_busy_with_a_way_forward(tmp_path, monkeypatch):
    """prices-ingest writing to prices.db for a moment is not "unreadable"."""
    import sqlite3

    from test_webapp_security import TOKEN, json_body
    from webapp_fixtures import build_fixture

    from thesis_tracker.webapp.server import WebApp

    fixture = build_fixture(tmp_path / "fixture")

    def locked(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("thesis_tracker.webapp.service.store.price_bounds", locked)
    app = WebApp(fact_db=fixture.fact_db, price_db=fixture.price_db,
                 card_db=fixture.card_db, job_db=tmp_path / "jobs.db", token=TOKEN,
                 port=0, start_worker=False)
    app.start()
    try:
        status, payload = json_body(app, "/api/overview")
    finally:
        app.stop()
    assert status == 503
    assert "占用" in payload["error"]
    assert "稍后" in payload["error"]


def test_a_database_that_is_not_locked_but_broken_keeps_the_generic_message(
        tmp_path, monkeypatch):
    import sqlite3

    from test_webapp_security import TOKEN, json_body
    from webapp_fixtures import build_fixture

    from thesis_tracker.webapp.server import WebApp

    fixture = build_fixture(tmp_path / "fixture")

    def broken(*_args, **_kwargs):
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr("thesis_tracker.webapp.service.store.price_bounds", broken)
    app = WebApp(fact_db=fixture.fact_db, price_db=fixture.price_db,
                 card_db=fixture.card_db, job_db=tmp_path / "jobs.db", token=TOKEN,
                 port=0, start_worker=False)
    app.start()
    try:
        status, payload = json_body(app, "/api/overview")
    finally:
        app.stop()
    assert status == 500
    assert "不可读取" in payload["error"]
