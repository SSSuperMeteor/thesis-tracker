"""The agent loop is bounded, auditable, and keeps tool dates under Python control."""

from __future__ import annotations

import json
import socket
import sqlite3

import pytest

from thesis_tracker.decision.agent import DeepSeekClient, _parse_draft, run_analysis
from thesis_tracker.decision.analyze_cli import main as analyze_main
from thesis_tracker.decision.core import capture_snapshot, read_card


class ScriptedClient:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    def complete(self, *, messages, tools, max_tokens):
        self.requests.append({"messages": json.loads(json.dumps(messages)),
                              "tools": tools, "max_tokens": max_tokens})
        reply = next(self.replies)
        if callable(reply):
            reply = reply(self.requests[-1])
        return {"model": "deepseek-flash", "system_fingerprint": "fp_test",
                "usage": {"prompt_tokens": 100, "completion_tokens": 50,
                          "prompt_cache_hit_tokens": 20},
                "message": {"role": "assistant", "content": None, "reasoning_content": "checked evidence",
                            "tool_calls": None}, "finish_reason": "stop", **reply}


def call(name="get_price_history", args=None, call_id="call_1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args or {"ticker": "AAPL"})}}


def tool_reply(*calls):
    return {"message": {"role": "assistant", "content": None,
                        "reasoning_content": "need local evidence", "tool_calls": list(calls)},
            "finish_reason": "tool_calls"}


def final_reply(draft):
    return {"message": {"role": "assistant", "content": json.dumps(draft, ensure_ascii=False),
                        "reasoning_content": "checked", "tool_calls": None}}


@pytest.fixture
def legal_draft():
    snapshot = capture_snapshot("AAPL", "2026-10-04")
    price = snapshot["calls"][0]["envelope"]["fact_id"]
    indicator = snapshot["calls"][1]["envelope"]["data"]["latest"]["values"]["rsi_14"]["fact_id"]
    return {"ticker": "AAPL", "as_of": "2026-10-04", "horizon": "中期", "bias": "看多",
            "action": "买入", "confidence": "中", "entry_range": [324, 334],
            "stop_loss": 290, "target_price": 400, "fact_ids": [price, indicator],
            "reasons": [{"text": "收盘价 {fact:" + price + "}，RSI {fact:" + indicator + "}。",
                         "fact_ids": [price, indicator]}],
            "invalidations": [{"kind": "close_below", "price": 290, "text": "收盘价跌破止损位"}],
            "stop_rationale": "跌破 {fact:" + price + "} 离场。",
            "target_rationale": "上看 {fact:" + indicator + "} 上方。"}


@pytest.fixture
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("network used in fake-client test")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


def test_normal_tool_loop_archives_valid_card_and_echoes_reasoning(legal_draft, offline, tmp_path):
    client = ScriptedClient([tool_reply(call(), call("get_indicators", call_id="call_2"),
                                        call("get_fundamental_metrics", call_id="call_3")),
                             final_reply(legal_draft)])
    path = tmp_path / "cards.db"
    result = run_analysis("AAPL", "2026-10-04", client=client, archive_path=path)
    assert result["status"] == "passed"
    assert result["stats"]["rounds"] == 2 and result["stats"]["tool_calls"] == 3
    assert result["stats"]["tools"] == [
        "get_price_history", "get_indicators", "get_fundamental_metrics"]
    assert len(result["snapshot"]["calls"]) == result["stats"]["prefetch_calls"] + 3
    assert [item["resolution"] for item in result["card"]["evidence_windows"]] == [
        "latest_and_landmarks", "monthly_2y", "latest", "latest", "latest"]
    assert "reasoning_content" in client.requests[1]["messages"][-4]
    assert [tool["function"]["name"] for tool in client.requests[0]["tools"]] == [
        "get_price_history", "get_indicators", "get_fundamental_metrics"]
    assert all("full_history" not in tool["function"]["parameters"]["properties"]
               and "limit" not in tool["function"]["parameters"]["properties"]
               and "end_date" not in tool["function"]["parameters"]["properties"]
               for tool in client.requests[0]["tools"])
    saved = read_card(path, result["card_id"])
    assert saved["requested_model"] == "deepseek-flash"
    assert saved["returned_model"] == "deepseek-flash"
    assert saved["fingerprint"] == "fp_test"
    assert saved["prompt_version"]
    assert saved["input_tokens"] == 200 and saved["output_tokens"] == 100
    assert saved["cache_hit_tokens"] == 40
    assert "AI 判断" in result["rendered"]
    with sqlite3.connect(path) as conn:
        tools = conn.execute("SELECT tool_name FROM decision_tool_calls ORDER BY call_no").fetchall()
    assert [row[0] for row in tools] == result["stats"]["tools"]


def test_model_as_of_is_rejected_and_never_reaches_tool(legal_draft, offline, tmp_path):
    client = ScriptedClient([tool_reply(call(args={"ticker": "AAPL", "as_of": "2020-01-01"})),
                             tool_reply(call()), final_reply(legal_draft)])
    result = run_analysis("AAPL", "2026-10-04", client=client, archive_path=tmp_path / "a.db")
    assert result["status"] == "passed"
    assert result["stats"]["tool_calls"] == 2
    assert all(item["args"]["as_of"] == "2026-10-04" for item in result["snapshot"]["calls"])
    assert "as_of" in client.requests[1]["messages"][-1]["content"]


@pytest.mark.parametrize(("replies", "reason"), [
    ([tool_reply(*[call(call_id=f"c{i}") for i in range(13)])], "tool_limit"),
    ([tool_reply() for _ in range(16)], "round_limit"),
    ([{"usage": {"prompt_tokens": 1_500_000, "completion_tokens": 1,
                 "prompt_cache_hit_tokens": 0}, **final_reply({})}], "token_limit"),
])
def test_hard_limits_reject_without_card(replies, reason, offline, tmp_path):
    result = run_analysis("AAPL", "2026-10-04", client=ScriptedClient(replies),
                          archive_path=tmp_path / "limit.db")
    assert result["status"] == "rejected"
    assert result["reason"] == reason
    assert result["card_id"] is None


def test_parse_and_validation_feedback_can_be_corrected(legal_draft, offline, tmp_path):
    bad = dict(legal_draft, bias="看空")
    client = ScriptedClient([tool_reply(call(), call("get_indicators", call_id="c2")),
                             {"message": {"role": "assistant", "content": "{bad",
                                          "reasoning_content": "", "tool_calls": None}},
                             final_reply(bad), final_reply(legal_draft)])
    result = run_analysis("AAPL", "2026-10-04", client=client,
                          archive_path=tmp_path / "cards.db")
    assert result["status"] == "passed"
    assert result["stats"]["revisions"] == 2
    assert "D00" in client.requests[2]["messages"][-1]["content"]
    assert "D04" in client.requests[3]["messages"][-1]["content"]
    with sqlite3.connect(tmp_path / "cards.db") as conn:
        rows = conn.execute("SELECT raw_output, violations_json, passed FROM decision_attempts ORDER BY attempt_no").fetchall()
    assert len(rows) == 3 and [r[2] for r in rows] == [0, 0, 1]
    assert rows[0][0] == "{bad" and "D04" in rows[1][1]


def test_correction_limit_rejects_after_three_bad_attempts(legal_draft, offline, tmp_path):
    bad = dict(legal_draft, bias="看空")
    client = ScriptedClient([tool_reply(call(), call("get_indicators", call_id="c2")),
                             final_reply(bad), final_reply(bad), final_reply(bad)])
    result = run_analysis("AAPL", "2026-10-04", client=client,
                          archive_path=tmp_path / "cards.db")
    assert result["status"] == "rejected" and result["reason"] == "correction_limit"
    assert result["stats"]["revisions"] == 2
    with sqlite3.connect(tmp_path / "cards.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM decision_attempts").fetchone()[0] == 3


def test_bad_tool_name_or_arguments_return_structured_errors(legal_draft, offline, tmp_path):
    client = ScriptedClient([tool_reply(call(name="unknown"),
                                        call(args={"ticker": "AAPL", "limit": 101}, call_id="c2")),
                             final_reply(legal_draft)])
    result = run_analysis("AAPL", "2026-10-04", client=client,
                          archive_path=tmp_path / "cards.db")
    assert result["status"] == "passed"
    tool_messages = [item for item in client.requests[1]["messages"] if item["role"] == "tool"]
    assert len(tool_messages) == 2
    assert all(json.loads(item["content"])["status"] == "error" for item in tool_messages)
    with sqlite3.connect(tmp_path / "cards.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM decision_tool_calls").fetchone()[0] == 2


def test_attempt_table_is_immutable_and_secret_never_persisted_or_printed(
    legal_draft, offline, tmp_path, capsys, caplog, monkeypatch,
):
    sentinel = "DEEPSEEK_SENTINEL_DO_NOT_STORE_92832"
    monkeypatch.setenv("DEEPSEEK_API_KEY", sentinel)
    client = ScriptedClient([tool_reply(call()), final_reply(legal_draft)])
    path = tmp_path / "cards.db"
    result = run_analysis("AAPL", "2026-10-04", client=client, archive_path=path)
    assert result["status"] == "passed"  # the indicator fact is now in the prefetched base pack
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT attempt_id FROM decision_attempts LIMIT 1").fetchone()
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            conn.execute("DELETE FROM decision_attempts WHERE attempt_id=?", row)
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            conn.execute("UPDATE decision_attempts SET passed=1 WHERE attempt_id=?", row)
    assert sentinel not in capsys.readouterr().out
    assert sentinel not in caplog.text
    assert sentinel.encode() not in path.read_bytes()
    assert sentinel not in json.dumps(result, ensure_ascii=False)


def test_analyze_command_prints_validated_chinese_card(legal_draft, offline, tmp_path, capsys):
    client = ScriptedClient([tool_reply(call(), call("get_indicators", call_id="c2")),
                             final_reply(legal_draft)])
    assert analyze_main(["AAPL", "--as-of", "2026-10-04"], client=client,
                        archive_path=tmp_path / "cards.db") == 0
    output = capsys.readouterr().out
    assert "AI 判断" in output and "数据缺口" in output
    assert "工具调用 2 次" in output
    assert "基础包字节" in output
    assert "逐轮 token" in output
    assert "逐次工具字节" in output


def test_api_key_is_never_sent_to_a_non_deepseek_host(monkeypatch, offline):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "DEEPSEEK_SENTINEL_HOST_GUARD")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.com")
    with pytest.raises(ValueError, match="DeepSeek endpoint"):
        DeepSeekClient()


def test_legacy_full_history_gets_resolution_error_and_can_recover(legal_draft, offline, tmp_path):
    client = ScriptedClient([tool_reply(call("get_indicators", args={
        "ticker": "AAPL", "full_history": True}, call_id="history")),
                             tool_reply(call("get_indicators", args={
                                 "ticker": "AAPL", "resolution": "weekly_3m",
                                 "fields": ["rsi_14"]}, call_id="tiered")),
                             final_reply(legal_draft)])
    result = run_analysis("AAPL", "2026-10-04", client=client,
                          archive_path=tmp_path / "cards.db")
    assert result["status"] == "passed"
    assert result["stats"]["rounds"] == 3
    assert json.loads(client.requests[1]["messages"][-1]["content"])["reason"]["code"] == "resolution_required"
    assert len(client.requests) == 3


def test_real_client_disables_hidden_http_retries(monkeypatch, offline):
    import openai

    configured = {}
    monkeypatch.setenv("DEEPSEEK_API_KEY", "DEEPSEEK_SENTINEL_NO_RETRY")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    def fake_openai(**kwargs):
        configured.update(kwargs)
        return object()

    monkeypatch.setattr(openai, "OpenAI", fake_openai)
    DeepSeekClient()
    assert configured["max_retries"] == 0


def test_prefetched_base_and_catalog_are_in_first_request(legal_draft, offline, tmp_path):
    client = ScriptedClient([final_reply(legal_draft)])
    result = run_analysis("AAPL", "2026-10-04", client=client,
                          archive_path=tmp_path / "base.db")
    assert result["status"] == "passed"
    assert result["stats"]["prefetch_calls"] > 3
    assert result["stats"]["tool_calls"] == 0
    first = client.requests[0]["messages"]
    assert "catalog" in first[1]["content"]
    assert "base_pack" in first[1]["content"]
    assert result["card"]["evidence_windows"] == result["snapshot"]["evidence_windows"]


def test_history_resolution_is_compact_and_recorded(legal_draft, offline, tmp_path):
    client = ScriptedClient([tool_reply(call("get_price_history", {
        "ticker": "AAPL", "resolution": "weekly_3m", "fields": ["close", "adjusted_close"]})),
                             final_reply(legal_draft)])
    result = run_analysis("AAPL", "2026-10-04", client=client,
                          archive_path=tmp_path / "history.db")
    assert result["status"] == "passed"
    payload = json.loads(client.requests[1]["messages"][-1]["content"])
    assert payload["data"]["resolution"] == "weekly_3m"
    assert 1 <= len(payload["data"]["rows"]) <= 15
    assert len(json.dumps(payload, ensure_ascii=False).encode()) <= 32768
    assert result["card"]["evidence_windows"][-1]["resolution"] == "weekly_3m"
    assert read_card(tmp_path / "history.db", result["card_id"])["card"]["evidence_windows"] == result["card"]["evidence_windows"]


def test_soft_limit_prevents_new_tools_and_hard_limit_rejects(offline, tmp_path):
    huge = {"usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1,
                      "prompt_cache_hit_tokens": 0}}
    client = ScriptedClient([{**huge, **tool_reply(call())}])
    result = run_analysis("AAPL", "2026-10-04", client=client,
                          archive_path=tmp_path / "soft.db")
    assert result["status"] == "rejected" and result["reason"] == "token_soft_limit"
    assert result["stats"]["tool_calls"] == 0
    assert len(client.requests) == 1
    over = {"usage": {"prompt_tokens": 1_500_001, "completion_tokens": 1,
                      "prompt_cache_hit_tokens": 0}}
    result = run_analysis("AAPL", "2026-10-04", client=ScriptedClient([{**over, **final_reply({})}]),
                          archive_path=tmp_path / "hard.db")
    assert result["status"] == "rejected" and result["reason"] == "token_limit"


def test_soft_limit_still_allows_json_correction(legal_draft, offline, tmp_path):
    first = {"usage": {"prompt_tokens": 1_000_001, "completion_tokens": 1,
                       "prompt_cache_hit_tokens": 0}, **final_reply({})}
    client = ScriptedClient([first, final_reply(legal_draft)])
    result = run_analysis("AAPL", "2026-10-04", client=client,
                          archive_path=tmp_path / "soft_correction.db")
    assert result["status"] == "passed" and result["stats"]["revisions"] == 1
    assert client.requests[1]["tools"] == []


def test_input_estimate_blocks_request_before_network(offline, tmp_path, monkeypatch):
    monkeypatch.setattr("thesis_tracker.decision.agent.MAX_REQUEST_INPUT_TOKENS", 100)
    client = ScriptedClient([])
    result = run_analysis("AAPL", "2026-10-04", client=client,
                          archive_path=tmp_path / "gate.db")
    assert result["status"] == "rejected" and result["reason"] == "request_input_limit"
    assert client.requests == []


def test_model_cannot_supply_evidence_windows(legal_draft):
    draft, errors = _parse_draft(json.dumps(dict(legal_draft, evidence_windows=[])))
    assert draft is None
    assert any(item["rule"] == "D00" and item["location"] == "evidence_windows" for item in errors)
