"""Read-only SQLite access for the local web app.

Every helper here opens its database with SQLite's ``mode=ro`` URI flag and
creates nothing.  The web app is a thin presentation layer: it never writes to
the fact, price or card databases, and it never recomputes a financial value.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import quote

FILING_COLUMNS = ("accession", "original_accession", "ticker", "cik", "form",
                  "period_end", "filed_at", "fiscal_year", "fiscal_period",
                  "registered_count")


def read_only(db_path: Path | str) -> sqlite3.Connection:
    """Open an existing SQLite file read-only; raise FileNotFoundError if absent."""
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    connection = sqlite3.connect(f"file:{quote(str(path.resolve()))}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _has_table(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def filing_rows(fact_db: Path | str, *, ticker: str | None = None) -> list[dict]:
    """Every stored filing snapshot, ordered by ticker and period end."""
    connection = read_only(fact_db)
    try:
        if not _has_table(connection, "filing_snapshots"):
            return []
        query = f"SELECT {', '.join(FILING_COLUMNS)} FROM filing_snapshots"
        parameters: tuple = ()
        if ticker is not None:
            query += " WHERE upper(ticker)=upper(?)"
            parameters = (ticker,)
        query += " ORDER BY ticker, period_end, filed_at, accession"
        return [dict(row) for row in connection.execute(query, parameters)]
    finally:
        connection.close()


def tickers(fact_db: Path | str) -> list[str]:
    """Distinct tickers that actually have stored filings; never a hardcoded list."""
    return sorted({row["ticker"].upper() for row in filing_rows(fact_db)})


def price_bounds(price_db: Path | str) -> dict[str, dict]:
    """Stored date range, row count and latest close per symbol, read-only."""
    try:
        connection = read_only(price_db)
    except FileNotFoundError:
        return {}
    try:
        if not _has_table(connection, "daily_prices"):
            return {}
        rows = connection.execute(
            "SELECT symbol, MIN(date) AS start_date, MAX(date) AS end_date, "
            "COUNT(*) AS rows FROM daily_prices GROUP BY symbol ORDER BY symbol"
        ).fetchall()
        result: dict[str, dict] = {}
        for row in rows:
            latest = connection.execute(
                "SELECT date, close FROM daily_prices WHERE symbol=? "
                "ORDER BY date DESC LIMIT 1", (row["symbol"],)
            ).fetchone()
            result[row["symbol"].upper()] = {
                "start_date": row["start_date"], "end_date": row["end_date"],
                "rows": row["rows"], "latest_date": latest["date"],
                "latest_close": latest["close"],
            }
        return result
    finally:
        connection.close()


def latest_price_date(price_db: Path | str) -> str | None:
    """Newest stored price date across all symbols, or None when there is none."""
    return max((item["end_date"] for item in price_bounds(price_db).values()), default=None)


def single_batch(connection: sqlite3.Connection, symbol: str) -> bool:
    """True when one symbol's rows all come from one complete stored window.

    Mirrors the read-only consistency check ``prices._snapshot`` applies before
    the price tool serves a symbol, so the web app never shows a range the
    analysis path would refuse.
    """
    windows = connection.execute(
        "SELECT start_date, end_date, retrieved_at, row_count, provider "
        "FROM price_windows WHERE symbol=?", (symbol,)
    ).fetchall()
    count, retrievals, retrieved = connection.execute(
        "SELECT COUNT(*), COUNT(DISTINCT retrieved_at), MIN(retrieved_at) "
        "FROM daily_prices WHERE symbol=?", (symbol,)
    ).fetchone()
    if not windows or len(windows) != 1:
        return False
    window = windows[0]
    if window["row_count"] != count:
        return False
    mismatches = connection.execute(
        "SELECT COUNT(*) FROM daily_prices WHERE symbol=? AND "
        "(window_start!=? OR window_end!=? OR retrieved_at!=? OR provider!=?)",
        (symbol, window["start_date"], window["end_date"], window["retrieved_at"],
         window["provider"]),
    ).fetchone()[0]
    if mismatches:
        return False
    return count == 0 or (retrievals == 1 and retrieved == window["retrieved_at"])


def price_windows(price_db: Path | str, symbol: str) -> list[dict]:
    """Stored fetch windows for one symbol, newest first."""
    try:
        connection = read_only(price_db)
    except FileNotFoundError:
        return []
    try:
        if not _has_table(connection, "price_windows"):
            return []
        return [dict(row) for row in connection.execute(
            "SELECT start_date, end_date, provider, retrieved_at, row_count "
            "FROM price_windows WHERE upper(symbol)=upper(?) "
            "ORDER BY retrieved_at DESC, start_date DESC", (symbol,))]
    finally:
        connection.close()


def card_rows(card_db: Path | str, *, ticker: str | None = None) -> list[dict]:
    """Archived card metadata, newest first; never the model call logs."""
    columns = ("card_id, created_at, ticker, as_of, creation_price, card_json, "
               "snapshot_json, snapshot_sha256, validator_version, validation_json, "
               "requested_model, returned_model, fingerprint, prompt_version, "
               "input_tokens, output_tokens, cache_hit_tokens")
    try:
        connection = read_only(card_db)
    except FileNotFoundError:
        return []
    try:
        if not _has_table(connection, "decision_cards"):
            return []
        query = f"SELECT {columns} FROM decision_cards"
        parameters: tuple = ()
        if ticker is not None:
            query += " WHERE upper(ticker)=upper(?)"
            parameters = (ticker,)
        query += " ORDER BY created_at DESC, card_id"
        return [dict(row) for row in connection.execute(query, parameters)]
    finally:
        connection.close()


def analysis_usage(card_db: Path | str, *, window: int = 5) -> list[dict]:
    """Token totals per recent analysis, newest first; never a money amount."""
    try:
        connection = read_only(card_db)
    except FileNotFoundError:
        return []
    try:
        if not _has_table(connection, "decision_model_calls"):
            return []
        rows = connection.execute(
            "SELECT analysis_id, MAX(created_at) AS last_at, "
            "SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, "
            "SUM(cache_hit_tokens) AS cache_hit_tokens, COUNT(*) AS rounds "
            "FROM decision_model_calls GROUP BY analysis_id "
            "ORDER BY last_at DESC, analysis_id LIMIT ?", (window,)
        ).fetchall()
        return [{"analysis_id": row["analysis_id"], "last_at": row["last_at"],
                 "input_tokens": row["input_tokens"] or 0,
                 "output_tokens": row["output_tokens"] or 0,
                 "cache_hit_tokens": row["cache_hit_tokens"] or 0,
                 "rounds": row["rounds"]} for row in rows]
    finally:
        connection.close()


def attempt_rows(card_db: Path | str, *, ticker: str | None = None) -> list[dict]:
    """Draft attempts, newest first; the rejected ones are shown, never hidden."""
    try:
        connection = read_only(card_db)
    except FileNotFoundError:
        return []
    try:
        if not _has_table(connection, "decision_attempts"):
            return []
        query = ("SELECT attempt_id, analysis_id, attempt_no, ticker, as_of, "
                 "violations_json, passed, created_at, requested_horizon "
                 "FROM decision_attempts")
        parameters: tuple = ()
        if ticker is not None:
            query += " WHERE upper(ticker)=upper(?)"
            parameters = (ticker,)
        query += " ORDER BY created_at DESC, attempt_no"
        return [dict(row) for row in connection.execute(query, parameters)]
    finally:
        connection.close()
