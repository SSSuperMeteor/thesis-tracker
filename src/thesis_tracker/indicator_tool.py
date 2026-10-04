"""Read-only indicator tool over validated, single-batch adjusted price snapshots."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

from thesis_tracker.indicators import Bar, calculate_indicator_series
from thesis_tracker.prices import (
    DEFAULT_DB,
    MAX_ROWS,
    _date,
    _reason,
    _result,
    _snapshot,
    _symbol,
)


def _read_bars(connection: sqlite3.Connection, symbol: str, cutoff: str) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT date, adj_high, adj_low, adj_close, adj_volume, provider, "
        "window_start, window_end, retrieved_at FROM daily_prices "
        "WHERE symbol=? AND date<=? ORDER BY date", (symbol, cutoff),
    ).fetchall()


def _as_bars(rows: list[sqlite3.Row]) -> list[Bar]:
    return [Bar(row["date"], row["adj_high"], row["adj_low"], row["adj_close"],
                row["adj_volume"]) for row in rows]


def get_indicators(
    symbol: str, *, as_of: str, db_path: Path | str = DEFAULT_DB,
    limit: int | None = None, full_history: bool = False, end_date: str | None = None,
) -> dict:
    """Compute adjusted-price indicators from local SQLite only."""
    symbol = _symbol(symbol)
    cutoff = _date(as_of).isoformat()
    page_end = min(cutoff, _date(end_date).isoformat()) if end_date else cutoff
    limit = limit if limit is not None else (MAX_ROWS if full_history else 20)
    if not 1 <= limit <= MAX_ROWS:
        raise ValueError(f"limit must be 1..{MAX_ROWS}")
    path = Path(db_path)
    if not path.exists():
        return _result("unavailable", cutoff, reason=_reason("no_data", "本地没有价格数据。"))
    connection = None
    try:
        connection = sqlite3.connect(f"file:{quote(str(path.resolve()))}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        with connection:
            valid, _, _, _ = _snapshot(connection, symbol)
            spy_valid, _, _, _ = _snapshot(connection, "SPY")
            ticker_rows = _read_bars(connection, symbol, cutoff)
            spy_rows = ticker_rows if symbol == "SPY" else _read_bars(connection, "SPY", cutoff)
    except sqlite3.Error:
        return _result("unavailable", cutoff, reason=_reason("provider_error", "本地价格库不可读取。"))
    finally:
        if connection is not None:
            connection.close()
    if not valid or not spy_valid:
        return _result("unavailable", cutoff, reason=_reason(
            "provider_error", "本地价格存在不同抓取批次，复权基准不一致；请整段重抓。"))
    if not ticker_rows or not spy_rows:
        return _result("unavailable", cutoff, reason=_reason("no_data", "标的或 SPY 在该日期之前没有价格数据。"))
    if ticker_rows[-1]["date"] != spy_rows[-1]["date"]:
        return _result("unavailable", cutoff, reason=_reason(
            "no_data", f"标的最新日期 {ticker_rows[-1]['date']} 与 SPY 最新日期 {spy_rows[-1]['date']} 不一致。"))
    available = [index for index, row in enumerate(ticker_rows) if row["date"] <= page_end]
    if not available:
        return _result("unavailable", cutoff, reason=_reason("no_data", "所选页面日期之前没有价格数据。"))
    selected = available[-limit:] if full_history else []
    positions = sorted(set([len(ticker_rows) - 1, *selected]))
    calculated = calculate_indicator_series(_as_bars(ticker_rows), _as_bars(spy_rows), positions=positions)
    by_index = dict(zip(positions, calculated))

    def annotate(record: dict) -> dict:
        for name, metric in record["values"].items():
            metric["fact_id"] = f"tiingo|{symbol}|{record['date']}|indicator|{name}"
        return record

    latest = annotate(by_index[len(ticker_rows) - 1])
    pages = [annotate(by_index[index]) for index in selected]
    has_more = full_history and len(available) > len(selected)
    next_end = ((date.fromisoformat(ticker_rows[selected[0]]["date"]) - timedelta(days=1)).isoformat()
                if has_more else None)
    newest = ticker_rows[-1]
    benchmark = spy_rows[-1]
    source = {
        "provider": newest["provider"], "endpoint": f"/tiingo/daily/{symbol}/prices",
        "request_window": {"start_date": newest["window_start"], "end_date": newest["window_end"]},
        "retrieved_at": newest["retrieved_at"],
        "benchmark": {"symbol": "SPY", "endpoint": "/tiingo/daily/SPY/prices",
                      "request_window": {"start_date": benchmark["window_start"],
                                         "end_date": benchmark["window_end"]},
                      "retrieved_at": benchmark["retrieved_at"]},
    }
    return _result("ok", cutoff, data={"symbol": symbol, "data_end_date": newest["date"],
                                       "bars_used": len(ticker_rows), "latest": latest,
                                       "rows": pages, "returned_rows": len(pages),
                                       "next_end_date": next_end},
                   source=source, fact_id=f"tiingo|{symbol}|{newest['date']}|indicators")
