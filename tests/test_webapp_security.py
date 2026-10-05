"""Loopback HTTP surface security: token, Host, Origin, POST-only, CSP, no secrets."""

from __future__ import annotations

import http.client
import json
import socket
import subprocess

import pytest
from webapp_fixtures import REPO_ROOT, build_fixture

TOKEN = "test-token-do-not-log-0123456789"
SENTINEL = "sk-sentinel-must-never-appear-3f9a"


@pytest.fixture
def fixture(tmp_path):
    return build_fixture(tmp_path / "fixture")


@pytest.fixture
def server(fixture, tmp_path, monkeypatch):
    """A real loopback server with deterministic paths but no real model client."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", SENTINEL)
    monkeypatch.setenv("DASHSCOPE_API_KEY", SENTINEL)
    monkeypatch.setenv("EDGAR_IDENTITY", SENTINEL)
    from thesis_tracker.webapp.server import WebApp

    app = WebApp(fact_db=fixture.fact_db, price_db=fixture.price_db,
                 card_db=fixture.card_db, job_db=tmp_path / "jobs.db",
                 token=TOKEN, port=0, start_worker=False)
    app.start()
    try:
        yield app
    finally:
        app.stop()


def request(server, path, *, method="GET", token=TOKEN, host=None, origin=None,
            body=None, headers=None):
    """One raw request; returns (status, headers, body bytes)."""
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    sent = dict(headers or {})
    sent["Host"] = host if host is not None else f"127.0.0.1:{server.port}"
    if origin is not None:
        sent["Origin"] = origin
    if token is not None:
        sent["Cookie"] = f"dsh_token={token}"
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        sent["Content-Type"] = "application/json"
    try:
        connection.request(method, path, body=payload, headers=sent)
        response = connection.getresponse()
        data = response.read()
        return response.status, dict(response.getheaders()), data
    finally:
        connection.close()


def json_body(server, path, **kwargs):
    """One request; returns (status, parsed payload)."""
    status, _, data = request(server, path, **kwargs)
    return status, json.loads(data.decode())


def test_a_valid_token_returns_the_read_only_data(server):
    status, payload = json_body(server, "/api/overview")
    assert status == 200
    assert sorted(payload["companies"]) == ["AAPL", "MSFT", "NVDA"]


def test_api_without_a_token_is_rejected(server):
    status, _, data = request(server, "/api/overview", token=None)
    assert status == 401
    assert json.loads(data)["error"]


def test_api_with_a_wrong_token_is_rejected(server):
    status, _, data = request(server, "/api/overview", token="not-the-token")
    assert status == 401
    assert "not-the-token" not in data.decode()


def test_the_token_is_accepted_from_a_query_parameter_as_well_as_a_cookie(server):
    status, _, _ = request(server, f"/api/overview?token={TOKEN}", token=None)
    assert status == 200
    status, _, _ = request(server, "/api/overview?token=wrong", token=None)
    assert status == 401
    status, _, body = request(server, f"/app.js?token={TOKEN}", token=None)
    assert status == 200
    assert TOKEN not in body.decode()


def test_the_token_is_accepted_from_a_header_as_well_as_a_cookie(server):
    status, _, _ = request(server, "/api/overview", token=None,
                           headers={"Authorization": f"Bearer {TOKEN}"})
    assert status == 200
    status, _, _ = request(server, "/api/overview", token=None,
                           headers={"X-DSH-Token": TOKEN})
    assert status == 200
    status, _, _ = request(server, "/api/overview", token=None,
                           headers={"Authorization": "Bearer wrong"})
    assert status == 401


@pytest.mark.parametrize("host", [
    "evil.example.com",
    "127.0.0.1",
    "localhost",
    "127.0.0.1:1",
    "127.0.0.1:99999",
    "127.0.0.1.evil.example.com",
    "",
])
def test_a_host_header_that_is_not_this_loopback_server_is_rejected(server, host):
    status, _, _ = request(server, "/api/overview", host=host)
    assert status == 403


@pytest.mark.parametrize("host", ["127.0.0.1:{port}", "localhost:{port}",
                                  "LOCALHOST:{port}"])
def test_the_two_allowed_host_names_are_accepted(server, host):
    status, _, _ = request(server, "/api/overview", host=host.format(port=server.port))
    assert status == 200


@pytest.mark.parametrize("origin", [
    "https://evil.example.com",
    "http://127.0.0.1:1",
    "null",
])
def test_a_foreign_origin_is_rejected(server, origin):
    status, _, _ = request(server, "/api/overview", origin=origin)
    assert status == 403


@pytest.mark.parametrize("origin", ["http://127.0.0.1:{port}", "http://localhost:{port}"])
def test_the_same_origin_is_accepted(server, origin):
    status, _, _ = request(server, "/api/overview", origin=origin.format(port=server.port))
    assert status == 200


def test_no_response_ever_offers_wildcard_cors(server):
    for path in ("/api/overview", "/api/cards", "/"):
        _, headers, _ = request(server, path)
        assert headers.get("Access-Control-Allow-Origin") is None
        assert headers.get("Access-Control-Allow-Credentials") is None


def test_every_response_carries_a_self_only_csp(server):
    for path in ("/", "/app.js", "/api/overview", "/api/cards", "/nope"):
        status, headers, _ = request(server, path)
        assert status in {200, 404}
        policy = headers["Content-Security-Policy"]
        assert policy.startswith("default-src 'self';")
        # Scripts can never be inline; only style attributes are relaxed, for the
        # price band's backend-computed marker positions.
        assert "script-src" not in policy
        assert "style-src 'self' 'unsafe-inline'" in policy
        assert "unsafe-eval" not in policy
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Referrer-Policy"] == "no-referrer"


def test_the_api_rejects_methods_other_than_get_and_post(server):
    for method in ("PUT", "PATCH", "DELETE"):
        status, _, _ = request(server, "/api/overview", method=method)
        assert status == 405


def test_state_changing_endpoints_only_accept_post(server):
    for method in ("GET", "HEAD"):
        status, _, _ = request(server, "/api/analyze", method=method)
        assert status == 405
    status, _, _ = request(server, "/api/analyze", method="POST",
                           body={"ticker": "AAPL", "confirm": True})
    assert status == 200


def test_the_root_sets_an_httponly_cookie_and_never_echoes_the_token(server):
    status, headers, data = request(server, f"/?token={TOKEN}", token=None)
    assert status == 302
    assert headers["Location"] == "/"
    cookie = headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert f"dsh_token={TOKEN}" in cookie
    assert TOKEN not in data.decode()


def test_an_unknown_api_path_is_404_without_leaking_paths(server):
    status, payload = json_body(server, "/api/does-not-exist")
    assert status == 404
    assert payload["error"] == "没有这个接口。"


def test_no_response_contains_any_environment_secret(server):
    paths = ["/", "/app.js", "/api/overview", "/api/companies/AAPL",
             "/api/cards", f"/api/cards/{'c' * 36}", "/api/jobs", "/api/usage",
             "/api/does-not-exist"]
    for path in paths:
        _, headers, data = request(server, path)
        assert SENTINEL not in data.decode(), path
        assert SENTINEL not in json.dumps(headers), path
        # The token only ever travels back to the page as its own cookie.
        assert TOKEN not in data.decode() or "dsh_token=" in data.decode(), path


def test_the_startup_url_never_appears_in_server_logs(server, capsys):
    request(server, f"/?token={TOKEN}", token=None)
    request(server, "/api/overview")
    captured = capsys.readouterr()
    assert TOKEN not in captured.out and TOKEN not in captured.err


def test_a_body_larger_than_the_limit_is_rejected(server):
    status, _, data = request(server, "/api/analyze", method="POST",
                              body={"ticker": "A" * 200_000, "confirm": True})
    assert status == 413
    assert b"sk-" not in data
    assert json.loads(data.decode())["error"] == "请求体超过上限。"


def test_malformed_json_is_rejected_without_a_traceback(server):
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    try:
        connection.request("POST", "/api/analyze", body=b"{not json",
                           headers={"Host": f"127.0.0.1:{server.port}",
                                    "Cookie": f"dsh_token={TOKEN}",
                                    "Content-Type": "application/json"})
        response = connection.getresponse()
        data = response.read()
    finally:
        connection.close()
    assert response.status == 400
    assert b"Traceback" not in data


def test_a_second_bind_on_the_same_port_fails_loudly(fixture, tmp_path):
    from thesis_tracker.webapp.server import WebApp

    first = WebApp(fact_db=fixture.fact_db, price_db=fixture.price_db,
                   card_db=fixture.card_db, job_db=tmp_path / "j.db",
                   token=TOKEN, port=0, start_worker=False)
    first.start()
    try:
        with pytest.raises(OSError):
            second = WebApp(fact_db=fixture.fact_db, price_db=fixture.price_db,
                            card_db=fixture.card_db, job_db=tmp_path / "j2.db",
                            token=TOKEN, port=first.port, start_worker=False)
            second.start()
    finally:
        first.stop()


def test_the_job_database_is_not_ignored_by_accident_but_is_untracked(tmp_path):
    """data/webapp/ must be gitignored and provably not tracked."""
    check = subprocess.run(["git", "check-ignore", "-v", "data/webapp/jobs.db"],
                           cwd=REPO_ROOT, capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    assert ".gitignore" in check.stdout
    tracked = subprocess.run(["git", "ls-files", "data/webapp"],
                             cwd=REPO_ROOT, capture_output=True, text=True)
    assert tracked.stdout.strip() == ""
    # And the real path the app would use is inside data/webapp, never data/cache.
    from thesis_tracker.webapp.jobs import DEFAULT_JOB_DB

    assert "cache" not in DEFAULT_JOB_DB.parts
    assert DEFAULT_JOB_DB.parent.name == "webapp"


def test_the_server_binds_loopback_only(server):
    assert server.host == "127.0.0.1"
    with socket.socket() as probe:
        probe.settimeout(5)
        assert probe.connect_ex(("127.0.0.1", server.port)) == 0


def test_an_oversized_body_is_always_answered_never_reset(server):
    """The 413 must reach the client every time, not only when the race is won.

    The server used to answer and close without reading the body it had refused;
    closing a socket that still holds unread data makes the kernel send a reset,
    and the client then saw a connection error instead of the 413 (one run in
    about fifteen).  Thirty in a row makes the old behaviour fail almost surely.
    """
    for _ in range(30):
        status, _, data = request(server, "/api/analyze", method="POST",
                                  body={"ticker": "A" * 200_000, "confirm": True})
        assert status == 413
        assert json.loads(data.decode())["error"] == "请求体超过上限。"
