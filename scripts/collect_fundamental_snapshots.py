"""Collect SEC periodic XBRL snapshots before using the read-only fundamentals tool."""

from __future__ import annotations

import argparse
import json
from datetime import date

from thesis_tracker.financial.pit_collect import collect_filing_snapshots
from thesis_tracker.prices_batch import load_universe


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tickers", nargs="*", default=load_universe()[:-1])
    parser.add_argument("--since", type=date.fromisoformat, default=date(2015, 1, 1))
    parser.add_argument("--until", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    for ticker in args.tickers:
        print(json.dumps(collect_filing_snapshots(
            ticker, since=args.since, until=args.until,
        )), flush=True)


if __name__ == "__main__":
    main()
