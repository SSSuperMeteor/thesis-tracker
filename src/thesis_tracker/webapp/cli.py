"""``uv run webapp``: start the loopback-only workbench and print its URL."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from thesis_tracker.decision.core import DEFAULT_ARCHIVE
from thesis_tracker.financial.pit_store import DEFAULT_FACT_DB
from thesis_tracker.prices import DEFAULT_DB as DEFAULT_PRICE_DB
from thesis_tracker.webapp.jobs import DEFAULT_JOB_DB

DEFAULT_PORT = 8765


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="webapp",
        description="Start the local thesis-tracker workbench (loopback only).")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (default 127.0.0.1, loopback only)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"port to bind (default {DEFAULT_PORT}); 0 picks a free one")
    parser.add_argument("--token", default=None,
                        help="fixed access token; by default a random one is generated")
    parser.add_argument("--fact-db", default=str(DEFAULT_FACT_DB))
    parser.add_argument("--price-db", default=str(DEFAULT_PRICE_DB))
    parser.add_argument("--card-db", default=str(DEFAULT_ARCHIVE))
    parser.add_argument("--job-db", default=str(DEFAULT_JOB_DB))
    parser.add_argument("--no-worker", action="store_true",
                        help="start the web server without the analysis worker")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Imported here so ``webapp --help`` never touches a database.
    from thesis_tracker.webapp.server import WebApp, format_startup

    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print(f"拒绝：只允许在本机回环地址上绑定，收到 --host {args.host}。", file=sys.stderr)
        return 2
    try:
        app = WebApp(fact_db=Path(args.fact_db), price_db=Path(args.price_db),
                     card_db=Path(args.card_db), job_db=Path(args.job_db),
                     host=args.host, port=args.port, token=args.token,
                     start_worker=not args.no_worker)
    except OSError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1
    app.start()
    print(format_startup(app), flush=True)
    try:
        # Park the main thread; the HTTP server and the worker run in daemons.
        import threading

        threading.Event().wait()
    except KeyboardInterrupt:
        print("已停止。")
    finally:
        app.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
