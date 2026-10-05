"""The optional progress callback must never change a run that does not pass one."""

from __future__ import annotations

import json

import pytest
from webapp_fixtures import guard_offline

from thesis_tracker.decision.agent import run_analysis
from thesis_tracker.decision.core import capture_snapshot

AS_OF = "2026-10-04"
TICKER = "AAPL"


class ScriptedClient:
    """Same scripted fake the existing agent tests use: no network, no model."""

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
                "message": {"role": "assistant", "content": None,
                            "reasoning_content": "checked evidence", "tool_calls": None},
                "finish_reason": "stop", **reply}


def call(name="get_price_history", args=None, call_id="call_1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args or {"ticker": TICKER})}}


def tool_reply(*calls):
    return {"message": {"role": "assistant", "content": None,
                        "reasoning_content": "need local evidence",
                        "tool_calls": list(calls)}, "finish_reason": "tool_calls"}


def final_reply(draft):
    return {"message": {"role": "assistant", "content": json.dumps(draft, ensure_ascii=False),
                        "reasoning_content": "checked", "tool_calls": None}}


@pytest.fixture
def legal_draft():
    snapshot = capture_snapshot(TICKER, AS_OF)
    price = snapshot["calls"][0]["envelope"]["fact_id"]
    indicator = snapshot["calls"][1]["envelope"]["data"]["latest"]["values"]["rsi_14"]["fact_id"]
    return {"ticker": TICKER, "as_of": AS_OF, "horizon": "中期", "bias": "看多",
            "action": "买入", "confidence": "中", "entry_range": [324, 334],
            "stop_loss": 290, "target_price": 400, "fact_ids": [price, indicator],
            "reasons": [{"text": "收盘价 {fact:" + price + "}，RSI {fact:" + indicator + "}。",
                         "fact_ids": [price, indicator]}],
            "invalidations": [{"kind": "close_below", "price": 290, "text": "收盘价跌破止损位"}],
            "stop_rationale": "跌破 {fact:" + price + "} 离场。",
            "target_rationale": "上看 {fact:" + indicator + "} 上方。"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    guard_offline(monkeypatch)


def passing_script(legal_draft):
    return ScriptedClient([
        tool_reply(call(), call("get_indicators", call_id="call_2")),
        final_reply(legal_draft),
    ])


def test_without_a_callback_the_run_and_its_result_are_unchanged(legal_draft, tmp_path):
    """The default call is byte-identical to before the parameter existed."""
    result = run_analysis(TICKER, AS_OF, client=passing_script(legal_draft),
                          archive_path=tmp_path / "cards.db")
    assert result["status"] == "passed"
    assert result["stats"]["rounds"] == 2
    assert result["stats"]["tool_calls"] == 2
    assert result["stats"]["tools"] == ["get_price_history", "get_indicators"]
    assert result["card"]["action"] == "买入"
    assert result["card_id"] and result["rendered"]
    assert set(result) == {"status", "reason", "violations", "card", "card_id",
                           "rendered", "snapshot", "stats", "analysis_id"}
    assert set(result["stats"]) == {
        "rounds", "tool_calls", "revisions", "input_tokens", "output_tokens",
        "cache_hit_tokens", "model_calls", "tools", "tool_details",
        "prefetch_calls", "base_pack_bytes", "catalog_bytes", "gate_reason"}
    # No progress key leaks into the result of a callback-free run.
    assert "progress" not in result and "progress" not in result["stats"]


def test_passing_none_explicitly_behaves_exactly_like_omitting_it(legal_draft, tmp_path):
    without = run_analysis(TICKER, AS_OF, client=passing_script(legal_draft),
                           archive_path=tmp_path / "a.db")
    with_none = run_analysis(TICKER, AS_OF, client=passing_script(legal_draft),
                             archive_path=tmp_path / "b.db", progress=None)
    assert with_none["status"] == without["status"] == "passed"
    assert with_none["stats"] == without["stats"]
    assert with_none["card"] == without["card"]
    assert with_none["rendered"] == without["rendered"]


def test_events_arrive_in_run_order_when_a_callback_is_given(legal_draft, tmp_path):
    events = []
    result = run_analysis(TICKER, AS_OF, client=passing_script(legal_draft),
                          archive_path=tmp_path / "cards.db", progress=events.append)
    assert result["status"] == "passed"
    names = [event["event"] for event in events]
    assert names[0] == "prefetch"
    assert names[-1] == "passed"
    assert "round_start" in names and "tool_call" in names
    assert "draft_rejected" not in names

    prefetch = events[0]
    assert prefetch["tool_calls"] == result["stats"]["prefetch_calls"]
    rounds = [event["round"] for event in events if event["event"] == "round_start"]
    assert rounds == [1, 2]
    tools = [event for event in events if event["event"] == "tool_call"]
    assert [event["tool"] for event in tools] == ["get_price_history", "get_indicators"]
    for event in tools:
        assert isinstance(event["bytes"], int) and event["bytes"] > 0
        assert event["status"] in {"ok", "unavailable", "error", "not_applicable"}
        assert event["args"]
    passed = events[-1]
    assert passed["card_id"] == result["card_id"]
    assert passed["input_tokens"] == result["stats"]["input_tokens"]
    assert passed["output_tokens"] == result["stats"]["output_tokens"]
    assert "card" not in passed and "rendered" not in passed


def test_per_round_usage_is_reported_for_the_progress_list(legal_draft, tmp_path):
    events = []
    run_analysis(TICKER, AS_OF, client=passing_script(legal_draft),
                 archive_path=tmp_path / "cards.db", progress=events.append)
    rounds = [event for event in events if event["event"] == "round_start"]
    assert [event["index"] for event in rounds] == [1, 2]
    # The usage on a round-start event is the running total before that request,
    # so the second round shows what the first one cost.
    assert [event["input_tokens"] for event in rounds] == [0, 100]
    assert [event["output_tokens"] for event in rounds] == [0, 50]
    assert [event["cache_hit_tokens"] for event in rounds] == [0, 20]
    assert events[-1]["event"] == "passed"
    assert events[-1]["input_tokens"] == 200
    assert events[-1]["output_tokens"] == 100
    assert events[-1]["cache_hit_tokens"] == 40


def test_a_rejected_draft_reports_its_rules(legal_draft, tmp_path):
    bad = dict(legal_draft, action="分批", entry_range=None, stop_loss=None,
               target_price=None, stop_rationale=None, target_rationale=None)
    events = []
    result = run_analysis(TICKER, AS_OF,
                          client=ScriptedClient([final_reply(bad)]),
                          archive_path=tmp_path / "cards.db", progress=events.append)
    assert result["status"] == "rejected"
    rejected = [event for event in events if event["event"] == "draft_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["attempt"] == 1
    assert rejected[0]["rules"], "a rejected draft must name its violated rules"
    assert all(set(item) == {"rule", "location", "message"}
               for item in rejected[0]["violations"])
    assert events[-1]["event"] == "rejected"
    assert events[-1]["reason"] == result["reason"]


def test_a_failing_callback_cannot_break_the_analysis(legal_draft, tmp_path):
    def explode(_event):
        raise RuntimeError("progress sink is broken")

    result = run_analysis(TICKER, AS_OF, client=passing_script(legal_draft),
                          archive_path=tmp_path / "cards.db", progress=explode)
    assert result["status"] == "passed"


def test_progress_events_carry_no_secret_or_raw_model_text(legal_draft, tmp_path):
    events = []
    run_analysis(TICKER, AS_OF, client=passing_script(legal_draft),
                 archive_path=tmp_path / "cards.db", progress=events.append)
    blob = json.dumps(events, ensure_ascii=False)
    assert "reasoning_content" not in blob
    assert "checked evidence" not in blob
    allowed = {"event", "round", "index", "tool", "args", "bytes", "status",
               "tool_calls", "input_tokens", "output_tokens", "cache_hit_tokens",
               "attempt", "rules", "violations", "reason", "card_id", "rounds",
               "revisions", "base_pack_bytes", "catalog_bytes"}
    for event in events:
        assert set(event) <= allowed, set(event) - allowed
