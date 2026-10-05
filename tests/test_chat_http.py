"""The chat HTTP surface: routes, guards and the fake-model turn end to end."""

from __future__ import annotations

import http.client
import json
import time

import pytest
from test_webapp_security import SENTINEL, TOKEN, request
from webapp_fixtures import build_fixture


@pytest.fixture
def fixture(tmp_path):
    return build_fixture(tmp_path / "fixture")


class _ChatClient:
    """Scripted local stand-in for DeepSeekClient."""

    replies: list = []

    def __init__(self) -> None:
        pass

    def complete(self, *, messages, tools, max_tokens):
        reply = type(self).replies.pop(0) if type(self).replies else {"content": "好。"}
        if callable(reply):
            reply = reply(messages)
        return {"model": "deepseek-flash", "system_fingerprint": "fp_chat",
                "usage": {"prompt_tokens": 120, "completion_tokens": 30,
                          "prompt_cache_hit_tokens": 60},
                "message": {"role": "assistant", "content": reply.get("content"),
                            "reasoning_content": None,
                            "tool_calls": reply.get("tool_calls")},
                "finish_reason": "stop"}


@pytest.fixture
def app(fixture, tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", SENTINEL)
    monkeypatch.setattr(_ChatClient, "replies", [])
    from thesis_tracker.webapp import server as web_server

    monkeypatch.setattr(web_server, "CLIENT_FACTORY", _ChatClient)
    application = web_server.WebApp(fact_db=fixture.fact_db, price_db=fixture.price_db,
                                    card_db=fixture.card_db,
                                    job_db=tmp_path / "jobs.db",
                                    chat_db=tmp_path / "chat.db",
                                    pricing_path=tmp_path / "absent-pricing.json",
                                    token=TOKEN, port=0)
    application.start()
    try:
        yield application
    finally:
        application.stop()


def get(app, path):
    """GET an API path and decode its JSON body."""
    status, _headers, raw = request(app, path)
    return status, json.loads(raw or b"{}")


def post(app, path, payload):
    """POST an API path with the access token and decode the JSON answer."""
    status, _headers, raw = request(app, path, method="POST", body=payload)
    return status, json.loads(raw or b"{}")


def make_conversation(app, ticker="AAPL", **extra):
    status, payload = post(app, "/api/companies/conversations",
                           {"ticker": ticker, **extra})
    assert status == 200, payload
    return payload["conversation"]


def wait_for_turn(app, conversation_id, *, timeout=15.0):
    """Poll the conversation until the newest message is an answer."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, payload = get(app, f"/api/conversations/{conversation_id}")
        assert status == 200, payload
        messages = payload["messages"]
        if messages and messages[-1]["role"] in {"assistant", "system"}:
            if messages[-1]["is_answer"] or messages[-1]["rejected"]:
                return payload
        time.sleep(0.05)
    raise AssertionError("the chat turn never finished")


# -- creating and listing ----------------------------------------------------

def test_a_conversation_is_created_for_a_known_company(app):
    conversation = make_conversation(app)
    assert conversation["ticker"] == "AAPL"
    assert conversation["title"] == "新对话"
    assert conversation["pinned_card_id"] is None


def test_an_unknown_company_cannot_start_a_conversation(app):
    status, payload = post(app, "/api/companies/conversations", {"ticker": "ZZZZ"})
    assert status == 400
    assert "公司" in payload["error"]


def test_the_company_page_lists_its_conversations(app):
    make_conversation(app)
    make_conversation(app)
    status, payload = get(app, "/api/companies/conversations?ticker=AAPL")
    assert status == 200
    assert payload["count"] == 2
    assert {item["ticker"] for item in payload["conversations"]} == {"AAPL"}
    assert payload["conversations"][0]["usage"]["levels"]["conversation"]


def test_another_company_has_no_conversations(app):
    make_conversation(app, "AAPL")
    status, payload = get(app, "/api/companies/conversations?ticker=NVDA")
    assert status == 200
    assert payload["conversations"] == []


def test_a_conversation_can_pin_one_of_its_companys_cards(app, fixture):
    conversation = make_conversation(app, card_id=fixture.card_id)
    assert conversation["pinned_card_id"] == fixture.card_id
    assert conversation["pinned_card_id_short"] == fixture.card_id[:8]


def test_a_card_from_another_company_cannot_be_pinned(app, fixture):
    status, payload = post(app, "/api/companies/conversations",
                           {"ticker": "NVDA", "card_id": fixture.card_id})
    assert status == 400
    assert "卡" in payload["error"]


def test_an_unknown_conversation_is_a_404(app):
    status, payload = get(app, "/api/conversations/does-not-exist")
    assert status == 404


# -- sending a message -------------------------------------------------------

def test_a_sent_message_starts_a_turn_job(app):
    conversation = make_conversation(app)
    status, payload = post(app, f"/api/conversations/{conversation['conversation_id']}"
                                "/messages", {"text": "现在能买吗？"})
    assert status == 200, payload
    assert payload["message"]["role"] == "user"
    assert payload["job"]["kind"] == "chat_turn"
    assert payload["job"]["kind_label"] == "回答提问"


def test_the_title_becomes_the_first_message(app):
    conversation = make_conversation(app)
    post(app, f"/api/conversations/{conversation['conversation_id']}/messages",
         {"text": "最近一期财报的毛利率趋势怎么样？"})
    status, payload = get(app, f"/api/conversations/{conversation['conversation_id']}")
    assert payload["title"] == "最近一期财报的毛利率趋势怎么样？"[:24]


def test_an_empty_message_is_refused(app):
    conversation = make_conversation(app)
    status, payload = post(app, f"/api/conversations/{conversation['conversation_id']}"
                                "/messages", {"text": "   "})
    assert status == 400
    assert "不能为空" in payload["error"]


def test_an_over_long_message_is_refused(app):
    conversation = make_conversation(app)
    status, payload = post(app, f"/api/conversations/{conversation['conversation_id']}"
                                "/messages", {"text": "字" * 4001})
    assert status == 400
    assert "4000" in payload["error"]


def test_an_archived_conversation_refuses_new_messages(app):
    conversation = make_conversation(app)
    post(app, "/api/conversations/archive",
         {"conversation_id": conversation["conversation_id"]})
    status, payload = post(app, f"/api/conversations/{conversation['conversation_id']}"
                                "/messages", {"text": "还在吗"})
    assert status == 400
    assert "归档" in payload["error"]


# -- a full turn -------------------------------------------------------------

def test_a_turn_answers_and_shows_its_usage(app):
    _ChatClient.replies = [{"content": "本地数据里最新价格是有的，我不替你决定。"}]
    conversation = make_conversation(app)
    post(app, f"/api/conversations/{conversation['conversation_id']}/messages",
         {"text": "现在怎么样？"})
    state = wait_for_turn(app, conversation["conversation_id"])
    answer = state["messages"][-1]
    assert answer["is_answer"] is True
    assert answer["text"].startswith("本地数据")
    assert answer["usage_line"].startswith("本条 输入 120 输出 30 token")
    assert answer["prompt_version"]
    assert state["history"]["note"] == "较早的消息不再发给模型"


def test_a_turn_can_call_a_tool_and_cite_it(app):
    def answer(messages):
        payload = json.loads(messages[-1]["content"])
        return {"content": f"最新收盘价 {{fact:{payload['fact_id']}}}。"}

    _ChatClient.replies = [
        {"tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "get_price_history",
                                      "arguments": json.dumps({"ticker": "AAPL"})}}]},
        answer,
    ]
    conversation = make_conversation(app)
    post(app, f"/api/conversations/{conversation['conversation_id']}/messages",
         {"text": "最新价格是多少？"})
    state = wait_for_turn(app, conversation["conversation_id"])
    answer_view = state["messages"][-1]
    assert "美元/股" in answer_view["text"]
    assert answer_view["tool_summary"]["count"] == 1
    assert answer_view["tool_summary"]["label"] == "查了 1 次数据"
    assert answer_view["tool_calls"][0]["tool"] == "get_price_history"
    assert answer_view["tool_calls"][0]["status_label"] == "成功"
    assert answer_view["segments"][1]["type"] == "fact"
    assert answer_view["segments"][1]["visible"].startswith("249.90")


def test_a_refused_turn_shows_the_violations_and_not_the_draft(app):
    _ChatClient.replies = [{"content": "建议买入。"} for _ in range(6)]
    conversation = make_conversation(app)
    post(app, f"/api/conversations/{conversation['conversation_id']}/messages",
         {"text": "现在能买吗？"})
    state = wait_for_turn(app, conversation["conversation_id"])
    refused = state["messages"][-1]
    assert refused["text"] is None
    assert refused["rejected"]["headline"] == "这条回答没有通过检查"
    assert "C02" in refused["rejected"]["rules"]
    assert refused["rejected"]["attempt_count"] == 3
    assert refused["rejected"]["has_draft"] is True
    # The draft itself never leaves the archive through the API.
    assert "建议买入" not in json.dumps(state, ensure_ascii=False)


def test_a_failed_turn_is_recorded_on_the_job(app):
    _ChatClient.replies = [{"content": "建议买入。"} for _ in range(6)]
    conversation = make_conversation(app)
    _, sent = post(app, f"/api/conversations/{conversation['conversation_id']}"
                        "/messages", {"text": "现在能买吗？"})
    job_id = sent["job"]["job_id"]
    deadline = time.time() + 15
    while time.time() < deadline:
        status, job = get(app, f"/api/jobs/{job_id}")
        if job["status"] in {"failed", "succeeded", "interrupted"}:
            break
        time.sleep(0.05)
    assert job["status"] == "failed"
    assert "没有通过校验" in job["error"]


# -- proposals ---------------------------------------------------------------

def test_a_turn_that_proposes_a_card_shows_a_pending_proposal(app, fixture):
    _ChatClient.replies = [
        {"tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "request_new_card",
                                      "arguments": json.dumps({"horizon": "mid",
                                                               "reason": "需要中期判断"})}}]},
        {"content": "要不要我生成一张中期的建议卡？"},
    ]
    conversation = make_conversation(app)
    post(app, f"/api/conversations/{conversation['conversation_id']}/messages",
         {"text": "现在能买吗？"})
    state = wait_for_turn(app, conversation["conversation_id"])
    proposal = state["pending_proposal"]
    assert proposal["status"] == "pending"
    assert proposal["status_label"] == "待确认"
    assert proposal["horizon_label"] == "中期"
    assert proposal["confirmable"] is True
    assert "DeepSeek" in proposal["estimate"]


def test_an_unconfirmed_proposal_creates_no_job(app):
    _ChatClient.replies = [
        {"tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "request_new_card",
                                      "arguments": json.dumps({"horizon": "mid",
                                                               "reason": "理由"})}}]},
        {"content": "要我生成吗？"},
    ]
    conversation = make_conversation(app)
    post(app, f"/api/conversations/{conversation['conversation_id']}/messages",
         {"text": "问题"})
    wait_for_turn(app, conversation["conversation_id"])
    status, payload = get(app, "/api/jobs")
    assert status == 200
    assert [job for job in payload["jobs"] if job["kind"] == "analyze"] == []


def test_confirming_a_proposal_creates_an_analysis_job(app):
    _ChatClient.replies = [
        {"tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "request_new_card",
                                      "arguments": json.dumps({"horizon": "mid",
                                                               "reason": "理由"})}}]},
        {"content": "要我生成吗？"},
    ]
    conversation = make_conversation(app)
    post(app, f"/api/conversations/{conversation['conversation_id']}/messages",
         {"text": "问题"})
    state = wait_for_turn(app, conversation["conversation_id"])
    proposal_id = state["pending_proposal"]["proposal_id"]
    status, payload = post(app, "/api/proposals/confirm", {"proposal_id": proposal_id})
    assert status == 200, payload
    assert payload["job"]["kind"] == "analyze"
    assert payload["proposal"]["status"] == "confirmed"
    # The analysis runs on the fixture's own newest price day.
    assert payload["job"]["parameters"][2]["value"] == "2026-09-07"


def test_confirming_twice_reports_a_conflict_and_creates_one_job(app):
    _ChatClient.replies = [
        {"tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "request_new_card",
                                      "arguments": json.dumps({"horizon": "mid",
                                                               "reason": "理由"})}}]},
        {"content": "要我生成吗？"},
    ]
    conversation = make_conversation(app)
    post(app, f"/api/conversations/{conversation['conversation_id']}/messages",
         {"text": "问题"})
    state = wait_for_turn(app, conversation["conversation_id"])
    proposal_id = state["pending_proposal"]["proposal_id"]
    post(app, "/api/proposals/confirm", {"proposal_id": proposal_id})
    status, payload = post(app, "/api/proposals/confirm", {"proposal_id": proposal_id})
    assert status == 409
    assert "已经确认" in payload["error"]
    _, jobs = get(app, "/api/jobs")
    assert len([job for job in jobs["jobs"] if job["kind"] == "analyze"]) == 1


def test_an_ignored_proposal_cannot_be_confirmed(app):
    _ChatClient.replies = [
        {"tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "request_new_card",
                                      "arguments": json.dumps({"horizon": "short",
                                                               "reason": "理由"})}}]},
        {"content": "要我生成吗？"},
    ]
    conversation = make_conversation(app)
    post(app, f"/api/conversations/{conversation['conversation_id']}/messages",
         {"text": "问题"})
    state = wait_for_turn(app, conversation["conversation_id"])
    proposal_id = state["pending_proposal"]["proposal_id"]
    status, dismissed = post(app, "/api/proposals/dismiss", {"proposal_id": proposal_id})
    assert status == 200
    assert dismissed["proposal"]["status"] == "dismissed"
    status, payload = post(app, "/api/proposals/confirm", {"proposal_id": proposal_id})
    assert status == 409
    _, jobs = get(app, "/api/jobs")
    assert [job for job in jobs["jobs"] if job["kind"] == "analyze"] == []


def test_a_proposal_survives_a_reload(app):
    _ChatClient.replies = [
        {"tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "request_new_card",
                                      "arguments": json.dumps({"horizon": "long",
                                                               "reason": "长期需要"})}}]},
        {"content": "要我生成吗？"},
    ]
    conversation = make_conversation(app)
    post(app, f"/api/conversations/{conversation['conversation_id']}/messages",
         {"text": "问题"})
    wait_for_turn(app, conversation["conversation_id"])
    status, payload = get(app, f"/api/conversations/{conversation['conversation_id']}")
    assert payload["pending_proposal"]["horizon_label"] == "长期"
    assert payload["proposals"][0]["status"] == "pending"


def test_an_unknown_proposal_is_a_404(app):
    status, payload = post(app, "/api/proposals/confirm", {"proposal_id": "nope"})
    assert status == 404


def test_the_startup_block_tells_the_page_what_to_say(app):
    conversation = make_conversation(app)
    status, payload = get(app, f"/api/conversations/{conversation['conversation_id']}")
    startup = payload["startup"]
    assert startup["billing_note"] == "每条消息会调用 DeepSeek 并按量计费"
    assert startup["max_message_chars"] == 4000
    assert startup["history_window"] == 12
    assert startup["ticker"] == "AAPL"
    assert startup["hint_chips"]
    assert startup["price_note"]


# -- usage levels over HTTP --------------------------------------------------

def test_the_page_can_see_all_four_usage_levels(app):
    _ChatClient.replies = [{"content": "好。"}]
    first = make_conversation(app)
    second = make_conversation(app)
    post(app, f"/api/conversations/{first['conversation_id']}/messages", {"text": "一"})
    wait_for_turn(app, first["conversation_id"])
    status, payload = get(app, f"/api/conversations/{first['conversation_id']}")
    levels = payload["usage"]["levels"]
    assert levels["conversation"]["input_tokens"] == 120
    assert levels["company"]["input_tokens"] == 120
    assert levels["today"]["input_tokens"] == 120
    # The conversation-level view has no single message to report on, so that
    # level is zero here; the answer's own row carries it (asserted elsewhere).
    assert levels["message"]["input_tokens"] == 0
    assert payload["usage"]["pricing_available"] is False
    assert payload["usage"]["price_note"] == "未找到价格配置，只显示 token"
    del second


# -- security ----------------------------------------------------------------

# Paths where a GET must be refused.  /api/companies/conversations is excluded
# on purpose: listing a company's conversations is a legitimate GET.
SECURITY_PATHS = [
    ("POST", "/api/conversations/x/messages"),
    ("POST", "/api/conversations/archive"),
    ("POST", "/api/proposals/confirm"),
    ("POST", "/api/proposals/dismiss"),
]


@pytest.mark.parametrize("method,path", SECURITY_PATHS)
def test_every_chat_post_requires_the_token(app, method, path):
    connection = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
    try:
        connection.request(method, path, body=json.dumps({}),
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == 401
        assert b"salt" not in response.read()
    finally:
        connection.close()


@pytest.mark.parametrize("method,path", SECURITY_PATHS)
def test_every_chat_post_rejects_a_wrong_host(app, method, path):
    connection = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
    try:
        connection.request(method, path, body=json.dumps({}),
                           headers={"Content-Type": "application/json",
                                    "X-DSH-Token": TOKEN, "Host": "evil.example"})
        response = connection.getresponse()
        assert response.status == 403
        response.read()
    finally:
        connection.close()


@pytest.mark.parametrize("method,path", SECURITY_PATHS)
def test_every_chat_post_rejects_a_foreign_origin(app, method, path):
    connection = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
    try:
        connection.request(method, path, body=json.dumps({}),
                           headers={"Content-Type": "application/json",
                                    "X-DSH-Token": TOKEN,
                                    "Origin": "https://evil.example"})
        response = connection.getresponse()
        assert response.status == 403
        response.read()
    finally:
        connection.close()


@pytest.mark.parametrize("method,path", SECURITY_PATHS)
def test_every_chat_post_refuses_get(app, method, path):
    connection = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
    try:
        connection.request("GET", path, headers={"X-DSH-Token": TOKEN})
        response = connection.getresponse()
        assert response.status == 405
        response.read()
    finally:
        connection.close()


def test_no_chat_response_contains_the_api_key(app):
    _ChatClient.replies = [{"content": "好。"}]
    conversation = make_conversation(app)
    post(app, f"/api/conversations/{conversation['conversation_id']}/messages",
         {"text": "问题"})
    state = wait_for_turn(app, conversation["conversation_id"])
    assert SENTINEL not in json.dumps(state, ensure_ascii=False)
    for path in ("/api/conversations/" + conversation["conversation_id"],
                 "/api/companies/conversations?ticker=AAPL", "/api/jobs", "/api/usage"):
        status, payload = get(app, path)
        assert status == 200
        assert SENTINEL not in json.dumps(payload, ensure_ascii=False)


def test_the_balance_endpoint_never_returns_the_key(app, monkeypatch):
    from thesis_tracker.webapp.chat import balance as balance_module

    monkeypatch.setattr(balance_module, "fetch_balance", lambda **kwargs: {
        "is_available": True,
        "balances": [{"currency": "USD", "total_balance": "5.00",
                      "granted_balance": "0.00", "topped_up_balance": "5.00"}],
        "error": None})
    status, payload = get(app, "/api/balance")
    assert status == 200
    assert payload["balances"][0]["total_balance"] == "5.00"
    assert SENTINEL not in json.dumps(payload)


def test_a_conversation_of_another_company_is_not_reachable_by_ticker(app):
    """The ticker comes from the conversation, never from the request."""
    conversation = make_conversation(app, "AAPL")
    _ChatClient.replies = [{"content": "好。"}]
    post(app, f"/api/conversations/{conversation['conversation_id']}/messages",
         {"text": "NVDA 现在怎么样？", "ticker": "NVDA"})
    wait_for_turn(app, conversation["conversation_id"])
    status, jobs = get(app, "/api/jobs")
    assert status == 200
    turn = next(job for job in jobs["jobs"] if job["kind"] == "chat_turn")
    # The job's ticker came from the stored conversation, not the request body.
    assert turn["parameters"][0]["value"] == "AAPL"
    assert "NVDA" not in json.dumps(turn["parameters"], ensure_ascii=False)
    # The stored conversation is still the AAPL one.
    status, payload = get(app, f"/api/conversations/{conversation['conversation_id']}")
    assert payload["ticker"] == "AAPL"
