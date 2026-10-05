"""What the page can know about a turn that has not produced an answer (yet).

A chat turn runs on a worker, so after the reader presses send there is a window
in which the question exists and the answer does not.  The page needs to be told
which of four things is going on: the turn is still in flight, it finished, it
failed without leaving any answer, or the software restarted underneath it.
"""

from __future__ import annotations

import time

import pytest
from test_chat_http import _ChatClient, get, make_conversation, post, wait_for_turn
from test_webapp_security import SENTINEL, TOKEN
from webapp_fixtures import build_fixture


@pytest.fixture
def fixture(tmp_path):
    return build_fixture(tmp_path / "fixture")


def build_app(fixture, tmp_path, monkeypatch, *, start_worker):
    monkeypatch.setenv("DEEPSEEK_API_KEY", SENTINEL)
    monkeypatch.setattr(_ChatClient, "replies", [])
    from thesis_tracker.webapp import server as web_server

    monkeypatch.setattr(web_server, "CLIENT_FACTORY", _ChatClient)
    application = web_server.WebApp(
        fact_db=fixture.fact_db, price_db=fixture.price_db, card_db=fixture.card_db,
        job_db=tmp_path / "jobs.db", chat_db=tmp_path / "chat.db",
        pricing_path=tmp_path / "absent-pricing.json", token=TOKEN, port=0,
        start_worker=start_worker)
    application.start()
    return application


@pytest.fixture
def idle_app(fixture, tmp_path, monkeypatch):
    """No worker: a sent message stays queued, like a turn that has not started."""
    application = build_app(fixture, tmp_path, monkeypatch, start_worker=False)
    try:
        yield application
    finally:
        application.stop()


@pytest.fixture
def live_app(fixture, tmp_path, monkeypatch):
    application = build_app(fixture, tmp_path, monkeypatch, start_worker=True)
    try:
        yield application
    finally:
        application.stop()


def send(app, conversation_id, text="现在怎么样？"):
    return post(app, f"/api/conversations/{conversation_id}/messages", {"text": text})


def turn_of(app, conversation_id):
    status, payload = get(app, f"/api/conversations/{conversation_id}")
    assert status == 200, payload
    return payload["turn"]


def test_a_conversation_with_no_messages_is_idle(idle_app):
    conversation = make_conversation(idle_app)
    turn = turn_of(idle_app, conversation["conversation_id"])
    assert turn["in_flight"] is False
    assert turn["error"] is None


def test_a_queued_turn_is_in_flight_and_says_so(idle_app):
    conversation = make_conversation(idle_app)
    send(idle_app, conversation["conversation_id"])
    turn = turn_of(idle_app, conversation["conversation_id"])
    assert turn["in_flight"] is True
    assert turn["status"] == "queued"
    assert turn["label"] == "已排队，等待回答"
    assert turn["error"] is None


def test_a_running_turn_is_in_flight(idle_app):
    conversation = make_conversation(idle_app)
    send(idle_app, conversation["conversation_id"])
    idle_app.store.claim_chat_turn()
    turn = turn_of(idle_app, conversation["conversation_id"])
    assert turn["in_flight"] is True
    assert turn["status"] == "running"
    assert turn["label"] == "正在回答"


def test_a_second_message_is_refused_while_one_turn_is_in_flight(idle_app):
    conversation = make_conversation(idle_app)
    first_status, _ = send(idle_app, conversation["conversation_id"], "第一条")
    status, payload = send(idle_app, conversation["conversation_id"], "第二条")
    assert first_status == 200
    assert status == 409
    assert "还在回答" in payload["error"]
    _, state = get(idle_app, f"/api/conversations/{conversation['conversation_id']}")
    assert [item["text"] for item in state["messages"]] == ["第一条"]


def test_another_conversation_is_not_blocked_by_an_in_flight_turn(idle_app):
    first = make_conversation(idle_app)
    second = make_conversation(idle_app)
    send(idle_app, first["conversation_id"])
    status, _ = send(idle_app, second["conversation_id"])
    assert status == 200


def test_an_answered_turn_is_no_longer_in_flight(live_app):
    _ChatClient.replies = [{"content": "本地数据里有价格。"}]
    conversation = make_conversation(live_app)
    send(live_app, conversation["conversation_id"])
    wait_for_turn(live_app, conversation["conversation_id"])
    deadline = time.time() + 5
    turn = turn_of(live_app, conversation["conversation_id"])
    while turn["in_flight"] and time.time() < deadline:
        time.sleep(0.05)
        turn = turn_of(live_app, conversation["conversation_id"])
    assert turn["in_flight"] is False
    assert turn["error"] is None


def test_a_refused_turn_needs_no_extra_notice(live_app):
    _ChatClient.replies = [{"content": "建议买入。"} for _ in range(6)]
    conversation = make_conversation(live_app)
    send(live_app, conversation["conversation_id"], "现在能买吗？")
    wait_for_turn(live_app, conversation["conversation_id"])
    deadline = time.time() + 5
    turn = turn_of(live_app, conversation["conversation_id"])
    while turn["in_flight"] and time.time() < deadline:
        time.sleep(0.05)
        turn = turn_of(live_app, conversation["conversation_id"])
    # The refusal itself is already on the page; a second notice would repeat it.
    assert turn["error"] is None


def test_a_turn_that_failed_without_an_answer_is_reported(fixture, tmp_path, monkeypatch):
    class Broken:
        def __init__(self):
            raise RuntimeError("no key")

    application = build_app(fixture, tmp_path, monkeypatch, start_worker=True)
    from thesis_tracker.webapp import server as web_server

    monkeypatch.setattr(web_server, "CLIENT_FACTORY", Broken)
    try:
        conversation = make_conversation(application)
        send(application, conversation["conversation_id"])
        deadline = time.time() + 10
        turn = turn_of(application, conversation["conversation_id"])
        while turn["in_flight"] and time.time() < deadline:
            time.sleep(0.05)
            turn = turn_of(application, conversation["conversation_id"])
        assert turn["in_flight"] is False
        assert turn["status"] == "failed"
        assert "没有得到回答" in turn["error"]
        # Asking again is allowed: a failed turn must not lock the conversation.
        status, _ = send(application, conversation["conversation_id"], "再问一次")
        assert status == 200
    finally:
        application.stop()


def test_a_turn_interrupted_by_a_restart_is_reported(idle_app):
    conversation = make_conversation(idle_app)
    send(idle_app, conversation["conversation_id"])
    idle_app.store.claim_chat_turn()
    idle_app.store.mark_interrupted()
    turn = turn_of(idle_app, conversation["conversation_id"])
    assert turn["in_flight"] is False
    assert turn["status"] == "interrupted"
    assert "中断" in turn["error"]
    status, _ = send(idle_app, conversation["conversation_id"], "再问一次")
    assert status == 200
