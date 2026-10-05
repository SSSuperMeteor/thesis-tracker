"""--horizon selects the prefetched history tier and is enforced by D13."""

from __future__ import annotations

import json
import socket

import pytest

from thesis_tracker.decision.agent import SYSTEM_PROMPT, run_analysis, system_prompt
from thesis_tracker.decision.analyze_cli import main as analyze_main
from thesis_tracker.decision.core import capture_snapshot
from thesis_tracker.decision.evidence import (
    HORIZON_RESOLUTIONS,
    prepare_evidence,
)

TICKER = "AAPL"
AS_OF = "2026-10-04"
TIER_TOOL = "get_price_history_tier"


@pytest.fixture
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


def test_system_prompt_states_the_requested_horizon():
    assert "__HORIZON__" in SYSTEM_PROMPT
    for label in ("短期", "中期", "长期"):
        text = system_prompt(label)
        assert "__HORIZON__" not in text
        assert f"「{label}」" in text


def test_horizon_to_tier_mapping_is_fixed():
    assert HORIZON_RESOLUTIONS == {"short": "weekly_3m", "mid": "monthly_2y",
                                   "long": "quarterly_5y"}


@pytest.mark.parametrize(("horizon", "resolution"), sorted(HORIZON_RESOLUTIONS.items()))
def test_prefetch_tier_follows_the_requested_horizon(offline, horizon, resolution):
    snapshot, base, _ = prepare_evidence(TICKER, AS_OF, horizon=horizon)
    tier_calls = [call for call in snapshot["calls"] if call["tool"] == TIER_TOOL]
    assert len(tier_calls) == 1
    assert tier_calls[0]["args"]["resolution"] == resolution
    assert tier_calls[0]["args"]["as_of"] == AS_OF
    assert tier_calls[0]["envelope"]["as_of"] == AS_OF
    assert base["horizon_history"]["resolution"] == resolution
    assert any(window["resolution"] == resolution for window in snapshot["evidence_windows"])
    assert snapshot["horizon_resolution"] == resolution


@pytest.mark.parametrize(("horizon", "label"), [("short", "短期"), ("mid", "中期"), ("long", "长期")])
def test_base_pack_is_horizon_invariant_apart_from_the_tier(offline, horizon, label):
    snapshot, base, catalog = prepare_evidence(TICKER, AS_OF, horizon=horizon)
    assert snapshot["requested_horizon"] == label
    reference, reference_base, reference_catalog = prepare_evidence(TICKER, AS_OF, horizon="mid")
    assert {key: value for key, value in base.items() if key != "horizon_history"} == \
        {key: value for key, value in reference_base.items() if key != "horizon_history"}
    assert catalog == reference_catalog


class ScriptedClient:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    def complete(self, *, messages, tools, max_tokens):
        self.requests.append(json.loads(json.dumps(messages)))
        reply = next(self.replies)
        return {"model": "deepseek-flash", "system_fingerprint": "fp",
                "usage": {"prompt_tokens": 100, "completion_tokens": 50,
                          "prompt_cache_hit_tokens": 0},
                "message": {"role": "assistant", "content": None,
                            "reasoning_content": "checked", "tool_calls": None},
                "finish_reason": "stop", **reply}


def final_reply(draft):
    return {"message": {"role": "assistant", "content": json.dumps(draft, ensure_ascii=False),
                        "reasoning_content": "checked", "tool_calls": None}}


def legal_draft(horizon):
    snapshot = capture_snapshot(TICKER, AS_OF)
    close = snapshot["calls"][0]["envelope"]["fact_id"]
    return {"ticker": TICKER, "as_of": AS_OF, "horizon": horizon, "bias": "看多",
            "action": "买入", "confidence": "中", "entry_range": [324, 334],
            "stop_loss": 290, "target_price": 400, "fact_ids": [close],
            "reasons": [{"text": "收盘价 {fact:" + close + "}", "fact_ids": [close]}],
            "invalidations": [{"kind": "close_below", "price": 290, "text": "跌破止损位"}],
            "stop_rationale": "跌破 {fact:" + close + "} 离场。",
            "target_rationale": "上看 {fact:" + close + "} 上方。"}


def test_run_analysis_defaults_to_mid_and_archives_the_requested_horizon(offline, tmp_path):
    client = ScriptedClient([final_reply(legal_draft("中期"))])
    path = tmp_path / "cards.db"
    result = run_analysis(TICKER, AS_OF, client=client, archive_path=path)
    assert result["status"] == "passed"
    assert result["snapshot"]["requested_horizon"] == "中期"
    assert result["card"]["horizon"] == "中期"
    import sqlite3
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT requested_horizon FROM decision_attempts").fetchall()
    assert [row[0] for row in rows] == ["中期"]


def test_card_horizon_mismatch_is_rejected_under_the_requested_horizon(offline, tmp_path):
    client = ScriptedClient([final_reply(legal_draft("中期")) for _ in range(3)])
    path = tmp_path / "cards.db"
    result = run_analysis(TICKER, AS_OF, horizon="short", client=client,
                          archive_path=path)
    assert result["status"] == "rejected"
    assert result["reason"] == "correction_limit"
    assert any(item["rule"] == "D13" for item in result["violations"])


def test_analyze_cli_exposes_horizon_and_defaults_to_mid(offline, tmp_path, capsys):
    client = ScriptedClient([final_reply(legal_draft("短期"))])
    assert analyze_main([TICKER, "--as-of", AS_OF, "--horizon", "short"],
                        client=client, archive_path=tmp_path / "short.db") == 0
    assert "__DSH" not in capsys.readouterr().out
    client = ScriptedClient([final_reply(legal_draft("中期"))])
    assert analyze_main([TICKER, "--as-of", AS_OF],
                        client=client, archive_path=tmp_path / "mid.db") == 0


def test_analyze_cli_rejects_an_unknown_horizon():
    with pytest.raises(SystemExit):
        analyze_main([TICKER, "--horizon", "weekly"])
