"""Chat rounds run in their own slots, two at a time, one per conversation."""

from __future__ import annotations

import threading

import pytest


@pytest.fixture
def store(tmp_path):
    from thesis_tracker.webapp.jobs import JobStore

    return JobStore(tmp_path / "jobs.db")


def chat_job(store, conversation_id, index):
    return store.create("chat_turn", {"conversation_id": conversation_id,
                                      "message_id": f"m{index}", "ticker": "NVDA"})


def test_a_chat_turn_is_a_job_kind_with_its_own_label(store):
    from thesis_tracker.webapp.jobs import KIND_CHAT_TURN, KIND_LABELS

    assert KIND_CHAT_TURN == "chat_turn"
    assert KIND_LABELS[KIND_CHAT_TURN] == "回答提问"
    job = chat_job(store, "c1", 1)
    assert job["kind"] == "chat_turn"
    assert job["kind_label"] == "回答提问"
    assert job["parameters"]["conversation_id"] == "c1"


def test_two_chat_turns_run_at_once_and_the_third_waits(store):
    from thesis_tracker.webapp.jobs import MAX_CHAT_SLOTS

    assert MAX_CHAT_SLOTS == 2
    first = chat_job(store, "c1", 1)
    second = chat_job(store, "c2", 2)
    third = chat_job(store, "c3", 3)
    assert store.claim_chat_turn()["job_id"] == first["job_id"]
    assert store.claim_chat_turn()["job_id"] == second["job_id"]
    assert store.claim_chat_turn() is None
    assert store.get(third["job_id"])["status"] == "queued"


def test_a_conversation_never_runs_two_turns_at_once(store):
    first = chat_job(store, "c1", 1)
    second = chat_job(store, "c1", 2)
    assert store.claim_chat_turn()["job_id"] == first["job_id"]
    # The same conversation's second turn must wait even though a slot is free.
    assert store.claim_chat_turn() is None
    assert store.get(second["job_id"])["status"] == "queued"
    store.finish(first["job_id"], status="succeeded", result={})
    assert store.claim_chat_turn()["job_id"] == second["job_id"]


def test_chat_slots_do_not_consume_the_analysis_slot(store):
    analysis = store.create("analyze", {"ticker": "AAPL"})
    assert store.claim_next()["job_id"] == analysis["job_id"]
    # The chat slot is free even while an analysis runs ...
    first = chat_job(store, "c1", 1)
    assert store.claim_chat_turn()["job_id"] == first["job_id"]
    # ... and an analysis can still start while a chat turn runs.
    second = store.create("analyze", {"ticker": "NVDA"})
    assert store.claim_next() is None  # the single analysis slot is taken
    store.finish(analysis["job_id"], status="succeeded", result={})
    assert store.claim_next()["job_id"] == second["job_id"]


def test_claiming_a_chat_turn_never_takes_an_analysis_job(store):
    store.create("analyze", {"ticker": "AAPL"})
    assert store.claim_chat_turn() is None


def test_claiming_the_analysis_slot_never_takes_a_chat_job(store):
    chat_job(store, "c1", 1)
    assert store.claim_next() is None


def test_concurrent_claims_cannot_exceed_the_slot_limit(store):
    """Two threads racing for one free chat slot: exactly one wins."""
    from thesis_tracker.webapp.jobs import MAX_CHAT_SLOTS

    for index in range(MAX_CHAT_SLOTS + 3):
        chat_job(store, f"c{index}", index)
    claimed: list[str] = []
    lock = threading.Lock()

    def worker():
        job = store.claim_chat_turn()
        if job is not None:
            with lock:
                claimed.append(job["job_id"])

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(claimed) == MAX_CHAT_SLOTS, claimed


def test_a_restart_still_marks_running_chat_turns_interrupted(store):
    from thesis_tracker.webapp.jobs import JobStore

    job = chat_job(store, "c1", 1)
    store.claim_chat_turn()
    reopened = JobStore(store.path)
    assert reopened.mark_interrupted() == [job["job_id"]]
    assert reopened.get(job["job_id"])["status"] == "interrupted"


def test_an_unknown_kind_is_still_accepted_for_later_rounds(store):
    job = store.create("ingest_filings", {"ticker": "AAPL"})
    assert job["kind_label"] == "ingest_filings"
