"""Tests for the chunking v2 retrieval regression runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from thesis_tracker.evaluation.retrieval_stage2 import audit_questions
from thesis_tracker.evaluation.retrieval_stage2_chunkv2 import (
    _crowding_analysis,
    _method_record,
    _rank_delta,
    load_chunkv2_catalog,
    logical_parent_id,
)

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "eval/retrieval_stage2_questions.json"


@dataclass(frozen=True)
class _FakeResult:
    chunk_id: str
    text: str = "evidence text"

    def to_dict(self) -> dict[str, str]:
        return {"chunk_id": self.chunk_id, "text": self.text}


def test_logical_parent_id_strips_subchunk_suffix_only() -> None:
    assert (
        logical_parent_id("acc::note::03::segment_reporting::chunk_004")
        == "acc::note::03::segment_reporting"
    )
    assert logical_parent_id("acc::note::03::segment_reporting") == "acc::note::03::segment_reporting"
    assert logical_parent_id("acc::part_i_item_1::chunk_012") == "acc::part_i_item_1"


def test_matching_accepts_subchunks_of_expected_parent() -> None:
    results = [
        _FakeResult("acc::part_i_item_2::chunk_001"),
        _FakeResult("acc::note::03::segment_reporting::chunk_003"),
        _FakeResult("acc::note::03::segment_reporting::chunk_000"),
    ]

    record = _method_record(
        results,
        ["acc::note::03::segment_reporting"],
        resolve=logical_parent_id,
        preview_chars=40,
    )

    assert record["expected_rank"] == 2
    assert record["hit_at_1"] is False
    assert record["hit_at_3"] is True
    assert record["reciprocal_rank"] == 0.5
    assert len(record["top20"]) == 3
    assert record["top20"][0]["text_preview"] == "evidence text"


def test_matching_still_misses_a_different_parent() -> None:
    results = [_FakeResult("acc::part_i_item_2::chunk_001")]

    record = _method_record(
        results,
        ["acc::note::03::segment_reporting"],
        resolve=logical_parent_id,
        preview_chars=40,
    )

    assert record["expected_rank"] is None
    assert record["hit_at_20"] is False
    assert record["reciprocal_rank"] == 0.0


def test_crowding_detects_sibling_slots_that_displace_ground_truth() -> None:
    record = {
        "id": "stage2-001",
        "ticker": "AMD",
        "question_type": "risk",
        "expected_chunk_ids": ["acc::note::03::risk_factors"],
        "retrieval": {
            "hybrid": {
                "expected_rank": 7,
                "top20": [
                    _FakeResult("acc::part_i_item_2::chunk_000").to_dict() | {"rank": 1},
                    _FakeResult("acc::part_i_item_2::chunk_001").to_dict() | {"rank": 2},
                    _FakeResult("acc::part_i_item_2::chunk_002").to_dict() | {"rank": 3},
                    _FakeResult("acc::note::03::risk_factors::chunk_000").to_dict() | {"rank": 4},
                    _FakeResult("acc::part_i_item_1::chunk_004").to_dict() | {"rank": 5},
                ],
            },
            "bm25": {"expected_rank": 4, "top20": []},
            "vector": {"expected_rank": 4, "top20": []},
        },
    }

    crowding = _crowding_analysis([record], logical_parent_id)["per_method"]["hybrid"]

    assert crowding["cases_with_duplicate_parent_in_top5"] == 1
    assert crowding["top5_slots_consumed_by_duplicates"] == 2
    assert crowding["cases_with_ground_truth_parent_crowding"] == 0
    assert crowding["crowding_cases_where_ground_truth_missed_top5"] == 1
    blocked = crowding["blocked_cases"][0]
    assert blocked["crowded_parent"] == "acc::part_i_item_2"
    assert blocked["crowded_parent_is_ground_truth"] is False
    assert blocked["unique_parents_top5"] == 3


def test_crowding_flags_ground_truth_parent_duplicates() -> None:
    record = {
        "id": "stage2-002",
        "ticker": "AMD",
        "question_type": "segment",
        "expected_chunk_ids": ["acc::note::03::segment_reporting"],
        "retrieval": {
            method: {
                "expected_rank": 1,
                "top20": [
                    _FakeResult("acc::note::03::segment_reporting::chunk_000").to_dict()
                    | {"rank": 1},
                    _FakeResult("acc::note::03::segment_reporting::chunk_001").to_dict()
                    | {"rank": 2},
                    _FakeResult("acc::part_i_item_2::chunk_003").to_dict() | {"rank": 3},
                ],
            }
            for method in ("bm25", "vector", "hybrid")
        },
    }

    crowding = _crowding_analysis([record], logical_parent_id)["per_method"]["hybrid"]

    assert crowding["cases_with_ground_truth_parent_crowding"] == 1
    assert crowding["crowding_cases_where_ground_truth_missed_top5"] == 0


def test_rank_delta_reports_direction_and_excluded_misses() -> None:
    assert _rank_delta(5, 2) == 3
    assert _rank_delta(2, 5) == -3
    assert _rank_delta(3, 3) == 0
    assert _rank_delta(None, None) == 0
    assert _rank_delta(None, 4) is None
    assert _rank_delta(4, None) is None


def test_v2_manifest_resolves_against_logical_parent_catalog() -> None:
    _, parents = load_chunkv2_catalog(ROOT / "data/corpus.db")

    valid, invalid = audit_questions(QUESTIONS, parents)

    assert invalid == []
    assert len(valid) == 70
    tickers = {question["ticker"] for question in valid}
    assert tickers == {"AMD", "LITE", "NVDA", "ORCL", "SNDK", "TSLA", "VRT"}
    for question in valid:
        assert question["expected_chunk_id"] in parents


def test_expected_parents_have_consistent_metadata() -> None:
    subchunks, parents = load_chunkv2_catalog(ROOT / "data/corpus.db")

    assert len(subchunks) == sum(item["subchunk_count"] for item in parents.values())
    for parent_id, parent in parents.items():
        assert parent["subchunk_count"] >= 1
        assert parent["subchunk_chars"] > 0
        for subchunk_id, subchunk in subchunks.items():
            if logical_parent_id(subchunk_id) != parent_id:
                continue
            assert subchunk["ticker"] == parent["ticker"]
            assert subchunk["form_type"] == parent["form_type"]
            assert subchunk["title"] == parent["title"]
