"""Batch price ingestion contract, with no network calls."""

from thesis_tracker.prices_batch import load_universe, run_batch


class Provider:
    def __init__(self):
        self.calls = []

    def fetch(self, symbol, start_date, end_date):
        self.calls.append((symbol, start_date, end_date))
        return [{
            "date": start_date, "open": 10, "high": 12, "low": 9, "close": 11,
            "volume": 100, "adjOpen": 8, "adjHigh": 10, "adjLow": 7,
            "adjClose": 9, "adjVolume": 120, "divCash": 0,
            "splitFactor": 1,
        }]


def test_universe_reads_existing_definition_and_adds_benchmark(tmp_path):
    definition = tmp_path / "universe.py"
    definition.write_text('TICKERS = ("AAA", "BBB")\n')
    assert load_universe(definition) == ("AAA", "BBB", "SPY")


def test_batch_completes_in_two_bounded_repeated_runs(tmp_path):
    definition = tmp_path / "universe.py"
    definition.write_text('TICKERS = ' + repr(tuple(f"SYMBOL{i}" for i in range(11))) + '\n')
    provider = Provider()
    db = tmp_path / "prices.db"
    first = run_batch(start_date="2015-01-01", end_date="2024-01-01", db_path=db,
                      universe_path=definition, provider=provider)
    assert first["requests"] == 10
    assert sum(value["status"] == "ok" for value in first["results"].values()) == 10
    second = run_batch(start_date="2015-01-01", end_date="2024-01-01", db_path=db,
                       universe_path=definition, provider=provider)
    assert second["requests"] == 2
    assert all(value["status"] == "ok" for value in second["results"].values())
    third = run_batch(start_date="2015-01-01", end_date="2024-01-01", db_path=db,
                      universe_path=definition, provider=provider)
    assert third["requests"] == 0
    assert len(provider.calls) == 12
