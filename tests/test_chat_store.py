"""The chat store: multiple conversations, append-only messages, proposals.

Every test here uses its own database file; nothing touches runtime data.
"""

from __future__ import annotations

import json
import sqlite3

import pytest


@pytest.fixture
def store(tmp_path):
    from thesis_tracker.webapp.chat.store import ChatStore

    return ChatStore(tmp_path / "chat.db")


def test_a_conversation_belongs_to_one_company_and_starts_unarchived(store):
    conversation = store.create_conversation("nvda", title_hint=None)
    assert conversation["ticker"] == "NVDA"
    assert conversation["archived"] == 0
    assert conversation["created_at"]
    assert conversation["card_id"] is None
    assert store.get_conversation(conversation["conversation_id"])["ticker"] == "NVDA"


def test_a_company_can_hold_many_conversations_and_they_list_newest_first(store):
    first = store.create_conversation("AAPL")
    second = store.create_conversation("AAPL")
    third = store.create_conversation("NVDA")
    aapl = store.conversations_for("AAPL")
    assert [item["conversation_id"] for item in aapl] == [second["conversation_id"],
                                                         first["conversation_id"]]
    assert [item["conversation_id"] for item in store.conversations_for("NVDA")] == [
        third["conversation_id"]]
    assert store.conversations_for("MSFT") == []
    assert third["ticker"] == "NVDA"


def test_the_title_is_the_first_user_message_truncated_to_24_characters(store):
    from thesis_tracker.webapp.chat.store import TITLE_LENGTH

    assert TITLE_LENGTH == 24
    conversation = store.create_conversation("AAPL")
    assert store.conversation_title(conversation["conversation_id"]) is None
    long_text = "现在能买吗？我想知道这家公司最近的财务质量到底怎么样，还有多远到止损"
    store.append_message(conversation["conversation_id"], role="user", text=long_text)
    title = store.conversation_title(conversation["conversation_id"])
    assert title == long_text[:24]
    assert len(title) == 24
    # A later message never rewrites the title.
    store.append_message(conversation["conversation_id"], role="user", text="第二个问题")
    assert store.conversation_title(conversation["conversation_id"]) == long_text[:24]


def test_a_short_first_message_becomes_the_whole_title(store):
    conversation = store.create_conversation("AAPL")
    store.append_message(conversation["conversation_id"], role="user", text="止损多远")
    assert store.conversation_title(conversation["conversation_id"]) == "止损多远"


def test_an_assistant_message_never_sets_the_title(store):
    conversation = store.create_conversation("AAPL")
    store.append_message(conversation["conversation_id"], role="assistant", text="这是回答")
    assert store.conversation_title(conversation["conversation_id"]) is None


def test_messages_keep_their_order_and_every_recorded_field(store):
    conversation = store.create_conversation("NVDA")
    first = store.append_message(conversation["conversation_id"], role="user",
                                 text="距离止损还有多远？")
    second = store.append_message(
        conversation["conversation_id"], role="assistant", text="距离止损 3.88%。",
        template_text="距离止损 {fact:derived|x|1}。",
        segments=[{"type": "text", "value": "距离止损 "},
                  {"type": "fact", "fact_id": "derived|x|1", "display": "3.88%"}],
        attempts=2, prompt_version="chat-v1")
    listed = store.messages(conversation["conversation_id"])
    assert [item["message_id"] for item in listed] == [first["message_id"],
                                                       second["message_id"]]
    assert listed[1]["role"] == "assistant"
    assert listed[1]["template_text"] == "距离止损 {fact:derived|x|1}。"
    assert listed[1]["segments"][0]["type"] == "text"
    assert listed[1]["attempts"] == 2
    assert listed[1]["prompt_version"] == "chat-v1"
    assert listed[1]["rejected"] is None
    assert listed[0]["segments"] is None


def test_a_rejected_answer_is_stored_with_its_violations_and_no_visible_draft(store):
    conversation = store.create_conversation("AAPL")
    rejected = store.append_message(
        conversation["conversation_id"], role="assistant", text=None,
        rejected={"draft": "现在可以买入。", "violations": [
            {"rule": "C02", "location": "body", "message": "正文里不能出现动作词“买入”。"}]},
        attempts=3, prompt_version="chat-v1")
    stored = store.messages(conversation["conversation_id"])[0]
    assert stored["message_id"] == rejected["message_id"]
    assert stored["text"] is None
    assert stored["rejected"]["draft"] == "现在可以买入。"
    assert stored["rejected"]["violations"][0]["rule"] == "C02"


def test_messages_cannot_be_updated_or_deleted(store):
    conversation = store.create_conversation("AAPL")
    store.append_message(conversation["conversation_id"], role="user", text="你好")
    with sqlite3.connect(store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE messages SET text='改过的'")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM messages")


def test_conversation_metadata_still_updates(store):
    """Only messages are immutable; renaming and archiving stay possible."""
    conversation = store.create_conversation("AAPL", title_hint="关注毛利率")
    store.rename_conversation(conversation["conversation_id"], "改过的标题")
    assert store.get_conversation(conversation["conversation_id"])["title"] == "改过的标题"
    store.archive_conversation(conversation["conversation_id"])
    archived = store.get_conversation(conversation["conversation_id"])
    assert archived["archived"] == 1
    assert store.conversations_for("AAPL") == []
    assert len(store.conversations_for("AAPL", include_archived=True)) == 1


def test_tool_calls_are_recorded_with_the_envelope_verbatim(store):
    conversation = store.create_conversation("NVDA")
    message = store.append_message(conversation["conversation_id"], role="user", text="价格")
    envelope = {"status": "ok", "data": {"symbol": "NVDA", "latest_close": {"value": 233.95}},
                "as_of": "2026-10-04", "fact_id": "tiingo|NVDA|2026-10-02|daily",
                "reason": None}
    store.append_tool_call(message["message_id"], tool="get_price_history",
                           args={"ticker": "NVDA"}, envelope=envelope)
    calls = store.tool_calls(message["message_id"])
    assert len(calls) == 1
    assert calls[0]["tool"] == "get_price_history"
    assert calls[0]["args"] == {"ticker": "NVDA"}
    assert calls[0]["envelope"] == envelope
    # The recorded byte count is the envelope's own UTF-8 length.
    assert calls[0]["bytes"] == len(json.dumps(envelope, ensure_ascii=False).encode("utf-8"))


def test_model_calls_record_both_model_names_and_usage(store):
    conversation = store.create_conversation("NVDA")
    message = store.append_message(conversation["conversation_id"], role="user", text="价格")
    store.append_model_call(message["message_id"], round_no=1, requested_model="deepseek-flash",
                            returned_model="deepseek-flash", fingerprint="fp_1",
                            input_tokens=100, output_tokens=20, cache_hit_tokens=64)
    calls = store.model_calls(message["message_id"])
    assert calls[0]["requested_model"] == "deepseek-flash"
    assert calls[0]["returned_model"] == "deepseek-flash"
    assert calls[0]["fingerprint"] == "fp_1"
    assert calls[0]["input_tokens"] == 100
    assert calls[0]["cache_hit_tokens"] == 64


def test_a_proposal_starts_pending_and_can_be_confirmed_once(store):
    conversation = store.create_conversation("NVDA")
    message = store.append_message(conversation["conversation_id"], role="assistant",
                                   text="我可以帮你生成一张新的建议卡。")
    proposal = store.create_proposal(conversation["conversation_id"], message["message_id"],
                                     horizon="mid", reason="现有卡是短期的，需要中期判断")
    assert proposal["status"] == "pending"
    assert proposal["horizon"] == "mid"
    assert proposal["job_id"] is None
    assert [item["proposal_id"] for item in store.pending_proposals(
        conversation["conversation_id"])] == [proposal["proposal_id"]]
    store.confirm_proposal(proposal["proposal_id"], job_id="job-1")
    confirmed = store.get_proposal(proposal["proposal_id"])
    assert confirmed["status"] == "confirmed"
    assert confirmed["job_id"] == "job-1"
    assert store.pending_proposals(conversation["conversation_id"]) == []
    # Confirming twice must not create a second job.
    assert store.confirm_proposal(proposal["proposal_id"], job_id="job-2") is None
    assert store.get_proposal(proposal["proposal_id"])["job_id"] == "job-1"


def test_a_dismissed_proposal_can_never_be_confirmed(store):
    conversation = store.create_conversation("NVDA")
    message = store.append_message(conversation["conversation_id"], role="assistant",
                                   text="需要新卡吗？")
    proposal = store.create_proposal(conversation["conversation_id"], message["message_id"],
                                     horizon="short", reason="理由")
    assert store.dismiss_proposal(proposal["proposal_id"]) is True
    assert store.get_proposal(proposal["proposal_id"])["status"] == "dismissed"
    assert store.confirm_proposal(proposal["proposal_id"], job_id="job-1") is None
    assert store.get_proposal(proposal["proposal_id"])["job_id"] is None
    assert store.dismiss_proposal(proposal["proposal_id"]) is False


def test_a_system_message_is_recorded_after_a_job_finishes(store):
    conversation = store.create_conversation("NVDA")
    message = store.append_message(conversation["conversation_id"], role="assistant",
                                   text="需要新卡吗？")
    proposal = store.create_proposal(conversation["conversation_id"], message["message_id"],
                                     horizon="short", reason="理由")
    store.confirm_proposal(proposal["proposal_id"], job_id="job-1")
    system = store.append_message(
        conversation["conversation_id"], role="system",
        text="建议卡已生成。",
        segments=[{"type": "text", "value": "建议卡已生成。"},
                  {"type": "card_link", "card_id": "card-1", "card_id_short": "card-1  "[:8]}],
        proposal_id=proposal["proposal_id"])
    stored = [item for item in store.messages(conversation["conversation_id"])
              if item["message_id"] == system["message_id"]][0]
    assert stored["role"] == "system"
    assert stored["proposal_id"] == proposal["proposal_id"]
    assert stored["segments"][1]["type"] == "card_link"


def test_an_unknown_conversation_id_raises(store):
    with pytest.raises(KeyError):
        store.get_conversation("does-not-exist")
    with pytest.raises(KeyError):
        store.messages("does-not-exist")


def test_an_unknown_proposal_id_raises(store):
    with pytest.raises(KeyError):
        store.get_proposal("nope")
    assert store.confirm_proposal("nope", job_id="job") is None
    assert store.dismiss_proposal("nope") is False


def test_two_conversations_of_one_company_share_nothing(store):
    """Messages, tool calls and proposals stay inside their own conversation."""
    first = store.create_conversation("NVDA")
    second = store.create_conversation("NVDA")
    a = store.append_message(first["conversation_id"], role="user", text="第一个对话")
    b = store.append_message(second["conversation_id"], role="user", text="第二个对话")
    store.append_tool_call(a["message_id"], tool="get_price_history", args={"ticker": "NVDA"},
                           envelope={"status": "ok"})
    store.create_proposal(first["conversation_id"], a["message_id"], horizon="short",
                          reason="只属于第一个对话")
    assert [item["text"] for item in store.messages(first["conversation_id"])] == ["第一个对话"]
    assert [item["text"] for item in store.messages(second["conversation_id"])] == ["第二个对话"]
    assert store.tool_calls(b["message_id"]) == []
    assert len(store.tool_calls(a["message_id"])) == 1
    assert store.pending_proposals(second["conversation_id"]) == []
    assert len(store.pending_proposals(first["conversation_id"])) == 1


def test_the_chat_database_lives_outside_any_cache_directory(store):
    assert "cache" not in store.path.parts
    assert store.path.name == "chat.db"


def test_the_schema_is_created_on_first_use(tmp_path):
    from thesis_tracker.webapp.chat.store import ChatStore

    path = tmp_path / "nested" / "chat.db"
    assert not path.exists()
    ChatStore(path)
    assert path.exists()
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"conversations", "messages", "chat_tool_calls", "chat_model_calls",
            "proposals"} <= tables
