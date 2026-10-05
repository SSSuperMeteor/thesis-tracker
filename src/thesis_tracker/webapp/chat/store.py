"""Durable storage for per-company chat conversations.

Design position, in one paragraph: a conversation is a private workspace for one
company.  Messages are append-only and immutable — an answer that fails
validation is archived with its violations instead of being overwritten, so the
audit trail of what the model actually said survives.  Conversations are never
deleted, only archived (hidden).  This is the same stance as the decision
archive in ``data/decisions/cards.db``, and it is enforced with the same SQLite
triggers rather than by convention.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CHAT_DB = Path("data/webapp/chat.db")

# The title is the first user message's leading characters; no model call.
TITLE_LENGTH = 24

ROLES = ("user", "assistant", "system")
PROPOSAL_STATUSES = ("pending", "confirmed", "dismissed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    title TEXT,
    card_id TEXT,
    created_at TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    last_activity_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS conversations_ticker ON conversations (ticker, archived);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT,
    template_text TEXT,
    segments_json TEXT,
    rejected_json TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    prompt_version TEXT,
    proposal_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations (conversation_id)
);
CREATE INDEX IF NOT EXISTS messages_conversation ON messages (conversation_id, created_at);

CREATE TABLE IF NOT EXISTS chat_tool_calls (
    tool_call_id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    call_no INTEGER NOT NULL,
    tool TEXT NOT NULL,
    args_json TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    envelope_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (message_id, call_no)
);

CREATE TABLE IF NOT EXISTS chat_model_calls (
    model_call_id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    round_no INTEGER NOT NULL,
    requested_model TEXT NOT NULL,
    returned_model TEXT,
    fingerprint TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_hit_tokens INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE (message_id, round_no)
);

-- Facts this conversation has already seen: everything a tool returned in any
-- of its turns, plus the price fields of every card it has read.  C03 resolves
-- placeholders against this, so it is per conversation and never global.
CREATE TABLE IF NOT EXISTS conversation_facts (
    conversation_id TEXT NOT NULL,
    fact_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (conversation_id, fact_id)
);

CREATE TABLE IF NOT EXISTS proposals (
    proposal_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    horizon TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    job_id TEXT,
    card_id TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    FOREIGN KEY (conversation_id) REFERENCES conversations (conversation_id)
);
CREATE INDEX IF NOT EXISTS proposals_conversation ON proposals (conversation_id, status);

-- Messages are append-only.  An answer that failed validation is archived with
-- its violations, never rewritten, so these two triggers are the guarantee
-- rather than a rule someone has to remember.
CREATE TRIGGER IF NOT EXISTS messages_no_update
BEFORE UPDATE ON messages BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER IF NOT EXISTS messages_no_delete
BEFORE DELETE ON messages BEGIN SELECT RAISE(ABORT, 'immutable'); END;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatStore:
    """Conversations, their messages, their audit rows and their proposals."""

    def __init__(self, path: Path | str = DEFAULT_CHAT_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    # -- conversations -------------------------------------------------------

    def create_conversation(self, ticker: str, *, title_hint: str | None = None,
                            card_id: str | None = None) -> dict:
        """Open a conversation for one company; never a hardcoded ticker list."""
        symbol = str(ticker).strip().upper()
        if not symbol:
            raise ValueError("ticker is required")
        conversation_id = str(uuid.uuid4())
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO conversations (conversation_id, ticker, title, card_id, "
                "created_at, archived, last_activity_at) VALUES (?,?,?,?,?,0,?)",
                (conversation_id, symbol, title_hint, card_id, now, now))
        return self.get_conversation(conversation_id)

    def get_conversation(self, conversation_id: str) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM conversations WHERE conversation_id=?",
                (conversation_id,)).fetchone()
        if row is None:
            raise KeyError(conversation_id)
        return _conversation(row)

    def conversations_for(self, ticker: str, *, include_archived: bool = False) -> list[dict]:
        query = ("SELECT * FROM conversations WHERE upper(ticker)=upper(?)")
        if not include_archived:
            query += " AND archived=0"
        query += " ORDER BY last_activity_at DESC, rowid DESC"
        with self._connect() as connection:
            rows = connection.execute(query, (ticker,)).fetchall()
        return [_conversation(row) for row in rows]

    def rename_conversation(self, conversation_id: str, title: str) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE conversations SET title=? WHERE conversation_id=?",
                               (title, conversation_id))

    def archive_conversation(self, conversation_id: str) -> None:
        """Hide a conversation.  There is deliberately no delete."""
        with self._connect() as connection:
            connection.execute("UPDATE conversations SET archived=1 WHERE conversation_id=?",
                               (conversation_id,))

    def conversation_title(self, conversation_id: str) -> str | None:
        """Stored title, or the first user message truncated to TITLE_LENGTH."""
        conversation = self.get_conversation(conversation_id)
        if conversation["title"]:
            return conversation["title"]
        with self._connect() as connection:
            row = connection.execute(
                "SELECT text FROM messages WHERE conversation_id=? AND role='user' "
                "ORDER BY created_at, rowid LIMIT 1", (conversation_id,)).fetchone()
        if row is None or not row["text"]:
            return None
        return str(row["text"])[:TITLE_LENGTH]

    # -- messages ------------------------------------------------------------

    def append_message(self, conversation_id: str, *, role: str, text: str | None,
                       template_text: str | None = None, segments: list | None = None,
                       rejected: dict | None = None, attempts: int = 0,
                       prompt_version: str | None = None,
                       proposal_id: str | None = None) -> dict:
        if role not in ROLES:
            raise ValueError(f"unknown role: {role}")
        message_id = str(uuid.uuid4())
        now = _now()
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM conversations WHERE conversation_id=?",
                (conversation_id,)).fetchone()
            if exists is None:
                raise KeyError(conversation_id)
            connection.execute(
                "INSERT INTO messages (message_id, conversation_id, role, text, "
                "template_text, segments_json, rejected_json, attempts, prompt_version, "
                "proposal_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (message_id, conversation_id, role, text, template_text,
                 None if segments is None else json.dumps(segments, ensure_ascii=False),
                 None if rejected is None else json.dumps(rejected, ensure_ascii=False),
                 attempts, prompt_version, proposal_id, now))
            connection.execute(
                "UPDATE conversations SET last_activity_at=? WHERE conversation_id=?",
                (now, conversation_id))
        return self.get_message(message_id)

    def get_message(self, message_id: str) -> dict:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM messages WHERE message_id=?",
                                     (message_id,)).fetchone()
        if row is None:
            raise KeyError(message_id)
        return _message(row)

    def messages(self, conversation_id: str) -> list[dict]:
        self.get_conversation(conversation_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE conversation_id=? "
                "ORDER BY created_at, rowid", (conversation_id,)).fetchall()
        return [_message(row) for row in rows]

    def recent_messages(self, conversation_id: str, limit: int) -> list[dict]:
        """The newest ``limit`` messages, returned oldest first."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE conversation_id=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (conversation_id, limit)).fetchall()
        return [_message(row) for row in reversed(rows)]

    # -- evidence set --------------------------------------------------------

    def add_facts(self, conversation_id: str, facts: list[dict]) -> list[str]:
        """Record facts for this conversation; return any id with a conflict.

        The same fact id must always describe the same value, so a second
        different value for one id is reported rather than silently replacing
        what an earlier answer already relied on.
        """
        conflicts: list[str] = []
        with self._connect() as connection:
            for fact in facts:
                payload = json.dumps(fact, ensure_ascii=False, sort_keys=True)
                existing = connection.execute(
                    "SELECT payload_json FROM conversation_facts WHERE "
                    "conversation_id=? AND fact_id=?",
                    (conversation_id, fact["fact_id"])).fetchone()
                if existing is None:
                    connection.execute(
                        "INSERT INTO conversation_facts (conversation_id, fact_id, "
                        "payload_json, first_seen_at) VALUES (?,?,?,?)",
                        (conversation_id, fact["fact_id"], payload, _now()))
                elif existing["payload_json"] != payload:
                    conflicts.append(fact["fact_id"])
        return conflicts

    def facts(self, conversation_id: str) -> dict[str, dict]:
        """This conversation's evidence set, keyed by fact id."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT fact_id, payload_json FROM conversation_facts "
                "WHERE conversation_id=? ORDER BY first_seen_at, fact_id",
                (conversation_id,)).fetchall()
        return {row["fact_id"]: json.loads(row["payload_json"]) for row in rows}

    # -- audit rows ----------------------------------------------------------

    def append_tool_call(self, message_id: str, *, tool: str, args: dict,
                         envelope: dict) -> dict:
        encoded = json.dumps(envelope, ensure_ascii=False)
        with self._connect() as connection:
            call_no = connection.execute(
                "SELECT COUNT(*) FROM chat_tool_calls WHERE message_id=?",
                (message_id,)).fetchone()[0] + 1
            tool_call_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO chat_tool_calls (tool_call_id, message_id, call_no, tool, "
                "args_json, bytes, envelope_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (tool_call_id, message_id, call_no, tool,
                 json.dumps(args, ensure_ascii=False, sort_keys=True),
                 len(encoded.encode("utf-8")), encoded, _now()))
        return self.tool_calls(message_id)[-1]

    def tool_calls(self, message_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM chat_tool_calls WHERE message_id=? ORDER BY call_no",
                (message_id,)).fetchall()
        return [{"tool_call_id": row["tool_call_id"], "message_id": row["message_id"],
                 "call_no": row["call_no"], "tool": row["tool"],
                 "args": json.loads(row["args_json"]), "bytes": row["bytes"],
                 "envelope": json.loads(row["envelope_json"]),
                 "created_at": row["created_at"]} for row in rows]

    def append_model_call(self, message_id: str, *, round_no: int, requested_model: str,
                          returned_model: str | None, fingerprint: str | None,
                          input_tokens: int | None, output_tokens: int | None,
                          cache_hit_tokens: int | None) -> dict:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO chat_model_calls (model_call_id, message_id, round_no, "
                "requested_model, returned_model, fingerprint, input_tokens, "
                "output_tokens, cache_hit_tokens, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), message_id, round_no, requested_model,
                 returned_model, fingerprint, input_tokens, output_tokens,
                 cache_hit_tokens, _now()))
        return self.model_calls(message_id)[-1]

    def model_calls(self, message_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM chat_model_calls WHERE message_id=? ORDER BY round_no",
                (message_id,)).fetchall()
        return [{"model_call_id": row["model_call_id"], "message_id": row["message_id"],
                 "round_no": row["round_no"], "requested_model": row["requested_model"],
                 "returned_model": row["returned_model"], "fingerprint": row["fingerprint"],
                 "input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"],
                 "cache_hit_tokens": row["cache_hit_tokens"],
                 "created_at": row["created_at"]} for row in rows]

    # -- proposals -----------------------------------------------------------

    def create_proposal(self, conversation_id: str, message_id: str, *, horizon: str,
                        reason: str) -> dict:
        """Record a suggestion to run a full analysis.  It never runs one."""
        self.get_conversation(conversation_id)
        proposal_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO proposals (proposal_id, conversation_id, message_id, horizon, "
                "reason, status, created_at) VALUES (?,?,?,?,?,'pending',?)",
                (proposal_id, conversation_id, message_id, horizon, reason, _now()))
        return self.get_proposal(proposal_id)

    def get_proposal(self, proposal_id: str) -> dict:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM proposals WHERE proposal_id=?",
                                     (proposal_id,)).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        return _proposal(row)

    def proposals_for(self, conversation_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM proposals WHERE conversation_id=? ORDER BY created_at, rowid",
                (conversation_id,)).fetchall()
        return [_proposal(row) for row in rows]

    def pending_proposals(self, conversation_id: str) -> list[dict]:
        return [item for item in self.proposals_for(conversation_id)
                if item["status"] == "pending"]

    def confirm_proposal(self, proposal_id: str, *, job_id: str) -> dict | None:
        """Move a pending proposal to confirmed exactly once.

        Returns None when it was already decided, which is what makes a double
        click unable to queue a second paid analysis.
        """
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE proposals SET status='confirmed', job_id=?, decided_at=? "
                "WHERE proposal_id=? AND status='pending'",
                (job_id, _now(), proposal_id))
            if cursor.rowcount != 1:
                return None
        return self.get_proposal(proposal_id)

    def dismiss_proposal(self, proposal_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE proposals SET status='dismissed', decided_at=? "
                "WHERE proposal_id=? AND status='pending'", (_now(), proposal_id))
        return cursor.rowcount == 1

    def attach_card(self, proposal_id: str, card_id: str) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE proposals SET card_id=? WHERE proposal_id=?",
                               (card_id, proposal_id))

    # -- usage ---------------------------------------------------------------

    def message_usage(self, message_id: str) -> dict:
        calls = self.model_calls(message_id)
        return {"input_tokens": sum(item["input_tokens"] or 0 for item in calls),
                "output_tokens": sum(item["output_tokens"] or 0 for item in calls),
                "cache_hit_tokens": sum(item["cache_hit_tokens"] or 0 for item in calls),
                "rounds": len(calls)}

    def conversation_usage(self, conversation_id: str) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(c.input_tokens),0) AS input_tokens, "
                "COALESCE(SUM(c.output_tokens),0) AS output_tokens, "
                "COALESCE(SUM(c.cache_hit_tokens),0) AS cache_hit_tokens, "
                "COUNT(c.model_call_id) AS rounds "
                "FROM chat_model_calls c JOIN messages m ON m.message_id = c.message_id "
                "WHERE m.conversation_id=?", (conversation_id,)).fetchone()
        return {"input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"],
                "cache_hit_tokens": row["cache_hit_tokens"], "rounds": row["rounds"]}

    def ticker_usage(self, ticker: str) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(c.input_tokens),0) AS input_tokens, "
                "COALESCE(SUM(c.output_tokens),0) AS output_tokens, "
                "COALESCE(SUM(c.cache_hit_tokens),0) AS cache_hit_tokens, "
                "COUNT(c.model_call_id) AS rounds "
                "FROM chat_model_calls c "
                "JOIN messages m ON m.message_id = c.message_id "
                "JOIN conversations v ON v.conversation_id = m.conversation_id "
                "WHERE upper(v.ticker)=upper(?)", (ticker,)).fetchone()
        return {"input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"],
                "cache_hit_tokens": row["cache_hit_tokens"], "rounds": row["rounds"]}

    def usage_since(self, start_iso: str) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(input_tokens),0) AS input_tokens, "
                "COALESCE(SUM(output_tokens),0) AS output_tokens, "
                "COALESCE(SUM(cache_hit_tokens),0) AS cache_hit_tokens, "
                "COUNT(model_call_id) AS rounds FROM chat_model_calls "
                "WHERE created_at >= ?", (start_iso,)).fetchone()
        return {"input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"],
                "cache_hit_tokens": row["cache_hit_tokens"], "rounds": row["rounds"]}

    def latest_activity(self, conversation_id: str) -> str | None:
        return self.get_conversation(conversation_id)["last_activity_at"]


def _conversation(row: sqlite3.Row) -> dict:
    return {"conversation_id": row["conversation_id"], "ticker": row["ticker"],
            "title": row["title"], "card_id": row["card_id"],
            "created_at": row["created_at"], "archived": row["archived"],
            "last_activity_at": row["last_activity_at"]}


def _message(row: sqlite3.Row) -> dict:
    return {
        "message_id": row["message_id"],
        "conversation_id": row["conversation_id"],
        "role": row["role"],
        "text": row["text"],
        "template_text": row["template_text"],
        "segments": None if row["segments_json"] is None else json.loads(row["segments_json"]),
        "rejected": None if row["rejected_json"] is None else json.loads(row["rejected_json"]),
        "attempts": row["attempts"],
        "prompt_version": row["prompt_version"],
        "proposal_id": row["proposal_id"],
        "created_at": row["created_at"],
    }


def _proposal(row: sqlite3.Row) -> dict:
    return {"proposal_id": row["proposal_id"], "conversation_id": row["conversation_id"],
            "message_id": row["message_id"], "horizon": row["horizon"],
            "reason": row["reason"], "status": row["status"], "job_id": row["job_id"],
            "card_id": row["card_id"], "created_at": row["created_at"],
            "decided_at": row["decided_at"]}
