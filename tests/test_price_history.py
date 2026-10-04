from datetime import date

import pytest

from thesis_tracker.prices import get_price_history, ingest_price_history


class FakeProvider:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.calls = []

    def fetch(self, symbol, start_date, end_date):
        self.calls.append((symbol, start_date, end_date))
        if self.error:
            raise self.error
        return self.rows


def row(day, **changes):
    value = {
        "date": day, "open": 10, "high": 12, "low": 9, "close": 11,
        "volume": 100, "adjOpen": 8, "adjHigh": 10, "adjLow": 7,
        "adjClose": 9, "adjVolume": 120, "divCash": 0.2,
        "splitFactor": 1,
    }
    value.update(changes)
    return value


def test_ingest_and_read_latest_window(tmp_path):
    db = tmp_path / "prices.db"
    provider = FakeProvider([row("2024-01-02T00:00:00.000Z"), row("2024-01-03T00:00:00.000Z")])
    result = ingest_price_history(["BRK.B"], "2024-01-02", "2024-01-03", db_path=db, provider=provider)
    assert result["BRK.B"]["status"] == "ok"
    assert provider.calls == [("BRK-B", "2024-01-02", "2024-01-03")]
    output = get_price_history("BRK.B", as_of="2024-01-03", db_path=db)
    assert output["status"] == "ok"
    assert output["data"]["data_end_date"] == "2024-01-03"
    assert output["data"]["rows"][-1]["adjusted_close"]["value"] == 9
    assert output["data"]["rows"][-1]["adjusted_close"]["adjusted"] is True
    assert output["data"]["rows"][-1]["dividend_cash"]["value"] == 0.2
    assert output["source"]["provider"] == "tiingo"
    assert output["fact_id"] == output["data"]["rows"][-1]["fact_id"]


def test_as_of_and_row_cap(tmp_path):
    db = tmp_path / "prices.db"
    provider = FakeProvider([row(f"2024-01-0{x}") for x in range(1, 5)])
    ingest_price_history(["AAPL"], "2024-01-01", "2024-01-04", db_path=db, provider=provider)
    result = get_price_history("AAPL", as_of="2024-01-03", db_path=db, limit=1, full_history=True)
    assert [r["date"] for r in result["data"]["rows"]] == ["2024-01-03"]
    assert result["data"]["data_end_date"] == "2024-01-03"
    with pytest.raises(ValueError):
        get_price_history("AAPL", as_of="2024-01-04", db_path=db, limit=101)


@pytest.mark.parametrize("changes,rule", [
    ({"high": 8}, "high >= low"),
    ({"open": 13}, "open within"),
    ({"close": 13}, "close within"),
    ({"low": -1}, "nonnegative price"),
    ({"volume": -1}, "nonnegative volume"),
    ({"date": "2099-01-01"}, "future date"),
    ({"adjClose": -1}, "nonnegative price"),
])
def test_invalid_row_rejects_entire_window(tmp_path, changes, rule):
    db = tmp_path / "prices.db"
    provider = FakeProvider([row("2024-01-01"), row("2024-01-02", **changes)])
    result = ingest_price_history(["AAPL"], "2024-01-01", "2024-01-03", db_path=db, provider=provider, today=date(2024, 1, 3))
    assert result["AAPL"]["status"] == "error"
    assert result["AAPL"]["reason"]["code"] == "provider_error"
    assert rule in result["AAPL"]["reason"]["message"]
    assert "row 2" in result["AAPL"]["reason"]["message"]
    assert get_price_history("AAPL", as_of="2024-01-03", db_path=db)["status"] == "unavailable"


@pytest.mark.parametrize("days", [["2024-01-02", "2024-01-02"], ["2024-01-03", "2024-01-02"]])
def test_dates_unique_and_increasing(tmp_path, days):
    db = tmp_path / "prices.db"
    result = ingest_price_history(["AAPL"], "2024-01-01", "2024-01-03", db_path=db,
                                  provider=FakeProvider([row(day) for day in days]), today=date(2024, 1, 3))
    assert result["AAPL"]["reason"]["code"] == "provider_error"
    assert "row 2" in result["AAPL"]["reason"]["message"]
    assert get_price_history("AAPL", as_of="2024-01-03", db_path=db)["status"] == "unavailable"


def test_rate_limit_stops_batch(tmp_path):
    from thesis_tracker.prices import RateLimited

    provider = FakeProvider(error=RateLimited())
    result = ingest_price_history(["AAPL", "AMD"], "2024-01-01", "2024-01-03", db_path=tmp_path / "prices.db", provider=provider)
    assert result["AAPL"]["reason"]["code"] == "rate_limited"
    assert result["AMD"]["reason"]["code"] == "rate_limited"
    assert len(provider.calls) == 1


def test_empty_and_cache_hit(tmp_path):
    db = tmp_path / "prices.db"
    provider = FakeProvider([])
    first = ingest_price_history(["AAPL"], "2024-01-01", "2024-01-03", db_path=db, provider=provider)
    second = ingest_price_history(["AAPL"], "2024-01-01", "2024-01-03", db_path=db, provider=provider)
    assert first["AAPL"]["reason"]["code"] == "no_data"
    assert second["AAPL"]["reason"]["code"] == "no_data"
    assert len(provider.calls) == 1
    assert get_price_history("AAPL", as_of="2024-01-03", db_path=db)["reason"]["code"] == "no_data"


def test_covered_window_cache_hit_and_request_cap(tmp_path):
    db = tmp_path / "prices.db"
    provider = FakeProvider([row("2024-01-02")])
    ingest_price_history(["AAPL"], "2024-01-01", "2024-01-03", db_path=db, provider=provider)
    result = ingest_price_history(["AAPL", "AMD"], "2024-01-02", "2024-01-02", db_path=db, provider=provider, max_requests=0)
    assert result["AAPL"]["status"] == "ok"
    assert result["AMD"]["reason"]["code"] == "rate_limited"
    assert len(provider.calls) == 1


def test_provider_uses_header_without_key_in_url(monkeypatch):
    from thesis_tracker.prices import TiingoProvider

    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self):
            return b"[]"

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        return Response()

    monkeypatch.setattr("thesis_tracker.prices.urlopen", fake_urlopen)
    assert TiingoProvider(token="example-secret").fetch("BRK-B", "2024-01-01", "2024-01-02") == []
    assert captured["authorization"] == "Token example-secret"
    assert "example-secret" not in captured["url"]


def test_provider_maps_http_429_to_rate_limit(monkeypatch):
    from urllib.error import HTTPError

    from thesis_tracker.prices import RateLimited, TiingoProvider

    def limited(request, timeout):
        raise HTTPError(request.full_url, 429, "limited", {}, None)

    monkeypatch.setattr("thesis_tracker.prices.urlopen", limited)
    with pytest.raises(RateLimited):
        TiingoProvider(token="example-secret").fetch("AAPL", "2024-01-01", "2024-01-02")


def test_tiingo_key_uses_project_settings(monkeypatch):
    from thesis_tracker.config import load_settings

    monkeypatch.setenv("TIINGO_API_KEY", "example-secret")
    assert load_settings().tiingo_api_key == "example-secret"


def test_summary_reports_latest_raw_and_adjusted_close(tmp_path):
    db = tmp_path / "prices.db"
    ingest_price_history(["AAPL"], "2024-01-01", "2024-01-02", db_path=db,
                         provider=FakeProvider([row("2024-01-02")]))
    result = get_price_history("AAPL", as_of="2024-01-02", db_path=db)
    assert result["data"]["latest_close"] == {"value": 11, "unit": "USD/share", "adjusted": False}
    assert result["data"]["latest_adjusted_close"] == {"value": 9, "unit": "USD/share", "adjusted": True}


@pytest.mark.parametrize("changes,rule", [
    ({"adjHigh": 6}, "adjusted high >= low"),
    ({"adjOpen": 11}, "adjusted open within"),
    ({"adjClose": 11}, "adjusted close within"),
])
def test_invalid_adjusted_ohlc_rejects_entire_window(tmp_path, changes, rule):
    db = tmp_path / "prices.db"
    result = ingest_price_history(["AAPL"], "2024-01-01", "2024-01-02", db_path=db,
                                  provider=FakeProvider([row("2024-01-02", **changes)]))
    assert rule in result["AAPL"]["reason"]["message"]
    assert get_price_history("AAPL", as_of="2024-01-02", db_path=db)["status"] == "unavailable"


def test_missing_adjusted_price_rejects_entire_window(tmp_path):
    db = tmp_path / "prices.db"
    incomplete = row("2024-01-02")
    del incomplete["adjClose"]
    result = ingest_price_history(["AAPL"], "2024-01-01", "2024-01-02", db_path=db,
                                  provider=FakeProvider([incomplete]))
    assert result["AAPL"]["reason"]["code"] == "provider_error"
    assert get_price_history("AAPL", as_of="2024-01-02", db_path=db)["status"] == "unavailable"


def test_agent_tool_public_entrypoint_reads_price_db(tmp_path):
    from thesis_tracker.agent.tools import get_price_history as tool

    db = tmp_path / "prices.db"
    ingest_price_history(["AAPL"], "2024-01-01", "2024-01-02", db_path=db,
                         provider=FakeProvider([row("2024-01-02")]))
    assert tool("AAPL", as_of="2024-01-02", db_path=db)["data"]["data_end_date"] == "2024-01-02"


def test_full_history_can_page_past_row_cap(tmp_path):
    from datetime import timedelta

    db = tmp_path / "prices.db"
    first = date(2024, 1, 1)
    rows = [row((first + timedelta(days=i)).isoformat()) for i in range(102)]
    ingest_price_history(["AAPL"], "2024-01-01", "2024-04-11", db_path=db,
                         provider=FakeProvider(rows))
    page1 = get_price_history("AAPL", as_of="2024-04-11", db_path=db, full_history=True)
    assert len(page1["data"]["rows"]) == 100
    assert page1["data"]["next_end_date"] == "2024-01-02"
    page2 = get_price_history("AAPL", as_of="2024-04-11", db_path=db, full_history=True,
                              end_date=page1["data"]["next_end_date"])
    assert [item["date"] for item in page2["data"]["rows"]] == ["2024-01-01", "2024-01-02"]
    assert page2["data"]["next_end_date"] is None


def test_paginated_summary_stays_at_as_of_latest(tmp_path):
    db = tmp_path / "prices.db"
    ingest_price_history(["AAPL"], "2024-01-01", "2024-01-03", db_path=db,
                         provider=FakeProvider([row("2024-01-01", close=10),
                                                row("2024-01-03", close=12)]))
    result = get_price_history("AAPL", as_of="2024-01-03", end_date="2024-01-01",
                               db_path=db, full_history=True)
    assert result["data"]["data_end_date"] == "2024-01-03"
    assert result["data"]["latest_close"]["value"] == 12


def test_source_reports_replacement_request_window(tmp_path):
    db = tmp_path / "prices.db"
    ingest_price_history(["AAPL"], "2024-01-01", "2024-01-02", db_path=db,
                         provider=FakeProvider([row("2024-01-02")]))
    ingest_price_history(["AAPL"], "2024-01-03", "2024-01-04", db_path=db,
                         provider=FakeProvider([row("2024-01-02"), row("2024-01-04")]))
    result = get_price_history("AAPL", as_of="2024-01-04", db_path=db)
    assert result["source"]["request_windows"] == [
        {"start_date": "2024-01-01", "end_date": "2024-01-04"},
    ]


def test_split_refresh_replaces_entire_symbol_without_mixing_adjustment_bases(tmp_path):
    import sqlite3

    db = tmp_path / "prices.db"
    old = FakeProvider([row("2024-01-02", adjOpen=100, adjHigh=112, adjLow=90, adjClose=100), row("2024-01-03", adjOpen=100, adjHigh=112, adjLow=90, adjClose=110)])
    ingest_price_history(["AAPL"], "2024-01-02", "2024-01-03", db_path=db, provider=old)
    after_split = FakeProvider([
        row("2024-01-02", adjOpen=50, adjHigh=62, adjLow=45, adjClose=50), row("2024-01-03", adjOpen=50, adjHigh=62, adjLow=45, adjClose=55),
        row("2024-01-04", adjOpen=50, adjHigh=62, adjLow=45, adjClose=60),
    ])
    result = ingest_price_history(["AAPL"], "2024-01-04", "2024-01-04", db_path=db,
                                  provider=after_split)
    assert result["AAPL"]["status"] == "ok"
    assert after_split.calls == [("AAPL", "2024-01-02", "2024-01-04")]
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT date, adj_close FROM daily_prices ORDER BY date"
        ).fetchall() == [("2024-01-02", 50), ("2024-01-03", 55), ("2024-01-04", 60)]
        assert connection.execute(
            "SELECT COUNT(DISTINCT retrieved_at) FROM daily_prices WHERE symbol='AAPL'"
        ).fetchone()[0] == 1


def test_failed_refresh_preserves_previous_complete_snapshot(tmp_path):
    db = tmp_path / "prices.db"
    ingest_price_history(["AAPL"], "2024-01-02", "2024-01-03", db_path=db,
                         provider=FakeProvider([row("2024-01-02", adjOpen=100, adjHigh=112, adjLow=90, adjClose=100)]))
    failed = FakeProvider([row("2024-01-02", adjOpen=50, adjHigh=62, adjLow=45, adjClose=50), row("2024-01-04", high=8)])
    result = ingest_price_history(["AAPL"], "2024-01-04", "2024-01-04", db_path=db,
                                  provider=failed)
    assert result["AAPL"]["reason"]["code"] == "provider_error"
    assert get_price_history("AAPL", as_of="2024-01-04", db_path=db)["data"]["latest_adjusted_close"]["value"] == 100


def test_mixed_legacy_snapshot_is_not_read_or_used_as_cache(tmp_path):
    import sqlite3

    db = tmp_path / "prices.db"
    ingest_price_history(["AAPL"], "2024-01-02", "2024-01-03", db_path=db,
                         provider=FakeProvider([row("2024-01-02"), row("2024-01-03")]))
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE daily_prices SET retrieved_at='old' WHERE date='2024-01-02'")
    assert get_price_history("AAPL", as_of="2024-01-03", db_path=db)["status"] == "unavailable"
    provider = FakeProvider([row("2024-01-02"), row("2024-01-03")])
    result = ingest_price_history(["AAPL"], "2024-01-02", "2024-01-03", db_path=db,
                                  provider=provider)
    assert result["AAPL"]["status"] == "ok"
    assert len(provider.calls) == 1


def test_snapshot_rejects_row_window_metadata_mismatch(tmp_path):
    import sqlite3

    db = tmp_path / "prices.db"
    ingest_price_history(["AAPL"], "2024-01-02", "2024-01-03", db_path=db,
                         provider=FakeProvider([row("2024-01-02"), row("2024-01-03")]))
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE daily_prices SET window_start='2024-01-01' WHERE date='2024-01-02'")
    result = get_price_history("AAPL", as_of="2024-01-03", db_path=db)
    assert result["status"] == "unavailable"
    assert result["reason"]["code"] == "provider_error"
