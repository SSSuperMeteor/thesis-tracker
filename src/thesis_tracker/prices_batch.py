"""Bounded CLI for loading the Stage 3 universe plus the market benchmark."""

from __future__ import annotations

import argparse
import ast
import json
from datetime import date
from pathlib import Path

from thesis_tracker.prices import DEFAULT_DB, TiingoProvider, ingest_price_history

DEFAULT_UNIVERSE = Path(__file__).resolve().parents[2] / "scripts/stage3_stress_8metric.py"


def load_universe(path: Path = DEFAULT_UNIVERSE) -> tuple[str, ...]:
    """Read the existing stress universe without importing its SEC pipeline."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "TICKERS" for target in node.targets
        ):
            tickers = ast.literal_eval(node.value)
            if not isinstance(tickers, tuple) or not all(isinstance(item, str) for item in tickers):
                raise ValueError("TICKERS must be a tuple of symbols")
            return tuple(dict.fromkeys((*tickers, "SPY")))
    raise ValueError("stress universe TICKERS not found")


class _CountingProvider:
    def __init__(self, provider):
        self.provider = provider
        self.requests = 0

    def fetch(self, symbol: str, start_date: str, end_date: str) -> list[dict]:
        self.requests += 1
        return self.provider.fetch(symbol, start_date, end_date)


def run_batch(
    *, start_date: str = "2015-01-01", end_date: str | None = None,
    db_path: Path | str = DEFAULT_DB, universe_path: Path = DEFAULT_UNIVERSE,
    provider=None, max_requests: int = 10,
) -> dict:
    """Run one bounded pass; repeat to fill the remaining uncached symbols."""
    end_date = end_date or date.today().isoformat()
    counted = _CountingProvider(provider or TiingoProvider())
    results = ingest_price_history(
        list(load_universe(universe_path)), start_date, end_date,
        db_path=db_path, provider=counted, max_requests=max_requests,
    )
    return {"requests": counted.requests, "results": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load Tiingo daily history for the stress universe and SPY")
    parser.add_argument("--start-date", default="2015-01-01")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB)
    arguments = parser.parse_args(argv)
    result = run_batch(start_date=arguments.start_date, end_date=arguments.end_date, db_path=arguments.db_path)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if any(item["status"] == "error" for item in result["results"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
