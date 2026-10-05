"""The model-facing tool contract must match what the dispatch layer accepts.

The tool schema, the English tool descriptions, the Chinese system prompt and the
catalog are all read by the model.  Anything they advertise must be accepted by
``_dispatch``; anything the dispatch rejects must carry an actionable repair hint
instead of ending the analysis.
"""

from __future__ import annotations

import json

import pytest

from thesis_tracker.decision.agent import (
    SYSTEM_PROMPT,
    TOOL_SCHEMAS,
    _dispatch,
    run_analysis,
)
from thesis_tracker.decision.core import capture_snapshot
from thesis_tracker.decision.evidence import (
    HISTORY_FIELDS,
    RESOLUTIONS,
    TOOL_HISTORY_FIELDS,
    history_field_help,
    prepare_evidence,
)

TICKER = "AAPL"
AS_OF = "2026-10-04"
HISTORY_TOOLS = tuple(TOOL_HISTORY_FIELDS)
PRICE_FIELD = TOOL_HISTORY_FIELDS["get_price_history"][0]


def function_schema(name: str) -> dict:
    return next(tool["function"] for tool in TOOL_SCHEMAS if tool["function"]["name"] == name)


def schema_properties(name: str) -> dict:
    return function_schema(name)["parameters"]["properties"]


@pytest.fixture(scope="module")
def snapshot():
    prepared, _, _ = prepare_evidence(TICKER, AS_OF)
    return prepared


def dispatch(name: str, raw_arguments: str, snapshot: dict):
    return _dispatch(name, raw_arguments, TICKER, AS_OF, snapshot)


# --------------------------------------------------------------------------
# 1. Consistency: every advertised parameter and enum value is accepted.
# --------------------------------------------------------------------------


def test_advertised_parameters_exist_and_are_accepted(snapshot):
    """Every property a schema advertises must survive dispatch with a valid value."""
    for tool in TOOL_SCHEMAS:
        name = tool["function"]["name"]
        properties = tool["function"]["parameters"]["properties"]
        assert set(properties) <= {"ticker", "resolution", "fields"}
        record, error = dispatch(name, json.dumps({"ticker": TICKER}), snapshot)
        assert error is None, (name, error)
        assert record is not None
        if "resolution" in properties:
            record, error = dispatch(
                name, json.dumps({"ticker": TICKER, "resolution": RESOLUTIONS[0],
                                  "fields": [TOOL_HISTORY_FIELDS[name][0]]}), snapshot)
            assert error is None, (name, error)


def test_advertised_history_fields_are_exactly_what_each_tool_accepts(snapshot):
    for name in HISTORY_TOOLS:
        advertised = set(schema_properties(name)["fields"]["items"]["enum"])
        accepted = set(TOOL_HISTORY_FIELDS[name])
        assert advertised == accepted, (name, sorted(advertised), sorted(accepted))
        for field in sorted(advertised):
            record, error = dispatch(
                name, json.dumps({"ticker": TICKER, "resolution": "daily_10",
                                  "fields": [field]}), snapshot)
            assert error is None, (name, field, error)


def test_each_tool_rejects_the_other_tools_fields_with_a_named_hint(snapshot):
    for name in HISTORY_TOOLS:
        accepted = set(TOOL_HISTORY_FIELDS[name])
        for field in sorted(set(HISTORY_FIELDS) - accepted):
            record, error = dispatch(
                name, json.dumps({"ticker": TICKER, "resolution": "daily_10",
                                  "fields": [field]}), snapshot)
            assert record is None
            message = error["reason"]["message"]
            assert field in message, (name, field, message)
            assert any(item in message for item in sorted(accepted)), (name, message)


def test_advertised_resolutions_are_exactly_what_each_tool_accepts(snapshot):
    for name in HISTORY_TOOLS:
        advertised = schema_properties(name)["resolution"]["enum"]
        assert list(advertised) == list(RESOLUTIONS), name
        for resolution in advertised:
            record, error = dispatch(
                name, json.dumps({"ticker": TICKER, "resolution": resolution,
                                  "fields": [TOOL_HISTORY_FIELDS[name][0]]}), snapshot)
            assert error is None, (name, resolution, error)


def test_spy_is_advertised_only_where_dispatch_accepts_it(snapshot):
    for name in HISTORY_TOOLS:
        record, error = dispatch(name, json.dumps({"ticker": "SPY"}), snapshot)
        assert error is None, (name, error)
        record, error = dispatch(
            name, json.dumps({"ticker": "SPY", "resolution": "daily_10",
                              "fields": [TOOL_HISTORY_FIELDS[name][0]]}), snapshot)
        assert record is None
        message = error["reason"]["message"]
        assert "SPY" in message and TICKER in message, (name, message)
    visible = [SYSTEM_PROMPT]
    for name in HISTORY_TOOLS:
        visible.append(schema_properties(name)["ticker"]["description"])
    for text in visible:
        if "SPY" in text:
            assert "仅" in text or "only" in text, text


def test_catalog_names_the_fields_each_tool_accepts():
    _, _, catalog = prepare_evidence(TICKER, AS_OF)
    assert history_field_help() in catalog
    for name in HISTORY_TOOLS:
        assert name in catalog


def test_descriptions_and_prompt_do_not_name_parameters_that_do_not_exist():
    banned = {"full_history", "limit", "end_date"}
    texts = [SYSTEM_PROMPT] + [function_schema(name)["description"] for name in HISTORY_TOOLS]
    texts += [schema_properties(name)["resolution"]["description"] for name in HISTORY_TOOLS]
    texts += [schema_properties(name)["fields"]["description"] for name in HISTORY_TOOLS]
    texts += [schema_properties(name)["ticker"]["description"] for name in HISTORY_TOOLS]
    for text in texts:
        for word in banned:
            assert word not in text, (word, text)


# --------------------------------------------------------------------------
# 2. Recovery: every tool error is structured, actionable, and non-fatal.
# --------------------------------------------------------------------------

ERROR_CASES = [
    ("unknown_tool", "get_options", json.dumps({"ticker": TICKER}),
     ["get_price_history", "get_indicators", "get_fundamental_metrics"]),
    ("unknown_field", "get_price_history", json.dumps({"ticker": TICKER, "bogus": 1}),
     ["bogus", "ticker", "resolution", "fields"]),
    ("spy_history", "get_price_history",
     json.dumps({"ticker": "SPY", "resolution": "daily_10", "fields": [PRICE_FIELD]}),
     ["SPY", TICKER]),
    ("resolution_without_fields", "get_price_history",
     json.dumps({"ticker": TICKER, "resolution": "daily_10"}), ["resolution", "fields"]),
    ("fields_without_resolution", "get_price_history",
     json.dumps({"ticker": TICKER, "fields": [PRICE_FIELD]}), ["resolution"]),
    ("bad_resolution", "get_price_history",
     json.dumps({"ticker": TICKER, "resolution": "hourly", "fields": [PRICE_FIELD]}),
     ["resolution", "daily_10"]),
    ("field_not_in_tool", "get_price_history",
     json.dumps({"ticker": TICKER, "resolution": "daily_10", "fields": ["rsi_14"]}),
     ["rsi_14", "close"]),
    ("fundamental_history", "get_fundamental_metrics",
     json.dumps({"ticker": TICKER, "resolution": "daily_10", "fields": [PRICE_FIELD]}),
     ["resolution", "fields"]),
    ("as_of_injected", "get_price_history",
     json.dumps({"ticker": TICKER, "as_of": "2020-01-01"}), ["as_of"]),
    ("bad_limit", "get_price_history", json.dumps({"ticker": TICKER, "limit": 101}),
     ["limit"]),
    ("legacy_full_history", "get_price_history",
     json.dumps({"ticker": TICKER, "full_history": True}), ["resolution", "fields"]),
    ("future_end_date", "get_price_history",
     json.dumps({"ticker": TICKER, "end_date": "2030-01-01"}), ["end_date", "as_of"]),
    ("spy_only_other", "get_price_history", json.dumps({"ticker": "MSFT"}),
     [TICKER, "SPY"]),
    ("malformed_json", "get_price_history", "{not json", ["JSON"]),
    ("non_object_json", "get_price_history", "[1, 2]", ["JSON"]),
]


@pytest.mark.parametrize(("label", "name", "raw", "fragments"),
                         ERROR_CASES, ids=[case[0] for case in ERROR_CASES])
def test_every_tool_error_is_structured_and_actionable(label, name, raw, fragments, snapshot):
    record, error = dispatch(name, raw, snapshot)
    assert record is None, label
    assert error["status"] == "error", label
    assert error["reason"]["code"], label
    message = error["reason"]["message"]
    for fragment in fragments:
        assert fragment in message, (label, fragment, message)


def _legal_draft() -> dict:
    snap = capture_snapshot(TICKER, AS_OF)
    price = snap["calls"][0]["envelope"]["fact_id"]
    return {"ticker": TICKER, "as_of": AS_OF, "horizon": "中期", "bias": "看多",
            "action": "买入", "confidence": "中", "entry_range": [324, 334],
            "stop_loss": 290, "target_price": 400, "fact_ids": [price],
            "reasons": [{"text": "收盘价 {fact:" + price + "}", "fact_ids": [price]}],
            "invalidations": [{"kind": "close_below", "price": 290, "text": "跌破止损位"}],
            "stop_rationale": "跌破 {fact:" + price + "} 离场。",
            "target_rationale": "上看 {fact:" + price + "} 上方。"}


class ScriptedClient:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    def complete(self, *, messages, tools, max_tokens):
        self.requests.append(json.loads(json.dumps(messages)))
        reply = next(self.replies)
        return {"model": "deepseek-flash", "system_fingerprint": "fp_test",
                "usage": {"prompt_tokens": 100, "completion_tokens": 50,
                          "prompt_cache_hit_tokens": 0},
                "message": {"role": "assistant", "content": None,
                            "reasoning_content": "checked", "tool_calls": None},
                "finish_reason": "stop", **reply}


def _tool_reply(calls):
    return {"message": {"role": "assistant", "content": None, "reasoning_content": "probe",
                        "tool_calls": calls}, "finish_reason": "tool_calls"}


def _call(name, args_json, index):
    return {"id": f"call_{index}", "type": "function",
            "function": {"name": name, "arguments": args_json}}


def test_bad_tool_round_returns_every_error_to_the_model_and_continues(tmp_path):
    """A round containing only invalid calls must not end the analysis."""
    cases = ERROR_CASES[:11]
    calls = [_call(name, raw, index) for index, (_, name, raw, _) in enumerate(cases)]
    legal = json.dumps(_legal_draft(), ensure_ascii=False)
    client = ScriptedClient([
        _tool_reply(calls),
        {"message": {"role": "assistant", "content": legal,
                     "reasoning_content": "ok", "tool_calls": None}},
    ])
    result = run_analysis(TICKER, AS_OF, client=client, archive_path=tmp_path / "errors.db")
    assert result["status"] == "passed", result["reason"]
    assert result["stats"]["tool_calls"] == len(cases)
    tool_messages = [item for item in client.requests[1] if item["role"] == "tool"]
    assert len(tool_messages) == len(cases)
    for message, (label, _, _, _) in zip(tool_messages, cases):
        payload = json.loads(message["content"])
        assert payload["status"] == "error", label
        assert payload["reason"]["message"], label
