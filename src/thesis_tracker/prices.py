"""Daily Tiingo price ingestion and read-only price history tool."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from thesis_tracker.config import load_settings

DEFAULT_DB = Path("data/cache/prices.db")
ENDPOINT = "https://api.tiingo.com/tiingo/daily"
MAX_ROWS = 100


class RateLimited(Exception):
    """The provider or local request budget prevented a request."""


class ProviderError(Exception):
    """The provider failed or returned an unusable response."""


class TiingoProvider:
    """Network adapter; the token is sent only in a request header."""

    def __init__(self, token: str | None = None):
        self._token = token or load_settings().tiingo_api_key

    def fetch(self, symbol: str, start_date: str, end_date: str) -> list[dict]:
        if not self._token:
            raise ProviderError("TIINGO_API_KEY is not configured")
        url = f"{ENDPOINT}/{quote(symbol, safe='')}/prices?{urlencode({'startDate': start_date, 'endDate': end_date})}"
        request = Request(url, headers={"Authorization": f"Token {self._token}", "Accept": "application/json"})
        try:
            with urlopen(request, timeout=20) as response:
                rows = json.load(response)
        except HTTPError as exc:
            if exc.code == 429:
                raise RateLimited() from None
            raise ProviderError(f"Tiingo HTTP {exc.code}") from None
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise ProviderError(f"Tiingo request or JSON failed ({type(exc).__name__})") from None
        if not isinstance(rows, list):
            raise ProviderError("Tiingo response is not a list")
        return rows


def _symbol(symbol: str) -> str:
    normalized = symbol.upper().replace(".", "-")
    if not normalized or not all(c.isascii() and (c.isalnum() or c == "-") for c in normalized):
        raise ValueError("invalid symbol")
    return normalized


def _date(value: str) -> date:
    return date.fromisoformat(value[:10])


def _connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("""CREATE TABLE IF NOT EXISTS price_windows (
        symbol TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
        provider TEXT NOT NULL, retrieved_at TEXT NOT NULL, row_count INTEGER NOT NULL,
        PRIMARY KEY(symbol, start_date, end_date)
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS daily_prices (
        symbol TEXT NOT NULL, date TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL,
        low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL,
        adj_open REAL, adj_high REAL, adj_low REAL, adj_close REAL, adj_volume REAL,
        dividend_cash REAL, split_factor REAL,
        window_start TEXT NOT NULL, window_end TEXT NOT NULL,
        retrieved_at TEXT NOT NULL, provider TEXT NOT NULL,
        PRIMARY KEY(symbol, date)
    )""")
    connection.commit()
    return connection


def _reason(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def _result(status: str, as_of: str, data=None, source=None, fact_id=None, reason=None) -> dict:
    return {"status": status, "data": data, "source": source, "as_of": as_of,
            "fact_id": fact_id, "reason": reason}


def _snapshot(connection: sqlite3.Connection, symbol: str) -> tuple[bool, str | None, str | None, int]:
    windows = connection.execute(
        "SELECT start_date, end_date, retrieved_at, row_count, provider "
        "FROM price_windows WHERE symbol=?",
        (symbol,),
    ).fetchall()
    row_count, retrieval_count, retrieval = connection.execute(
        "SELECT COUNT(*), COUNT(DISTINCT retrieved_at), MIN(retrieved_at) "
        "FROM daily_prices WHERE symbol=?", (symbol,),
    ).fetchone()
    if not windows:
        return row_count == 0, None, None, row_count
    mismatches = 0
    if len(windows) == 1 and row_count:
        mismatches = connection.execute(
            "SELECT COUNT(*) FROM daily_prices WHERE symbol=? AND "
            "(window_start!=? OR window_end!=? OR retrieved_at!=? OR provider!=?)",
            (symbol, windows[0][0], windows[0][1], windows[0][2], windows[0][4]),
        ).fetchone()[0]
    valid = (len(windows) == 1 and windows[0][3] == row_count and mismatches == 0
             and (row_count == 0 or (retrieval_count == 1 and retrieval == windows[0][2])))
    start = min(item[0] for item in windows)
    end = max(item[1] for item in windows)
    return valid, start, end, row_count


def _validate(rows: list[dict], start: date, end: date, today: date) -> list[dict]:
    failures = []
    validated = []
    previous = None
    for number, row in enumerate(rows, 1):
        rules = []
        try:
            day = _date(row["date"])
            if day > today:
                rules.append("future date")
            if not start <= day <= end:
                rules.append("date outside request window")
            if previous is not None and day <= previous:
                rules.append("dates unique and increasing")
            previous = day
            prices = [row[key] for key in ("open", "high", "low", "close")]
            adjusted = [row[key] for key in ("adjOpen", "adjHigh", "adjLow", "adjClose")]
            row["adjVolume"]
            if any(not isinstance(x, (int, float)) or isinstance(x, bool) or not 0 <= x < float("inf")
                   for x in prices + [x for x in adjusted if x is not None]):
                rules.append("nonnegative price")
            if row["high"] < row["low"]:
                rules.append("high >= low")
            if not row["low"] <= row["open"] <= row["high"]:
                rules.append("open within [low, high]")
            if not row["low"] <= row["close"] <= row["high"]:
                rules.append("close within [low, high]")
            if all(value is not None for value in adjusted):
                adj_open, adj_high, adj_low, adj_close = adjusted
                if adj_high < adj_low:
                    rules.append("adjusted high >= low")
                if not adj_low <= adj_open <= adj_high:
                    rules.append("adjusted open within [low, high]")
                if not adj_low <= adj_close <= adj_high:
                    rules.append("adjusted close within [low, high]")
            for key in ("volume", "adjVolume"):
                value = row.get(key)
                if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)
                                          or not 0 <= value < float("inf")):
                    rules.append("nonnegative volume")
            for key in ("divCash", "splitFactor"):
                value = row.get(key)
                if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)
                                          or not 0 <= value < float("inf")):
                    rules.append(f"invalid {key}")
            validated.append({**row, "date": day.isoformat()})
        except (KeyError, TypeError, ValueError):
            rules.append("missing or malformed required field")
        if rules:
            failures.append(f"row {number}: {', '.join(rules)}")
    if failures:
        raise ProviderError("; ".join(failures))
    return validated


def ingest_price_history(
    symbols: list[str], start_date: str, end_date: str, *, db_path: Path | str = DEFAULT_DB,
    provider: TiingoProvider | None = None, max_requests: int = 10, today: date | None = None,
) -> dict[str, dict]:
    """Fetch a bounded batch, validate whole windows, then atomically store them."""
    start, end = _date(start_date), _date(end_date)
    if start > end or max_requests < 0 or max_requests > 10:
        raise ValueError("invalid window or request budget")
    provider = provider or TiingoProvider()
    today = today or date.today()
    outcomes = {}
    requests = 0
    stopped = False
    with _connect(db_path) as connection:
        for given_symbol in symbols:
            symbol = _symbol(given_symbol)
            if stopped:
                outcomes[given_symbol] = _result("unavailable", end_date, reason=_reason("rate_limited", "已停止抓取，达到请求上限或提供方限流。"))
                continue
            valid, stored_start, stored_end, row_count = _snapshot(connection, symbol)
            if valid and stored_start is not None and stored_start <= start_date and stored_end >= end_date:
                code = "ok" if row_count else "unavailable"
                reason = None if row_count else _reason("no_data", "该日期窗口没有价格数据。")
                outcomes[given_symbol] = _result(code, end_date, reason=reason)
                continue
            if requests >= max_requests:
                stopped = True
                outcomes[given_symbol] = _result("unavailable", end_date, reason=_reason("rate_limited", "本次运行已达到请求上限。"))
                continue
            requests += 1
            request_start = min(start_date, stored_start) if stored_start else start_date
            request_end = max(end_date, stored_end) if stored_end else end_date
            try:
                raw = provider.fetch(symbol, request_start, request_end)
                if not isinstance(raw, list):
                    raise ProviderError("response is not a list")
                rows = _validate(raw, _date(request_start), _date(request_end), today)
            except RateLimited:
                stopped = True
                outcomes[given_symbol] = _result("unavailable", end_date, reason=_reason("rate_limited", "Tiingo 限流，已停止后续请求。"))
                continue
            except ProviderError as exc:
                outcomes[given_symbol] = _result("error", end_date, reason=_reason("provider_error", str(exc)))
                continue
            retrieved = datetime.now(timezone.utc).isoformat()
            with connection:
                connection.execute("DELETE FROM daily_prices WHERE symbol=?", (symbol,))
                connection.execute("DELETE FROM price_windows WHERE symbol=?", (symbol,))
                for row in rows:
                    connection.execute("""INSERT INTO daily_prices VALUES (
                        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                        symbol, row["date"], row["open"], row["high"], row["low"], row["close"], row["volume"],
                        row.get("adjOpen"), row.get("adjHigh"), row.get("adjLow"), row.get("adjClose"),
                        row.get("adjVolume"), row.get("divCash"), row.get("splitFactor"),
                        request_start, request_end, retrieved, "tiingo",
                    ))
                connection.execute("INSERT INTO price_windows VALUES (?,?,?,?,?,?)",
                                   (symbol, request_start, request_end, "tiingo", retrieved, len(rows)))
            status = "ok" if rows else "unavailable"
            reason = None if rows else _reason("no_data", "该日期窗口没有价格数据。")
            outcomes[given_symbol] = _result(status, end_date, reason=reason)
    return outcomes


def get_price_history(
    symbol: str, *, as_of: str, db_path: Path | str = DEFAULT_DB,
    limit: int | None = None, full_history: bool = False, end_date: str | None = None,
) -> dict:
    """Read stored prices only; full_history is a separate call mode."""
    symbol = _symbol(symbol)
    cutoff = _date(as_of).isoformat()
    page_end = min(cutoff, _date(end_date).isoformat()) if end_date else cutoff
    limit = limit if limit is not None else (MAX_ROWS if full_history else 20)
    if not 1 <= limit <= MAX_ROWS:
        raise ValueError(f"limit must be 1..{MAX_ROWS}")
    path = Path(db_path)
    if not path.exists():
        return _result("unavailable", cutoff, reason=_reason("no_data", "本地没有价格数据。"))
    try:
        connection = sqlite3.connect(f"file:{quote(str(path.resolve()))}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        with connection:
            valid, _, _, _ = _snapshot(connection, symbol)
            global_latest = connection.execute(
                "SELECT * FROM daily_prices WHERE symbol=? AND date<=? ORDER BY date DESC LIMIT 1",
                (symbol, cutoff),
            ).fetchone()
            rows = connection.execute(
                "SELECT * FROM daily_prices WHERE symbol=? AND date<=? ORDER BY date DESC LIMIT ?",
                (symbol, page_end, limit + 1),
            ).fetchall()
    except sqlite3.Error:
        return _result("unavailable", cutoff, reason=_reason("provider_error", "本地价格库不可读取。"))
    finally:
        if "connection" in locals():
            connection.close()
    if not valid:
        return _result("unavailable", cutoff, reason=_reason(
            "provider_error", "本地价格行来自多个抓取批次，复权基准不一致；需要整段重新抓取。"))
    if not rows:
        return _result("unavailable", cutoff, reason=_reason("no_data", "该日期之前没有价格数据。"))
    has_more = len(rows) > limit
    rows = rows[:limit]
    selected = list(reversed(rows))
    def price(value, adjusted):
        return None if value is None else {"value": value, "unit": "USD/share", "adjusted": adjusted}
    output_rows = []
    for row in selected:
        output_rows.append({
            "date": row["date"], "fact_id": f"tiingo|{symbol}|{row['date']}|daily",
            "open": price(row["open"], False), "high": price(row["high"], False),
            "low": price(row["low"], False), "close": price(row["close"], False),
            "volume": {"value": row["volume"], "unit": "shares", "adjusted": False},
            "adjusted_open": price(row["adj_open"], True),
            "adjusted_high": price(row["adj_high"], True),
            "adjusted_low": price(row["adj_low"], True),
            "adjusted_close": price(row["adj_close"], True),
            "adjusted_volume": None if row["adj_volume"] is None else
                {"value": row["adj_volume"], "unit": "shares", "adjusted": True},
            "dividend_cash": price(row["dividend_cash"], False),
            "split_factor": None if row["split_factor"] is None else
                {"value": row["split_factor"], "unit": "ratio"},
        })
    request_windows = sorted({(row["window_start"], row["window_end"]) for row in rows}
                             | {(global_latest["window_start"], global_latest["window_end"])})
    source = {"provider": global_latest["provider"], "endpoint": f"/tiingo/daily/{symbol}/prices",
              "request_window": {"start_date": global_latest["window_start"],
                                 "end_date": global_latest["window_end"]},
              "request_windows": [{"start_date": start, "end_date": end}
                                  for start, end in request_windows],
              "retrieved_at": global_latest["retrieved_at"]}
    next_end = (date.fromisoformat(rows[-1]["date"]) - timedelta(days=1)).isoformat() if has_more else None
    return _result("ok", cutoff, data={"symbol": symbol, "data_end_date": global_latest["date"],
                                        "latest_close": price(global_latest["close"], False),
                                        "latest_adjusted_close": price(global_latest["adj_close"], True),
                                        "rows": output_rows, "returned_rows": len(output_rows),
                                        "next_end_date": next_end},
                   source=source, fact_id=f"tiingo|{symbol}|{global_latest['date']}|daily")
