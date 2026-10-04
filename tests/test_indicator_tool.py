"""Read-only indicator tool and pagination using a fake price provider."""

from datetime import date, timedelta

import pytest

from thesis_tracker.agent.tools import get_indicators
from thesis_tracker.prices import ingest_price_history


class Provider:
    def __init__(self, rows):
        self.rows = rows

    def fetch(self, symbol, start_date, end_date):
        return self.rows


def price_rows(count, slope=0.2):
    first = date(2024, 1, 1)
    output = []
    for index in range(count):
        day = (first + timedelta(days=index)).isoformat()
        close = 100 + slope * index
        output.append({"date": day, "open": close, "high": close + 1,
                       "low": close - 1, "close": close, "volume": 1000 + index,
                       "adjOpen": close, "adjHigh": close + 1,
                       "adjLow": close - 1, "adjClose": close,
                       "adjVolume": 1000 + index, "divCash": 0, "splitFactor": 1})
    return output


def store(db, symbol, rows):
    ingest_price_history([symbol], rows[0]["date"], rows[-1]["date"],
                         db_path=db, provider=Provider(rows))


def test_tool_returns_latest_adjusted_values_and_as_of(tmp_path):
    db = tmp_path / "prices.db"
    store(db, "AAPL", price_rows(300))
    store(db, "SPY", price_rows(300, slope=0.1))
    cutoff = price_rows(260)[-1]["date"]
    result = get_indicators("AAPL", as_of=cutoff, db_path=db)
    assert result["status"] == "ok"
    assert result["as_of"] == cutoff
    assert result["data"]["data_end_date"] == cutoff
    assert result["data"]["bars_used"] == 260
    assert result["data"]["latest"]["values"]["sma_200"]["value"] is not None
    assert result["data"]["latest"]["values"]["sma_200"]["date"] == cutoff
    assert result["data"]["latest"]["values"]["sma_200"]["unit"] == "USD/share"
    assert result["data"]["rows"] == []
    assert result["source"]["provider"] == "tiingo"
    assert result["fact_id"]


def test_full_series_cap_and_pagination(tmp_path):
    db = tmp_path / "prices.db"
    store(db, "AAPL", price_rows(102))
    store(db, "SPY", price_rows(102))
    end = price_rows(102)[-1]["date"]
    first = get_indicators("AAPL", as_of=end, db_path=db, full_history=True)
    assert len(first["data"]["rows"]) == 100
    assert first["data"]["next_end_date"] == price_rows(2)[-1]["date"]
    second = get_indicators("AAPL", as_of=end, db_path=db, full_history=True,
                            end_date=first["data"]["next_end_date"])
    assert len(second["data"]["rows"]) == 2
    assert second["data"]["next_end_date"] is None
    with pytest.raises(ValueError):
        get_indicators("AAPL", as_of=end, db_path=db, limit=101)


def test_missing_history_only_nulls_affected_metrics(tmp_path):
    db = tmp_path / "prices.db"
    store(db, "AAPL", price_rows(30))
    store(db, "SPY", price_rows(30))
    result = get_indicators("AAPL", as_of=price_rows(30)[-1]["date"], db_path=db)
    values = result["data"]["latest"]["values"]
    assert values["sma_20"]["value"] is not None
    assert values["sma_200"]["value"] is None
    assert values["sma_200"]["reason"]["code"] == "insufficient_history"
    assert "200" in values["sma_200"]["reason"]["message"]
    assert "30" in values["sma_200"]["reason"]["message"]


def test_missing_or_misaligned_spy_is_unavailable(tmp_path):
    db = tmp_path / "prices.db"
    store(db, "AAPL", price_rows(30))
    end = price_rows(30)[-1]["date"]
    missing = get_indicators("AAPL", as_of=end, db_path=db)
    assert missing["status"] == "unavailable"
    store(db, "SPY", price_rows(29))
    misaligned = get_indicators("AAPL", as_of=end, db_path=db)
    assert misaligned["status"] == "unavailable"
    assert "日期" in misaligned["reason"]["message"]


def test_as_of_before_first_bar_is_unavailable(tmp_path):
    db = tmp_path / "prices.db"
    store(db, "AAPL", price_rows(30))
    store(db, "SPY", price_rows(30))
    result = get_indicators("AAPL", as_of="2023-12-31", db_path=db)
    assert result["status"] == "unavailable"
    assert result["reason"]["code"] == "no_data"
