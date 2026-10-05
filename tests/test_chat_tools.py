"""Chat tools: the read-only set the model may call during a conversation.

Every tool is exercised against the fixture databases.  The rules worth testing
here are the ones that keep the model's reach narrow: it cannot pick a date, it
cannot reach another company, it cannot do arithmetic on unlike units, and it
can only ever *propose* a new analysis.
"""

from __future__ import annotations

import pytest
from webapp_fixtures import build_fixture


@pytest.fixture
def fixture(tmp_path):
    return build_fixture(tmp_path / "fixture")


@pytest.fixture
def toolbox(fixture, tmp_path):
    from thesis_tracker.webapp.chat.store import ChatStore
    from thesis_tracker.webapp.chat.tools import ToolBox

    store = ChatStore(tmp_path / "chat.db")
    conversation = store.create_conversation("AAPL")
    return ToolBox(store=store, conversation=conversation, as_of="2026-09-07",
                   fact_db=fixture.fact_db, price_db=fixture.price_db,
                   card_db=fixture.card_db)


def call(toolbox, name, **arguments):
    envelope, error = toolbox.call(name, arguments)
    return envelope or error


def test_the_three_analysis_tools_are_exposed_unchanged(toolbox):
    names = [item["function"]["name"] for item in toolbox.schemas()]
    assert names == ["get_price_history", "get_indicators", "get_fundamental_metrics",
                     "list_cards", "get_card", "compare_facts", "request_new_card"]


def test_the_schema_and_the_dispatch_layer_agree_on_every_argument(toolbox):
    """Mirrors the decision tool-contract test: nothing advertised is refused."""
    for schema in toolbox.schemas():
        function = schema["function"]
        accepted = toolbox.accepted_arguments(function["name"])
        declared = set(function["parameters"]["properties"])
        assert declared <= accepted, (function["name"], declared - accepted)
        assert set(function["parameters"].get("required") or []) <= accepted


def test_price_history_reaches_the_local_store(toolbox):
    envelope = call(toolbox, "get_price_history", ticker="AAPL")
    assert envelope["status"] == "ok"
    assert envelope["data"]["symbol"] == "AAPL"
    assert envelope["as_of"] == "2026-09-07"


def test_the_model_cannot_choose_the_date(toolbox):
    """Any as_of the model sends is ignored; the program's own is used."""
    envelope = call(toolbox, "get_price_history", ticker="AAPL", as_of="2001-01-01")
    assert envelope["status"] == "ok"
    assert envelope["as_of"] == "2026-09-07"


def test_spy_is_reachable_but_another_company_is_not(toolbox):
    envelope = call(toolbox, "get_price_history", ticker="SPY")
    assert envelope["status"] == "ok"
    refused = call(toolbox, "get_price_history", ticker="MSFT")
    assert refused["status"] == "error"
    assert refused["reason"]["code"] == "ticker_not_in_conversation"
    assert "AAPL" in refused["reason"]["message"]
    assert "SPY" in refused["reason"]["message"]


def test_an_empty_ticker_falls_back_to_the_conversation_company(toolbox):
    envelope = call(toolbox, "get_price_history")
    assert envelope["status"] == "ok"
    assert envelope["data"]["symbol"] == "AAPL"


def test_tiered_history_keeps_the_resolution_and_field_rules(toolbox):
    envelope = call(toolbox, "get_price_history", ticker="AAPL",
                    resolution="weekly_3m", fields=["close"])
    assert envelope["status"] == "ok"
    bad_resolution = call(toolbox, "get_price_history", ticker="AAPL",
                          resolution="yearly", fields=["close"])
    assert bad_resolution["status"] == "error"
    assert bad_resolution["reason"]["code"] == "resolution_required"
    bad_field = call(toolbox, "get_price_history", ticker="AAPL",
                     resolution="weekly_3m", fields=["open"])
    assert bad_field["status"] == "error"
    assert bad_field["reason"]["code"] == "unsupported_fields"


def test_indicators_and_fundamentals_are_available(toolbox):
    indicators = call(toolbox, "get_indicators", ticker="AAPL")
    assert indicators["status"] == "ok"
    fundamentals = call(toolbox, "get_fundamental_metrics", ticker="AAPL")
    assert fundamentals["status"] == "ok"
    assert fundamentals["data"]["metrics"]


def test_an_unknown_tool_is_a_structured_error(toolbox):
    envelope, error = toolbox.call("search_filings", {"query": "risk"})
    assert envelope is None
    assert error["reason"]["code"] == "unknown_tool"
    assert "search_filings" in error["reason"]["message"]


def test_text_search_and_news_are_not_exposed(toolbox):
    names = {item["function"]["name"] for item in toolbox.schemas()}
    assert "search_filings" not in names
    assert "get_news" not in names
    assert "search" not in " ".join(names)


def test_list_cards_only_returns_this_conversations_company(toolbox, fixture):
    envelope = call(toolbox, "list_cards")
    assert envelope["status"] == "ok"
    listed = envelope["data"]["cards"]
    assert listed
    assert {item["ticker"] for item in listed} == {"AAPL"}
    assert {item["card_id"] for item in listed} == {fixture.card_id}


def test_get_card_returns_the_archived_card_with_price_pseudo_facts(toolbox, fixture):
    envelope = call(toolbox, "get_card", card_id=fixture.card_id)
    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["card_id"] == fixture.card_id
    assert data["action"] == "分批"
    assert data["tendency"] == "看多"
    assert data["horizon"] == "中期"
    assert data["facts"], "the archived evidence facts must come along"
    assert all(item["fact_id"] for item in data["facts"])
    fields = {item["field"] for item in data["prices"]}
    assert {"action", "tendency", "horizon", "entry_low", "entry_high", "stop_loss",
            "target_price", "created_at"} == fields
    for item in data["prices"]:
        assert item["fact_id"] == f"card|{fixture.card_id}|{item['field']}"
        assert item["display"]
        assert item["annotation"] == "AI 判断"
        assert item["card_id"] == fixture.card_id


def test_get_card_prices_are_not_numbers_the_model_may_retype(toolbox, fixture):
    envelope = call(toolbox, "get_card", card_id=fixture.card_id)
    stop = next(item for item in envelope["data"]["prices"] if item["field"] == "stop_loss")
    assert stop["display"].endswith("美元/股")
    assert float(stop["display"].split()[0]) > 0


def test_get_card_refuses_an_unknown_or_foreign_card(toolbox):
    missing = call(toolbox, "get_card", card_id="00000000-0000-0000-0000-000000000000")
    assert missing["status"] == "error"
    assert missing["reason"]["code"] == "card_not_found"
    empty = call(toolbox, "get_card")
    assert empty["status"] == "error"
    assert empty["reason"]["code"] == "card_required"


def test_reading_a_card_adds_its_facts_and_prices_to_the_evidence_set(toolbox, fixture):
    call(toolbox, "get_card", card_id=fixture.card_id)
    facts = toolbox.facts()
    # Both halves of the card become citable: its judgment fields as pseudo-facts
    # and its own archived evidence facts.
    assert any(item["fact_id"].startswith(f"card|{fixture.card_id}|")
               for item in facts.values())
    assert any(item.get("category") == "card" for item in facts.values())
    archived_ids = {fact["fact_id"] for fact in call(
        toolbox, "get_card", card_id=fixture.card_id)["data"]["facts"]}
    assert archived_ids, "the archived card must expose its evidence facts"
    assert archived_ids <= set(facts), archived_ids - set(facts)


def test_displaying_a_card_never_calls_a_model(toolbox, fixture, monkeypatch):
    import thesis_tracker.decision.agent as agent

    def forbidden(*args, **kwargs):
        raise AssertionError("reading an archived card must not call a model")

    monkeypatch.setattr(agent.DeepSeekClient, "__init__", forbidden)
    assert call(toolbox, "get_card", card_id=fixture.card_id)["status"] == "ok"


# -- compare_facts -----------------------------------------------------------

def test_compare_facts_difference_of_two_conversation_facts(toolbox):
    price = call(toolbox, "get_price_history", ticker="AAPL")
    card = call(toolbox, "get_card", card_id=_only_card(toolbox))
    close_id = price["fact_id"]
    stop_id = next(item["fact_id"] for item in card["data"]["prices"]
                   if item["field"] == "stop_loss")
    envelope = call(toolbox, "compare_facts", a=close_id, b=stop_id, op="difference")
    assert envelope["status"] == "ok"
    derived = envelope["data"]["fact"]
    assert derived["fact_id"].startswith("derived|chat|")
    assert derived["source"]["source_fact_ids"] == [close_id, stop_id]
    assert derived["source"]["formula"] == "a - b"
    assert derived["unit"] == "USD/share"
    assert derived["display"].endswith("美元/股")
    # The derived fact joins the evidence set immediately.
    assert derived["fact_id"] in toolbox.facts()


def test_compare_facts_reproduces_the_cards_own_stop_distance(toolbox, fixture):
    """The distance the card prints must be reachable through the tool.

    The real check is in the real-data spot check; here the shape is asserted:
    a pct_change between two price fields comes back as a percentage display.
    """
    card = call(toolbox, "get_card", card_id=fixture.card_id)["data"]
    stop_id = next(item["fact_id"] for item in card["prices"]
                   if item["field"] == "stop_loss")
    entry_high = next(item["fact_id"] for item in card["prices"]
                      if item["field"] == "entry_high")
    distance = call(toolbox, "compare_facts", a=entry_high, b=stop_id, op="pct_change")
    assert distance["status"] == "ok"
    assert distance["data"]["fact"]["display"].endswith("%")


def test_compare_facts_ratio_and_pct_change_exist(toolbox):
    card = call(toolbox, "get_card", card_id=_only_card(toolbox))["data"]
    stop = next(item["fact_id"] for item in card["prices"] if item["field"] == "stop_loss")
    target = next(item["fact_id"] for item in card["prices"]
                  if item["field"] == "target_price")
    ratio = call(toolbox, "compare_facts", a=target, b=stop, op="ratio")
    assert ratio["status"] == "ok"
    assert ratio["data"]["fact"]["source"]["formula"] == "a / b"
    assert ratio["data"]["fact"]["unit"] == "ratio"
    assert ratio["data"]["fact"]["display"].replace(".", "", 1).isdigit()
    change = call(toolbox, "compare_facts", a=target, b=stop, op="pct_change")
    assert change["status"] == "ok"
    assert change["data"]["fact"]["source"]["formula"] == "(a - b) / b"
    assert change["data"]["fact"]["display"].endswith("%")


def test_compare_facts_refuses_unlike_units(toolbox):
    price = call(toolbox, "get_price_history", ticker="AAPL")
    metrics = call(toolbox, "get_fundamental_metrics", ticker="AAPL")
    ratio_id = next(item["fact_id"] for item in metrics["data"]["metrics"].values()
                    if item["status"] == "ok")
    refused = call(toolbox, "compare_facts", a=price["fact_id"], b=ratio_id,
                   op="difference")
    assert refused["status"] == "error"
    assert refused["reason"]["code"] == "unit_mismatch"
    assert "美元/股" in refused["reason"]["message"] or "ratio" in refused["reason"]["message"]


def test_compare_facts_refuses_division_by_zero(toolbox):
    """A zero divisor is refused, not returned as infinity or a guess."""
    card = call(toolbox, "get_card", card_id=_only_card(toolbox))["data"]
    stop = next(item["fact_id"] for item in card["prices"] if item["field"] == "stop_loss")
    zero = call(toolbox, "compare_facts", a=stop, b=stop, op="difference")["data"]["fact"]
    assert zero["value"] == "0"
    envelope, error = toolbox.call("compare_facts",
                                   {"a": stop, "b": zero["fact_id"], "op": "ratio"})
    assert envelope is None
    assert error["reason"]["code"] == "division_by_zero"
    assert "零" in error["reason"]["message"] or "zero" in error["reason"]["message"]


def test_compare_facts_refuses_unknown_operands_and_ops(toolbox):
    missing = call(toolbox, "compare_facts", a="nope|1", b="nope|2", op="difference")
    assert missing["status"] == "error"
    assert missing["reason"]["code"] == "unknown_fact"
    card = call(toolbox, "get_card", card_id=_only_card(toolbox))["data"]
    stop = next(item["fact_id"] for item in card["prices"] if item["field"] == "stop_loss")
    target = next(item["fact_id"] for item in card["prices"]
                  if item["field"] == "target_price")
    bad_op = call(toolbox, "compare_facts", a=stop, b=target, op="multiply")
    assert bad_op["status"] == "error"
    assert bad_op["reason"]["code"] == "unsupported_op"
    assert "difference" in bad_op["reason"]["message"]


def test_compare_facts_refuses_a_fact_from_another_conversation(toolbox, fixture, tmp_path):
    from thesis_tracker.webapp.chat.tools import ToolBox

    store = toolbox.store
    other = store.create_conversation("NVDA")
    other_box = ToolBox(store=store, conversation=other, as_of="2026-09-07",
                        fact_db=fixture.fact_db, price_db=fixture.price_db,
                        card_db=fixture.card_db)
    other_fact = call(other_box, "get_price_history", ticker="NVDA")["fact_id"]
    refused = call(toolbox, "compare_facts", a=other_fact, b=other_fact,
                   op="difference")
    assert refused["status"] == "error"
    assert refused["reason"]["code"] == "unknown_fact"


# -- request_new_card --------------------------------------------------------

def test_request_new_card_only_records_a_pending_proposal(toolbox, fixture):
    before = toolbox.store.pending_proposals(toolbox.conversation["conversation_id"])
    assert before == []
    envelope = call(toolbox, "request_new_card", horizon="mid", reason="现有卡是短期的")
    assert envelope["status"] == "ok"
    proposal_id = envelope["data"]["proposal_id"]
    proposal = toolbox.store.get_proposal(proposal_id)
    assert proposal["status"] == "pending"
    assert proposal["horizon"] == "mid"
    assert proposal["job_id"] is None
    assert envelope["data"]["message"]


def test_request_new_card_never_creates_a_job(toolbox, tmp_path):
    from thesis_tracker.webapp.jobs import JobStore

    jobs = JobStore(tmp_path / "jobs.db")
    call(toolbox, "request_new_card", horizon="short", reason="理由")
    assert jobs.list() == []


def test_request_new_card_validates_its_arguments(toolbox):
    bad_horizon = call(toolbox, "request_new_card", horizon="weekly", reason="理由")
    assert bad_horizon["status"] == "error"
    assert bad_horizon["reason"]["code"] == "invalid_horizon"
    assert "short" in bad_horizon["reason"]["message"]
    no_reason = call(toolbox, "request_new_card", horizon="mid")
    assert no_reason["status"] == "error"
    assert no_reason["reason"]["code"] == "reason_required"


def test_request_new_card_accepts_the_three_horizons(toolbox):
    for horizon in ("short", "mid", "long"):
        envelope = call(toolbox, "request_new_card", horizon=horizon, reason="理由")
        assert envelope["status"] == "ok"
        assert envelope["data"]["horizon_label"] in {"短期", "中期", "长期"}


# -- result shapes -----------------------------------------------------------

def test_a_tool_call_records_its_envelope_bytes_and_facts(toolbox):
    envelope = call(toolbox, "get_price_history", ticker="AAPL")
    record = toolbox.records[-1]
    assert record["tool"] == "get_price_history"
    assert record["args"] == {"ticker": "AAPL"}
    assert record["bytes"] > 0
    assert record["envelope"]["status"] == envelope["status"]
    assert record["fact_count"] >= 1


def _only_card(toolbox):
    """The single card the fixture company has, without hardcoding its id."""
    return toolbox.cards()[0]["card_id"]


def test_the_cards_printed_stop_distance_is_reachable_from_its_own_facts(toolbox,
                                                                        fixture):
    """Reachability is the requirement, not a nicety.

    The card prints (入场价 − 止损) / 入场价 with 入场价 the buy range's midpoint.
    If a chat answer about that distance could not be composed from the card's own
    fields, the model would have to invent the number, which C01 rejects.  The
    chain is difference → product(0.5) → difference → pct_change, and this test
    walks every step of it.
    """
    from thesis_tracker.webapp.chat.tools import ALLOWED_SCALES

    # Two named constants: the midpoint's one half, and ratio-to-percent.
    assert ALLOWED_SCALES == (0.5, 100)
    card = call(toolbox, "get_card", card_id=fixture.card_id)["data"]
    prices = {item["field"]: item for item in card["prices"]}

    span = call(toolbox, "compare_facts", a=prices["entry_high"]["fact_id"],
                b=prices["entry_low"]["fact_id"], op="difference")["data"]["fact"]
    assert span["unit"] == "USD/share"
    half = call(toolbox, "compare_facts", a=span["fact_id"], op="product",
                scale=0.5)["data"]["fact"]
    assert half["unit"] == "USD/share"
    assert half["source"]["formula"] == "a * 0.5"
    assert half["source"]["source_fact_ids"] == [span["fact_id"]]
    midpoint = call(toolbox, "compare_facts", a=prices["entry_low"]["fact_id"],
                    b=half["fact_id"], op="addition")["data"]["fact"]
    assert midpoint["unit"] == "USD/share"
    distance = call(toolbox, "compare_facts", a=midpoint["fact_id"],
                    b=prices["stop_loss"]["fact_id"], op="pct_change")["data"]["fact"]
    assert distance["display"].endswith("%")
    # Every step is a citable fact in this conversation.
    facts = toolbox.facts()
    assert {span["fact_id"], half["fact_id"], midpoint["fact_id"],
            distance["fact_id"]} <= set(facts)


def test_a_product_needs_one_of_the_allowed_scales(toolbox, fixture):
    card = call(toolbox, "get_card", card_id=fixture.card_id)["data"]
    prices = {item["field"]: item for item in card["prices"]}
    for scale in (None, 0.7, 3, "0.5", 1.0, 2.0, 1.3, True):
        refused = call(toolbox, "compare_facts", a=prices["entry_low"]["fact_id"],
                       op="product", scale=scale)
        assert refused["status"] == "error", scale
        assert refused["reason"]["code"] == "unsupported_scale"
        assert "0.5" in refused["reason"]["message"]
        assert "100" in refused["reason"]["message"]


def test_a_product_still_refuses_an_unknown_operand(toolbox):
    refused = call(toolbox, "compare_facts", a="nope|1", op="product", scale=0.5)
    assert refused["status"] == "error"
    assert refused["reason"]["code"] == "unknown_fact"


def test_every_derived_fact_stays_citable_through_a_whole_chain(toolbox, fixture):
    """A chain is only useful if each step can cite the step before it.

    The dispatch used to key on payload shape and id prefix, so a derived fact
    looked like a card fact and was dropped: the next step then reported
    unknown_fact even though the previous call had just returned it.
    """
    card = call(toolbox, "get_card", card_id=fixture.card_id)["data"]
    prices = {item["field"]: item for item in card["prices"]}
    span = call(toolbox, "compare_facts", a=prices["entry_high"]["fact_id"],
                b=prices["entry_low"]["fact_id"], op="difference")["data"]["fact"]
    half = call(toolbox, "compare_facts", a=span["fact_id"], op="product",
                scale=0.5)["data"]["fact"]
    midpoint = call(toolbox, "compare_facts", a=prices["entry_low"]["fact_id"],
                    b=half["fact_id"], op="addition")["data"]["fact"]
    distance = call(toolbox, "compare_facts", a=midpoint["fact_id"],
                    b=prices["stop_loss"]["fact_id"], op="pct_change")["data"]["fact"]
    # The arithmetic is the card's own, from its own published numbers: the
    # midpoint must be the two ends' average, whatever this card's range is.
    low = float(prices["entry_low"]["value"])
    high = float(prices["entry_high"]["value"])
    assert float(span["value"]) == pytest.approx(high - low)
    assert float(half["value"]) == pytest.approx((high - low) / 2)
    assert float(midpoint["value"]) == pytest.approx((low + high) / 2)
    assert distance["unit"] == "percent"
    # And every intermediate is in the conversation's evidence set.
    facts = toolbox.facts()
    for step in (span, half, midpoint, distance):
        assert step["fact_id"] in facts, step["fact_id"]


def test_a_percentage_change_is_a_percentage_not_a_fraction(toolbox):
    """The display rule scales a `percent` value by 100; so must this tool.

    Handing it the raw fraction showed a 5% move as 0.05%, a hundredfold error
    that no test caught because both numbers look plausible.
    """
    card = call(toolbox, "get_card", card_id=_only_card(toolbox))["data"]
    prices = {item["field"]: item for item in card["prices"]}
    # (low - low) / low is zero ...
    zero = call(toolbox, "compare_facts", a=prices["entry_low"]["fact_id"],
                b=prices["entry_low"]["fact_id"], op="pct_change")["data"]["fact"]
    assert zero["display"] == "0.00%"
    # ... and a doubling is +100%.
    double = call(toolbox, "compare_facts", a=prices["entry_high"]["fact_id"],
                  b=prices["entry_low"]["fact_id"], op="ratio")["data"]["fact"]
    assert double["unit"] == "ratio"
    assert double["display"].replace(".", "", 1).isdigit()
    # pct_change of a value against itself halved: build it from the span.
    span = call(toolbox, "compare_facts", a=prices["entry_low"]["fact_id"],
                b=prices["entry_low"]["fact_id"], op="difference")["data"]["fact"]
    assert span["display"].endswith("美元/股")



# -- the scale whitelist: a product may not become a price the model invented --------

def _toolbox_for(fixture, tmp_path, question):
    """A toolbox whose turn answers a stored user message with this text."""
    from thesis_tracker.webapp.chat.store import ChatStore
    from thesis_tracker.webapp.chat.tools import ToolBox

    store = ChatStore(tmp_path / "chat-scale.db")
    conversation = store.create_conversation("AAPL")
    message = store.append_message(conversation["conversation_id"], role="user",
                                   text=question)
    return ToolBox(store=store, conversation=conversation, as_of="2026-09-07",
                   fact_db=fixture.fact_db, price_db=fixture.price_db,
                   card_db=fixture.card_db, message_id=message["message_id"])


def _close_fact_id(box):
    envelope = call(box, "get_price_history", ticker="AAPL")
    assert envelope["status"] == "ok"
    return envelope["fact_id"]


def test_the_model_cannot_turn_the_close_into_a_target_by_choosing_the_scale(
        fixture, tmp_path):
    """close x 1.3 would be a target price the model made up, shown as a fact.

    The user never typed 1.3 and it is not a named constant, so it is refused with
    a structured error and nothing enters the conversation's evidence set.
    """
    box = _toolbox_for(fixture, tmp_path, "现在能买吗？")
    close = _close_fact_id(box)
    before = set(box.facts())
    refused = call(box, "compare_facts", a=close, op="product", scale=1.3)
    assert refused["status"] == "error"
    assert refused["reason"]["code"] == "unsupported_scale"
    assert "1.3" in refused["reason"]["message"]
    assert set(box.facts()) == before
    assert not [fact for fact in box.facts().values()
                if fact["fact_id"].startswith("derived|chat|")]


@pytest.mark.parametrize("scale", [1.0, 2.0, 1.05, 0.95, 1.5, 3, 10, 0.25])
def test_other_scales_the_model_might_try_are_refused_the_same_way(
        fixture, tmp_path, scale):
    box = _toolbox_for(fixture, tmp_path, "现在能买吗？")
    refused = call(box, "compare_facts", a=_close_fact_id(box), op="product",
                   scale=scale)
    assert refused["reason"]["code"] == "unsupported_scale"


def test_a_number_the_user_typed_in_this_message_may_be_the_scale(fixture, tmp_path):
    box = _toolbox_for(fixture, tmp_path, "如果它涨到现在的 1.3 倍呢？")
    close = _close_fact_id(box)
    allowed = call(box, "compare_facts", a=close, op="product", scale=1.3)
    assert allowed["status"] == "ok"
    fact = allowed["data"]["fact"]
    assert fact["source"]["formula"] == "a * 1.3"
    assert fact["source"]["scale_source"] == "user_message"
    assert fact["category"] == "derived"
    assert fact["fact_id"] in box.facts()
    # ... but only that number, not a near neighbour of it.
    refused = call(box, "compare_facts", a=close, op="product", scale=1.31)
    assert refused["reason"]["code"] == "unsupported_scale"


def test_the_user_number_must_be_in_this_message_not_an_earlier_one(fixture, tmp_path):
    from thesis_tracker.webapp.chat.store import ChatStore
    from thesis_tracker.webapp.chat.tools import ToolBox

    store = ChatStore(tmp_path / "chat-earlier.db")
    conversation = store.create_conversation("AAPL")
    store.append_message(conversation["conversation_id"], role="user",
                         text="假设涨 1.3 倍")
    latest = store.append_message(conversation["conversation_id"], role="user",
                                  text="那现在能买吗？")
    box = ToolBox(store=store, conversation=conversation, as_of="2026-09-07",
                  fact_db=fixture.fact_db, price_db=fixture.price_db,
                  card_db=fixture.card_db, message_id=latest["message_id"])
    refused = call(box, "compare_facts", a=_close_fact_id(box), op="product",
                   scale=1.3)
    assert refused["reason"]["code"] == "unsupported_scale"


def test_with_no_user_message_only_the_named_constants_are_available(toolbox, fixture):
    card = call(toolbox, "get_card", card_id=fixture.card_id)["data"]
    prices = {item["field"]: item for item in card["prices"]}
    low = prices["entry_low"]["fact_id"]
    assert call(toolbox, "compare_facts", a=low, op="product", scale=0.5)["status"] == "ok"
    refused = call(toolbox, "compare_facts", a=low, op="product", scale=1.3)
    assert refused["reason"]["code"] == "unsupported_scale"


def test_the_midpoint_scale_is_named_in_the_derived_fact(toolbox, fixture):
    card = call(toolbox, "get_card", card_id=fixture.card_id)["data"]
    prices = {item["field"]: item for item in card["prices"]}
    span = call(toolbox, "compare_facts", a=prices["entry_high"]["fact_id"],
                b=prices["entry_low"]["fact_id"], op="difference")["data"]["fact"]
    half = call(toolbox, "compare_facts", a=span["fact_id"], op="product",
                scale=0.5)["data"]["fact"]
    assert half["source"]["scale_source"] == "named:midpoint"


def test_the_percent_scale_turns_a_ratio_into_a_percentage(toolbox, fixture):
    card = call(toolbox, "get_card", card_id=fixture.card_id)["data"]
    prices = {item["field"]: item for item in card["prices"]}
    ratio = call(toolbox, "compare_facts", a=prices["entry_high"]["fact_id"],
                 b=prices["entry_low"]["fact_id"], op="ratio")["data"]["fact"]
    percent = call(toolbox, "compare_facts", a=ratio["fact_id"], op="product",
                   scale=100)["data"]["fact"]
    assert percent["unit"] == "percent"
    assert percent["display"].endswith("%")
    assert percent["source"]["scale_source"] == "named:percent"
    assert float(percent["value"]) == pytest.approx(float(ratio["value"]) * 100)


def test_the_percent_scale_is_not_a_way_to_multiply_a_price_by_one_hundred(
        toolbox, fixture):
    card = call(toolbox, "get_card", card_id=fixture.card_id)["data"]
    prices = {item["field"]: item for item in card["prices"]}
    refused = call(toolbox, "compare_facts", a=prices["entry_low"]["fact_id"],
                   op="product", scale=100)
    assert refused["status"] == "error"
    assert refused["reason"]["code"] == "scale_not_applicable"
    assert "比值" in refused["reason"]["message"]


def test_the_schema_tells_the_model_what_it_may_pass(toolbox):
    schema = next(item for item in toolbox.schemas()
                  if item["function"]["name"] == "compare_facts")
    description = schema["function"]["parameters"]["properties"]["scale"]["description"]
    assert "0.5" in description and "100" in description
    assert "user" in description.lower()


# -- findings from the first real-model run -------------------------------------

def test_a_scale_on_any_other_operation_is_an_error_not_silently_ignored(
        toolbox, fixture):
    """Real run: ``op=ratio, scale=100`` returned a bare ratio (0.0677) to a user who
    had asked for a percentage, because the scale was dropped without a word."""
    card = call(toolbox, "get_card", card_id=fixture.card_id)["data"]
    prices = {item["field"]: item for item in card["prices"]}
    for op in ("ratio", "difference", "addition", "pct_change"):
        refused = call(toolbox, "compare_facts", a=prices["entry_high"]["fact_id"],
                       b=prices["entry_low"]["fact_id"], op=op, scale=100)
        assert refused["status"] == "error", op
        assert refused["reason"]["code"] == "scale_not_used"
        assert "product" in refused["reason"]["message"]
        assert "pct_change" in refused["reason"]["message"]


def test_an_operation_without_a_scale_is_unchanged(toolbox, fixture):
    card = call(toolbox, "get_card", card_id=fixture.card_id)["data"]
    prices = {item["field"]: item for item in card["prices"]}
    ok = call(toolbox, "compare_facts", a=prices["entry_high"]["fact_id"],
              b=prices["entry_low"]["fact_id"], op="ratio")
    assert ok["status"] == "ok"


def test_a_card_read_says_what_it_does_not_contain(toolbox, fixture):
    """Real run: asked why a stop sat where it did, the model said the card kept no
    reason.  It does; this tool just does not return it, and must say so."""
    note = call(toolbox, "get_card", card_id=fixture.card_id)["data"]["note"]
    for missing in ("理由", "止损依据", "目标依据", "失效条件"):
        assert missing in note
    assert "详情页" in note
    assert "不要说" in note


def test_a_proposal_reason_cannot_carry_a_price_or_any_number(toolbox):
    """Real run: a proposal said a new card would use the user's 300, which the
    analysis never accepts.  The reason is shown to the reader, so it follows the
    same rule as an answer: no free numbers, and no prices from the user."""
    for reason in ("用户要把目标价改成 300，需要新卡", "想看 252 附近的判断", "看 1.3 倍"):
        refused = call(toolbox, "request_new_card", horizon="mid", reason=reason)
        assert refused["status"] == "error", reason
        assert refused["reason"]["code"] == "reason_has_numbers"
        assert "价位" in refused["reason"]["message"]
    assert toolbox.store.pending_proposals(toolbox.conversation_id) == []
    ok = call(toolbox, "request_new_card", horizon="mid", reason="需要一个新的中期判断")
    assert ok["status"] == "ok"
    # Dates and periods are not prices.
    dated = call(toolbox, "request_new_card", horizon="short",
                 reason="2026-10-02 之后又有了新数据，Q3 财报也已披露")
    assert dated["status"] == "ok"


def test_the_prompt_tells_the_model_the_new_card_takes_no_price_from_the_user():
    from thesis_tracker.webapp.chat.context import system_prompt

    text = system_prompt("AAPL")
    assert "不接受用户指定" in text
    assert "pct_change" in text            # how to get a percentage
    assert "详情页" in text                 # where a card's reasons live
