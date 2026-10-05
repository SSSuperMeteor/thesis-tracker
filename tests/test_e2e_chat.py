"""The chat page end to end: a real browser, the real server, a scripted model.

Every assertion here is about what a person sees and can do.  The model is a
scripted stand-in injected only in this test module; the server's production
client factory is untouched and no command-line switch or environment variable
was added for it.
"""

from __future__ import annotations

import json

import pytest
from e2e_support import requires_browser, run_scenario
from test_chat_http import _ChatClient
from test_webapp_security import SENTINEL, TOKEN
from webapp_fixtures import build_fixture

pytestmark = requires_browser


@pytest.fixture
def fixture(tmp_path):
    return build_fixture(tmp_path / "fixture")


@pytest.fixture
def app(fixture, tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", SENTINEL)
    monkeypatch.setattr(_ChatClient, "replies", [])
    from thesis_tracker.webapp import server as web_server

    monkeypatch.setattr(web_server, "CLIENT_FACTORY", _ChatClient)
    application = web_server.WebApp(
        fact_db=fixture.fact_db, price_db=fixture.price_db, card_db=fixture.card_db,
        job_db=tmp_path / "jobs.db", chat_db=tmp_path / "chat.db",
        pricing_path=tmp_path / "absent-pricing.json", token=TOKEN, port=0)
    application.start()
    try:
        yield application
    finally:
        application.stop()


def tool(name, **arguments):
    return {"tool_calls": [{"id": f"call-{name}", "type": "function",
                            "function": {"name": name,
                                         "arguments": json.dumps(arguments)}}]}


def run(app, **extra):
    result = run_scenario("chat", app, token=TOKEN, extra=extra)
    assert [item for item in result["problems"] if "http 4" in item] == [], result
    return result


def test_a_person_can_start_a_conversation_ask_and_read_the_answer(app):
    def answer(messages):
        payload = json.loads(messages[-1]["content"])
        return {"content": f"最新收盘价 {{fact:{payload['fact_id']}}}。"}

    _ChatClient.replies = [tool("get_price_history", ticker="AAPL"), answer,
                           {"content": "收到。"}]
    result = run(app, flow="answer")
    checks = result["checks"]
    assert checks["new_conversation_opens_chat"] is True, result
    assert checks["send_post_status"] == 200, result
    assert checks["answer_arrives_without_reload"] is True, result
    assert "美元/股" in checks["answer_text"]
    assert checks["marker_count"] == 1
    assert checks["marker_highlights_evidence_row"] is True
    assert checks["composer_enabled_after_answer"] is True
    assert checks["other_conversation_has_no_messages"] is True
    assert checks["other_conversation_has_no_evidence"] is True
    assert checks["first_conversation_keeps_its_evidence"] is True
    assert checks["long_message_page_overflow"] == 0
    assert checks["long_message_block_overflow"] == 0
    assert result["problems"] == []


def test_a_card_field_in_an_answer_points_at_its_evidence_row(app, fixture):
    def answer(messages):
        return {"content": f"那张卡的动作是 {{card:{fixture.card_id}:action}}。"}

    _ChatClient.replies = [tool("get_card", card_id=fixture.card_id), answer]
    result = run(app, flow="card")
    checks = result["checks"]
    assert checks["answer_arrives_without_reload"] is True, result
    assert checks["marker_count"] == 1
    assert checks["marker_highlights_evidence_row"] is True, result


def test_a_refused_answer_shows_the_rules_and_never_the_draft(app):
    _ChatClient.replies = [{"content": "建议买入。"} for _ in range(6)]
    result = run(app, flow="refused", question="现在能买吗？")
    checks = result["checks"]
    assert checks["answer_arrives_without_reload"] is True, result
    assert "这条回答没有通过检查" in checks["refusal_text"]
    assert "C02" in checks["refusal_text"]
    assert "建议买入" not in checks["refusal_text"]
    assert checks["no_assistant_answer_shown"] is True


def propose(horizon="mid"):
    return [tool("request_new_card", horizon=horizon, reason="需要中期判断"),
            {"content": "要不要我生成一张中期的建议卡？"}]


def test_a_proposal_can_be_dismissed_from_the_page(app):
    _ChatClient.replies = propose()
    result = run(app, flow="dismiss", question="现在能买吗？")
    checks = result["checks"]
    assert checks["answer_arrives_without_reload"] is True, result
    assert checks["proposal_visible"] is True
    assert checks["decision_post_status"] == 200, result
    assert "已忽略" in checks["proposal_text_after"]
    assert "已忽略" in checks["proposal_state_survives_reload"]
    assert checks["proposal_buttons_after_reload"] == 0


def test_a_proposal_can_be_confirmed_from_the_page(app):
    _ChatClient.replies = propose()
    result = run(app, flow="confirm", question="现在能买吗？")
    checks = result["checks"]
    assert checks["proposal_visible"] is True
    assert checks["decision_post_status"] == 200, result
    assert "已确认" in checks["proposal_text_after"]
    assert "已确认" in checks["proposal_state_survives_reload"]
    assert checks["proposal_buttons_after_reload"] == 0
    kinds = [job["kind"] for job in app.store.list()]
    assert kinds.count("analyze") == 1
