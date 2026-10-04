"""Read-only local evidence inspection."""

from __future__ import annotations

import argparse
from datetime import date

from thesis_tracker.decision.core import capture_snapshot, data_gaps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect local Decision Mode evidence")
    parser.add_argument("ticker")
    parser.add_argument("--as-of", default=date.today().isoformat())
    args = parser.parse_args(argv)
    snapshot = capture_snapshot(args.ticker, args.as_of)
    print(f"{snapshot['ticker']} {snapshot['as_of']} 事实表")
    for item in snapshot["fact_index"].values():
        print(f"{item['name']} | {item['value']} {item['unit']} | {item['date_or_period']} | {item['fact_id']}")
    print("数据缺口")
    for gap in data_gaps(snapshot):
        reason = gap["reason"] or {}
        print(f"{gap['name']} | {gap['status']} | {reason.get('code', '')} | {reason.get('message', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
