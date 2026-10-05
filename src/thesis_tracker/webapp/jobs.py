"""Durable background job queue for the local web app.

Jobs live in their own SQLite file under ``data/webapp/`` — deliberately not in
a ``cache`` directory, because a queued analysis is work the user asked for and
must survive a restart.  The table is mutable by design (a running job's status
and progress change); unlike the decision archive it does not forbid UPDATE.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_JOB_DB = Path("data/webapp/jobs.db")

# The four terminal-or-not states a job can be in.  ``interrupted`` is what a
# job left ``running`` by a restart becomes: we cannot know whether the model
# call completed, so it is never silently retried or reported as success.
STATUSES = ("queued", "running", "succeeded", "failed", "interrupted")
FINISHED_STATUSES = ("succeeded", "failed", "interrupted")

STATUS_LABELS = {
    "queued": "排队中",
    "running": "运行中",
    "succeeded": "成功",
    "failed": "失败",
    "interrupted": "中断",
}

# Kinds this round implements.  Later rounds add scraping and ingestion kinds;
# the column is free text so adding one needs no migration.
KIND_ANALYZE = "analyze"
KIND_LABELS = {KIND_ANALYZE: "生成建议卡"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    progress_json TEXT NOT NULL DEFAULT '[]',
    result_json TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs (status, created_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    """Append-mostly job records with a single running slot."""

    def __init__(self, path: Path | str = DEFAULT_JOB_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    # -- reads ---------------------------------------------------------------

    def get(self, job_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return None if row is None else _row_to_job(row)

    def list(self, *, kind: str | None = None, limit: int | None = None) -> list[dict]:
        query = "SELECT * FROM jobs"
        parameters: list = []
        if kind is not None:
            query += " WHERE kind=?"
            parameters.append(kind)
        query += " ORDER BY created_at DESC, rowid DESC"
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_row_to_job(row) for row in rows]

    def queued(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE status='queued' "
                "ORDER BY created_at ASC, rowid ASC").fetchall()
        return [_row_to_job(row) for row in rows]

    def next_queued(self) -> dict | None:
        queued = self.queued()
        return queued[0] if queued else None

    def running_job(self) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE status='running' "
                "ORDER BY started_at ASC, rowid ASC LIMIT 1").fetchone()
        return None if row is None else _row_to_job(row)

    # -- writes --------------------------------------------------------------

    def create(self, kind: str, parameters: dict) -> dict:
        if not kind or not isinstance(kind, str):
            raise ValueError("job kind is required")
        job_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO jobs (job_id, kind, status, parameters_json, created_at, "
                "progress_json) VALUES (?,?,?,?,?,'[]')",
                (job_id, kind, "queued",
                 json.dumps(parameters, ensure_ascii=False, sort_keys=True), _now()))
        return self.get(job_id)

    def claim_next(self) -> dict | None:
        """Take the oldest queued job for the single running slot, if it is free."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            busy = connection.execute(
                "SELECT 1 FROM jobs WHERE status='running' LIMIT 1").fetchone()
            if busy is not None:
                connection.rollback()
                return None
            row = connection.execute(
                "SELECT job_id FROM jobs WHERE status='queued' "
                "ORDER BY created_at ASC, rowid ASC LIMIT 1").fetchone()
            if row is None:
                connection.rollback()
                return None
            connection.execute(
                "UPDATE jobs SET status='running', started_at=? WHERE job_id=?",
                (_now(), row["job_id"]))
            connection.commit()
            claimed = connection.execute(
                "SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone()
        return _row_to_job(claimed)

    def mark_running(self, job_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET status='running', started_at=COALESCE(started_at, ?) "
                "WHERE job_id=?", (_now(), job_id))

    def append_progress(self, job_id: str, event: dict) -> None:
        recorded = {**event, "at": _now()}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT progress_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(job_id)
            events = json.loads(row["progress_json"] or "[]")
            events.append(recorded)
            connection.execute("UPDATE jobs SET progress_json=? WHERE job_id=?",
                               (json.dumps(events, ensure_ascii=False), job_id))
            connection.commit()

    def finish(self, job_id: str, *, status: str, result: dict | None = None,
               error: str | None = None) -> None:
        if status not in FINISHED_STATUSES:
            raise ValueError(f"unknown terminal status: {status}")
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET status=?, finished_at=?, result_json=?, error=? "
                "WHERE job_id=?", (status, _now(),
                                   None if result is None else json.dumps(
                                       result, ensure_ascii=False, sort_keys=True),
                                   error, job_id))

    def mark_interrupted(self) -> list[str]:
        """Mark every job still ``running`` as interrupted; called once at startup."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT job_id FROM jobs WHERE status='running'").fetchall()
            job_ids = [row["job_id"] for row in rows]
            if job_ids:
                connection.execute(
                    "UPDATE jobs SET status='interrupted', finished_at=?, error=? "
                    "WHERE status='running'",
                    (_now(), "软件重启时该任务仍在运行，已标记为中断；未确认的模型调用不会被重试。"))
        return job_ids


def _row_to_job(row: sqlite3.Row) -> dict:
    return {
        "job_id": row["job_id"],
        "kind": row["kind"],
        "kind_label": KIND_LABELS.get(row["kind"], row["kind"]),
        "status": row["status"],
        "status_label": STATUS_LABELS.get(row["status"], row["status"]),
        "parameters": json.loads(row["parameters_json"] or "{}"),
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "progress": json.loads(row["progress_json"] or "[]"),
        "result": None if row["result_json"] is None else json.loads(row["result_json"]),
        "error": row["error"],
    }
