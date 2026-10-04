"""Compact decision evidence must remain traceable to unmodified tool facts."""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from thesis_tracker.decision.core import (
    build_card,
    capture_snapshot,
    data_gaps,
    fact_index,
    render_card,
)
from thesis_tracker.decision.evidence import (
    MAX_ENVELOPE_BYTES,
    bucket_dates,
    display_value,
    fit_envelope,
    history_view,
    prepare_evidence,
    recompute_derived,
)

BASELINE = Path(__file__).parent / "fixtures/decision_baseline_2026-10-04.json"


@pytest.mark.parametrize("ticker", ["AAPL", "NVDA"])
def test_existing_fact_ids_and_exact_values_match_prechange_snapshot(ticker):
    before = json.loads(BASELINE.read_text())[ticker]
    after = capture_snapshot(ticker, "2026-10-04")
    assert {key: value["value"] for key, value in fact_index(after)[0].items()} == {
        key: value["value"] for key, value in before["fact_index"].items()
    }


@pytest.mark.parametrize("ticker", ["AAPL", "NVDA"])
def test_fact_id_regression_reports_no_added_disappeared_or_changed_ids(ticker):
    """Counts before/after a change: total, identical, added, disappeared, changed."""
    before = json.loads(BASELINE.read_text())[ticker]["fact_index"]
    after = fact_index(capture_snapshot(ticker, "2026-10-04"))[0]
    before_ids, after_ids = set(before), set(after)
    identical = before_ids & after_ids
    added = sorted(after_ids - before_ids)
    disappeared = sorted(before_ids - after_ids)
    changed = sorted(key for key in identical if before[key]["value"] != after[key]["value"])
    assert disappeared == [], f"{ticker} disappeared fact_ids: {disappeared}"
    assert added == [], f"{ticker} added fact_ids: {added}"
    assert changed == [], f"{ticker} changed values: {changed}"
    assert len(after_ids) == len(before_ids)
    assert len(identical) == len(before_ids)


def test_week_and_month_use_last_actual_trading_day_with_gaps():
    days = ["2026-01-29", "2026-01-30", "2026-02-02", "2026-02-05",
            "2026-02-10", "2026-02-27", "2026-03-02"]
    assert bucket_dates(days, "weekly_3m", "2026-03-02") == [
        "2026-01-30", "2026-02-05", "2026-02-10", "2026-02-27", "2026-03-02"]
    assert bucket_dates(days, "monthly_2y", "2026-03-02") == [
        "2026-01-30", "2026-02-27", "2026-03-02"]


def test_window_before_data_start_and_daily_limit():
    days = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(15)]
    assert bucket_dates(days, "quarterly_5y", days[-1]) == [days[-1]]
    assert bucket_dates(days, "daily_10", days[-1]) == days[-10:]


def test_display_rounding_is_deterministic():
    assert display_value("1.234567890123456789", "ratio") == "1.2346"
    assert display_value(333.695, "USD/share") == "333.70"
    assert display_value(None, "ratio") is None


def test_derived_landmark_card_renders_shared_display():
    snapshot, _, _ = prepare_evidence("AAPL", "2026-10-04")
    landmark = next(item for item in snapshot["derived_facts"] if item["name"] == "high_52w")
    draft = {"ticker": "AAPL", "as_of": "2026-10-04", "horizon": "中期",
             "bias": "中性", "action": "回避", "confidence": "低",
             "entry_range": None, "stop_loss": None, "target_price": None,
             "fact_ids": [landmark["fact_id"]],
             "reasons": [{"text": "区间高点 {fact:" + landmark["fact_id"] + "}",
                          "fact_ids": [landmark["fact_id"]]}],
             "invalidations": [{"kind": "close_above", "price": 400, "text": "突破区间"}]}
    card = build_card(draft, snapshot)
    assert display_value(landmark["value"], landmark["unit"]) in render_card(card, snapshot)


def test_byte_cap_uses_actual_wire_json_spacing():
    result = fit_envelope({"status": "ok", "data": {
        "rows": [{"date": str(i), "facts": [{"fact_id": "x" * 50, "display": "1.00"}]} for i in range(30)]}},
        max_bytes=2000)
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 2000


def test_oversized_base_is_explicitly_marked(monkeypatch):
    monkeypatch.setattr("thesis_tracker.decision.evidence.MAX_ENVELOPE_BYTES", 1000)
    _, base, _ = prepare_evidence("AAPL", "2026-10-04")
    assert base["truncated"] is True
    assert base["status"] == "error"
    assert base["truncation_reason"]


def test_envelope_limit_is_explicit_and_never_silent():
    rows = [{"date": str(i), "fact_id": "x" * 1000} for i in range(100)]
    result = fit_envelope({"status": "ok", "data": {"rows": rows}}, max_bytes=2048)
    assert result["truncated"] is True
    assert result["truncation_reason"]
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 2048
    assert len(result["data"]["rows"]) < len(rows)
    assert MAX_ENVELOPE_BYTES == 32 * 1024


def test_base_pack_and_landmarks_are_grounded_in_snapshot():
    snapshot, base, catalog = prepare_evidence("AAPL", "2026-10-04")
    index, conflicts = fact_index(snapshot)
    assert not conflicts
    for item in base["facts"]:
        assert item["fact_id"] in index
        assert item["display"] == display_value(index[item["fact_id"]]["value"], item["unit"])
    assert {"high_52w", "low_52w", "high_3y", "low_3y", "high_5y", "low_5y",
            "return_1m", "return_3m", "return_6m", "return_1y"} <= {
        item["name"] for item in base["facts"]}
    assert any(item["name"] == "sma_200" for item in base["facts"])
    assert "data" not in catalog.lower()
    assert base["gaps"] == data_gaps(snapshot)


def test_card_render_uses_same_display_rule_as_model_evidence():
    snapshot = capture_snapshot("AAPL", "2026-10-04")
    rsi = snapshot["calls"][1]["envelope"]["data"]["latest"]["values"]["rsi_14"]
    price = snapshot["calls"][0]["envelope"]["fact_id"]
    draft = {"ticker": "AAPL", "as_of": "2026-10-04", "horizon": "中期",
             "bias": "看多", "action": "买入", "confidence": "中",
             "entry_range": [300, 320], "stop_loss": 290, "target_price": 400,
             "fact_ids": [price, rsi["fact_id"]],
             "reasons": [{"text": "RSI {fact:" + rsi["fact_id"] + "}",
                          "fact_ids": [rsi["fact_id"]]}],
             "invalidations": [{"kind": "close_below", "price": 290, "text": "跌破止损位"}]}
    card = build_card(draft, snapshot)
    assert card["facts"][1]["display"] == display_value(rsi["value"], rsi["unit"])
    assert display_value(rsi["value"], rsi["unit"]) in render_card(card, snapshot)


def test_return_landmark_does_not_use_stale_window_start():
    def row(day, close):
        value = {"value": close, "unit": "USD/share"}
        return {"date": day, "fact_id": f"tiingo|TEST|{day}|daily", "close": value,
                "adjusted_close": value, "high": value, "low": value}
    snapshot = {"ticker": "TEST", "calls": [{"tool": "get_price_history",
        "args": {"symbol": "TEST"}, "envelope": {"status": "ok", "data": {
            "rows": [row("2025-01-02", 100), row("2026-10-02", 200)]}}}]}
    names = {item["name"] for item in recompute_derived(snapshot)}
    assert "return_1m" not in names
    assert "return_1y" not in names
    assert "high_52w" not in names


def test_indicator_history_facts_are_recoverable_from_snapshot():
    snapshot, _, _ = prepare_evidence("AAPL", "2026-10-04")
    view = history_view(snapshot, "get_indicators", "weekly_3m", ["rsi_14", "macd_histogram"])
    index, conflicts = fact_index(snapshot)
    assert not conflicts
    assert 1 <= len(view["data"]["rows"]) <= 15
    for row in view["data"]["rows"]:
        for fact in row["facts"]:
            assert index[fact["fact_id"]]["value"] is not None
            assert fact["display"] == index[fact["fact_id"]]["display"]


def test_historical_null_indicator_remains_a_visible_gap():
    snapshot = {"calls": [{"tool": "get_indicators_history",
        "args": {"symbol": "TEST"}, "envelope": {"status": "ok", "as_of": "2026-10-04",
        "data": {"rows": [{"date": "2026-01-02", "values": {
            "rsi_14": {"value": None, "reason": {"code": "insufficient_history", "message": "需要更多价格。"}}}}]}}}]}
    gaps = data_gaps(snapshot)
    assert len(gaps) == 1
    assert gaps[0]["reason"]["code"] == "insufficient_history"


def test_history_view_shows_null_reason_to_model(monkeypatch):
    snapshot, _, _ = prepare_evidence("AAPL", "2026-10-04")

    def missing(symbol, *, as_of, full_history, limit, end_date):
        return {"status": "ok", "as_of": as_of, "data": {"rows": [{"date": end_date,
            "values": {"rsi_14": {"value": None, "fact_id": None, "unit": "index",
                                  "reason": {"code": "insufficient_history", "message": "根数不足。"}}}}],
            "latest": None}}

    monkeypatch.setattr("thesis_tracker.decision.evidence.get_indicators", missing)
    view = history_view(snapshot, "get_indicators", "daily_10", ["rsi_14"])
    assert all(row["gaps"][0]["reason"]["code"] == "insufficient_history"
               for row in view["data"]["rows"])
