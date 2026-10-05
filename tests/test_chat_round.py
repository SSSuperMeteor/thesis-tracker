"""One chat round: context, tools, the correction loop and the limits."""

from __future__ import annotations

import json

import pytest
from webapp_fixtures import build_fixture


@pytest.fixture
def fixture(tmp_path):
    return build_fixture(tmp_path / "fixture")


class ScriptedChatClient:
    """Offline stand-in for DeepSeekClient that replays a script."""

    def __init__(self, replies, *, usage=None):
        self.replies = list(replies)
        self.requests: list[dict] = []
        self.usage = usage or {"prompt_tokens": 100, "completion_tokens": 20,
                               "prompt_cache_hit_tokens": 64}

    def complete(self, *, messages, tools, max_tokens):
        self.requests.append({"messages": json.loads(json.dumps(messages)),
                              "tools": tools, "max_tokens": max_tokens})
        reply = self.replies.pop(0) if self.replies else {"content": "没有更多脚本了。"}
        if callable(reply):
            reply = reply(self.requests[-1])
        return {"model": "deepseek-flash", "system_fingerprint": "fp_chat",
                "usage": dict(self.usage),
                "message": {"role": "assistant", "content": reply.get("content"),
                            "reasoning_content": reply.get("reasoning_content"),
                            "tool_calls": reply.get("tool_calls")},
                "finish_reason": reply.get("finish_reason", "stop")}


def tool_call(name, arguments, call_id="call_1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


@pytest.fixture
def conversation(fixture, tmp_path):
    from thesis_tracker.webapp.chat.store import ChatStore

    store = ChatStore(tmp_path / "chat.db")
    return store, store.create_conversation("AAPL")


def round_for(fixture, store, conversation, client, *, question="现在能买吗？",
              progress=None):
    """A round for a freshly stored user message; ``.message`` is that message.

    Tool calls and model calls are audited against the *user* message that asked
    the question, which is what makes one turn's spend reconstructible.
    """
    from thesis_tracker.webapp.chat.round import ChatRound

    message = store.append_message(conversation["conversation_id"], role="user",
                                   text=question)
    turn = ChatRound(store=store, conversation=store.get_conversation(
        conversation["conversation_id"]), message=message, as_of="2026-09-07",
        fact_db=fixture.fact_db, price_db=fixture.price_db,
        card_db=fixture.card_db, client=client, progress=progress)
    turn.question = message
    return turn


def test_a_round_without_tools_uses_the_company_brief(conversation, fixture):
    store, conv = conversation
    client = ScriptedChatClient([{"content": "我看了本地数据。"}])
    result = round_for(fixture, store, conv, client).run()
    assert result["status"] == "passed"
    sent = client.requests[0]["messages"]
    assert sent[0]["role"] == "system"
    brief = json.loads(sent[1]["content"])
    assert brief["company"]["ticker"] == "AAPL"
    assert brief["company"]["latest_price_date"]
    assert "AAPL" in client.requests[0]["messages"][0]["content"]


def test_the_company_brief_carries_only_the_code_and_the_latest_price_day(conversation,
                                                                         fixture):
    store, conv = conversation
    client = ScriptedChatClient([{"content": "好。"}])
    round_for(fixture, store, conv, client).run()
    brief = json.loads(client.requests[0]["messages"][1]["content"])
    assert set(brief["company"]) == {"ticker", "latest_price_date"}
    assert brief["company"]["ticker"] == "AAPL"


def test_a_pinned_card_is_included_when_the_conversation_has_one(fixture, tmp_path):
    from thesis_tracker.webapp.chat.round import ChatRound
    from thesis_tracker.webapp.chat.store import ChatStore

    store = ChatStore(tmp_path / "chat.db")
    conv = store.create_conversation("AAPL", card_id=fixture.card_id)
    message = store.append_message(conv["conversation_id"], role="user", text="这张卡怎么说？")
    client = ScriptedChatClient([{"content": "卡上写的是判断。"}])
    ChatRound(store=store, conversation=conv, message=message, as_of="2026-09-07",
              fact_db=fixture.fact_db, price_db=fixture.price_db,
              card_db=fixture.card_db, client=client).run()
    brief = json.loads(client.requests[0]["messages"][1]["content"])
    assert brief["pinned_card"]["card_id"] == fixture.card_id
    assert brief["pinned_card"]["action"] == "分批"


def test_tool_calls_run_and_their_results_reach_the_model(conversation, fixture):
    store, conv = conversation
    client = ScriptedChatClient([
        {"tool_calls": [tool_call("get_price_history", {"ticker": "AAPL"})]},
        {"content": "已读取。"},
    ])
    result = round_for(fixture, store, conv, client).run()
    assert result["status"] == "passed"
    assert result["stats"]["tool_calls"] == 1
    tool_message = client.requests[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert "AAPL" in tool_message["content"]
    # The audit row hangs off the user message this turn answered, so a turn's
    # actions and spend are reconstructible from the archive.
    store_tool_calls = store.tool_calls(_question_id(store, conv))
    assert store_tool_calls[0]["tool"] == "get_price_history"
    assert store_tool_calls[0]["envelope"]["status"] == "ok"


def test_the_model_may_answer_with_a_placeholder_from_a_tool_it_called(conversation,
                                                                      fixture):
    store, conv = conversation

    def answer_with_the_tool_fact(previous):
        payload = json.loads(previous["messages"][-1]["content"])
        fact_id = payload["fact_id"]
        return {"content": f"最新收盘价 {{fact:{fact_id}}}。"}

    client = ScriptedChatClient([
        {"tool_calls": [tool_call("get_price_history", {"ticker": "AAPL"})]},
        answer_with_the_tool_fact,
    ])
    result = round_for(fixture, store, conv, client).run()
    assert result["status"] == "passed"
    assert "美元/股" in result["text"]
    assert result["message"]["segments"][1]["type"] == "fact"


def test_an_answer_citing_a_card_field_passes(conversation, fixture):
    store, conv = conversation

    def answer(previous):
        payload = json.loads(previous["messages"][-1]["content"])
        card_id = payload["data"]["card_id"]
        return {"content": f"这张卡的动作是 {{card:{card_id}:action}}。"}

    client = ScriptedChatClient([
        {"tool_calls": [tool_call("get_card", {"card_id": fixture.card_id})]},
        answer,
    ])
    result = round_for(fixture, store, conv, client).run()
    assert result["status"] == "passed"
    assert "AI 判断" in result["text"]


def test_invalid_tool_arguments_come_back_without_stopping_the_round(conversation,
                                                                    fixture):
    store, conv = conversation
    client = ScriptedChatClient([
        {"tool_calls": [tool_call("get_price_history", {"ticker": "MSFT"})]},
        {"content": "那家不在这个对话里。"},
    ])
    result = round_for(fixture, store, conv, client).run()
    assert result["status"] == "passed"
    payload = json.loads(client.requests[1]["messages"][-1]["content"])
    assert payload["status"] == "error"
    assert payload["reason"]["code"] == "ticker_not_in_conversation"


def test_a_rejected_draft_is_fed_back_and_corrected(conversation, fixture):
    store, conv = conversation
    client = ScriptedChatClient([
        {"content": "建议买入。"},
        {"content": "我无法替你决定。"},
    ])
    result = round_for(fixture, store, conv, client).run()
    assert result["status"] == "passed"
    assert result["stats"]["attempts"] == 2
    feedback = client.requests[1]["messages"][-1]["content"]
    assert "C02" in feedback
    assert result["text"] == "我无法替你决定。"


def test_the_correction_limit_is_two_and_then_the_round_fails(conversation, fixture):
    store, conv = conversation
    client = ScriptedChatClient([{"content": "建议买入。"} for _ in range(6)])
    result = round_for(fixture, store, conv, client).run()
    assert result["status"] == "rejected"
    assert result["stats"]["attempts"] == 3
    assert result["violations"]
    # Every attempt is archived, including the ones that were refused.
    assert len(client.requests) == 3


def test_a_rejected_round_shows_no_draft_and_keeps_the_attempts(conversation, fixture):
    store, conv = conversation
    client = ScriptedChatClient([{"content": "建议买入。"} for _ in range(6)])
    result = round_for(fixture, store, conv, client).run()
    message = result["message"]
    assert message["text"] is None
    assert message["segments"] is None
    assert message["rejected"]["draft"] == "建议买入。"
    assert message["rejected"]["violations"]
    assert message["attempts"] == 3
    assert message["prompt_version"]
    # The refused draft is not shown where an answer would be.
    assert result["text"] is None


def test_a_rejected_round_reports_every_attempt_it_made(conversation, fixture):
    """The archive keeps all three refused drafts, not only the last."""
    store, conv = conversation
    # 卖出 is in the D11 vocabulary ("减持" is not, so it would pass).
    client = ScriptedChatClient([{"content": "买入。"}, {"content": "卖出。"},
                                 {"content": "观望。"}])
    result = round_for(fixture, store, conv, client).run()
    assert result["status"] == "rejected"
    attempts = result["message"]["rejected"]["attempts"]
    assert [item["violations"] for item in attempts]
    assert all(item["draft"] for item in attempts)


def test_the_tool_call_limit_is_enforced(conversation, fixture):
    from thesis_tracker.webapp.chat.round import MAX_TOOL_CALLS

    assert MAX_TOOL_CALLS == 8
    store, conv = conversation
    replies = [{"tool_calls": [tool_call("get_price_history", {"ticker": "AAPL"},
                                         call_id=f"c{index}")]} for index in range(20)]
    client = ScriptedChatClient(replies + [{"content": "够了。"}])
    result = round_for(fixture, store, conv, client).run()
    assert result["stats"]["tool_calls"] <= MAX_TOOL_CALLS
    if result["status"] == "passed":
        assert result["stats"]["tool_calls"] == MAX_TOOL_CALLS
    else:
        assert result["stats"]["tool_calls"] == MAX_TOOL_CALLS


def test_the_round_limit_is_enforced(conversation, fixture):
    from thesis_tracker.webapp.chat.round import MAX_ROUNDS

    assert MAX_ROUNDS == 10
    store, conv = conversation
    client = ScriptedChatClient(
        [{"tool_calls": [tool_call("get_price_history", {"ticker": "AAPL"},
                                   call_id=f"c{index}")]} for index in range(30)])
    result = round_for(fixture, store, conv, client).run()
    assert len(client.requests) <= MAX_ROUNDS
    assert result["status"] == "rejected"
    assert result["reason"] == "round_limit"


def test_a_single_over_large_request_is_never_sent(conversation, fixture, monkeypatch):
    """The 200k estimate gate mirrors the analysis loop's conservative check."""
    from thesis_tracker.webapp.chat import round as chat_round

    monkeypatch.setattr(chat_round, "MAX_REQUEST_INPUT_TOKENS", 500)
    store, conv = conversation
    client = ScriptedChatClient([{"content": "不会发送。"}])
    result = round_for(fixture, store, conv, client).run()
    assert result["status"] == "rejected"
    assert result["reason"] == "request_input_limit"
    assert client.requests == []


def test_a_round_that_exceeds_its_token_ceiling_fails(conversation, fixture):
    from thesis_tracker.webapp.chat.round import MAX_TURN_TOKENS

    store, conv = conversation
    client = ScriptedChatClient([{"content": "好。"}],
                                usage={"prompt_tokens": MAX_TURN_TOKENS + 1,
                                       "completion_tokens": 1,
                                       "prompt_cache_hit_tokens": 0})
    result = round_for(fixture, store, conv, client).run()
    assert result["status"] == "rejected"
    assert result["reason"] == "token_limit"


def test_there_is_no_conversation_level_ceiling(conversation, fixture):
    """A long conversation keeps working: only the single turn is bounded."""
    store, conv = conversation
    for _ in range(3):
        round_for(fixture, store, conv,
                  ScriptedChatClient([{"content": "继续。"}])).run()
    usage = store.conversation_usage(conv["conversation_id"])
    assert usage["input_tokens"] > 0
    assert round_for(fixture, store, conv,
                     ScriptedChatClient([{"content": "还可以继续。"}])).run()["status"] == "passed"


def test_each_model_call_is_recorded_with_both_names_and_usage(conversation, fixture):
    store, conv = conversation
    client = ScriptedChatClient([
        {"tool_calls": [tool_call("get_price_history", {"ticker": "AAPL"})]},
        {"content": "好。"},
    ])
    result = round_for(fixture, store, conv, client).run()
    calls = store.model_calls(_question_id(store, conv))
    assert len(calls) == 2
    assert calls[0]["requested_model"] == "deepseek-flash"
    assert calls[0]["returned_model"] == "deepseek-flash"
    assert calls[0]["fingerprint"] == "fp_chat"
    assert calls[0]["input_tokens"] == 100
    assert calls[0]["cache_hit_tokens"] == 64
    assert result["stats"]["input_tokens"] == 200


def test_the_history_window_is_twelve_messages_and_older_ones_stay_archived(
        conversation, fixture):
    from thesis_tracker.webapp.chat.round import HISTORY_WINDOW

    assert HISTORY_WINDOW == 12
    store, conv = conversation
    for index in range(8):
        store.append_message(conv["conversation_id"], role="user", text=f"问题 {index}")
        store.append_message(conv["conversation_id"], role="assistant",
                             text=f"回答 {index}")
    client = ScriptedChatClient([{"content": "最新的回答。"}])
    result = round_for(fixture, store, conv, client, question="最后一个问题",
                       progress=None).run()
    sent = client.requests[0]["messages"]
    # system + brief + 12 history + the current question
    assert len(sent) == 2 + HISTORY_WINDOW + 1
    assert sent[-1]["content"] == "最后一个问题"
    assert "问题 0" not in json.dumps(sent, ensure_ascii=False)
    assert "问题 7" in json.dumps(sent, ensure_ascii=False)
    # Everything is still archived, and the page is told what was dropped.
    archived = store.messages(conv["conversation_id"])
    assert any(item["text"] == "问题 0" for item in archived)
    assert result["history"]["truncated"] is True
    assert result["history"]["sent"] == HISTORY_WINDOW
    assert result["history"]["total"] > HISTORY_WINDOW


def test_history_is_sent_as_templates_not_as_rendered_numbers(conversation, fixture):
    store, conv = conversation

    def first_answer(previous):
        payload = json.loads(previous["messages"][-1]["content"])
        return {"content": f"收盘价 {{fact:{payload['fact_id']}}}。"}

    client = ScriptedChatClient([
        {"tool_calls": [tool_call("get_price_history", {"ticker": "AAPL"})]},
        first_answer,
    ])
    first = round_for(fixture, store, conv, client).run()
    assert first["status"] == "passed"
    assert "美元/股" in first["text"]

    follow_up = ScriptedChatClient([{"content": "接着上面说。"}])
    round_for(fixture, store, conv, follow_up, question="那止损呢？").run()
    history = json.dumps(follow_up.requests[0]["messages"], ensure_ascii=False)
    # The model sees the placeholder form, so it continues in that style ...
    assert first["message"]["template_text"] in history
    assert "{fact:tiingo|AAPL" in history
    # ... and never the rendered digits.
    assert "美元/股" not in history


def test_the_short_window_note_is_reported_so_the_page_can_say_so(conversation, fixture):
    store, conv = conversation
    for index in range(9):
        store.append_message(conv["conversation_id"], role="user", text=f"问 {index}")
        store.append_message(conv["conversation_id"], role="assistant", text=f"答 {index}")
    client = ScriptedChatClient([{"content": "好。"}])
    result = round_for(fixture, store, conv, client).run()
    assert result["history"]["note"] == "较早的消息不再发给模型"


def test_the_system_prompt_version_is_recorded_on_the_message(conversation, fixture):
    from thesis_tracker.webapp.chat import PROMPT_VERSION

    store, conv = conversation
    client = ScriptedChatClient([{"content": "好。"}])
    result = round_for(fixture, store, conv, client).run()
    assert result["message"]["prompt_version"] == PROMPT_VERSION
    assert PROMPT_VERSION in client.requests[0]["messages"][0]["content"]


def test_progress_events_describe_the_round(conversation, fixture):
    store, conv = conversation
    events = []
    client = ScriptedChatClient([
        {"tool_calls": [tool_call("get_price_history", {"ticker": "AAPL"})]},
        {"content": "好。"},
    ])
    round_for(fixture, store, conv, client, progress=events.append).run()
    names = [item["event"] for item in events]
    # The model's first response is the first event: a "round started" event sent
    # before the request carried no usage and read as "0 tokens" on the job page.
    assert "round_start" not in names
    assert names[0] == "round"
    assert "tool_call" in names
    assert "round" in names
    assert names[-1] == "passed"
    assert [item["tool"] for item in events if item["event"] == "tool_call"] == [
        "get_price_history"]


def test_a_rejected_round_reports_progress_too(conversation, fixture):
    store, conv = conversation
    events = []
    # Four refused drafts: the first three are the attempts and their two
    # revisions, which is exactly the correction budget.
    client = ScriptedChatClient([{"content": "建议买入。"} for _ in range(4)])
    round_for(fixture, store, conv, client, progress=events.append).run()
    assert any(item["event"] == "draft_rejected" for item in events)
    assert events[-1]["event"] == "rejected"


def test_a_client_error_fails_the_round_without_a_draft(conversation, fixture):
    store, conv = conversation

    class Broken:
        def complete(self, **kwargs):
            raise RuntimeError("transport")

    result = round_for(fixture, store, conv, Broken()).run()
    assert result["status"] == "rejected"
    assert result["reason"] == "model_error"
    assert result["message"]["text"] is None


def test_an_unusable_usage_report_fails_the_round(conversation, fixture):
    store, conv = conversation
    client = ScriptedChatClient([{"content": "好。"}],
                                usage={"prompt_tokens": 1, "completion_tokens": 1,
                                       "prompt_cache_hit_tokens": 99})
    result = round_for(fixture, store, conv, client).run()
    assert result["status"] == "rejected"
    assert result["reason"] == "usage_unavailable"


def _question_id(store, conversation):
    """The id of the newest user message: what a turn's audit rows hang off."""
    users = [item for item in store.messages(conversation["conversation_id"])
             if item["role"] == "user"]
    return users[-1]["message_id"]
