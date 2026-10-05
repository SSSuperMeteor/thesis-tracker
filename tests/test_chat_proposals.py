"""Confirming a chat proposal runs the identical analysis path."""

from __future__ import annotations

import json

import pytest
from webapp_fixtures import build_fixture


@pytest.fixture
def fixture(tmp_path):
    return build_fixture(tmp_path / "fixture")


@pytest.fixture
def parts(fixture, tmp_path):
    from thesis_tracker.webapp.chat.store import ChatStore
    from thesis_tracker.webapp.jobs import JobStore

    store = ChatStore(tmp_path / "chat.db")
    jobs = JobStore(tmp_path / "jobs.db")
    conversation = store.create_conversation("AAPL")
    message = store.append_message(conversation["conversation_id"], role="assistant",
                                   text="要不要我生成一张新的中期卡？")
    return store, jobs, conversation, message


def make_proposal(store, conversation, message, *, horizon="mid"):
    return store.create_proposal(conversation["conversation_id"], message["message_id"],
                                 horizon=horizon, reason="现有卡是短期的，需要中期判断")


def test_confirming_creates_an_analysis_job_on_the_same_parameters(parts, fixture):
    from thesis_tracker.webapp.chat.proposals import confirm_proposal

    store, jobs, conversation, message = parts
    proposal = make_proposal(store, conversation, message)
    result = confirm_proposal(store, jobs, proposal["proposal_id"], ticker="AAPL",
                              known_tickers={"AAPL"}, price_db=fixture.price_db)
    assert result["status"] == "confirmed"
    job = jobs.get(result["job"]["job_id"])
    assert job["kind"] == "analyze"
    assert job["parameters"]["ticker"] == "AAPL"
    assert job["parameters"]["horizon"] == "mid"
    assert job["parameters"]["as_of"]
    assert store.get_proposal(proposal["proposal_id"])["job_id"] == job["job_id"]


def test_confirming_twice_creates_only_one_job(parts, fixture):
    from thesis_tracker.webapp.chat.proposals import confirm_proposal

    store, jobs, conversation, message = parts
    proposal = make_proposal(store, conversation, message)
    first = confirm_proposal(store, jobs, proposal["proposal_id"], ticker="AAPL",
                             known_tickers={"AAPL"}, price_db=fixture.price_db)
    second = confirm_proposal(store, jobs, proposal["proposal_id"], ticker="AAPL",
                              known_tickers={"AAPL"}, price_db=fixture.price_db)
    assert first["status"] == "confirmed"
    assert second["status"] == "conflict"
    assert len(jobs.list(kind="analyze")) == 1


def test_a_dismissed_proposal_cannot_be_confirmed(parts, fixture):
    from thesis_tracker.webapp.chat.proposals import confirm_proposal

    store, jobs, conversation, message = parts
    proposal = make_proposal(store, conversation, message)
    store.dismiss_proposal(proposal["proposal_id"])
    result = confirm_proposal(store, jobs, proposal["proposal_id"], ticker="AAPL",
                              known_tickers={"AAPL"}, price_db=fixture.price_db)
    assert result["status"] == "dismissed"
    assert jobs.list() == []


def test_an_unknown_proposal_is_reported_not_run(parts, fixture):
    from thesis_tracker.webapp.chat.proposals import confirm_proposal

    store, jobs, conversation, message = parts
    result = confirm_proposal(store, jobs, "nope", ticker="AAPL",
                              known_tickers={"AAPL"}, price_db=fixture.price_db)
    assert result["status"] == "missing"
    assert jobs.list() == []


def test_an_unknown_ticker_is_refused_before_a_job_exists(parts, fixture):
    from thesis_tracker.webapp.chat.proposals import confirm_proposal

    store, jobs, conversation, message = parts
    proposal = make_proposal(store, conversation, message)
    result = confirm_proposal(store, jobs, proposal["proposal_id"], ticker="AAPL",
                              known_tickers={"NVDA"}, price_db=fixture.price_db)
    assert result["status"] == "unknown_ticker"
    assert jobs.list() == []
    # The proposal is untouched, so the user can retry after fixing the data.
    assert store.get_proposal(proposal["proposal_id"])["status"] == "pending"


def test_the_latest_local_price_day_is_used_not_today(parts, fixture):
    """An analysis dated after the stored prices would fail D03; use the data's day."""
    from thesis_tracker.webapp.chat.proposals import confirm_proposal

    store, jobs, conversation, message = parts
    proposal = make_proposal(store, conversation, message)
    result = confirm_proposal(store, jobs, proposal["proposal_id"], ticker="AAPL",
                              known_tickers={"AAPL"}, price_db=fixture.price_db)
    job = jobs.get(result["job"]["job_id"])
    assert job["parameters"]["as_of"] == "2026-09-07"


def test_the_confirmation_result_explains_the_cost(parts, fixture):
    from thesis_tracker.webapp.chat.proposals import confirm_proposal

    store, jobs, conversation, message = parts
    proposal = make_proposal(store, conversation, message)
    result = confirm_proposal(store, jobs, proposal["proposal_id"], ticker="AAPL",
                              known_tickers={"AAPL"}, price_db=fixture.price_db)
    assert result["estimate"]["analyses"] == 0
    assert result["message"]


def test_a_finished_job_appends_a_system_message_with_the_card_link(parts, fixture):
    from thesis_tracker.decision.core import read_card
    from thesis_tracker.webapp.chat.proposals import (
        confirm_proposal,
        note_finished_analysis,
    )

    store, jobs, conversation, message = parts
    proposal = make_proposal(store, conversation, message)
    confirmed = confirm_proposal(store, jobs, proposal["proposal_id"], ticker="AAPL",
                                known_tickers={"AAPL"}, price_db=fixture.price_db)
    job = jobs.get(confirmed["job"]["job_id"])
    jobs.finish(job["job_id"], status="succeeded",
                result={"card_id": fixture.card_id, "ticker": "AAPL", "as_of": "2026-09-07",
                        "horizon": "中期"})
    noted = note_finished_analysis(store, jobs, conversation["conversation_id"],
                                   card_db=fixture.card_db)
    assert noted is not None
    assert noted["role"] == "system"
    assert noted["proposal_id"] == proposal["proposal_id"]
    link = next(item for item in noted["segments"] if item["type"] == "card_link")
    assert link["card_id"] == fixture.card_id
    assert link["card_id_short"] == fixture.card_id[:8]
    assert link["action"]
    assert link["horizon"] == "中期"
    # The summary is produced by the existing renderer from the archive, with no
    # model call anywhere in this path.
    archived = read_card(fixture.card_db, fixture.card_id)
    assert link["action"] == archived["card"]["action"]
    assert store.get_proposal(proposal["proposal_id"])["card_id"] == fixture.card_id


def test_a_failed_job_appends_a_system_message_without_a_card_link(parts, fixture):
    from thesis_tracker.webapp.chat.proposals import (
        confirm_proposal,
        note_finished_analysis,
    )

    store, jobs, conversation, message = parts
    proposal = make_proposal(store, conversation, message)
    confirmed = confirm_proposal(store, jobs, proposal["proposal_id"], ticker="AAPL",
                                known_tickers={"AAPL"}, price_db=fixture.price_db)
    jobs.finish(confirmed["job"]["job_id"], status="failed",
                error="未通过校验：correction_limit")
    noted = note_finished_analysis(store, jobs, conversation["conversation_id"],
                                   card_db=fixture.card_db)
    assert noted is not None
    assert "没有生成" in noted["text"] or "失败" in noted["text"]
    assert not any(item["type"] == "card_link" for item in noted["segments"])


def test_a_running_job_appends_nothing_yet(parts, fixture):
    from thesis_tracker.webapp.chat.proposals import (
        confirm_proposal,
        note_finished_analysis,
    )

    store, jobs, conversation, message = parts
    proposal = make_proposal(store, conversation, message)
    confirm_proposal(store, jobs, proposal["proposal_id"], ticker="AAPL",
                    known_tickers={"AAPL"}, price_db=fixture.price_db)
    before = len(store.messages(conversation["conversation_id"]))
    assert note_finished_analysis(store, jobs, conversation["conversation_id"],
                                 card_db=fixture.card_db) is None
    assert len(store.messages(conversation["conversation_id"])) == before


def test_a_finished_job_is_announced_only_once(parts, fixture):
    from thesis_tracker.webapp.chat.proposals import (
        confirm_proposal,
        note_finished_analysis,
    )

    store, jobs, conversation, message = parts
    proposal = make_proposal(store, conversation, message)
    confirmed = confirm_proposal(store, jobs, proposal["proposal_id"], ticker="AAPL",
                                known_tickers={"AAPL"}, price_db=fixture.price_db)
    jobs.finish(confirmed["job"]["job_id"], status="succeeded",
                result={"card_id": fixture.card_id, "ticker": "AAPL",
                        "as_of": "2026-09-07", "horizon": "中期"})
    assert note_finished_analysis(store, jobs, conversation["conversation_id"],
                                 card_db=fixture.card_db) is not None
    assert note_finished_analysis(store, jobs, conversation["conversation_id"],
                                 card_db=fixture.card_db) is None


def test_the_card_summary_is_read_from_the_archive(tmp_path, fixture):
    from thesis_tracker.webapp.chat.proposals import card_summary

    summary = card_summary(fixture.card_db, fixture.card_id)
    assert summary["card_id"] == fixture.card_id
    assert summary["action"]
    assert summary["tendency"]
    assert summary["horizon"]
    assert summary["created_at"]
    assert summary["entry_text"]
    assert summary["stop_text"]
    assert summary["target_text"]
    assert json.dumps(summary, ensure_ascii=False)


def test_reading_a_card_for_a_summary_never_calls_a_model(fixture, monkeypatch):
    import thesis_tracker.decision.agent as agent
    from thesis_tracker.webapp.chat.proposals import card_summary

    def forbidden(*args, **kwargs):
        raise AssertionError("an archived card is displayed without a model")

    monkeypatch.setattr(agent.DeepSeekClient, "__init__", forbidden)
    assert card_summary(fixture.card_db, fixture.card_id)["action"]


def test_two_conversations_proposals_stay_separate(parts, fixture):
    """Confirming one conversation's proposal must not announce it in another."""
    from thesis_tracker.webapp.chat.proposals import (
        confirm_proposal,
        note_finished_analysis,
    )

    store, jobs, conversation, message = parts
    other = store.create_conversation("AAPL")
    other_message = store.append_message(other["conversation_id"], role="assistant",
                                         text="另一个对话")
    mine = make_proposal(store, conversation, message)
    theirs = make_proposal(store, other, other_message, horizon="short")
    confirmed = confirm_proposal(store, jobs, mine["proposal_id"], ticker="AAPL",
                                known_tickers={"AAPL"}, price_db=fixture.price_db)
    jobs.finish(confirmed["job"]["job_id"], status="succeeded",
                result={"card_id": fixture.card_id, "ticker": "AAPL",
                        "as_of": "2026-09-07", "horizon": "中期"})
    noted = note_finished_analysis(store, jobs, conversation["conversation_id"],
                                  card_db=fixture.card_db)
    assert noted is not None
    assert store.get_proposal(theirs["proposal_id"])["status"] == "pending"
    assert store.pending_proposals(other["conversation_id"]) == [
        store.get_proposal(theirs["proposal_id"])]
    # The other conversation has no system message at all.
    assert [item["role"] for item in store.messages(other["conversation_id"])] == [
        "assistant"]
