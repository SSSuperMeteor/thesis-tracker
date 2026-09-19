from __future__ import annotations

import re
from pathlib import Path

import pytest

from thesis_tracker.evaluation.retrieval_stage2 import (
    calculate_metrics,
    load_and_validate_questions,
    load_valid_chunk_catalog,
)

ROOT = Path(__file__).resolve().parents[1]


def test_ground_truth_covers_every_current_ticker_with_ten_questions(
    stage2_benchmark_corpus: Path,
) -> None:
    catalog = load_valid_chunk_catalog(stage2_benchmark_corpus)
    logical_catalog = dict(catalog)
    for chunk_id, metadata in catalog.items():
        logical_id = re.sub(r"::chunk_\d{3}$", "", chunk_id)
        logical_catalog.setdefault(logical_id, metadata)
    questions = load_and_validate_questions(
        ROOT / "eval/retrieval_stage2_questions.json",
        logical_catalog,
    )

    corpus_tickers = {metadata["ticker"] for metadata in catalog.values()}
    question_tickers = {question["ticker"] for question in questions}

    assert question_tickers == corpus_tickers
    for ticker in corpus_tickers:
        assert sum(question["ticker"] == ticker for question in questions) == 10


def test_calculate_metrics_uses_expected_rank() -> None:
    records = [
        {
            "retrieval": {
                "bm25": {
                    "expected_rank": 2,
                    "hit_at_1": False,
                    "hit_at_3": True,
                    "hit_at_5": True,
                    "hit_at_10": True,
                    "hit_at_20": True,
                    "reciprocal_rank": 0.5,
                }
            }
        },
        {
            "retrieval": {
                "bm25": {
                    "expected_rank": None,
                    "hit_at_1": False,
                    "hit_at_3": False,
                    "hit_at_5": False,
                    "hit_at_10": False,
                    "hit_at_20": False,
                    "reciprocal_rank": 0.0,
                }
            }
        },
    ]

    metrics = calculate_metrics(records, "bm25")

    assert metrics == {
        "questions": 2,
        "recall_at_1": 0.0,
        "recall_at_3": 0.5,
        "recall_at_5": 0.5,
        "recall_at_10": 0.5,
        "recall_at_20": 0.5,
        "mrr": 0.25,
    }


def test_ground_truth_rejects_a_missing_expected_chunk(tmp_path: Path) -> None:
    manifest = tmp_path / "questions.json"
    manifest.write_text(
        """[
          {
            "ticker": "TEST",
            "form_type": "10-Q",
            "question_type": "risk",
            "question": "What changed?",
            "expected_chunk_id": "missing",
            "expected_section": "Risk Factors"
          }
        ]""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="each ticker must have 10 questions"):
        load_and_validate_questions(
            manifest,
            {
                "real": {
                    "ticker": "TEST",
                    "form_type": "10-Q",
                    "title": "Risk Factors",
                }
            },
        )
