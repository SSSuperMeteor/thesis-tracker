"""The one command that starts the workbench."""

from __future__ import annotations

import json
import urllib.request

import pytest


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect handler that returns the 302 instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def test_help_lists_the_port_and_host_options(capsys):
    from thesis_tracker.webapp.cli import main

    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    printed = capsys.readouterr().out
    assert "--port" in printed and "--host" in printed
    assert "127.0.0.1" in printed  # the loopback default is stated in the help


def test_a_non_loopback_host_is_refused_before_starting(capsys):
    from thesis_tracker.webapp.cli import main

    assert main(["--host", "0.0.0.0"]) == 2
    assert "只允许在本机回环地址上绑定" in capsys.readouterr().err


def test_startup_line_prints_a_usable_url_with_the_token(capsys, tmp_path):
    from thesis_tracker.webapp.cli import build_parser
    from thesis_tracker.webapp.server import WebApp, format_startup

    args = build_parser().parse_args(["--port", "0", "--no-worker"])
    app = WebApp(fact_db=tmp_path / "f.db", price_db=tmp_path / "p.db",
                 card_db=tmp_path / "c.db", job_db=tmp_path / "j.db",
                 host=args.host, port=0, start_worker=False)
    app.start()
    try:
        text = format_startup(app)
        assert app.url in text
        assert "127.0.0.1" in text and str(app.port) in text
        # The printed URL really is the thing that opens the page.  It answers
        # with the cookie-setting redirect, so no redirect handler is used.
        opener = urllib.request.build_opener(_NoRedirect)
        request = urllib.request.Request(app.url)
        request.add_header("Host", f"127.0.0.1:{app.port}")
        with pytest.raises(urllib.error.HTTPError) as redirect:
            opener.open(request, timeout=10)
        assert redirect.value.code == 302
        assert "dsh_token=" in redirect.value.headers["Set-Cookie"]
    finally:
        app.stop()


def test_the_webapp_console_script_is_declared():
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["project"]["scripts"]["webapp"] == "thesis_tracker.webapp.cli:main"


def test_startup_reports_interrupted_jobs(tmp_path):
    from thesis_tracker.webapp.server import WebApp, format_startup

    app = WebApp(fact_db=tmp_path / "f.db", price_db=tmp_path / "p.db",
                 card_db=tmp_path / "c.db", job_db=tmp_path / "j.db", port=0,
                 start_worker=False)
    job = app.store.create("analyze", {"ticker": "AAPL"})
    app.store.mark_running(job["job_id"])
    app.start()
    try:
        assert job["job_id"] in app.interrupted_jobs
        assert "已标记为中断" in format_startup(app)
        job_record = app.store.get(job["job_id"])
        assert job_record["status"] == "interrupted"
        assert json.dumps(job_record, ensure_ascii=False)
    finally:
        app.stop()
