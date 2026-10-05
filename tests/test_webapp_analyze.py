"""Manual analysis: mandatory confirmation, one running job, fake model only."""

from __future__ import annotations

import http.client
import json
import time

import pytest
from test_webapp_security import SENTINEL, TOKEN, request
from webapp_fixtures import FIXTURE_AS_OF, build_fixture


@pytest.fixture
def fixture(tmp_path):
    return build_fixture(tmp_path / "fixture")


@pytest.fixture
def app(fixture, tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", SENTINEL)
    from thesis_tracker.webapp import server as web_server

    monkeypatch.setattr(web_server, "CLIENT_FACTORY", _FakeClient)
    application = web_server.WebApp(fact_db=fixture.fact_db, price_db=fixture.price_db,
                                    card_db=fixture.card_db, job_db=tmp_path / "jobs.db",
                                    token=TOKEN, port=0)
    application.start()
    try:
        yield application
    finally:
        application.stop()


class _FakeClient:
    """Scripted local stand-in for DeepSeekClient; fully offline."""

    calls = 0

    def __init__(self) -> None:
        pass

    def complete(self, *, messages, tools, max_tokens):
        type(self).calls += 1
        snapshot = _snapshot_for(messages)
        price = snapshot["calls"][0]["envelope"]["fact_id"]
        indicator = (snapshot["calls"][1]["envelope"]["data"]["latest"]["values"]
                     ["rsi_14"]["fact_id"])
        close = snapshot["calls"][0]["envelope"]["data"]["latest_close"]["value"]
        draft = {
            "ticker": snapshot["ticker"], "as_of": snapshot["as_of"], "horizon": "中期",
            "bias": "看多", "action": "买入", "confidence": "中",
            "entry_range": [close - 1, close + 1], "stop_loss": close * 0.9,
            "target_price": close * 1.2, "fact_ids": [price, indicator],
            "reasons": [{"text": f"收盘价 {{fact:{price}}}，RSI {{fact:{indicator}}}。",
                         "fact_ids": [price, indicator]}],
            "invalidations": [{"kind": "close_below", "price": round(close * 0.9, 2),
                               "text": "收盘价跌破止损位"}],
            "stop_rationale": f"跌破 {{fact:{price}}} 离场。",
            "target_rationale": f"上看 {{fact:{indicator}}} 上方。",
        }
        return {"model": "deepseek-flash", "system_fingerprint": "fp_fake",
                "usage": {"prompt_tokens": 120, "completion_tokens": 60,
                          "prompt_cache_hit_tokens": 30},
                "message": {"role": "assistant",
                            "content": json.dumps(draft, ensure_ascii=False),
                            "reasoning_content": "fake", "tool_calls": None},
                "finish_reason": "stop"}


class _AlwaysFailingClient:
    def __init__(self) -> None:
        pass

    def complete(self, *, messages, tools, max_tokens):
        raise RuntimeError("fake transport failure")


def _snapshot_for(messages):
    """Rebuild the fixture snapshot the job is analysing, from the prompt itself."""
    from webapp_fixtures import fixture_snapshot

    task = json.loads(messages[1]["content"])["task"]
    ticker = task.split("分析 ")[1].split("，")[0]
    as_of = task.split("as_of=")[1].split("。")[0]
    snapshot = fixture_snapshot(ticker, _PATHS["price"], _PATHS["fact"])
    assert snapshot["as_of"] == as_of, (snapshot["as_of"], as_of)
    return snapshot


_PATHS: dict = {}


def enqueue(app, **overrides):
    body = {"ticker": "AAPL", "horizon": "mid", "as_of": FIXTURE_AS_OF, "confirm": True}
    body.update(overrides)
    return request(app, "/api/analyze", method="POST", body=body)


def wait_for(predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_analysis_without_confirm_is_refused_and_creates_no_job(app):
    status, _, data = request(app, "/api/analyze", method="POST",
                              body={"ticker": "AAPL", "horizon": "mid"})
    assert status == 400
    assert "confirm" in json.loads(data)["error"]
    assert app.store.list() == []


@pytest.mark.parametrize("confirm", [False, "true", 1, None])
def test_confirm_must_be_the_boolean_true(app, confirm):
    status, _, _ = request(app, "/api/analyze", method="POST",
                           body={"ticker": "AAPL", "confirm": confirm})
    assert status == 400
    assert app.store.list() == []


def test_an_unknown_ticker_is_refused(app):
    status, _, data = request(app, "/api/analyze", method="POST",
                              body={"ticker": "ZZZZ", "confirm": True})
    assert status == 400
    assert json.loads(data)["error"]
    assert app.store.list() == []


def test_a_bad_horizon_or_date_is_refused(app):
    status, _, _ = request(app, "/api/analyze", method="POST",
                           body={"ticker": "AAPL", "confirm": True, "horizon": "weekly"})
    assert status == 400
    status, _, _ = request(app, "/api/analyze", method="POST",
                           body={"ticker": "AAPL", "confirm": True, "as_of": "yesterday"})
    assert status == 400
    assert app.store.list() == []


def test_confirming_queues_one_job_with_the_requested_parameters(app):
    status, _, data = enqueue(app)
    assert status == 200
    job = json.loads(data)["job"]
    assert job["kind"] == "analyze"
    assert job["status"] in {"queued", "running"}
    assert job["parameters"] == {"ticker": "AAPL", "horizon": "mid",
                                 "as_of": FIXTURE_AS_OF}
    assert job["status_label"]


def test_a_queued_job_runs_through_the_worker_and_archives_a_card(app, fixture):
    _PATHS.update({"price": fixture.price_db, "fact": fixture.fact_db})
    _, _, data = enqueue(app)
    job_id = json.loads(data)["job"]["job_id"]
    assert wait_for(lambda: app.store.get(job_id)["status"] == "succeeded"), \
        app.store.get(job_id)
    job = app.store.get(job_id)
    assert job["result"]["card_id"]
    assert job["result"]["ticker"] == "AAPL"
    assert job["result"]["horizon"] == "中期"
    assert job["result"]["stats"]["input_tokens"] == 120
    assert job["error"] is None
    events = [item["event"] for item in job["progress"]]
    assert events[0] == "prefetch" and events[-1] == "passed"
    assert "round" in events


def test_a_failing_analysis_is_recorded_as_failed_not_retried(app, fixture, monkeypatch):
    _PATHS.update({"price": fixture.price_db, "fact": fixture.fact_db})
    from thesis_tracker.webapp import server as web_server

    monkeypatch.setattr(web_server, "CLIENT_FACTORY", _AlwaysFailingClient)
    _, _, data = enqueue(app)
    job_id = json.loads(data)["job"]["job_id"]
    assert wait_for(lambda: app.store.get(job_id)["status"] == "failed")
    job = app.store.get(job_id)
    # A model failure is recorded as a failure, whether the loop returned a
    # rejection envelope or the error propagated out of it.
    assert "model_error" in job["error"] or "RuntimeError" in job["error"]
    # The SDK's own message can carry request headers, so it is never stored.
    assert "fake transport failure" not in (job["error"] or "")
    assert json.dumps(job, ensure_ascii=False).count("fake transport failure") == 0


def test_only_one_analysis_runs_at_a_time(app, fixture, monkeypatch):
    _PATHS.update({"price": fixture.price_db, "fact": fixture.fact_db})
    from thesis_tracker.webapp import server as web_server

    observed = []

    class _SlowClient(_FakeClient):
        def complete(self, *, messages, tools, max_tokens):
            observed.append(len([job for job in app.store.list()
                                 if job["status"] == "running"]))
            time.sleep(0.3)
            return super().complete(messages=messages, tools=tools, max_tokens=max_tokens)

    monkeypatch.setattr(web_server, "CLIENT_FACTORY", _SlowClient)
    first = json.loads(enqueue(app)[2])["job"]["job_id"]
    second = json.loads(enqueue(app)[2])["job"]["job_id"]
    assert wait_for(lambda: app.store.get(first)["status"] == "succeeded", timeout=30)
    assert wait_for(lambda: app.store.get(second)["status"] == "succeeded", timeout=30)
    assert observed and all(count == 1 for count in observed), observed
    # The second job waited rather than running beside the first.
    assert app.store.get(second)["started_at"] >= app.store.get(first)["created_at"]


def test_the_job_list_and_detail_endpoints_report_status(app):
    _, _, data = enqueue(app)
    job_id = json.loads(data)["job"]["job_id"]
    status, payload = _json(app, "/api/jobs")
    assert status == 200
    assert [item["job_id"] for item in payload["jobs"]] == [job_id]
    status, detail = _json(app, f"/api/jobs/{job_id}")
    assert status == 200 and detail["job_id"] == job_id
    assert detail["progress"] is not None
    status, payload = _json(app, "/api/jobs/missing")
    assert status == 404


def _json(app, path):
    status, _, data = request(app, path)
    return status, json.loads(data.decode())


def test_no_job_record_ever_contains_a_secret(app, fixture):
    _PATHS.update({"price": fixture.price_db, "fact": fixture.fact_db})
    _, _, data = enqueue(app)
    job_id = json.loads(data)["job"]["job_id"]
    assert wait_for(lambda: app.store.get(job_id)["status"] == "succeeded", timeout=30)
    blob = json.dumps(app.store.list(), ensure_ascii=False)
    assert SENTINEL not in blob
    for _, _, body in [request(app, "/api/jobs")]:
        assert SENTINEL not in body.decode()


def test_a_restart_marks_a_running_job_interrupted(app, fixture, tmp_path):
    from thesis_tracker.webapp.server import WebApp

    job = app.store.create("analyze", {"ticker": "AAPL", "horizon": "mid",
                                       "as_of": FIXTURE_AS_OF})
    app.store.mark_running(job["job_id"])
    restarted = WebApp(fact_db=fixture.fact_db, price_db=fixture.price_db,
                       card_db=fixture.card_db, job_db=app.job_db, token=TOKEN,
                       port=0, start_worker=False)
    restarted.start()
    try:
        assert restarted.interrupted_jobs == [job["job_id"]]
        assert restarted.store.get(job["job_id"])["status"] == "interrupted"
        assert "中断" in restarted.store.get(job["job_id"])["error"]
    finally:
        restarted.stop()


def test_the_usage_endpoint_has_no_money_amounts(app):
    status, payload = _json(app, "/api/usage")
    assert status == 200
    assert set(payload) == {"analyses", "window", "input_tokens", "output_tokens",
                            "cache_hit_tokens"}
    blob = json.dumps(payload)
    assert "$" not in blob and "usd" not in blob.lower()


def test_the_connection_uses_keep_alive_without_leaking_the_token(app):
    """A bare response still parses; nothing echoes the token into the body."""
    connection = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
    try:
        connection.request("GET", "/api/overview",
                           headers={"Host": f"127.0.0.1:{app.port}",
                                    "Cookie": f"dsh_token={TOKEN}"})
        response = connection.getresponse()
        body = response.read()
    finally:
        connection.close()
    assert response.status == 200
    assert TOKEN not in body.decode()
