"""Durable background job store for the local web app."""

from __future__ import annotations

import pytest
from webapp_fixtures import build_fixture


@pytest.fixture
def fixture(tmp_path):
    return build_fixture(tmp_path / "fixture")


@pytest.fixture
def store(tmp_path):
    from thesis_tracker.webapp import jobs

    return jobs.JobStore(tmp_path / "webapp" / "jobs.db")


def test_a_new_job_is_queued_with_its_parameters(store):
    job = store.create("analyze", {"ticker": "AAPL", "horizon": "mid",
                                   "as_of": "2026-09-07"})
    assert job["kind"] == "analyze"
    assert job["status"] == "queued"
    assert job["parameters"]["ticker"] == "AAPL"
    assert job["created_at"] and job["started_at"] is None and job["finished_at"] is None
    assert job["progress"] == []
    assert job["result"] is None and job["error"] is None
    again = store.get(job["job_id"])
    assert again == job


def test_queue_order_is_oldest_first_and_listing_is_newest_first(store):
    first = store.create("analyze", {"ticker": "AAPL"})
    second = store.create("analyze", {"ticker": "NVDA"})
    assert [item["job_id"] for item in store.queued()] == [first["job_id"],
                                                           second["job_id"]]
    assert [item["job_id"] for item in store.list()] == [second["job_id"],
                                                         first["job_id"]]
    assert store.next_queued()["job_id"] == first["job_id"]


def test_the_kind_field_is_extensible_for_later_job_types(store):
    store.create("analyze", {"ticker": "AAPL"})
    store.create("ingest_filings", {"ticker": "NVDA"})
    assert {item["kind"] for item in store.list()} == {"analyze", "ingest_filings"}
    assert [item["kind"] for item in store.list(kind="ingest_filings")] == ["ingest_filings"]


def test_status_transitions_are_recorded(store):
    job = store.create("analyze", {"ticker": "AAPL"})
    store.mark_running(job["job_id"])
    running = store.get(job["job_id"])
    assert running["status"] == "running"
    assert running["started_at"] is not None
    store.append_progress(job["job_id"], {"event": "round", "round": 1})
    store.append_progress(job["job_id"], {"event": "tool", "tool": "get_price_history"})
    progress = store.get(job["job_id"])["progress"]
    assert [item["event"] for item in progress] == ["round", "tool"]
    assert all(item["at"] for item in progress)
    store.finish(job["job_id"], status="succeeded", result={"card_id": "c1"})
    done = store.get(job["job_id"])
    assert done["status"] == "succeeded"
    assert done["finished_at"] is not None
    assert done["result"] == {"card_id": "c1"}
    assert done["error"] is None


def test_a_failure_keeps_an_error_summary_without_secrets(store):
    job = store.create("analyze", {"ticker": "AAPL"})
    store.mark_running(job["job_id"])
    store.finish(job["job_id"], status="failed", error="模型调用失败（ValueError）。")
    failed = store.get(job["job_id"])
    assert failed["status"] == "failed"
    assert failed["error"] == "模型调用失败（ValueError）。"
    assert failed["result"] is None


def test_finish_rejects_an_unknown_status(store):
    job = store.create("analyze", {"ticker": "AAPL"})
    with pytest.raises(ValueError):
        store.finish(job["job_id"], status="almost", error=None)


def test_a_restart_marks_running_jobs_as_interrupted(store):
    running = store.create("analyze", {"ticker": "AAPL"})
    store.mark_running(running["job_id"])
    queued = store.create("analyze", {"ticker": "NVDA"})
    done = store.create("analyze", {"ticker": "MSFT"})
    store.mark_running(done["job_id"])
    store.finish(done["job_id"], status="succeeded", result={})
    # A new store over the same file is what a restart looks like.
    from thesis_tracker.webapp import jobs

    reopened = jobs.JobStore(store.path)
    marked = reopened.mark_interrupted()
    assert marked == [running["job_id"]]
    interrupted = reopened.get(running["job_id"])
    assert interrupted["status"] == "interrupted"
    assert interrupted["finished_at"] is not None
    assert "中断" in interrupted["error"]
    assert reopened.get(queued["job_id"])["status"] == "queued"
    assert reopened.get(done["job_id"])["status"] == "succeeded"


def test_only_one_job_can_hold_the_running_slot(store):
    first = store.create("analyze", {"ticker": "AAPL"})
    queued = store.create("analyze", {"ticker": "NVDA"})
    assert store.claim_next()["job_id"] == first["job_id"]
    assert store.running_job()["job_id"] == first["job_id"]
    assert store.claim_next() is None
    assert [item["job_id"] for item in store.queued()] == [queued["job_id"]]


def test_claim_next_returns_none_when_the_queue_is_empty(store):
    assert store.claim_next() is None
    job = store.create("analyze", {"ticker": "AAPL"})
    assert store.claim_next()["job_id"] == job["job_id"]
    store.finish(job["job_id"], status="failed", error="boom")
    assert store.claim_next() is None


def test_parameters_never_contain_a_secret(store):
    job = store.create("analyze", {"ticker": "AAPL", "as_of": "2026-09-07",
                                   "horizon": "mid"})
    assert set(job["parameters"]) == {"ticker", "as_of", "horizon"}
    assert "key" not in repr(job).lower() or "deepseek" not in repr(job).lower()


def test_the_store_lives_outside_any_cache_directory(store):
    assert "cache" not in store.path.parts
    assert store.path.name == "jobs.db"


def test_the_task_page_gets_a_short_identifier_for_every_job(tmp_path):
    """One identifier form everywhere: eight characters, plus a full copy."""
    from thesis_tracker.webapp.jobs import JobStore
    from thesis_tracker.webapp.service import job_view

    store = JobStore(tmp_path / "jobs.db")
    job = store.create("analyze", {"ticker": "NVDA", "horizon": "mid",
                                   "as_of": "2026-10-04"})
    view = job_view(store.get(job["job_id"]))
    assert view["job_id_short"] == job["job_id"][:8]
    assert len(view["job_id_short"]) == 8
    assert view["job_id"] == job["job_id"]
